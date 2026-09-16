"""Agent-facing JSON shapes, errors, and gate text. Single source for MCP schemas."""
from __future__ import annotations

import json
import math

GATE_TEXT = """# Context Lab contract

1. Commit: run `python3 -m context_lab hook recall-for --purpose commit` before `git commit` (lease = retrieval under bound git state, not comprehension).
2. Soft clients: call `memory_context` with client `cwd` + `task` and optional `since` as the last `place`; do not poll `memory_scope` first. Claude/Codex standing inject is SessionStart only.
3. Candidates never affect retrieval until confirmed in the local UI. Propose one sharp ticket-scoped claim per outcome (`title` + `claim` + `source_ids`).
4. Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`.

Setup is not recall. Client hooks are guardrails, not a security boundary.
"""

KINDS = ["fact", "constraint", "decision", "event", "lesson", "standing_rule"]
DETAIL_LEVELS = frozenset({"agent", "prose", "inspect", "full"})
MUST_REVIEW_KINDS = frozenset({"constraint", "standing_rule"})


def review_tier(memory):
    """Derive review urgency from existing fields. Presentation only; never stored.

    must  — lab-wide, baseline, constraints, standing rules (read before confirm)
    batch — ticket-scoped facts/events/decisions/lessons (multi-confirm ok)
    """
    if not isinstance(memory, dict):
        return "must"
    project = str(memory.get("project") or "")
    ticket = str(memory.get("ticket") or "").strip()
    kind = str(memory.get("kind") or "")
    if project == "__global__" or kind in MUST_REVIEW_KINDS or not ticket:
        return "must"
    return "batch"


STANDING_RESERVE_RATIO = 0.25
STANDING_MAX_TOKENS = 500
STANDING_MIN_TOKENS = 300

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

FLAT_CONTEXT_EXAMPLE = '{"cwd":"/path/to/workspace","task":"what you are about to do"}'

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
            "description": "Lab-wide Obsidian vault root, or 'none' to decline. Optional when auto-detect succeeds on first OS touch.",
        },
    },
}

PROSE_KEYS = ("run_id", "context", "estimated_tokens", "needs", "warnings", "dependency_gaps", "picks")
FULL_KEYS = PROSE_KEYS + ("selected", "trace", "conflicts")
COMPACT_TITLE_LIMIT = 64


def compact_record_title(record, limit=COMPACT_TITLE_LIMIT):
    """Bounded CompactView label. Documents use heading (or stem); others use title."""
    if record.get("kind") == "document":
        text = (record.get("heading") or "").strip()
        if not text or text == "Overview":
            path = record.get("path") or ""
            text = path.rsplit("/", 1)[-1]
            if text.endswith(".md"):
                text = text[:-3]
            text = text or (record.get("title") or "note")
    else:
        text = (record.get("title") or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


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


def wire_dumps(obj):
    """Single JSON serialization for MCP tool results and wire metering."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def wire_estimated_tokens(obj_or_text):
    """ceil(UTF-8 bytes / 4) over the exact text MCP will send (or an object via wire_dumps)."""
    text = obj_or_text if isinstance(obj_or_text, str) else wire_dumps(obj_or_text)
    return math.ceil(len(text.encode("utf-8")) / 4)


def with_wire_estimated_tokens(obj):
    out = dict(obj)
    estimate = 0
    for _ in range(4):
        out["wire_estimated_tokens"] = estimate
        measured = wire_estimated_tokens(out)
        if measured == estimate:
            return out
        estimate = measured
    out["wire_estimated_tokens"] = estimate
    return out


def format_context(packet, detail="agent"):
    """Project a compile packet for the agent (compact) or inspect (trace) surface.

    Returns (view, wire_text) where wire_text is the exact JSON MCP should send as tool text.
    """
    compact = detail in {"agent", "prose"}
    keys = PROSE_KEYS if compact else FULL_KEYS
    out = {k: packet[k] for k in keys if k in packet and k != "picks"}
    out["picks"] = [{"id": m["id"], "title": compact_record_title(m)} for m in packet.get("selected", [])]
    out = with_wire_estimated_tokens(out)
    return out, wire_dumps(out)


def activation_hint(store, project, ticket=""):
    pending = sum(1 for m in store.memories(project, ticket) if m.get("status") == "candidate")
    return {
        "via": "ui",
        "command": "context-lab review",
        "pending_candidates": pending,
        "message": "Candidates stay out of retrieval until confirmed in the local UI.",
    }
