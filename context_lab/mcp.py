"""Minimal MCP stdio server for Context Lab tools."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import agent_api, usage
from .schemas import (
    FLAT_CONTEXT_EXAMPLE,
    GATE_TEXT,
    KNOWLEDGE_SCHEMA,
    MEMORY_DRAFT_SCHEMA,
    TASK_SCHEMA,
    AgentError,
    error_payload,
    wire_dumps,
    wire_estimated_tokens,
)
from .scope import (
    ROUTE_ONLY,
    BoundIdentity,
    BoundRequest,
    MemoryScope,
    SessionIdentity,
    _bind_identity,
    bind_request,
    identity_at,
    identity_to_inspect,
    place_token,
    switch_token,
)
from .store import Store

_TASK_FIELDS = ("project", "ticket", "actions", "needs", "state", "as_of")
_CWD = {
    "cwd": {
        "type": "string",
        "minLength": 1,
        "description": "Client workspace location used to resolve repository scope.",
    }
}
_CONTEXT_PROPERTIES = {
    **_CWD,
    "project": {"type": "string", "minLength": 1, "description": "Optional explicit project scope."},
    "task": {"type": "string", "minLength": 1, "description": "What you are about to do."},
    "query": {"type": "string", "description": "Alias for task."},
    "ticket": TASK_SCHEMA["properties"]["ticket"],
    "actions": TASK_SCHEMA["properties"]["actions"],
    "needs": TASK_SCHEMA["properties"]["needs"],
    "state": TASK_SCHEMA["properties"]["state"],
    "as_of": TASK_SCHEMA["properties"]["as_of"],
    "budget": {"type": "integer", "minimum": 128, "maximum": 16000},
    "detail": {"type": "string", "enum": ["agent", "prose", "inspect", "full"]},
    "since": {"type": "string", "description": "Previous place returned by this server."},
}
_MCP_MEMORY_DRAFT_SCHEMA = {
    **MEMORY_DRAFT_SCHEMA,
    "required": [field for field in MEMORY_DRAFT_SCHEMA["required"] if field != "project"],
}


def tool(name, description, properties, required, read_only=True):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    }


TOOLS = [
    tool(
        "memory_initiate",
        "Initialize or reuse exact project/ticket notes.",
        {
            **_CWD,
            "project": {"type": "string"},
            "ticket": {"type": "string"},
            "knowledge": KNOWLEDGE_SCHEMA,
            "path": {"type": "string"},
            "empty": {"type": "boolean"},
            "refresh": {"type": "boolean"},
        },
        ["cwd"],
        False,
    ),
    tool(
        "memory_allocate_ticket",
        "Mint a work ticket without creating notes.",
        {},
        [],
    ),
    tool(
        "memory_scope",
        "Show compact repository identity from the client cwd.",
        {
            **_CWD,
            "since": {"type": "string"},
            "detail": {"type": "string", "enum": ["compact", "inspect"]},
        },
        ["cwd"],
    ),
    tool("memory_catalog", "List supported task actions and information needs.", {}, []),
    tool(
        "memory_context",
        "Build task-targeted context from client cwd and task.",
        _CONTEXT_PROPERTIES,
        ["cwd", "task"],
        False,
    ),
    tool(
        "memory_inspect_run",
        "Load inspect projection for a prior memory_context run_id.",
        {**_CWD, "run_id": {"type": "string"}},
        ["cwd", "run_id"],
    ),
    tool(
        "memory_source",
        "Read immutable evidence by id in task scope or an ancestor layer.",
        {
            **_CWD,
            "source_id": {"type": "string"},
            "project": {"type": "string"},
            "ticket": {"type": "string"},
        },
        ["cwd", "source_id"],
    ),
    tool(
        "memory_observe",
        "Store evidence only.",
        {
            **_CWD,
            "project": {"type": "string"},
            "ticket": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "confirm_global": {"type": "boolean"},
        },
        ["cwd", "title", "body"],
        False,
    ),
    tool(
        "memory_propose",
        "Store candidate memories for local UI review.",
        {
            **_CWD,
            "memories": {
                "type": "array",
                "items": _MCP_MEMORY_DRAFT_SCHEMA,
                "minItems": 1,
            },
        },
        ["cwd", "memories"],
        False,
    ),
    tool(
        "memory_feedback",
        "Report feedback for a saved run.",
        {
            **_CWD,
            "run_id": {"type": "string"},
            "memory_id": {"type": "string"},
            "observation": {
                "type": "string",
                "enum": ["helpful", "missed", "irrelevant", "stale"],
            },
            "note": {"type": "string"},
        },
        ["cwd", "run_id", "memory_id", "observation"],
        False,
    ),
    tool(
        "memory_promote",
        "Promote a confirmed ticket memory to a project-baseline candidate.",
        {
            **_CWD,
            "memory_id": {"type": "string"},
            "title": {"type": "string"},
            "claim": {"type": "string"},
        },
        ["cwd", "memory_id"],
        False,
    ),
    tool(
        "memory_journal",
        "Write and index one ticket journal note.",
        {
            **_CWD,
            "project": {"type": "string"},
            "ticket": {"type": "string"},
            "kind": {"type": "string", "enum": ["plan", "decision", "progress", "handoff"]},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        ["cwd", "kind", "title", "body"],
        False,
    ),
    tool(
        "memory_park",
        "Park an unrelated later fix for human triage.",
        {
            **_CWD,
            "project": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "later": {"type": "string"},
            "capture_key": {"type": "string"},
        },
        ["cwd", "title", "body"],
        False,
    ),
]

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
ID_ADDRESSED = {"memory_inspect_run", "memory_feedback", "memory_promote"}


@dataclass(frozen=True)
class ContextEnvelope:
    task: dict
    requested: MemoryScope | None
    budget: int
    detail: str
    since: str | None


@dataclass
class ActiveRequest:
    name: str
    args: dict
    identity: SessionIdentity | None
    scope: MemoryScope | None
    store: Store
    owns_store: bool
    context: ContextEnvelope | None = None

    def close(self):
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


def _context_value_error(exc):
    message = str(exc)
    field = "task"
    if "requires query" in message:
        field = "query"
    elif "requires project" in message:
        field = "project"
    elif "state" in message.lower():
        field = "state"
    return AgentError("validation", message, field=field, hint=FLAT_CONTEXT_EXAMPLE)


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


@contextmanager
def open_request(bootstrap: Store, name: str, args: dict):
    if not isinstance(args, dict):
        raise AgentError("validation", "arguments must be an object")
    if name in STATELESS:
        yield ActiveRequest(name, dict(args), None, None, bootstrap, False)
        return
    cwd = _require_cwd(args)
    if name in SCOPE_DIAGNOSTIC:
        yield ActiveRequest(name, dict(args), identity_at(cwd), None, bootstrap, False)
        return
    context = None
    normalized = dict(args)
    if name == "memory_context":
        context = _parse_context_envelope(args)
        bound = bind_request(cwd, context.requested)
        context = ContextEnvelope(
            task=_fill_scope(context.task, bound.scope),
            requested=context.requested,
            budget=context.budget,
            detail=context.detail,
            since=context.since,
        )
        normalized = {
            **{key: value for key, value in args.items() if key not in _TASK_FIELDS + ("query", "task")},
            "task": context.task,
        }
    elif name == "memory_propose":
        memories = args.get("memories")
        if not isinstance(memories, list) or not memories:
            raise AgentError("validation", "memories must be a nonempty array", field="memories")
        identity = identity_at(cwd)
        filled = []
        scopes = []
        for draft in memories:
            if not isinstance(draft, dict):
                raise AgentError("validation", "each memory must be an object", field="memories")
            item_bound = _bind_identity(identity, _scope_from_record(draft))
            filled.append(_fill_scope(draft, item_bound.scope))
            scopes.append(item_bound.scope)
        bound = BoundRequest(
            identity=identity,
            scope=scopes[0] if len(set(scopes)) == 1 else None,
            database=identity.database if isinstance(identity, BoundIdentity) else None,
        )
        normalized["memories"] = filled
    elif name in ID_ADDRESSED:
        bound = bind_request(cwd, ROUTE_ONLY)
    elif name == "memory_park":
        identity = identity_at(cwd)
        requested = _scope_from_record(args)
        bound = _bind_identity(
            identity,
            None if isinstance(identity, BoundIdentity) else requested,
        )
        normalized = _fill_scope(args, bound.scope)
        if requested is not None and requested.project != bound.scope.project:
            raise AgentError(
                "scope_mismatch",
                f"Parking project {requested.project} does not match binding "
                f"{bound.scope.project} on {bound.identity.branch}",
                field="project",
            )
    elif name in SCOPE_BEARING:
        bound = bind_request(cwd, _scope_from_record(args))
        normalized = _fill_scope(args, bound.scope)
    else:
        raise AgentError("unknown_tool", f"Unknown tool: {name}")
    store, owns_store = _select_store(bootstrap, bound)
    request = ActiveRequest(
        name=name,
        args=normalized,
        identity=bound.identity,
        scope=bound.scope,
        store=store,
        owns_store=owns_store,
        context=context,
    )
    try:
        yield request
    finally:
        request.close()


def _attach_compact_identity(view: dict, identity: SessionIdentity, since: str | None) -> dict:
    current = place_token(identity)
    out = dict(view)
    if since is None:
        out["place"] = current
    elif since != current:
        out["place"] = current
        out["switch"] = switch_token(since, current)
    estimate = 0
    for _ in range(4):
        estimate = wire_estimated_tokens({**out, "wire_estimated_tokens": estimate})
    out["wire_estimated_tokens"] = estimate
    return out


def _memory_scope(request: ActiveRequest) -> dict:
    current = place_token(request.identity)
    out = {"place": current}
    since = request.args.get("since")
    if since is not None:
        if not isinstance(since, str):
            raise AgentError("validation", "since must be a string", field="since")
        if since != current:
            out["switch"] = switch_token(since, current)
    detail = request.args.get("detail", "compact")
    if detail not in {"compact", "inspect"}:
        raise AgentError("validation", "detail must be compact or inspect", field="detail")
    if detail == "inspect":
        out["identity"] = identity_to_inspect(request.identity)
    return out


def _dispatch(request: ActiveRequest) -> dict:
    args = {key: value for key, value in request.args.items() if key != "cwd"}
    if request.name == "memory_initiate":
        return agent_api.initiate(request.store, **args)
    if request.name == "memory_allocate_ticket":
        return agent_api.allocate_ticket()
    if request.name == "memory_scope":
        return _memory_scope(request)
    if request.name == "memory_catalog":
        return agent_api.list_catalog()
    if request.name == "memory_context":
        try:
            view = agent_api.context(
                request.store,
                request.context.task,
                budget=request.context.budget,
                detail=request.context.detail,
            )
        except ValueError as exc:
            raise _context_value_error(exc) from exc
        return _attach_compact_identity(view, request.identity, request.context.since)
    if request.name == "memory_inspect_run":
        return agent_api.inspect_run(request.store, args["run_id"])
    if request.name == "memory_source":
        return agent_api.source(
            request.store,
            args["source_id"],
            args["project"],
            args.get("ticket", ""),
        )
    if request.name == "memory_observe":
        return agent_api.observe(request.store, args)
    if request.name == "memory_propose":
        return agent_api.propose(request.store, args["memories"])
    if request.name == "memory_feedback":
        return agent_api.feedback(
            request.store,
            args["run_id"],
            args["memory_id"],
            args["observation"],
            args.get("note", ""),
        )
    if request.name == "memory_promote":
        return agent_api.promote(
            request.store,
            args["memory_id"],
            title=args.get("title"),
            claim=args.get("claim"),
        )
    if request.name == "memory_journal":
        return agent_api.journal(
            request.store,
            args["project"],
            args["ticket"],
            args["kind"],
            args["title"],
            args["body"],
        )
    if request.name == "memory_park":
        return agent_api.park(
            request.store,
            args,
            captured_while_ticket=request.scope.ticket if request.scope else "",
        )
    raise AgentError("unknown_tool", f"Unknown tool: {request.name}")


def call(bootstrap: Store, name: str, args: dict) -> dict:
    with open_request(bootstrap, name, args) as request:
        return _dispatch(request)


def serve_mcp(store, instream=None, outstream=None):
    instream, outstream = instream or sys.stdin, outstream or sys.stdout
    initialized = False
    supported = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
    tool_names = {item["name"] for item in TOOLS}
    for line in instream:
        if not line.strip():
            continue
        rid = None
        try:
            msg = json.loads(line)
            if (
                not isinstance(msg, dict)
                or msg.get("jsonrpc") != "2.0"
                or not isinstance(msg.get("method"), str)
            ):
                raise ValueError("Invalid JSON-RPC request")
            rid = msg.get("id")
            method = msg["method"]
            if rid is None:
                continue
            params = msg.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            if method == "initialize":
                requested = params.get("protocolVersion")
                result = {
                    "protocolVersion": requested if requested in supported else "2025-11-25",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "context-lab", "version": "0.1.0"},
                    "instructions": GATE_TEXT,
                }
                initialized = True
                usage.record(store, "mcp_setup", method, response=GATE_TEXT)
            elif method == "ping":
                result = {}
            elif not initialized:
                raise ValueError("Initialize the server first")
            elif method == "tools/list":
                result = {"tools": TOOLS}
                usage.record(store, "mcp_setup", method, response=wire_dumps(TOOLS))
            elif method == "tools/call":
                name = params.get("name")
                if name not in tool_names:
                    response = {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32602, "message": "Unknown tool"},
                    }
                    outstream.write(json.dumps(response) + "\n")
                    outstream.flush()
                    continue
                args = params.get("arguments", {})
                try:
                    with open_request(store, name, args) as request:
                        try:
                            data = _dispatch(request)
                            result = {
                                "content": [{"type": "text", "text": wire_dumps(data)}],
                                "isError": False,
                            }
                        except (AgentError, ValueError, TypeError, KeyError, OSError) as exc:
                            result = {
                                "content": [{"type": "text", "text": wire_dumps(error_payload(exc))}],
                                "isError": True,
                            }
                        usage.record_mcp(request.store, name, request.args, result)
                except (AgentError, ValueError, TypeError, KeyError, OSError) as exc:
                    result = {
                        "content": [{"type": "text", "text": wire_dumps(error_payload(exc))}],
                        "isError": True,
                    }
                    usage.record_mcp(store, name, args, result)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "Method not found"},
                }
                outstream.write(json.dumps(response) + "\n")
                outstream.flush()
                continue
            response = {"jsonrpc": "2.0", "id": rid, "result": result}
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        except (ValueError, TypeError, KeyError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32602, "message": str(exc)},
            }
        outstream.write(json.dumps(response) + "\n")
        outstream.flush()
