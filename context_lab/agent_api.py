"""Deep agent surface: initiate modes, lab vault binding, context shaping, propose hints."""
from __future__ import annotations

from . import knowledge as knowledge_mod
from .engine import catalog
from .schemas import AgentError, activation_hint, format_context
from .service import recall_context
from .ticket_workspace import TicketWorkspace
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
    out = knowledge_mod.with_obsidian(
        result, store, auto_detected=auto_detected, folder_created=created)
    # Provision may bind path after empty initiate; echo it so callers need not dig into obsidian.
    notes = out.get("obsidian", {}).get("notes")
    if notes and not out.get("path"):
        out["path"] = notes
    return out


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


def context(store, task, budget=1200, detail="agent", strategy="targeted", embeddings=None, model_planner=None):
    return recall_context(
        store, task, budget=budget, detail=detail, strategy=strategy,
        options={"embeddings": embeddings, "model_planner": model_planner},
    )


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
                hint="Open the review UI with: context-lab review",
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
    if store.visibility().source_hidden(source_id):
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


def delete(store, kind, artifact_id, *, bound):
    """MCP hide. Exact-scope only; lab-wide is proposed. Agents cannot restore."""
    from .visibility import McpActor, parse_ref

    try:
        ref = parse_ref({"kind": kind, "id": artifact_id})
    except ValueError as e:
        raise AgentError("validation", str(e), field="kind") from e
    if not isinstance(bound, tuple) or len(bound) != 2:
        raise AgentError("validation", "bound scope required", field="cwd")
    try:
        rows = store.hide(ref, actor=McpActor(bound=(bound[0], bound[1])))
    except ValueError as e:
        message = str(e)
        code = "scope_mismatch" if "scope_mismatch" in message else "validation"
        raise AgentError(code, message, field="id") from e
    return {
        "tombstones": [
            {
                "kind": t.kind,
                "id": t.id,
                "state": t.state,
                "project": t.project,
                "ticket": t.ticket,
            }
            for t in rows
        ]
    }


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
        with TicketWorkspace.open(store, project, ticket) as ws:
            result = ws.write_journal(kind, title, body)
    except AgentError:
        raise
    except ValueError as e:
        raise AgentError("journal_not_ready", str(e), field="ticket") from e
    out = {
        "path": result.relative_path,
        "kind": kind,
        "status": result.index_status,
        "source_id": result.source_id,
    }
    if result.chunks is not None:
        out["chunks"] = result.chunks
    return out


def park(store, args, *, captured_while_ticket=""):
    from .parking import ParkingLot, compact_item, triage_command

    if not isinstance(args, dict):
        raise AgentError("validation", "arguments must be an object")
    project = args.get("project", "")
    title = args.get("title", "")
    body = args.get("body", "")
    later = args.get("later", "")
    capture_key = args.get("capture_key")
    try:
        item = ParkingLot(store).capture(
            project=project,
            title=title,
            body=body,
            later=later,
            captured_by="mcp-local",
            captured_while_ticket=captured_while_ticket,
            capture_key=capture_key,
        )
    except ValueError as e:
        raise AgentError("validation", str(e), field="project") from e
    return {"item": compact_item(item), "triage": triage_command(item["project"])}


def allocate_ticket():
    return {"ticket": knowledge_mod.allocate_ticket()}


def list_catalog():
    return catalog()
