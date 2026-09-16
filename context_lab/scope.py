"""Branch ref → (project, ticket) registry. Authoritative scope routing for hooks and MCP."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .engine import DEFAULT_DB
from .schemas import AgentError

BIND_HINT = "Run: context-lab scope bind --project P --ticket T"
_NO_BINDING = frozenset({"not_a_worktree", "unbound_branch", "detached_head"})


def _git(args, cwd=None, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "git failed").strip()
        line = err.splitlines()[-1] if err else "git failed"
        raise AgentError("not_a_worktree", line, hint=BIND_HINT)
    return result


def git_common_dir(cwd=None):
    cwd = cwd or os.getcwd()
    out = _git(["rev-parse", "--git-common-dir"], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.resolve()


def require_worktree(cwd=None):
    _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return cwd or os.getcwd()


def default_db():
    return str(DEFAULT_DB.expanduser().resolve())


def current_branch_ref(cwd=None):
    result = _git(["symbolic-ref", "-q", "HEAD"], cwd=cwd, check=False)
    if result.returncode != 0:
        raise AgentError("detached_head", "Detached HEAD; scope bind requires a branch", hint=BIND_HINT)
    ref = result.stdout.strip()
    if not ref:
        raise AgentError("detached_head", "Detached HEAD; scope bind requires a branch", hint=BIND_HINT)
    return ref


def _scope_json_path(cwd):
    out = _git(["rev-parse", "--git-path", "context-lab"], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.resolve() / "scope.json"


def _write_legacy_scope(project, ticket, db, cwd):
    resolved_db = str(Path(db).expanduser().resolve())
    path = _scope_json_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"project": project.strip(), "ticket": ticket.strip(), "db": resolved_db}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


@dataclass(frozen=True)
class MemoryScope:
    project: str
    ticket: str


@dataclass(frozen=True)
class BranchBinding:
    branch: str
    scope: MemoryScope


@dataclass(frozen=True)
class GitWorktree:
    cwd: str
    git_common_dir: str


@dataclass(frozen=True)
class ResolvedScope:
    worktree: GitWorktree
    branch: str
    scope: MemoryScope
    database: Path


@dataclass(frozen=True)
class BoundIdentity:
    status: Literal["bound"]
    worktree: Path
    git_common_dir: Path
    branch: str
    scope: MemoryScope
    database: Path


@dataclass(frozen=True)
class UnboundIdentity:
    status: Literal["unbound"]
    worktree: Path
    git_common_dir: Path
    branch: str


@dataclass(frozen=True)
class DetachedIdentity:
    status: Literal["detached"]
    worktree: Path
    git_common_dir: Path


@dataclass(frozen=True)
class OutsideIdentity:
    status: Literal["unavailable"]
    observed_cwd: Path
    reason: Literal["not_a_worktree"]


SessionIdentity = BoundIdentity | UnboundIdentity | DetachedIdentity | OutsideIdentity


@dataclass(frozen=True)
class RouteOnly:
    pass


ROUTE_ONLY = RouteOnly()
ScopeRequest = MemoryScope | None | RouteOnly


@dataclass(frozen=True)
class BoundRequest:
    identity: SessionIdentity
    scope: MemoryScope | None
    database: Path | None


class BranchBindingRegistry:
    def __init__(self, git_common_dir: Path):
        self._git_common_dir = Path(git_common_dir)
        self._path = self._git_common_dir / "context-lab" / "branch-bindings.sqlite3"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self):
        self._conn.close()

    def _ensure_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS repository_config (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
              memory_db TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS branch_bindings (
              branch_ref TEXT PRIMARY KEY,
              project TEXT NOT NULL CHECK (trim(project) <> ''),
              ticket TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
        """)
        sql = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='branch_bindings'",
        ).fetchone()[0]
        if sql and "trim(ticket)" in sql:
            self._conn.executescript("""
                CREATE TABLE branch_bindings_new (
                  branch_ref TEXT PRIMARY KEY,
                  project TEXT NOT NULL CHECK (trim(project) <> ''),
                  ticket TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                INSERT INTO branch_bindings_new
                  SELECT branch_ref, project, ticket, updated_at FROM branch_bindings;
                DROP TABLE branch_bindings;
                ALTER TABLE branch_bindings_new RENAME TO branch_bindings;
            """)
        self._conn.commit()

    def database(self) -> Path:
        row = self._conn.execute(
            "SELECT memory_db FROM repository_config WHERE singleton = 1",
        ).fetchone()
        if row:
            return Path(row["memory_db"])
        return Path(default_db())

    def _ensure_memory_db(self, database: Path | None) -> Path:
        row = self._conn.execute(
            "SELECT memory_db FROM repository_config WHERE singleton = 1",
        ).fetchone()
        if database is not None:
            resolved = Path(database).expanduser().resolve()
            if row:
                if row["memory_db"] != str(resolved):
                    self._conn.execute(
                        "UPDATE repository_config SET memory_db = ? WHERE singleton = 1",
                        (str(resolved),),
                    )
            else:
                self._conn.execute(
                    "INSERT INTO repository_config (singleton, memory_db) VALUES (1, ?)",
                    (str(resolved),),
                )
            self._conn.commit()
            return resolved
        if row:
            return Path(row["memory_db"])
        resolved = Path(default_db())
        self._conn.execute(
            "INSERT INTO repository_config (singleton, memory_db) VALUES (1, ?)",
            (str(resolved),),
        )
        self._conn.commit()
        return resolved

    def bind(self, branch: str, project: str, ticket: str, *, replace=False) -> BranchBinding:
        project = project.strip()
        ticket = ticket.strip()
        if not project:
            raise AgentError("validation", "project required", field="project")
        scope = MemoryScope(project=project, ticket=ticket)
        existing = self.get(branch)
        if existing:
            if existing.scope == scope:
                return existing
            if not replace:
                raise AgentError(
                    "binding_conflict",
                    f"Branch already bound to {existing.scope.project}/{existing.scope.ticket}",
                    hint="Use --replace to overwrite",
                )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            """INSERT INTO branch_bindings (branch_ref, project, ticket, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(branch_ref) DO UPDATE SET
                 project = excluded.project,
                 ticket = excluded.ticket,
                 updated_at = excluded.updated_at""",
            (branch, project, ticket, now),
        )
        self._conn.commit()
        return BranchBinding(branch=branch, scope=scope)

    def unbind(self, branch: str) -> bool:
        cur = self._conn.execute("DELETE FROM branch_bindings WHERE branch_ref = ?", (branch,))
        self._conn.commit()
        return cur.rowcount > 0

    def get(self, branch: str) -> BranchBinding | None:
        row = self._conn.execute(
            "SELECT branch_ref, project, ticket FROM branch_bindings WHERE branch_ref = ?",
            (branch,),
        ).fetchone()
        if not row:
            return None
        return BranchBinding(
            branch=row["branch_ref"],
            scope=MemoryScope(project=row["project"], ticket=row["ticket"]),
        )

    def list_bindings(self) -> list[dict]:
        db = str(self.database())
        rows = self._conn.execute(
            "SELECT branch_ref, project, ticket, updated_at FROM branch_bindings ORDER BY branch_ref",
        ).fetchall()
        return [
            {
                "branch": row["branch_ref"],
                "project": row["project"],
                "ticket": row["ticket"],
                "updated_at": row["updated_at"],
                "database": db,
            }
            for row in rows
        ]


