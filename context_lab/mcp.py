"""Minimal MCP stdio server: initialize, ping, tools/list, tools/call.

Implements the 2025-11-25 tool subset using newline-delimited JSON-RPC.
No HTTP MCP transport, notifications, resources, or background tasks.
"""
import json
import sys

from . import agent_api
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
         "Check or initialize a project/ticket knowledge base. On first OS touch for this DB, Context Lab "
         "auto-detects an Obsidian vault from the Obsidian app config (and shallow common folders) and binds it, "
         "notifying via obsidian.auto_detected. If none is found, returns needs_obsidian_vault until knowledge.vault "
         "is a vault/journal folder path or 'none'. Prefer knowledge={mode, vault?}. mode auto|empty|import|reuse for "
         "ticket notes. refresh=true rescans ticket notes. Never edits notes.",
         {"project": {"type": "string"}, "ticket": {"type": "string"},
          "knowledge": KNOWLEDGE_SCHEMA,
          "path": {"type": "string"}, "empty": {"type": "boolean"}, "refresh": {"type": "boolean"}},
         ["project"], False),
    tool("memory_allocate_ticket",
         "Allocate a generated work-unit ticket id (work-YYYYMMDD-HHMMSS UTC) when the user wants Obsidian notes and memories but has no ticket yet.",
         {}, []),
    tool("memory_catalog", "List the supported task actions and information needs. Use these to describe your next decision.", {}, []),
    tool("memory_context",
         "Build task-targeted context from layered scopes: lab-wide (__global__), then project baseline (empty ticket), then the exact ticket. "
         "Default detail=agent returns a CompactView (prose, picks, needs, warnings) whose wire_estimated_tokens must fit budget. "
         "Pass detail=inspect or full for selected/trace/conflicts (workbench). Prefer memory_inspect_run for a saved run.",
         {"task": TASK_SCHEMA, "budget": {"type": "integer", "minimum": 128, "maximum": 16000},
          "detail": {"type": "string", "enum": ["agent", "prose", "inspect", "full"]}},
         ["task"], False),
    tool("memory_inspect_run",
         "Load the full inspect projection (selected, trace, conflicts) for a prior memory_context run_id. "
         "Use this instead of widening the default agent wire.",
         {"run_id": {"type": "string"}},
         ["run_id"]),
    tool("memory_source",
         "Read an immutable evidence source by ID. Allowed when the source is in the task scope or an ancestor layer (lab-wide / project baseline).",
         {"source_id": {"type": "string"}, "project": {"type": "string"}, "ticket": {"type": "string"}},
         ["source_id", "project"]),
    tool("memory_observe",
         "Record a source observation. This stores evidence only; it does not create a confirmed lesson. "
         "For lab-wide standing rules use project=__global__ with empty ticket and confirm_global=true only after the user explicitly approves; keep those sparse.",
         {"project": {"type": "string"}, "ticket": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
          "confirm_global": {"type": "boolean", "description": "Required true when project is __global__; only after explicit user approval."}},
         ["project", "title", "body"], False),
    tool("memory_propose",
         "Store structured candidate memories for review in the local UI. All proposed records remain candidates and cannot influence retrieval until confirmed there. "
         "id is optional (server mints). Each needs an existing evidence source. Response includes activation.via=ui. "
         "Lab-wide candidates need project=__global__, empty ticket, and confirm_global=true after explicit user approval; keep them sparse.",
         {"memories": {"type": "array", "items": MEMORY_DRAFT_SCHEMA, "minItems": 1}},
         ["memories"], False),
    tool("memory_feedback",
         "Record reported helpful, missed, irrelevant or stale memory and diagnose the pipeline stage from a run snapshot. Feedback is not verified ground truth and does not auto-promote lessons.",
         {"run_id": {"type": "string"}, "memory_id": {"type": "string"},
          "observation": {"type": "string", "enum": ["helpful", "missed", "irrelevant", "stale"]}, "note": {"type": "string"}},
         ["run_id", "memory_id", "observation"], False),
    tool("memory_promote",
         "Promote a confirmed ticket memory to a project-baseline candidate with a scoped summary source and opaque provenance. Raw ticket notes stay isolated. Idempotent on origin+claim.",
         {"memory_id": {"type": "string"}, "title": {"type": "string"}, "claim": {"type": "string"}},
         ["memory_id"], False),
]


def call(store, name, args):
    if name == "memory_initiate":
        return agent_api.initiate(store, **args)
    if name == "memory_allocate_ticket":
        return agent_api.allocate_ticket()
    if name == "memory_catalog":
        return agent_api.list_catalog()
    if name == "memory_context":
        return agent_api.context(store, args["task"], budget=args.get("budget", 1200), detail=args.get("detail", "agent"))
    if name == "memory_inspect_run":
        return agent_api.inspect_run(store, args["run_id"])
    if name == "memory_source":
        return agent_api.source(store, args["source_id"], args["project"], args.get("ticket", ""))
    if name == "memory_observe":
        return agent_api.observe(store, args)
    if name == "memory_propose":
        return agent_api.propose(store, args["memories"])
    if name == "memory_feedback":
        return agent_api.feedback(store, args["run_id"], args["memory_id"], args["observation"], args.get("note", ""))
    if name == "memory_promote":
        return agent_api.promote(store, args["memory_id"], title=args.get("title"), claim=args.get("claim"))
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
            elif method == "ping":
                result = {}
            elif not initialized:
                raise ValueError("Initialize the server first")
            elif method == "tools/list":
                result = {"tools": TOOLS}
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
