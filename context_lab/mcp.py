"""Minimal MCP stdio server: initialize, ping, tools/list, tools/call.

Implements the 2025-11-25 tool subset using newline-delimited JSON-RPC.
No HTTP MCP transport, notifications, resources, or background tasks.
"""
import json
import sys

from . import agent_api, usage
from .schemas import (
    GATE_TEXT,
    KNOWLEDGE_SCHEMA,
    MEMORY_DRAFT_SCHEMA,
    TASK_SCHEMA,
    AgentError,
    error_payload,
    wire_dumps,
)


def tool(name, description, properties, required, read_only=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": False}}


TOOLS = [
    tool("memory_initiate",
         "Initialize or reuse exact project/ticket notes. knowledge.vault is a lab-wide Obsidian choice. "
         "mode=import binds an existing ticket folder; auto/empty with a bound vault and a ticket provisions "
         "Context Lab/{project}/{ticket}. First OS touch auto-detects a vault or returns needs_obsidian_vault. "
         "obsidian.journaling is ready|vault_only|unavailable. refresh=true rescans notes. Never edits notes.",
         {"project": {"type": "string"}, "ticket": {"type": "string"},
          "knowledge": KNOWLEDGE_SCHEMA,
          "path": {"type": "string"}, "empty": {"type": "boolean"}, "refresh": {"type": "boolean"}},
         ["project"], False),
    tool("memory_allocate_ticket",
         "Mint work-YYYYMMDD-HHMMSS when the user wants notes/memories but has no ticket yet. Does not create a notes folder.",
         {}, []),
    tool("memory_scope",
         "Show branch binding (project, ticket, db). Fails if unbound or detached HEAD.",
         {}, []),
    tool("memory_catalog", "List supported task actions and information needs.", {}, []),
    tool("memory_context",
         "Build a task-targeted context packet (lab → project baseline → exact ticket). "
         "Default detail=agent returns the agent packet under budget. detail=inspect|full is for the workbench; prefer memory_inspect_run for a saved run.",
         {"task": TASK_SCHEMA, "budget": {"type": "integer", "minimum": 128, "maximum": 16000},
          "detail": {"type": "string", "enum": ["agent", "prose", "inspect", "full"]}},
         ["task"], False),
    tool("memory_inspect_run",
         "Load inspect projection for a prior memory_context run_id.",
         {"run_id": {"type": "string"}},
         ["run_id"]),
    tool("memory_source",
         "Read immutable evidence by id in task scope or an ancestor layer.",
         {"source_id": {"type": "string"}, "project": {"type": "string"}, "ticket": {"type": "string"}},
         ["source_id", "project"]),
    tool("memory_observe",
         "Store evidence only (not a confirmed lesson). Lab-wide: project=__global__, empty ticket, confirm_global=true after explicit user approval.",
         {"project": {"type": "string"}, "ticket": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
          "confirm_global": {"type": "boolean", "description": "Required true when project is __global__; only after explicit user approval."}},
         ["project", "title", "body"], False),
    tool("memory_propose",
         "Store candidate memories for local UI review. Prefer one ticket-scoped draft: title, claim, source_ids, kind. "
         "Candidates never affect retrieval until confirmed. Lab-wide needs confirm_global=true after user approval.",
         {"memories": {"type": "array", "items": MEMORY_DRAFT_SCHEMA, "minItems": 1}},
         ["memories"], False),
    tool("memory_feedback",
         "Report helpful/missed/irrelevant/stale for a run. Does not auto-promote.",
         {"run_id": {"type": "string"}, "memory_id": {"type": "string"},
          "observation": {"type": "string", "enum": ["helpful", "missed", "irrelevant", "stale"]}, "note": {"type": "string"}},
         ["run_id", "memory_id", "observation"], False),
    tool("memory_promote",
         "Promote a confirmed ticket memory to a project-baseline candidate. Idempotent on origin+claim.",
         {"memory_id": {"type": "string"}, "title": {"type": "string"}, "claim": {"type": "string"}},
         ["memory_id"], False),
    tool("memory_journal",
         "Write and index one plan|decision|progress|handoff note under the ticket folder "
         "(imported or auto-provisioned). Requires an exact ticket; vault binding alone is not enough. "
         "Does not create confirmed lessons.",
         {"project": {"type": "string"}, "ticket": {"type": "string"},
          "kind": {"type": "string", "enum": ["plan", "decision", "progress", "handoff"]},
          "title": {"type": "string"}, "body": {"type": "string"}},
         ["project", "ticket", "kind", "title", "body"], False),
]


def _enforce_branch_scope(project, ticket=""):
    """When cwd has a branch binding for this project, reject a mismatched ticket."""
    import os
    from .scope import BranchScopes, MemoryScope
    try:
        resolved = BranchScopes.resolve_current(os.getcwd())
    except AgentError:
        return
    if resolved.scope.project != (project or "").strip():
        return
    BranchScopes.require_request_scope(
        os.getcwd(), MemoryScope(project=project, ticket=ticket or ""))


def call(store, name, args):
    if name == "memory_initiate":
        return agent_api.initiate(store, **args)
    if name == "memory_allocate_ticket":
        return agent_api.allocate_ticket()
    if name == "memory_scope":
        from .scope import BranchScopes, resolved_to_dict
        return resolved_to_dict(BranchScopes.resolve_current())
    if name == "memory_catalog":
        return agent_api.list_catalog()
    if name == "memory_context":
        task = args["task"]
        _enforce_branch_scope(task["project"], task.get("ticket", ""))
        return agent_api.context(store, task, budget=args.get("budget", 1200), detail=args.get("detail", "agent"))
    if name == "memory_inspect_run":
        return agent_api.inspect_run(store, args["run_id"])
    if name == "memory_source":
        _enforce_branch_scope(args["project"], args.get("ticket", ""))
        return agent_api.source(store, args["source_id"], args["project"], args.get("ticket", ""))
    if name == "memory_observe":
        _enforce_branch_scope(args["project"], args.get("ticket", ""))
        return agent_api.observe(store, args)
    if name == "memory_propose":
        for draft in args["memories"]:
            _enforce_branch_scope(draft["project"], draft.get("ticket", ""))
        return agent_api.propose(store, args["memories"])
    if name == "memory_feedback":
        return agent_api.feedback(store, args["run_id"], args["memory_id"], args["observation"], args.get("note", ""))
    if name == "memory_promote":
        return agent_api.promote(store, args["memory_id"], title=args.get("title"), claim=args.get("claim"))
    if name == "memory_journal":
        _enforce_branch_scope(args["project"], args["ticket"])
        return agent_api.journal(
            store, args["project"], args["ticket"], args["kind"], args["title"], args["body"])
    raise AgentError("unknown_tool", f"Unknown tool: {name}")


def serve_mcp(store, instream=None, outstream=None):
    instream, outstream = instream or sys.stdin, outstream or sys.stdout
    initialized = False
    supported = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
    for line in instream:
        if not line.strip():
            continue
        rid = None
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
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
                result = {"protocolVersion": requested if requested in supported else "2025-11-25",
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "context-lab", "version": "0.1.0"},
                          "instructions": GATE_TEXT}
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
                if params.get("name") not in {t["name"] for t in TOOLS}:
                    response = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "Unknown tool"}}
                    outstream.write(json.dumps(response) + "\n")
                    outstream.flush()
                    continue
                try:
                    args = params.get("arguments", {})
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                    data = call(store, params["name"], args)
                    result = {"content": [{"type": "text", "text": wire_dumps(data)}], "isError": False}
                except (AgentError, ValueError, TypeError, KeyError, OSError) as e:
                    result = {"content": [{"type": "text", "text": wire_dumps(error_payload(e))}], "isError": True}
                usage.record_mcp(store, params["name"], args, result)
            else:
                response = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
                outstream.write(json.dumps(response) + "\n")
                outstream.flush()
                continue
            response = {"jsonrpc": "2.0", "id": rid, "result": result}
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except (ValueError, TypeError, KeyError) as e:
            response = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(e)}}
        outstream.write(json.dumps(response) + "\n")
        outstream.flush()
