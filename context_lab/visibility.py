"""Visibility ledger: soft-delete orthogonal to review STATUSES."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence


HideKind = Literal["memory", "source", "document"]
LedgerState = Literal["proposed", "active"]
Plane = Literal["mcp", "operator"]


@dataclass(frozen=True)
class MemoryRef:
    id: str
    kind: Literal["memory"] = "memory"


@dataclass(frozen=True)
class SourceRef:
    id: str
    kind: Literal["source"] = "source"


@dataclass(frozen=True)
class DocumentRef:
    id: str
    kind: Literal["document"] = "document"


HideRef = MemoryRef | SourceRef | DocumentRef


def parse_ref(raw: object) -> HideRef:
    """Validate {kind, id} at the trust boundary. Reject extra keys and unknown kinds."""
    if not isinstance(raw, dict):
        raise ValueError("ref must be an object")
    if set(raw.keys()) - {"kind", "id"}:
        raise ValueError("ref only allows kind and id")
    kind = raw.get("kind")
    rid = raw.get("id")
    if not isinstance(kind, str) or not isinstance(rid, str) or not rid.strip():
        raise ValueError("ref requires nonempty kind and id")
    rid = rid.strip()
    if kind == "memory":
        return MemoryRef(rid)
    if kind == "source":
        return SourceRef(rid)
    if kind == "document":
        return DocumentRef(rid)
    raise ValueError("ref kind must be memory|source|document")


@dataclass(frozen=True)
class McpActor:
    """Exact bind from admission. Lab-wide hide is always proposed."""

    bound: tuple[str, str]


@dataclass(frozen=True)
class OperatorActor:
    """HTTP / review UI / CLI. Lab-wide active hide/unhide requires confirm_global."""

    confirm_global: bool = False


Actor = McpActor | OperatorActor


@dataclass(frozen=True)
class Tombstone:
    kind: HideKind
    id: str
    state: LedgerState
    hidden_at: str
    hidden_by: Plane
    project: str
    ticket: str
    group_id: str = ""


@dataclass(frozen=True)
class Visibility:
    """Active hides only. Document hide is explicit or any cited source is hidden."""

    active: frozenset[tuple[HideKind, str]]

    def memory_hidden(self, mid: str) -> bool:
        return ("memory", mid) in self.active

    def source_hidden(self, sid: str) -> bool:
        return ("source", sid) in self.active

    def document_hidden(self, doc: dict) -> bool:
        if ("document", doc.get("id", "")) in self.active:
            return True
        return any(self.source_hidden(sid) for sid in doc.get("source_ids", []))


_SNEAK_KEYS = frozenset({"deleted", "deleted_at", "deleted_by", "hidden", "hidden_at"})


def strip_visibility_keys(payload: dict) -> dict:
    """put_memories must not treat sneaked keys as truth."""
    return {k: v for k, v in payload.items() if k not in _SNEAK_KEYS}


def as_refs(refs: HideRef | Sequence[HideRef]) -> list[HideRef]:
    if isinstance(refs, (MemoryRef, SourceRef, DocumentRef)):
        return [refs]
    return list(refs)
