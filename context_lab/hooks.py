"""Worktree-scoped harness hooks: scope, ambient inject, session-start, commit lease."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import agent_api, usage
from .engine import DEFAULT_DB, ROOT
from .schemas import GATE_TEXT, AgentError, wire_dumps
from .store import Store

INJECT_BUDGET = 800  # header ~175 wire tokens; one journal pick ~270. 400 dropped every pick.
LEASE_TTL_MINUTES = 10
SCOPE_HINT = "Run: context-lab scope bind --project P --ticket T"
RECALL_HINT = "Run: context-lab hook recall-for --purpose commit"
CONTEXT_LAB_HOME = str(ROOT)
INSTALL_HINT = "pipx install git+https://github.com/erreep/context-lab.git"
# Advisory only: the pre-commit hook is the enforcement point. Handles `git -C x commit`, `git -c k=v commit`.
GIT_COMMIT_RE = re.compile(r"\bgit\s+(?:-{1,2}\S+(?:\s+[^-\s]\S*)?\s+)*commit\b")
UNBORN_HEAD = "unborn"
PRE_COMMIT_SCRIPT = """#!/bin/sh
# Context Lab recall lease gate. Must run before formatters that rewrite the index.
# lint-staged style rewrites need a fresh lease after they re-stage files.
# Upgrade path toward remote verification: a Context-Lab-Run commit trailer.
# Fails closed: a missing install blocks the commit with the fix below, never silently.
CONTEXT_LAB_HOME={home}
if command -v context-lab >/dev/null 2>&1; then
  exec context-lab hook gate-git commit
fi
if PYTHONPATH="$CONTEXT_LAB_HOME" python3 -c "import context_lab" >/dev/null 2>&1; then
  PYTHONPATH="$CONTEXT_LAB_HOME" exec python3 -m context_lab hook gate-git commit