def resolved_to_dict(resolved: ResolvedScope) -> dict:
    return {
        "branch": resolved.branch,
        "project": resolved.scope.project,
        "ticket": resolved.scope.ticket,
        "database": str(resolved.database),
        "worktree": resolved.worktree.cwd,
        "git_common_dir": resolved.worktree.git_common_dir,
    }


_UNAVAILABLE_REASONS = {
    "not_a_worktree": "mcp_process_cwd_not_worktree",
    "unbound_branch": "unbound_branch",
    "detached_head": "detached_head",
}


def scope_status(cwd=None):
    """MCP diagnostic. Never raises for missing git cwd or unbound branch."""
    try:
        resolved = BranchScopes.resolve_current(cwd)
    except AgentError as e:
        reason = _UNAVAILABLE_REASONS.get(e.code, e.code)
        if e.code == "not_a_worktree":
            message = (
                "The MCP server was not started in a Git worktree; "
                "pass project to memory_context."
            )
        else:
            message = e.message
        return {"status": "unavailable", "reason": reason, "message": message}
    out = resolved_to_dict(resolved)
    out["status"] = "available"
    return out


def identity_at(cwd: str | Path) -> SessionIdentity:
    observed = Path(cwd).expanduser().resolve()
    root_result = _git(["rev-parse", "--show-toplevel"], cwd=observed, check=False)
    if root_result.returncode != 0:
        return OutsideIdentity(
            status="unavailable",
            observed_cwd=observed,
            reason="not_a_worktree",
        )
    worktree = Path(root_result.stdout.strip()).resolve()
    common = git_common_dir(observed)
    branch_result = _git(["symbolic-ref", "-q", "HEAD"], cwd=observed, check=False)
    branch = branch_result.stdout.strip()
    if branch_result.returncode != 0 or not branch:
        return DetachedIdentity(
            status="detached",
            worktree=worktree,
            git_common_dir=common,
        )
    registry = BranchBindingRegistry(common)
    try:
        binding = registry.get(branch)
        if binding is None:
            return UnboundIdentity(
                status="unbound",
                worktree=worktree,
                git_common_dir=common,
                branch=branch,
            )
        return BoundIdentity(
            status="bound",
            worktree=worktree,
            git_common_dir=common,
            branch=branch,
            scope=binding.scope,
            database=registry.database(),
        )
    finally:
        registry.close()


