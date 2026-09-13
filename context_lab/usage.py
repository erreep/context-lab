"""Local payload estimates, not provider usage. Never store request/response bodies."""
import sqlite3
import sys

from .schemas import wire_dumps, wire_estimated_tokens
from .store import now, scope_key

# ponytail: bytes/4 is a coarse payload proxy; use a local tokenizer if exact counts become required.


def record(store, channel, operation, scope=None, request="", response=""):
    project, ticket = scope if scope is not None else (None, None)
    try:
        with store.db:
            store.db.execute(
                "INSERT INTO usage_events (created_at, project, ticket, channel, operation, "
                "request_estimated_tokens, response_estimated_tokens) VALUES (?,?,?,?,?,?,?)",
                (now(), project, ticket, channel, operation,
                 wire_estimated_tokens(request), wire_estimated_tokens(response)),
            )
    except (sqlite3.Error, UnicodeError):
        # A failed meter must not turn a completed write into a retryable tool error.
        print("Context Lab usage meter: could not persist event; report may be incomplete.", file=sys.stderr)


def _mcp_scope(store, name, args):
    if not isinstance(args, dict):
        return None
    if name == "memory_context":
        candidates = [args.get("task")]
    elif name == "memory_propose":
        candidates = args.get("memories")
    elif name in {"memory_inspect_run", "memory_feedback"}:
        run = store.run(args["run_id"]) if isinstance(args.get("run_id"), str) else None
        candidates = [run.get("task") if run else None]
    elif name == "memory_promote":
        candidates = [store.memory(args["memory_id"]) if isinstance(args.get("memory_id"), str) else None]
    else:
        candidates = [args]
    if not isinstance(candidates, list) or not candidates or not all(isinstance(c, dict) for c in candidates):
        return None
    try:
        scopes = {scope_key(c) for c in candidates}
    except ValueError:
        return None
    # Mixed-scope batches count once, unassigned, rather than charging every ticket.
    return next(iter(scopes)) if len(scopes) == 1 else None


def record_mcp(store, name, args, result):
    try:
        scope = _mcp_scope(store, name, args)
    except (sqlite3.Error, UnicodeError):
        scope = None
    record(store, "mcp", name, scope,
           request=wire_dumps({"name": name, "arguments": args}),
           response="\n".join(c["text"] for c in result["content"] if c["type"] == "text"))


def report(store, project=None, ticket=None):
    if ticket is not None and project is None:
        raise ValueError("--ticket requires --project")
    filters, params = [], []
    if project is not None:
        project, _ = scope_key({"project": project})
        filters.append("project = ?")
        params.append(project)
    if ticket is not None:
        ticket = ticket.strip()
        filters.append("ticket = ?")
        params.append(ticket)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    rows = [dict(row) for row in store.db.execute(
        "SELECT project, ticket, channel, operation, COUNT(*) AS events, "
        "SUM(request_estimated_tokens) AS request_estimated_tokens, "
        "SUM(response_estimated_tokens) AS response_estimated_tokens, "
        "MIN(created_at) AS first_event_at, MAX(created_at) AS last_event_at "
        "FROM usage_events" + where + " GROUP BY project, ticket, channel, operation "
        "ORDER BY project, ticket, channel, operation", params)]
    totals = {key: sum(row[key] for row in rows) for key in
              ("events", "request_estimated_tokens", "response_estimated_tokens")}
    return {
        "estimator": "ceil(UTF-8 bytes / 4) per request/response; not actual model tokens or cost",
        "coverage": "MCP name/arguments, result text, setup instructions/tool definitions, and hook output. "
                    "Excludes transport framing, skill loading, history replay/cache, model calls, and other CLI/UI operations.",
        "project": project, "ticket": ticket,
        **totals,
        "total_estimated_tokens": totals["request_estimated_tokens"] + totals["response_estimated_tokens"],
        "unassigned_events_in_database": store.db.execute(
            "SELECT COUNT(*) FROM usage_events WHERE project IS NULL").fetchone()[0],
        "breakdown": rows,
    }


def format_report(data):
    lines = ["Context Lab usage — estimates only", data["estimator"],
             f"Events: {data['events']} | Request: {data['request_estimated_tokens']} | "
             f"Response/injection: {data['response_estimated_tokens']} | Total: {data['total_estimated_tokens']}"]
    if not data["events"]:
        lines.append("No recorded traffic in this scope. Metering starts when updated MCP/hook processes run.")
    for row in data["breakdown"]:
        label = "unassigned" if row["project"] is None else f"{row['project']}/{row['ticket'] or '(baseline)'}"
        lines.append(f"{label}  {row['channel']}:{row['operation']}  {row['events']} events  "
                     f"request={row['request_estimated_tokens']} response={row['response_estimated_tokens']}")
    lines.append(f"Unassigned events in database: {data['unassigned_events_in_database']} "
                 "(included only in the unfiltered report; mixed batches and setup cannot be charged to a ticket).")
    lines.append(data["coverage"])
    return "\n".join(lines)
