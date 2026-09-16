from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import agent_api, usage
from .admission import (
    Admission,
    ContextEnvelope,
    admit_mcp,
)
from .schemas import (
    FLAT_CONTEXT_EXAMPLE,
    GATE_TEXT,
    KNOWLEDGE_SCHEMA,
    MEMORY_DRAFT_SCHEMA,
    TASK_SCHEMA,
    AgentError,
    error_payload,
    with_wire_estimated_tokens,
    wire_dumps,
)
from .scope import (
    MemoryScope,
    SessionIdentity,
    identity_to_inspect,
    place_token,
    switch_token,
)
from .store import Store

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


@dataclass
class ActiveRequest:
    name: str
    args: dict
    identity: SessionIdentity | None
    scope: MemoryScope | None
    store: Store
    owns_store: bool
    context: ContextEnvelope | None = None
    admission: Admission | None = None

    @classmethod
    def from_admission(cls, admission: Admission) -> ActiveRequest:
        return cls(
            name=admission.tool or "",
            args=admission.normalized_args or {},
            identity=admission.identity,
            scope=admission.scope,
            store=admission.store,
            owns_store=admission.owns_store,
            context=admission.context,
            admission=admission,
        )

    def close(self):
        if self.admission is not None:
            self.admission.close()
        elif self.owns_store:
            self.store.close()


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


@contextmanager
def open_request(bootstrap: Store, name: str, args: dict):
    with admit_mcp(bootstrap, name, args) as admission:
        request = ActiveRequest.from_admission(admission)
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
    return with_wire_estimated_tokens(out)


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
    admission = request.admission
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
        record = admission.load_run(args["run_id"])
        admission.with_derived_scope(record["task"])
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
        record = admission.load_run(args["run_id"])
        admission.with_derived_scope(record["task"])
        return agent_api.feedback(
            request.store,
            args["run_id"],
            args["memory_id"],
            args["observation"],
            args.get("note", ""),
        )
    if request.name == "memory_promote":
        record = admission.load_memory(args["memory_id"])
        admission.with_derived_scope(record)
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
