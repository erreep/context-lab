"""Shared application operations for CLI, HTTP and MCP."""
import os

from .engine import ROOT, STRATEGIES, catalog, compile_context, plan_task
from .evaluate import evaluate
from .provider import ModelEndpoint
from .knowledge import allocate_ticket, initiate
from .store import GLOBAL_PROJECT, scope_key, scope_layers


def provider_flags(store, options):
    model = ModelEndpoint(store) if options.get("model_planner") or options.get("embeddings") else None
    return {"planner": model if options.get("model_planner") else None,
            "embeddings": model if options.get("embeddings") else None}


def compare(store, payload):
    flags = provider_flags(store, payload)
    task = plan_task(payload.get("task", {}), planner=flags["planner"])
    packets = [compile_context(store, task, arm, payload.get("budget", 1200), embeddings=flags["embeddings"], planning_metadata=task["planning"])
               for arm in STRATEGIES]
    return {"packets": packets}


def dispatch(store, operation, payload):
    if operation == "initiate":
        return initiate(store, **payload)
    if operation == "compare":
        return compare(store, payload)
    if operation == "context":
        return compile_context(store, payload.get("task", {}), payload.get("strategy", "targeted"),
                               payload.get("budget", 1200), **provider_flags(store, payload))
    if operation == "benchmark":
        return evaluate(store, ROOT / "data" / "scenarios.json", payload.get("budget", 1200), **provider_flags(store, payload))
    if operation == "source":
        return store.add_source(payload)
    if operation == "memories":
        return {"memories": store.put_memories(payload.get("memories", []))}
    if operation == "feedback":
        return store.log_feedback(payload.get("run_id"), payload.get("memory_id"), payload.get("observation"), payload.get("note", ""))
    if operation == "draft":
        source = store.source(payload.get("source_id"))
        if not source:
            raise ValueError("Unknown source_id")
        return ModelEndpoint(store).draft(source)
    if operation == "allocate-ticket":
        return {"ticket": allocate_ticket()}
    if operation == "promote":
        return store.promote(payload.get("memory_id"), title=payload.get("title"), claim=payload.get("claim"))
    raise ValueError("Unknown operation")


def scopes(store):
    rows = store.list_scope_rows()
    by_project = {}
    for row in rows:
        by_project.setdefault(row["project"], []).append(row)
    return {"scopes": rows, "by_project": by_project}


def info(store, project=None, ticket=None):
    bases = store.knowledge_bases()
    scope_rows = store.list_scope_rows()
    projects = sorted({s["project"] for s in store.sources()} | {b["project"] for b in bases} | {GLOBAL_PROJECT},
                      key=lambda p: (p != GLOBAL_PROJECT, p))
    payload = {"version": "0.1.0",
               "projects": projects,
               "knowledge_bases": bases, "scopes": scope_rows, "feedback": store.feedback(),
               "catalog": catalog(),
               "model_available": bool(os.environ.get("CONTEXT_LAB_BASE_URL") and os.environ.get("CONTEXT_LAB_MODEL")),
               "embeddings_available": bool(os.environ.get("CONTEXT_LAB_BASE_URL") and os.environ.get("CONTEXT_LAB_EMBEDDING_MODEL"))}
    if project is not None:
        key = scope_key({"project": project, "ticket": ticket if ticket is not None else ""})
        project, ticket = key
        payload["memories"] = store.memories(project=project, ticket=ticket)
        payload["sources"] = store.sources(project=project, ticket=ticket)
        inherited_m, inherited_s = [], []
        for layer_project, layer_ticket in scope_layers({"project": project, "ticket": ticket}):
            if (layer_project, layer_ticket) == key:
                continue
            inherited_m.extend(store.memories(project=layer_project, ticket=layer_ticket))
            inherited_s.extend(store.sources(project=layer_project, ticket=layer_ticket))
        payload["inherited_memories"] = inherited_m
        payload["inherited_sources"] = inherited_s
        payload["knowledge_bases"] = [b for b in bases if scope_key(b) == key]
        payload["feedback"] = [f for f in payload["feedback"]
                               if isinstance(f.get("task"), dict) and scope_key(f["task"]) == key]
        payload["scope"] = next((r for r in scope_rows if r["project"] == project and r["ticket"] == ticket), None)
    else:
        payload["memories"] = store.memories()
        payload["sources"] = store.sources()
        payload["inherited_memories"] = []
        payload["inherited_sources"] = []
    return payload
