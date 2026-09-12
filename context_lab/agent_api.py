"""Deep agent surface: initiate modes, lab vault binding, context shaping, propose hints."""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import knowledge as knowledge_mod
from .engine import catalog, compile_context, plan_task
from .schemas import AgentError, DETAIL_LEVELS, activation_hint, format_context, wire_estimated_tokens
from .service import provider_flags
from .store import new_id, scope_covers, scope_key


def initiate(store, project, ticket="", knowledge=None, path=None, empty=False, refresh=False):
    """One-shot setup. Prefer knowledge={mode, vault?}; legacy path/empty still work.

    First OS touch (lab vault undecided) auto-detects Obsidian via app config, else
    returns needs_obsidian_vault until knowledge.vault is a path/'none'.
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
        return knowledge_mod.with_obsidian(
            {"status": "vault_configured", "project": project, "ticket": ""},
            store, auto_detected=False)

    if binding["state"] == "undecided":
        return knowledge_mod.with_obsidian(
            {"status": "needs_obsidian_vault", "project": project, "ticket": ticket},
            store, auto_detected=False)

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
            return knowledge_mod.with_obsidian(
                knowledge_mod.initiate(store, project, ticket), store, auto_detected=auto_detected)
        if mode == "auto":
            if previous and not refresh:
                return knowledge_mod.with_obsidian(
                    knowledge_mod.initiate(store, project, ticket), store, auto_detected=auto_detected)
            if kpath:
                return knowledge_mod.with_obsidian(
                    knowledge_mod.initiate(store, project, ticket, path=kpath, refresh=refresh),
                    store, auto_detected=auto_detected)
            return knowledge_mod.with_obsidian(
                knowledge_mod.initiate(store, project, ticket, empty=True, refresh=refresh),
                store, auto_detected=auto_detected)
        if mode == "empty":
            return knowledge_mod.with_obsidian(
                knowledge_mod.initiate(store, project, ticket, empty=True, refresh=refresh),
                store, auto_detected=auto_detected)
        if not isinstance(kpath, str) or not kpath.strip():
            raise AgentError("missing_field", "knowledge.path required for mode=import", field="knowledge.path")
        return knowledge_mod.with_obsidian(
            knowledge_mod.initiate(store, project, ticket, path=kpath, refresh=refresh),
            store, auto_detected=auto_detected)

    return knowledge_mod.with_obsidian(
        knowledge_mod.initiate(store, project, ticket, path=path, empty=empty, refresh=refresh),
        store, auto_detected=auto_detected)


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
    """Write evidence into the bound ticket folder and index it immediately."""
    project, ticket = scope_key({"project": project, "ticket": ticket})
    if kind not in JOURNAL_KINDS:
        raise AgentError("validation", "kind must be plan|decision|progress|handoff", field="kind")
    if not isinstance(title, str) or not title.strip():
        raise AgentError("validation", "title required", field="title")
    if not isinstance(body, str) or not body.strip():
        raise AgentError("validation", "body required", field="body")
    if not ticket:
        raise AgentError("no_ticket_folder", "journal requires a ticket with a bound notes folder", field="ticket")
    kb = store.knowledge_base(project, ticket)
    if not kb or not isinstance(kb.get("path"), str) or not kb["path"].strip():
        raise AgentError(
            "no_ticket_folder",
            "Ticket has no bound Obsidian/notes folder",
            field="ticket",
            hint="Initiate with knowledge.mode import and a path, or bind a folder first",
        )
    root = Path(kb["path"]).expanduser().resolve(strict=True)
    created = datetime.now(timezone.utc)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")[:48] or "entry"
    journal_dir = root / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    dest = journal_dir / f"{kind}-{stamp}-{slug}.md"
    front = (
        f"---\nkind: {kind}\nproject: {project}\nticket: {ticket}\n"
        f"created_at: {created.strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n"
        f"# {title.strip()}\n\n{body.strip()}\n"
    )
    fd, tmp = tempfile.mkstemp(prefix=".journal-", suffix=".md", dir=str(journal_dir))
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
    return {
        "path": str(dest),
        "relative_path": dest.relative_to(root).as_posix(),
        "kind": kind,
        "project": project,
        "ticket": ticket,
        "index": indexed,
    }


def allocate_ticket():
    return {"ticket": knowledge_mod.allocate_ticket()}


def list_catalog():
    return catalog()
