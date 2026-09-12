"""Minimal MCP stdio server: initialize, ping, tools/list, tools/call.

Implements the 2025-11-25 tool subset using newline-delimited JSON-RPC.
No HTTP MCP transport, notifications, resources, or background tasks.
"""
import json
import sys

from .engine import compile_context


TASK = {"type": "object", "properties": {
    "query": {"type": "string"}, "project": {"type": "string"},
    "actions": {"type": "array", "items": {"type": "string"}},
    "needs": {"type": "array", "items": {"type": "string"}},
    "state": {"type": "object"}, "as_of": {"type": "string"}}, "required": ["query", "project"]}


def tool(name, description, properties, required, read_only=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": False}}


TOOLS = [
    tool("memory_catalog", "List the supported task actions and information needs. Use these to describe your next decision.", {}, []),
    tool("memory_context", "Build task-targeted context. Supply current state and actions where known. Reports unresolved evidence needs. Read source evidence before assuming a conditional lesson applies. Does not prove task sufficiency.",
         {"task": TASK, "budget": {"type": "integer", "minimum": 128, "maximum": 16000}}, ["task"], False),
    tool("memory_source", "Read an immutable evidence source by ID within the specified project.",
         {"source_id": {"type": "string"}, "project": {"type": "string"}}, ["source_id", "project"]),
    tool("memory_observe", "Record a source observation. This stores evidence only; it does not create a confirmed lesson.",
         {"project": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}, ["project", "title", "body"], False),
    tool("memory_propose", "Store structured candidate memories for review in the local UI. All proposed records remain candidates and cannot influence retrieval until confirmed there. Each needs an existing evidence source.",
         {"memories": {"type": "array", "items": {"type": "object"}}}, ["memories"], False),
    tool("memory_feedback", "Record reported helpful, missed, irrelevant or stale memory and diagnose the pipeline stage from a run snapshot. Feedback is not verified ground truth and does not auto-promote lessons.",
         {"run_id": {"type": "string"}, "memory_id": {"type": "string"},
          "observation": {"type": "string", "enum": ["helpful", "missed", "irrelevant", "stale"]}, "note": {"type": "string"}},
         ["run_id", "memory_id", "observation"], False),
]


def call(store, name, args):
    if name == "memory_catalog":
        from .engine import catalog
        return catalog()
    if name == "memory_context":
        p = compile_context(store, args["task"], budget=args.get("budget", 1200))
        return {k: p[k] for k in ("run_id", "context", "estimated_tokens", "needs", "warnings", "dependency_gaps")}
    if name == "memory_source":
        s = store.source(args["source_id"])
        if not s or s["project"] != args["project"]:
            raise ValueError("Source not found in this project")
        return s
    if name == "memory_observe":
        return store.add_source(args)
    if name == "memory_propose":
        entries = []
        for item in args["memories"]:
            if store.memory(item.get("id")):
                raise ValueError("Use a new candidate ID; existing memories are revised through review")
            entries.append(dict(item, status="candidate"))
        return {"memories": store.put_memories(entries)}
    if name == "memory_feedback":
        return store.log_feedback(args["run_id"], args["memory_id"], args["observation"], args.get("note", ""))
    raise ValueError("Unknown tool")


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
                          "serverInfo": {"name": "context-lab", "version": "0.1.0"}}
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
                    result = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}
                except (ValueError, TypeError, KeyError) as e:
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
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
