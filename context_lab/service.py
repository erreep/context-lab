"""Shared application operations for CLI, HTTP and MCP."""
import os

from .engine import ROOT, STRATEGIES, catalog, compile_context, plan_task
from .evaluate import evaluate
from .provider import ModelEndpoint
from .knowledge import initiate


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
    if operation == "mem0-extract":
        from .mem0_bridge import extract
        return extract(store, **payload)
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
    raise ValueError("Unknown operation")


def info(store):
    bases = store.knowledge_bases()
    return {"version": "0.1.0", "projects": sorted({s["project"] for s in store.sources()} | {b["project"] for b in bases}),
            "knowledge_bases": bases,
            "memories": store.memories(), "sources": store.sources(), "feedback": store.feedback(),
            "catalog": catalog(),
            "model_available": bool(os.environ.get("CONTEXT_LAB_BASE_URL") and os.environ.get("CONTEXT_LAB_MODEL")),
            "embeddings_available": bool(os.environ.get("CONTEXT_LAB_BASE_URL") and os.environ.get("CONTEXT_LAB_EMBEDDING_MODEL"))}