def place_token(identity: SessionIdentity) -> str:
    if isinstance(identity, BoundIdentity):
        ticket = identity.scope.ticket or "(baseline)"
        return f"{identity.scope.project}/{ticket}"
    if isinstance(identity, UnboundIdentity):
        return f"unbound:{identity.branch}"
    if isinstance(identity, DetachedIdentity):
        return "detached"
    return f"unavailable:{identity.reason}"


def switch_token(previous: str, current: str) -> str | None:
    if previous == current:
        return None

    def bound_parts(place: str) -> tuple[str, str] | None:
        if place == "detached" or place.startswith(("unbound:", "unavailable:")):
            return None
        project, separator, ticket = place.partition("/")
        return (project, ticket) if separator and project else None

    old = bound_parts(previous)
    new = bound_parts(current)
    if old and new and old[0] == new[0]:
        return f"ticket {old[1]}→{new[1]}"
    if old and new:
        return f"project {old[0]}→{new[0]}"
    return f"place {previous}→{current}"


def identity_to_inspect(identity: SessionIdentity) -> dict:
    if isinstance(identity, OutsideIdentity):
        return {
            "status": identity.status,
            "observed_cwd": str(identity.observed_cwd),
            "reason": identity.reason,
        }
    out = {
        "status": identity.status,
        "worktree": str(identity.worktree),
        "git_common_dir": str(identity.git_common_dir),
    }
    if isinstance(identity, DetachedIdentity):
        return out
    out["branch"] = identity.branch
    if isinstance(identity, BoundIdentity):
        out.update({
            "project": identity.scope.project,
            "ticket": identity.scope.ticket,
            "database": str(identity.database),
        })
    return out


def _bind_identity(identity: SessionIdentity, requested: ScopeRequest) -> BoundRequest:
    database = identity.database if isinstance(identity, BoundIdentity) else None
    if requested is ROUTE_ONLY:
        return BoundRequest(identity=identity, scope=None, database=database)
    if requested is None:
        if not isinstance(identity, BoundIdentity):
            raise AgentError(
                "validation",
                "project required when cwd has no bound scope",
                field="project",
            )
        return BoundRequest(
            identity=identity,
            scope=identity.scope,
            database=identity.database,
        )
    project = requested.project.strip()
    ticket = requested.ticket.strip()
    if not project:
        raise AgentError("validation", "project required", field="project")
    scope = MemoryScope(project=project, ticket=ticket)
    if (
        isinstance(identity, BoundIdentity)
        and scope.project == identity.scope.project
        and scope.ticket != identity.scope.ticket
    ):
        raise AgentError(
            "scope_mismatch",
            f"Request scope {scope.project}/{scope.ticket} does not match binding "
            f"{identity.scope.project}/{identity.scope.ticket} on {identity.branch}",
        )
    return BoundRequest(identity=identity, scope=scope, database=database)


def bind_request(cwd: str | Path, requested: ScopeRequest) -> BoundRequest:
    return _bind_identity(identity_at(cwd), requested)


