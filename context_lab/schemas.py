"""Agent-facing JSON shapes, errors, and gate text. Single source for MCP schemas."""
from __future__ import annotations

GATE_TEXT = """# Context Lab hard gates

Call `memory_context` before:
1. After `memory_initiate` / allocate-ticket (or choosing an existing scope), before other work.
2. Before every `git commit` or `git push`.
3. Before any decision that depends on prior incidents, constraints, lessons, or project state.

Setup is not recall. Candidates never affect retrieval until confirmed in the local UI.
Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`.
"""

KINDS = ["fact", "constraint", "decision", "event", "lesson"]

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "project": {"type": "string"},
        "ticket": {
            "type": "string",
            "description": "Exact ticket ID; omit for project baseline. Retrieval also includes project baseline and sparse lab-wide (__global__) memories. Other tickets stay excluded.",
        },
        "actions": {"type": "array", "items": {"type": "string"}},
        "needs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only list needs that match memory need_tags (or catalog needs). Invented labels show as missing even when related standing rules were selected.",
        },
        "state": {
            "type": "object",
            "description": "Inspected scalars only (string/number/bool/null). No arrays or nested objects.",
        },
        "as_of": {"type": "string"},
    },
    "required": ["query", "project"],
}

MEMORY_DRAFT_SCHEMA = {
    "type": "object",
    "required": ["project", "kind", "title", "claim", "source_ids"],
    "properties": {
        "id": {"type": "string", "description": "Optional; server mints one when omitted."},
        "project": {"type": "string"},
        "ticket": {"type": "string"},
        "kind": {"type": "string", "enum": KINDS},
        "title": {"type": "string", "minLength": 1},
        "claim": {"type": "string", "minLength": 1},
        "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "rationale": {"type": "string"},
        "expected_effect": {"type": "string"},
        "quote": {"type": "string"},
        "valid_from": {"type": "string"},
        "valid_until": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "need_tags": {"type": "array", "items": {"type": "string"}},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "supersedes": {"type": "array", "items": {"type": "string"}},
        "applies": {"type": "object"},
        "unless": {"type": "object"},
        "assumptions": {"type": "object"},
        "assertions": {"type": "object"},
        "confirm_global": {
            "type": "boolean",
            "description": "Required true when project is __global__; only after explicit user approval.",
        },
    },
    "additionalProperties": False,
}

KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["auto", "reuse", "empty", "import"]},
        "path": {"type": "string", "description": "Ticket notes folder when mode is import (or auto with a path)."},
        "vault": {
            "type": "string",
            "description": "Obsidian vault root or any journal folder, or 'none' to decline. Lab-wide for this DB. Optional when auto-detect succeeds on first OS touch.",
        },
    },
}

PROSE_KEYS = ("run_id", "context", "estimated_tokens", "needs", "warnings", "dependency_gaps")
FULL_KEYS = PROSE_KEYS + ("selected", "trace", "conflicts")


class AgentError(ValueError):
    def __init__(self, code, message, field=None, hint=None):
        super().__init__(message)
        self.code, self.message, self.field, self.hint = code, message, field, hint

    def as_dict(self):
        out = {"code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        if self.hint:
            out["hint"] = self.hint
        return out


def error_payload(exc):
    if isinstance(exc, AgentError):
        return {"error": exc.as_dict()}
    return {"error": {"code": "validation", "message": str(exc)}}


def format_context(packet, detail="full"):
    keys = PROSE_KEYS if detail == "prose" else FULL_KEYS
    return {k: packet[k] for k in keys if k in packet}


def activation_hint(store, project, ticket=""):
    pending = sum(1 for m in store.memories(project, ticket) if m.get("status") == "candidate")
    return {
        "via": "ui",
        "url": "http://127.0.0.1:8765",
        "pending_candidates": pending,
        "message": "Candidates stay out of retrieval until confirmed in the local UI.",
    }