fi
echo "context-lab: not installed. Run: {install}  (one-off bypass: git commit --no-verify)" >&2
exit 1
"""


def _git(args, cwd=None, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "git failed").strip()
        line = err.splitlines()[-1] if err else "git failed"
        raise AgentError("not_a_worktree", line, hint=SCOPE_HINT)
    return result


def git_path(name, cwd=None):
    out = _git(["rev-parse", "--git-path", name], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return path.resolve()


def require_worktree(cwd=None):
    _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return cwd or os.getcwd()


def default_db():
    # ponytail: db identity is the resolved path in v1; upgrade to a stored id in knowledge_bases.
    return str(DEFAULT_DB.resolve())


def scope_path(cwd=None):
    return git_path("context-lab", cwd=cwd) / "scope.json"


def lease_path(cwd=None):
    return git_path("context-lab", cwd=cwd) / "lease-commit.json"


def load_scope(cwd=None):
    """Resolve active scope from the branch binding registry (legacy scope.json is a cache)."""
    from .scope import BranchScopes
    try:
        resolved = BranchScopes.resolve_current(cwd)
    except AgentError as e:
        if e.code in {"unbound_branch", "detached_head", "not_a_worktree"}:
            raise AgentError("missing_scope", e.message, hint=SCOPE_HINT) from e
        raise
    return {
        "project": resolved.scope.project,
        "ticket": resolved.scope.ticket,
        "db": str(resolved.database),
        "branch": resolved.branch,
    }


def set_scope(project, ticket, db=None, cwd=None):
    """Bind the current branch; also refreshes legacy scope.json for migration."""
    from .scope import BranchScopes, MemoryScope
    resolved = BranchScopes.bind_current(
        cwd or os.getcwd(),
        MemoryScope(project=project, ticket=ticket),
        database=db,
        replace=True,
    )
    return {
        "project": resolved.scope.project,
        "ticket": resolved.scope.ticket,
        "db": str(resolved.database),
        "branch": resolved.branch,
    }


def _read_hook_stdin(raw=None):
    text = sys.stdin.read() if raw is None else raw
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("hook stdin must be a JSON object")
    return data


def _cwd_from_payload(payload, fallback=None):
    cwd = payload.get("cwd") or fallback or os.getcwd()
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("cwd required")
    return cwd


def inject(payload=None, event_name="UserPromptSubmit"):
    payload = payload if payload is not None else _read_hook_stdin()
    cwd = _cwd_from_payload(payload)
    try:
        scope = load_scope(cwd=cwd)
    except AgentError as e:
        print(e.hint or SCOPE_HINT, file=sys.stderr)
        return 0
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str):
        prompt = str(prompt)
    store = Store(scope["db"])
    try:
        view = agent_api.context(
            store,
            {"query": prompt, "project": scope["project"], "ticket": scope["ticket"]},
            budget=INJECT_BUDGET,
            detail="agent",
        )
        text = wire_dumps(view)
        usage.record(store, "hook", event_name, (scope["project"], scope["ticket"]), response=text)
    finally:
        store.close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }, ensure_ascii=False))
    return 0


def session_start(payload=None):
    payload = payload if payload is not None else _read_hook_stdin()
    cwd = _cwd_from_payload(payload)
    try:
        scope = load_scope(cwd=cwd)
    except AgentError as e:
        print(e.hint or SCOPE_HINT, file=sys.stderr)
        return 0
    store = Store(scope["db"])
    try:
        # Baseline + __global__ only: omit ticket so ticket decisions stay out.
        view = agent_api.context(
            store,
            {
                "query": "standing rules and hard gates for this session",
                "project": scope["project"],
            },
            budget=INJECT_BUDGET,
            detail="agent",
        )
        text = GATE_TEXT.strip() + "\n\n" + wire_dumps(view)
        usage.record(store, "hook", "SessionStart", (scope["project"], scope["ticket"]), response=text)
    finally:
        store.close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }, ensure_ascii=False))
    return 0


def _git_state(cwd=None):
    head = _git(["rev-parse", "HEAD"], cwd=cwd, check=False)
    head_oid = head.stdout.strip() if head.returncode == 0 else UNBORN_HEAD
    # Absolute: --git-dir is relative to cwd, so recall from root and gate from a subdir would mismatch.
    git_dir = _git(["rev-parse", "--absolute-git-dir"], cwd=cwd).stdout.strip()
    index_tree = _git(["write-tree"], cwd=cwd).stdout.strip()
    return head_oid, git_dir, index_tree


def recall_for(purpose="commit", cwd=None, budget=INJECT_BUDGET):
    if purpose != "commit":
        raise AgentError("validation", "purpose must be commit", field="purpose")
    cwd = require_worktree(cwd)
    scope = load_scope(cwd=cwd)
    staged = _git(["diff", "--cached", "--name-only"], cwd=cwd).stdout.strip()
    query = f"purpose={purpose}\nstaged:\n{staged or '(none)'}"
    store = Store(scope["db"])
    try:
        view = agent_api.context(
            store,
            {"query": query, "project": scope["project"], "ticket": scope["ticket"]},
            budget=budget,
            detail="agent",
        )
        head_oid, git_dir, index_tree = _git_state(cwd=cwd)
        now = datetime.now(timezone.utc)
        lease = {
            "run_id": view["run_id"],
            "db": scope["db"],
            "project": scope["project"],
            "ticket": scope["ticket"],
            "purpose": purpose,
            "worktree_git_dir": git_dir,
            "head_oid": head_oid,
            "index_tree": index_tree,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(minutes=LEASE_TTL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path = lease_path(cwd=cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lease, indent=2) + "\n", encoding="utf-8")
        text = json.dumps(view, indent=2, ensure_ascii=False)
        usage.record(store, "hook", "recall-for", (scope["project"], scope["ticket"]), response=text)
    finally:
        store.close()
    print(text)
    return 0


def _deny_out(adapter, reason):
    if adapter == "claude":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        return 0
    if adapter == "cursor":
        print(json.dumps({"permission": "deny", "userMessage": reason}))
        return 0
    print(reason, file=sys.stderr)
    return 1


def _gate_payload(adapter, payload=None):
    """Return (command, cwd) from the harness payload. Only the git adapter runs without stdin."""
    if adapter == "git":
        return None, None
    payload = payload if payload is not None else _read_hook_stdin()
    if adapter == "claude":
        tool_input = payload.get("tool_input") or {}
        return tool_input.get("command"), payload.get("cwd")
    roots = payload.get("workspace_roots") or []
    return payload.get("command"), payload.get("cwd") or (roots[0] if roots else None)


def gate_git(purpose="commit", cwd=None, adapter="git", payload=None):
    if purpose != "commit":
        return _deny_out(adapter, f"unsupported purpose {purpose}; {RECALL_HINT}")
    command, payload_cwd = _gate_payload(adapter, payload)
    # Never trust the harness matcher alone: it fired on non-commit commands. Pass those through silently.
    if isinstance(command, str) and not GIT_COMMIT_RE.search(command):
        return 0
    cwd = cwd or payload_cwd or os.getcwd()
    try:
        require_worktree(cwd)
        scope = load_scope(cwd=cwd)
    except AgentError as e:
        return _deny_out(adapter, f"{e.message}; {e.hint or SCOPE_HINT}")
    path = lease_path(cwd=cwd)
    if not path.is_file():
        return _deny_out(adapter, f"missing lease; {RECALL_HINT}")
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _deny_out(adapter, f"invalid lease; {RECALL_HINT}")

    def fail(check):
        return _deny_out(adapter, f"{check}; {RECALL_HINT}")

    try:
        expires = datetime.strptime(lease["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return fail("lease expires_at invalid")
    if datetime.now(timezone.utc) > expires:
        return fail("lease expired")
    if lease.get("db") != scope["db"]:
        return fail("lease db mismatch")
    if lease.get("project") != scope["project"] or lease.get("ticket") != scope["ticket"]:
        return fail("lease project/ticket mismatch")
    if lease.get("purpose") != purpose:
        return fail("lease purpose mismatch")
    head_oid, git_dir, index_tree = _git_state(cwd=cwd)
    if lease.get("worktree_git_dir") != git_dir:
        return fail("lease worktree_git_dir mismatch")
    if lease.get("head_oid") != head_oid:
        return fail("lease head_oid mismatch")
    if lease.get("index_tree") != index_tree:
        return fail("lease index_tree mismatch")
    run_id = lease.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return fail("lease run_id missing")
    store = Store(scope["db"])
    try:
        if not store.run(run_id):
            return fail("lease run_id absent from database")
    finally:
        store.close()
    return 0


def install_git(cwd=None):
    cwd = require_worktree(cwd)
    configured = _git(["config", "--get", "core.hooksPath"], cwd=cwd, check=False)
    if configured.returncode == 0 and configured.stdout.strip():
        print(
            f"core.hooksPath is set; add this line to your pre-commit:\npython3 -m context_lab hook gate-git commit",
            file=sys.stderr,
        )
        return 1
    hooks_dir = git_path("hooks", cwd=cwd)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    target = hooks_dir / "pre-commit"
    if target.exists():
        print(
            f"pre-commit already exists; add this line:\npython3 -m context_lab hook gate-git commit",
            file=sys.stderr,
        )
        return 1
    home = CONTEXT_LAB_HOME
    target.write_text(PRE_COMMIT_SCRIPT.format(
        home=shlex.quote(home), install=INSTALL_HINT), encoding="utf-8")
    target.chmod(0o755)
    try:
        print(target.relative_to(Path(cwd)))
    except ValueError:
        print(target)
    return 0


HARNESS_CONFIGS = {
    "claude": {"hooks": {
        "SessionStart": [{"matcher": m, "hooks": [{"type": "command", "command": "context-lab hook session-start"}]}
                         for m in ("startup", "resume", "compact")],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "context-lab hook inject"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "if": "Bash(git commit *)",
                                                      "command": "context-lab hook gate-git commit --adapter claude"}]}],
    }},
    "codex": {"hooks": {
        "SessionStart": [{"matcher": m, "hooks": [{"type": "command", "command": "context-lab hook session-start"}]}
                         for m in ("startup", "resume", "compact")],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "context-lab hook inject"}]}],
    }},
    "cursor": {"version": 1, "hooks": {
        "beforeShellExecution": [{"command": "context-lab hook gate-git commit --adapter cursor",
                                  "matcher": "git commit"}],
    }},
}
CONFIG_PATHS = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "cursor": ".cursor/hooks.json"}


def print_config(harness):
    """Config for another repository. Uses the installed `context-lab` console script, so it is machine-portable."""
    print(f"# {CONFIG_PATHS[harness]}", file=sys.stderr)
    print(json.dumps(HARNESS_CONFIGS[harness], indent=2))
    return 0


MCP_JSON = {
    "mcpServers": {
        "context-lab": {
            "command": "context-lab",
            "args": ["mcp"],
        }
    }
}

MCP_PATHS = {
    "claude": ".mcp.json",
    "codex": ".codex/config.toml",
    "cursor": ".cursor/mcp.json",
}

CURSOR_RULES_PATH = ".cursor/rules/context-lab-memory.mdc"

CURSOR_RULES = """---
description: Context Lab memory soft contract (Cursor has no ambient CompactView inject)
alwaysApply: true
---

