---
name: context-lab
description: Daily Context Lab loop for scoped recall, evidence, and candidate memories. Use for initiate/resume, memory_context, observe/propose, journal notes, and commit recall. Setup (vault/ticket allocate) is first-run only.
---

# Context Lab

MCP tools are the source of truth. Contract text lives in MCP `initialize.instructions` (`GATE_TEXT`). Do not restate long gate essays.

## Daily loop

1. **Scope** — `memory_scope` (or initiate below if unbound).
2. **Recall** — Soft clients (Cursor: no ambient inject): call `memory_context` after bind and before history-dependent work. Ambient clients (Claude/Codex): standing context is already injected; call `memory_context` when you need a focused query or before commit.
3. **Work** — use the context packet; read evidence with `memory_source` when a condition matters.
4. **Record** — `memory_observe` for evidence; `memory_propose` one sharp ticket-scoped candidate per outcome (`title` + `claim` + `source_ids`). Unrelated later-fixes go through `memory_park`, not propose/journal/baseline. Candidates stay inert until the human confirms in the local UI. When `obsidian.journaling` is `ready`, durable plan/decision/progress/handoff notes go through `memory_journal` under the ticket folder.
5. **Commit** — run `context-lab hook recall-for --purpose commit` before `git commit`.

Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`. Keep them sparse.

## First setup only

1. Ask for `project`. Ask whether this work has a ticket id.
2. If yes, use their exact id. If no and they want notes/memories, call `memory_allocate_ticket` and tell them the id (minting does not create a notes folder). If no ticket and no notes, omit ticket (project baseline).
3. Call `memory_initiate` with `project`, optional `ticket`, and `knowledge` as needed. On first DB touch the server may auto-bind an Obsidian vault; if it returns `needs_obsidian_vault`, ask for a vault path or `knowledge.vault: "none"`. Use `mode: "import"` + `path` for an existing ticket folder; `mode: "auto"` or `"empty"` with a bound vault and a ticket provisions `{vault}/Context Lab/{project}/{ticket}/`.
4. Read `response.obsidian`. Relay `announce` if set. If `journaling` is `unavailable` or `vault_only`, follow `obsidian.next`. When `ready`, journal writes are allowed.
5. Report identity and vault/journaling status. Then return to the daily loop (recall).

Refresh/reindex only when the user asks: `memory_initiate` with `refresh: true`.

## CLI fallback

If MCP is unavailable, use the skill-bundled shim (prefers `context-lab` on PATH):

```sh
python3 <skill-directory>/scripts/context_lab.py allocate-ticket
python3 <skill-directory>/scripts/context_lab.py initiate --project P --ticket T
python3 <skill-directory>/scripts/context_lab.py context --task task.json
```