class BranchScopes:
    @staticmethod
    def _open(cwd=None):
        cwd = require_worktree(cwd)
        common = git_common_dir(cwd)
        wt = GitWorktree(cwd=str(Path(cwd).resolve()), git_common_dir=str(common))
        reg = BranchBindingRegistry(common)
        return wt, reg, cwd

    @classmethod
    def bind_current(
        cls,
        cwd,
        scope: MemoryScope,
        *,
        database=None,
        replace=False,
    ) -> ResolvedScope:
        wt, reg, cwd = cls._open(cwd)
        try:
            branch = current_branch_ref(cwd)
            db = reg._ensure_memory_db(Path(database) if database else None)
            binding = reg.bind(branch, scope.project, scope.ticket, replace=replace)
            _write_legacy_scope(binding.scope.project, binding.scope.ticket, db, cwd)
            return ResolvedScope(worktree=wt, branch=branch, scope=binding.scope, database=db)
        finally:
            reg.close()

    @classmethod
    def unbind_current(cls, cwd=None) -> bool:
        wt, reg, cwd = cls._open(cwd)
        try:
            branch = current_branch_ref(cwd)
            return reg.unbind(branch)
        finally:
            reg.close()

    @classmethod
    def resolve_current(cls, cwd=None) -> ResolvedScope:
        identity = identity_at(cwd or os.getcwd())
        if isinstance(identity, OutsideIdentity):
            raise AgentError("not_a_worktree", "Not inside a Git worktree", hint=BIND_HINT)
        if isinstance(identity, DetachedIdentity):
            raise AgentError(
                "detached_head",
                "Detached HEAD; scope bind requires a branch",
                hint=BIND_HINT,
            )
        if isinstance(identity, UnboundIdentity):
            raise AgentError(
                "unbound_branch",
                f"No scope binding for {identity.branch}",
                hint=BIND_HINT,
            )
        return ResolvedScope(
            worktree=GitWorktree(
                cwd=str(identity.worktree),
                git_common_dir=str(identity.git_common_dir),
            ),
            branch=identity.branch,
            scope=identity.scope,
            database=identity.database,
        )

    @classmethod
    def require_request_scope(cls, cwd, requested: MemoryScope) -> ResolvedScope:
        resolved = cls.resolve_current(cwd)
        req = MemoryScope(project=requested.project.strip(), ticket=requested.ticket.strip())
        if resolved.scope != req:
            raise AgentError(
                "scope_mismatch",
                f"Request scope {req.project}/{req.ticket} does not match binding "
                f"{resolved.scope.project}/{resolved.scope.ticket} on {resolved.branch}",
            )
        return resolved

    @classmethod
    def list_bindings(cls, cwd=None) -> list[dict]:
        _, reg, _ = cls._open(cwd)
        try:
            return reg.list_bindings()
        finally:
            reg.close()


def try_current_scope(cwd=None):
    try:
        return BranchScopes.resolve_current(cwd)
    except AgentError as e:
        if e.code in _NO_BINDING:
            return None
        raise



def build_parser(sub):
    scope = sub.add_parser("scope", help="Bind git branches to memory scope")
    scope_sub = scope.add_subparsers(dest="scope_command", required=True)
    bind_p = scope_sub.add_parser("bind", help="Bind current branch to project/ticket")
    bind_p.add_argument("--project", required=True)
    bind_p.add_argument("--ticket", default="")
    bind_p.add_argument("--db", default=None, help="Memory database path for this repository")
    bind_p.add_argument("--replace", action="store_true", help="Overwrite an existing binding")
    scope_sub.add_parser("unbind", help="Remove binding for current branch")
    scope_sub.add_parser("show", help="Show binding for current branch")
    scope_sub.add_parser("list", help="List all branch bindings in this repository")
    return scope


def dispatch(args):
    if args.scope_command == "bind":
        resolved = BranchScopes.bind_current(
            os.getcwd(),
            MemoryScope(args.project, args.ticket),
            database=args.db,
            replace=args.replace,
        )
        print(json.dumps(resolved_to_dict(resolved), indent=2))
        return 0
    if args.scope_command == "unbind":
        removed = BranchScopes.unbind_current()
        print(json.dumps({"removed": removed}, indent=2))
        return 0
    if args.scope_command == "show":
        resolved = BranchScopes.resolve_current()
        print(json.dumps(resolved_to_dict(resolved), indent=2))
        return 0
    if args.scope_command == "list":
        print(json.dumps(BranchScopes.list_bindings(), indent=2))
        return 0
    raise SystemExit(f"unknown scope command: {args.scope_command}")
