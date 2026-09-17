"""MCP/operator admission: exact branch bind and store selection."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .schemas import FLAT_CONTEXT_EXAMPLE, AgentError
from .scope import (
    ROUTE_ONLY,
    BoundIdentity,
    BoundRequest,
    MemoryScope,
    SessionIdentity,
    bind_request,
    identity_at,
)
from .store import Store, scope_covers

STATELESS = {"memory_catalog", "memory_allocate_ticket"}
SCOPE_DIAGNOSTIC = {"memory_scope"}
SCOPE_BEARING = {
    "memory_initiate",
    "memory_context",
    "memory_source",
    "memory_observe",
    "memory_propose",
    "memory_journal",
    "memory_park",
}
ID_ADDRESSED = {"memory_inspect_run", "memory_feedback", "memory_promote", "memory_delete"}

_TASK_FIELDS = ("project", "ticket", "actions", "needs", "state", "as_of")


class Plane(str, Enum):
    MCP = "mcp"
    OPERATOR = "operator"


@dataclass(frozen=True)
class ContextEnvelope:
    task: dict
    requested: MemoryScope | None
    budget: int
    detail: str
    since: str | None


@dataclass(frozen=True)
class Admission:
    """Read-only authorization + store selection for one call."""

    plane: Plane
    identity: SessionIdentity | None
    scope: MemoryScope | None
    store: Store
    owns_store: bool
    tool: str | None = None
    context: ContextEnvelope | None = None
    normalized_args: dict | None = None

    @classmethod
    def for_mcp(cls, bootstrap: Store, tool: str, args: dict) -> Admission:
        if not isinstance(args, dict):
            raise AgentError("validation", "arguments must be an object")
        if tool in STATELESS:
            return cls(
                plane=Plane.MCP,
                identity=None,
                scope=None,
                store=bootstrap,
                owns_store=False,
                tool=tool,
                normalized_args=dict(args),
            )
        cwd = _require_cwd(args)
        identity = identity_at(cwd)
        if tool in SCOPE_DIAGNOSTIC:
            return cls(
                plane=Plane.MCP,
                identity=identity,
                scope=None,
                store=bootstrap,
                owns_store=False,
                tool=tool,
                normalized_args=dict(args),
            )
        context = None
        normalized = dict(args)
        if tool == "memory_context":
            context = _parse_context_envelope(args)
            scope = _effective_scope(identity, context.requested)
            _authorize_exact(identity, scope)
            scope = _binding_scope(identity, scope)
            context = ContextEnvelope(
                task=_fill_scope(context.task, scope),
                requested=context.requested,
                budget=context.budget,
                detail=context.detail,
                since=context.since,
            )
            normalized = {
                **{
                    key: value
                    for key, value in args.items()
                    if key not in _TASK_FIELDS + ("query", "task")
                },
                "task": context.task,
            }
        elif tool == "memory_propose":
            memories = args.get("memories")
            if not isinstance(memories, list) or not memories:
                raise AgentError(
                    "validation",
                    "memories must be a nonempty array",
                    field="memories",
                )
            filled = []
            for draft in memories:
                if not isinstance(draft, dict):
                    raise AgentError(
                        "validation",
                        "each memory must be an object",
                        field="memories",
                    )
                requested = _scope_from_record(draft)
                scope = _effective_scope(identity, requested)
                _authorize_exact(identity, scope)
                scope = _binding_scope(identity, scope)
                filled.append(_fill_scope(draft, scope))
            normalized["memories"] = filled
            scope = _scope_from_record(filled[0]) if filled else None
        elif tool in ID_ADDRESSED:
            bound = bind_request(cwd, ROUTE_ONLY)
            store, owns_store = _select_store(bootstrap, bound)
            return cls(
                plane=Plane.MCP,
                identity=bound.identity,
                scope=None,
                store=store,
                owns_store=owns_store,
                tool=tool,
                normalized_args=dict(args),
            )
        elif tool == "memory_park":
            requested = _scope_from_record(args)
            if isinstance(identity, BoundIdentity):
                if requested is not None and requested.project != identity.scope.project:
                    raise AgentError(
                        "scope_mismatch",
                        f"Parking project {requested.project} does not match binding "
                        f"{identity.scope.project} on {identity.branch}",
                        field="project",
                    )
                scope = identity.scope
            else:
                scope = _effective_scope(identity, requested)
            normalized = _fill_scope(args, scope)
        elif tool in SCOPE_BEARING:
            requested = _scope_from_record(args)
            scope = _effective_scope(identity, requested)
            _authorize_exact(identity, scope)
            scope = _binding_scope(identity, scope)
            normalized = _fill_scope(args, scope)
        else:
            raise AgentError("unknown_tool", f"Unknown tool: {tool}")
        bound = BoundRequest(
            identity=identity,
            scope=scope,
            database=identity.database if isinstance(identity, BoundIdentity) else None,
        )
        store, owns_store = _select_store(bootstrap, bound)
        return cls(
            plane=Plane.MCP,
            identity=identity,
            scope=scope,
            store=store,
            owns_store=owns_store,
            tool=tool,
            context=context,
            normalized_args=normalized,
        )

    @classmethod
    def for_operator(cls, store: Store, project: str, ticket: str = "") -> Admission:
        return cls(
            plane=Plane.OPERATOR,
            identity=None,
            scope=MemoryScope(project.strip(), ticket.strip()),
            store=store,
            owns_store=False,
        )

    def with_derived_scope(self, record: dict) -> Admission:
        derived = _scope_from_record(record)
        if derived is None:
            raise AgentError("validation", "project required", field="project")
        self.authorize_exact(derived)
        return Admission(
            plane=self.plane,
            identity=self.identity,
            scope=derived,
            store=self.store,
            owns_store=False,
            tool=self.tool,
            context=self.context,
            normalized_args=self.normalized_args,
        )

    def authorize_exact(self, effective: MemoryScope) -> None:
        _authorize_exact(self.identity, effective)

    def allow_read(self, owner: dict) -> bool:
        if self.scope is None:
            return False
        return scope_covers(owner, {"project": self.scope.project, "ticket": self.scope.ticket})

    def fill_task(self, record: dict) -> dict:
        if self.scope is None:
            return dict(record)
        return _fill_scope(record, self.scope)

    def load_memory(self, memory_id: str) -> dict:
        record = self.store.memory(memory_id)
        if not record:
            raise AgentError("not_found", "Unknown memory_id", field="memory_id")
        return record

    def load_run(self, run_id: str) -> dict:
        record = self.store.run(run_id)
        if not record:
            raise AgentError("not_found", "Unknown run_id", field="run_id")
        return record

    def close(self) -> None:
        if self.owns_store:
            self.store.close()


def _task_error(message, field="task"):
    return AgentError("validation", message, field=field, hint=FLAT_CONTEXT_EXAMPLE)


def _scope_from_record(record: dict) -> MemoryScope | None:
    if "project" not in record:
        if "ticket" in record:
            raise AgentError("validation", "ticket requires project", field="project")
        return None
    project = record.get("project")
    ticket = record.get("ticket", "")
    if not isinstance(project, str) or not project.strip():
        raise AgentError("validation", "project required", field="project")
    if not isinstance(ticket, str):
        raise AgentError("validation", "ticket must be a string", field="ticket")
    return MemoryScope(project.strip(), ticket.strip())


def _fill_scope(record: dict, scope: MemoryScope) -> dict:
    out = dict(record)
    out.setdefault("project", scope.project)
    out.setdefault("ticket", scope.ticket)
    return out


def _require_cwd(args: dict) -> str:
    cwd = args.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        raise AgentError("validation", "cwd required", field="cwd")
    return cwd


def _select_store(bootstrap: Store, bound: BoundRequest) -> tuple[Store, bool]:
    if bound.database is None:
        return bootstrap, False
    database = bound.database.expanduser().resolve()
    if bootstrap.path != ":memory:" and Path(bootstrap.path).expanduser().resolve() == database:
        return bootstrap, False
    return Store(str(database)), True


def _effective_scope(identity: SessionIdentity, requested: MemoryScope | None) -> MemoryScope:
    if isinstance(identity, BoundIdentity):
        return requested if requested is not None else identity.scope
    if requested is None:
        raise AgentError(
            "validation",
            "project required when cwd has no bound scope",
            field="project",
        )
    return requested


def _binding_scope(identity: SessionIdentity, effective: MemoryScope) -> MemoryScope:
    if isinstance(identity, BoundIdentity):
        return identity.scope
    return effective


def _authorize_exact(identity: SessionIdentity, effective: MemoryScope) -> None:
    if isinstance(identity, BoundIdentity) and effective != identity.scope:
        raise AgentError(
            "scope_mismatch",
            f"Request scope {effective.project}/{effective.ticket} does not match binding "
            f"{identity.scope.project}/{identity.scope.ticket} on {identity.branch}",
        )


def _parse_context_envelope(args: dict) -> ContextEnvelope:
    task_val = args.get("task")
    budget = args.get("budget", 1200)
    detail = args.get("detail", "agent")
    since = args.get("since")
    if since is not None and not isinstance(since, str):
        raise AgentError("validation", "since must be a string", field="since")
    if isinstance(task_val, dict):
        mixed = [key for key in _TASK_FIELDS + ("query",) if key in args]
        if mixed:
            raise _task_error(
                "Do not mix a nested task object with top-level " + ", ".join(mixed),
            )
        task = dict(task_val)
    else:
        query = None
        if isinstance(task_val, str) and task_val.strip():
            query = task_val.strip()
        elif task_val is not None:
            raise _task_error("task must be a string or a nested task object")
        alias = args.get("query")
        if isinstance(alias, str) and alias.strip():
            alias = alias.strip()
            if query is not None and alias != query:
                raise _task_error("task and query disagree", field="query")
            query = query or alias
        elif alias is not None and alias != "":
            raise _task_error("query must be a string", field="query")
        if not query:
            raise _task_error("Pass task (what you are about to do)")
        task = {key: args[key] for key in _TASK_FIELDS if key in args}
        task["query"] = query
    requested = _scope_from_record(task)
    return ContextEnvelope(
        task=task,
        requested=requested,
        budget=budget,
        detail=detail,
        since=since,
    )


@contextmanager
def admit_mcp(bootstrap: Store, tool: str, args: dict):
    admission = Admission.for_mcp(bootstrap, tool, args)
    try:
        yield admission
    finally:
        admission.close()
