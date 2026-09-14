"""Deep agent surface: initiate modes, lab vault binding, context shaping, propose hints."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from . import knowledge as knowledge_mod
from .engine import catalog, compile_context, plan_task
from .schemas import AgentError, DETAIL_LEVELS, activation_hint, format_context, wire_estimated_tokens
from .service import provider_flags
from .store import new_id, scope_covers, scope_key


def _finish_initiate(store, result, *, auto_detected=False):
    created = False
    status = result.get("status")
    if status not in {"needs_obsidian_vault", "needs_knowledge_base"} and result.get("ticket"):
        try:
            _, created = knowledge_mod.ensure_journal_home(
                store, result.get("project", ""), result.get("ticket", ""))
        except OSError:
            created = False
    return knowledge_mod.with_obsidian(
        result, store, auto_detected=auto_detected, folder_created=created)


def initiate(store, project, ticket="", knowledge=None, path=None, empty=False, refresh=False):
    """One-shot setup. Prefer knowledge={mode, vault?}; legacy path/empty still work.

    First OS touch (lab vault undecided) auto-detects Obsidian via app config, else
    returns needs_obsidian_vault until knowledge.vault is a path/'none'.
    auto/empty with a bound vault and a ticket provisions Context Lab/{project}/{ticket}.
    """
    project, ticket = scope_key({"project": project, "ticket": ticket})
    vault_arg = None
    mode = None
    kpath = None
    if knowledge is not None:
        if not isinstance(knowledge, dict):
            raise AgentError("validation", "knowledge must be an object", field="knowledge")
        vault_arg = knowledge.get("vault")
        mode = knowledge.get("mode")
        kpath = knowledge.get("path")
        if mode is not None and mode not in {"auto", "reuse", "empty", "import"}:
            raise AgentError("validation", "knowledge.mode must be auto|reuse|empty|import", field="knowledge.mode")

    try:
        binding, auto_detected = knowledge_mod.ensure_lab_vault(store, vault_arg)
    except (ValueError, OSError) as e:
        raise AgentError("validation", str(e), field="knowledge.vault") from e

    # Vault-only configure (lab-wide); no ticket notes work.
    if ticket == "" and mode is None and path is None and not empty and vault_arg is not None:
        return _finish_initiate(
            store, {"status": "vault_configured", "project": project, "ticket": ""}, auto_detected=False)

    if binding["state"] == "undecided":
        return _finish_initiate(
            store, {"status": "needs_obsidian_vault", "project": project, "ticket": ticket}, auto_detected=False)

    if knowledge is not None:
        previous = store.knowledge_base(project, ticket)
        if mode is None:
            raise AgentError(
                "missing_field",
                "knowledge.mode is required for ticket setup after the vault decision",
                field="knowledge.mode",
            )
        if mode == "reuse":
            if not previous:
                raise AgentError(
                    "missing_field",
                    "Scope is not initialized; use knowledge.mode auto, empty, or import",
                    field="knowledge.mode",
                    hint="Pass knowledge={mode:'auto'} for first setup",
                )
            return _finish_initiate(
                store, knowledge_mod.initiate(store, project, ticket), auto_detected=auto_detected)
        if mode == "auto":
            if previous and not refresh:
                return _finish_initiate(
                    store, knowledge_mod.initiate(store, project, ticket), auto_detected=auto_detected)
            if kpath:
                return _finish_initiate(
                    store, knowledge_mod.initiate(store, project, ticket, path=kpath, refresh=refresh),
                    auto_detected=auto_detected)
            return _finish_initiate(
                store, knowledge_mod.initiate(store, project, ticket, empty=True, refresh=refresh),
                auto_detected=auto_detected)
        if mode == "empty":
            return _finish_initiate(
                store, knowledge_mod.initiate(store, project, ticket, empty=True, refresh=refresh),
                auto_detected=auto_detected)
        if not isinstance(kpath, str) or not kpath.strip():
            raise AgentError("missing_field", "knowledge.path required for mode=import", field="knowledge.path")
        return _finish_initiate(
            store, knowledge_mod.initiate(store, project, ticket, path=kpath, refresh=refresh),
            auto_detected=auto_detected)

    return _finish_initiate(
        store, knowledge_mod.initiate(store, project, ticket, path=path, empty=empty, refresh=refresh),
        auto_detected=auto_detected)


def context(store, task, budget=1200, detail="agent"):
    if detail not in DETAIL_LEVELS:
        raise AgentError("validation", "detail must be agent, prose, inspect, or full", field="detail")
    compact = detail in {"agent", "prose"}
    flags = provider_flags(store)
    # Plan once. Shrink packs the same planned task; only the final packet is saved.
    planned = plan_task(task, planner=flags["planner"])
    select_budget = budget
    packet, view, wire_text = None, None, ""
    for _ in range(12):
        packet = compile_context(
            store, planned, budget=select_budget,
            embeddings=flags["embeddings"], planner=None, persist=False,
            planning_metadata=planned.get("planning"),
        )
        view, wire_text = format_context(packet, detail)
        if not compact or wire_estimated_tokens(wire_text) <= budget:
            break
        if select_budget <= 128:
            break
        select_budget = max(128, int(select_budget * 0.85))
    if compact and wire_estimated_tokens(wire_text) > budget:
        raise AgentError(
            "wire_budget_exceeded",
            "Compact response exceeds budget; shorten the task or raise budget",
            field="budget",
            hint="Use memory_inspect_run for traces; do not widen the agent wire",
        )
    packet = store.save_run(packet)
    view, _ = format_context(packet, detail)
    return view


def inspect_run(store, run_id):
    packet = store.run(run_id)
    if not packet:
        raise AgentError("not_found", "Unknown run_id", field="run_id")
    view, _ = format_context(packet, detail="inspect")
    return view


def propose(store, memories):
    if not isinstance(memories, list) or not memories:
        raise AgentError("validation", "memories must be a nonempty array", field="memories")
    entries = []
    for item in memories:
        if not isinstance(item, dict):
            raise AgentError("validation", "each memory must be an object", field="memories")
        draft = dict(item)
        draft.pop("status", None)
        if not draft.get("id"):
            draft["id"] = new_id("mem")
        if store.memory(draft["id"]):
            raise AgentError(
                "memory_exists_revise_via_ui",
                "Use a new candidate ID; existing memories are revised through review",
                field="id",
                hint="Open http://127.0.0.1:8765 Memories tab",
            )
        entries.append(dict(draft, status="candidate"))
    saved = store.put_memories(entries)
    project, ticket = scope_key(saved[0])
    return {"memories": saved, "activation": activation_hint(store, project, ticket)}


def source(store, source_id, project, ticket=""):
    s = store.source(source_id)
    if not s or not scope_covers(s, {"project": project, "ticket": ticket}):
        raise AgentError(
            "source_not_in_scope",
            "Source not found in this project/ticket or an ancestor layer",
            field="source_id",
        )
    return s


def observe(store, args):
    return store.add_source(args)


def feedback(store, run_id, memory_id, observation, note=""):
    return store.log_feedback(run_id, memory_id, observation, note)


def promote(store, memory_id, title=None, claim=None):
    return store.promote(memory_id, title=title, claim=claim)


JOURNAL_KINDS = frozenset({"plan", "decision", "progress", "handoff"})


def journal(store, project, ticket, kind, title, body):
    """Write one durable ticket note and index it. Provisions a vault folder if that is all that is missing."""
    project, ticket = scope_key({"project": project, "ticket": ticket})
    if kind not in JOURNAL_KINDS:
        raise AgentError("validation", "kind must be plan|decision|progress|handoff", field="kind")
    if not isinstance(title, str) or not title.strip():
        raise AgentError("validation", "title required", field="title")
    if not isinstance(body, str) or not body.strip():
        raise AgentError("validation", "body required", field="body")
    try:
        home, _created = knowledge_mod.require_journal_home(store, project, ticket)
    except ValueError as e:
        raise AgentError("journal_not_ready", str(e), field="ticket") from e
    dest, already = knowledge_mod.journal_note_path(home.notes, kind, title, body)
    if not already:
        created = datetime.now(timezone.utc)
        digest = knowledge_mod.journal_digest(kind, title, body)
        dest.parent.mkdir(parents=True, exist_ok=True)
        front = (
            f"---\nkind: {kind}\nproject: {project}\nticket: {ticket}\n"
            f"created_at: {created.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"digest: {digest}\n---\n\n"
            f"# {title.strip()}\n\n{body.strip()}\n"
        )
        fd, tmp = tempfile.mkstemp(prefix=".journal-", suffix=".md", dir=str(dest.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(front)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, dest)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    indexed = knowledge_mod.index_ticket_file(store, project, ticket, dest)
    out = {
        "path": indexed["path"],
        "kind": kind,
        "status": indexed["status"],
        "source_id": indexed["source_id"],
    }
    if "chunks" in indexed:
        out["chunks"] = indexed["chunks"]
    return out


def allocate_ticket():
    return {"ticket": knowledge_mod.allocate_ticket()}


def list_catalog():
    return catalog()