# Context Lab memory (Cursor)

Cursor does **not** inject CompactView on SessionStart or prompts. Scope alone never enables hooks.
This install wrote MCP tools plus a git-commit shell gate; you must call recall yourself.

## Mandatory recall

Call `memory_context` before:

1. After `memory_initiate` / allocate-ticket (or choosing an existing scope), before other work.
2. Before every `git commit` or `git push`.
3. Before any decision that depends on prior incidents, constraints, lessons, or project state.

Setup is not recall. Candidates never affect retrieval until confirmed in the local UI.
Before `git commit`, run `context-lab hook recall-for --purpose commit` so the lease gate can pass.
"""

CODEX_MCP_BLOCK = """# BEGIN context-lab
[mcp_servers.context-lab]
command = "context-lab"
args = ["mcp"]
# END context-lab
"""

AMBIENT = {
    "claude": "SessionStart + UserPromptSubmit (CompactView inject) + PreToolUse git gate",
    "codex": "SessionStart + UserPromptSubmit (CompactView inject); enable [features] codex_hooks = true",
    "cursor": "none — no CompactView / prompt injection on Cursor",
}


def _mcp_payload(client):
    if client in ("claude", "cursor"):
        return json.dumps(MCP_JSON, indent=2) + "\n"
    return CODEX_MCP_BLOCK if not CODEX_MCP_BLOCK.endswith("\n") else CODEX_MCP_BLOCK


def _write_file(path: Path, content: str, force: bool):
    if path.exists() and not force:
        raise AgentError(
            "already_exists",
            f"{path} already exists; pass --force to overwrite",
            hint=f"Remove it or re-run with --force",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_codex_mcp(path: Path, force: bool):
    """Write or replace a marked context-lab MCP section in Codex config.toml."""
    begin, end = "# BEGIN context-lab", "# END context-lab"
    block = CODEX_MCP_BLOCK if CODEX_MCP_BLOCK.endswith("\n") else CODEX_MCP_BLOCK + "\n"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")
        return path
    text = path.read_text(encoding="utf-8")
    if begin in text and end in text:
        if not force:
            raise AgentError(
                "already_exists",
                f"{path} already has a context-lab MCP section; pass --force to replace",
            )
        pre = text.split(begin, 1)[0].rstrip()
        post = text.split(end, 1)[1].lstrip("\n")
        path.write_text((pre + "\n\n" if pre else "") + block + (post if not post or post.startswith("\n") else "\n" + post), encoding="utf-8")
        return path
    if text.strip() and not force:
        raise AgentError(
            "already_exists",
            f"{path} already exists; pass --force to append a context-lab MCP section",
        )
    sep = "" if not text or text.endswith("\n") else "\n"
    path.write_text(text + sep + ("\n" if text.strip() else "") + block, encoding="utf-8")
    return path


def install(client, project=None, ticket=None, db=None, git=True, force=False, cwd=None):
    """One-shot local opt-in: scope, MCP, client hooks, git lease; Cursor soft-contract rules."""
    if client not in HARNESS_CONFIGS:
        raise AgentError("validation", f"unknown client {client!r}", field="client")
    if (project is None) ^ (ticket is None):
        raise AgentError(
            "validation",
            "pass both --project and --ticket, or neither (reuse bound scope)",
            field="project",
        )
    cwd = require_worktree(cwd)
    root = Path(cwd)

    # Fail before writes when scope is missing.
    if project is not None:
        scope = set_scope(project, ticket, db=db, cwd=cwd)
    else:
        scope = load_scope(cwd=cwd)

    written = []
    mcp_rel = MCP_PATHS[client]
    mcp_path = root / mcp_rel
    if client == "codex":
        written.append(str(_write_codex_mcp(mcp_path, force).relative_to(root)))
    else:
        written.append(str(_write_file(mcp_path, _mcp_payload(client), force).relative_to(root)))

    hooks_rel = CONFIG_PATHS[client]
    hooks_path = root / hooks_rel
    hooks_body = json.dumps(HARNESS_CONFIGS[client], indent=2) + "\n"
    written.append(str(_write_file(hooks_path, hooks_body, force).relative_to(root)))

    if client == "cursor":
        rules_path = root / CURSOR_RULES_PATH
        written.append(str(_write_file(rules_path, CURSOR_RULES, force).relative_to(root)))

    git_status = "skipped (--no-git)"
    if git:
        code = install_git(cwd=cwd)
        git_status = "installed pre-commit lease gate" if code == 0 else "not installed (existing hook or core.hooksPath; see stderr)"

    print(f"Context Lab install ({client})")
    print(f"  scope: project={scope['project']!r} ticket={scope['ticket']!r} branch={scope.get('branch')!r}")
    print(f"  wrote: {', '.join(written)}")
    print(f"  ambient inject: {AMBIENT[client]}")
    print(f"  hard gate: git commit lease ({git_status})")
    if client == "cursor":
        print(f"  soft contract: {CURSOR_RULES_PATH} (call memory_context; no ambient CompactView)")
        print("  note: Cursor has no CompactView prompt injection. MCP + rules + git lease only.")
    elif client == "codex":
        print("  note: Codex needs [features] codex_hooks = true and may require trusting project hooks.")
    print("  Scope alone never enables hooks. This command did.")
    print("  Next: restart / reconnect the client so it reloads MCP and hooks.")
    return 0



def build_parser(sub):
    hook = sub.add_parser("hook", help="Harness hooks: scope, inject, lease, git gate")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    scope_p = hook_sub.add_parser("set-scope", help="Bind project/ticket/db to this worktree")
    scope_p.add_argument("--project", required=True)
    scope_p.add_argument("--ticket", required=True)
    scope_p.add_argument("--db", default=None)
    hook_sub.add_parser("inject", help="UserPromptSubmit ambient CompactView injection")
    hook_sub.add_parser("session-start", help="SessionStart standing rules + gate text")
    recall = hook_sub.add_parser("recall-for", help="Recall and issue a commit lease")
    recall.add_argument("--purpose", required=True, choices=["commit"])
    recall.add_argument("--budget", type=int, default=INJECT_BUDGET)
    gate = hook_sub.add_parser("gate-git", help="Verify the commit recall lease")
    gate.add_argument("purpose", choices=["commit"])
    gate.add_argument("--adapter", choices=["git", "claude", "cursor"], default="git")
    hook_sub.add_parser("install-git", help="Install worktree pre-commit lease gate")
    cfg = hook_sub.add_parser("print-config", help="Print an opt-in hook config for another repository")
    cfg.add_argument("harness", choices=sorted(HARNESS_CONFIGS))
    inst = hook_sub.add_parser("install", help="One-shot local opt-in: MCP + hooks + git lease (+ Cursor soft contract)")
    inst.add_argument("--client", required=True, choices=sorted(HARNESS_CONFIGS))
    inst.add_argument("--project", default=None)
    inst.add_argument("--ticket", default=None)
    inst.add_argument("--db", default=None)
    inst.add_argument("--no-git", action="store_true", help="Skip pre-commit lease install")
    inst.add_argument("--force", action="store_true", help="Overwrite existing client files")
    return hook


def dispatch(args):
    if args.hook_command == "set-scope":
        print(json.dumps(set_scope(args.project, args.ticket, db=args.db), indent=2))
        return 0
    if args.hook_command == "inject":
        return inject()
    if args.hook_command == "session-start":
        return session_start()
    if args.hook_command == "recall-for":
        try:
            return recall_for(purpose=args.purpose, budget=args.budget)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if args.hook_command == "gate-git":
        return gate_git(purpose=args.purpose, adapter=args.adapter)
    if args.hook_command == "install-git":
        return install_git()
    if args.hook_command == "print-config":
        return print_config(args.harness)
    if args.hook_command == "install":
        return install(
            args.client,
            project=args.project,
            ticket=args.ticket,
            db=args.db,
            git=not args.no_git,
            force=args.force,
        )
    raise SystemExit(f"unknown hook command: {args.hook_command}")
