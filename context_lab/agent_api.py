"""Deep agent surface: initiate modes, lab vault binding, context shaping, propose hints."""
from __future__ import annotations

from . import knowledge as knowledge_mod
from .engine import catalog, compile_context
from .schemas import AgentError, activation_hint, format_context
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


def context(store, task, budget=1200, detail="full"):
    if detail not in {"full", "prose"}:
        raise AgentError("validation", "detail must be full or prose", field="detail")
    packet = compile_context(store, task, budget=budget)
    return format_context(packet, detail)


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


def allocate_ticket():
    return {"ticket": knowledge_mod.allocate_ticket()}


def list_catalog():
    return catalog()
