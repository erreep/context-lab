---
name: context-lab-memory
description: >-
  Use Context Lab for agent memory and recall via MCP. Apply when the user asks
  to remember, recall, look up project lessons, build task context, record an
  observation, propose a candidate lesson, or give feedback on retrieved memory.
---

# Context Lab memory

Use the `context-lab` MCP server. Do not invent project history when these tools are available.

For first-time setup or resuming a ticket, use the `context-lab` skill. Ask
project and ticket (allocate only when they want notes without a ticket id).
Call `memory_initiate` with `knowledge: { "mode": "auto", "vault": "<path>|none" }`
on first project touch (vault required). Only offer `Cl/` journal when
`obsidian.journaling` is `"available"`. Keep `project`/`ticket` on every recall
and write. Omitted ticket means project baseline, not all tickets. Lab-wide
writes need user approval + `confirm_global=true`. See `skills/context-lab/gates.md`.

## Recall before a decision

**Required** after initiate/resume and **before every git commit/push**, and before
any consequential decision that depends on prior lessons or constraints. Setup is
not recall.

`task.state` must be scalars only (string/number/bool/null). Never pass file-path
arrays or nested objects — summarize as `file_count`, short validation strings, etc.

Declared `needs` only resolve when memories carry matching `need_tags`. Prefer
omitting invented need labels, or use `memory_catalog`. Missing need status with
selected standing rules is still OK to proceed if the context text covers the risk.

Call `memory_context` with:

```json
{
  "task": {
    "query": "what you are about to do",
    "project": "fieldnote",
    "actions": ["optional", "known", "actions"],
    "needs": ["optional", "known", "needs"],
    "state": {"only": "inspected scalars"},
    "as_of": "YYYY-MM-DD"
  },
  "budget": 1200
}
```

Then:

1. Read the returned context text.
2. Inspect need statuses and warnings.
3. Call `memory_source` for linked evidence before trusting a conditional lesson.
4. Keep `run_id` for later feedback.

Demo corpus project id is `fieldnote`. Omit unknown state keys. Do not treat missing as false.

## Record and learn

| Intent | Tool |
| --- | --- |
| Save raw evidence | `memory_observe` |
| Extract candidates with local Mem0 when requested | `memory_extract` with the source ID and exact project/ticket (up to 4000 UTF-8 bytes) |
| Suggest a lesson for human review | `memory_propose` |
| Report helpful / missed / irrelevant / stale | `memory_feedback` |

Proposed memories stay `candidate` and cannot affect retrieval until confirmed in the local UI (`python3 start.py`).
Mem0 extraction is explicit, not automatic transcript/vault ingestion. It preserves
linked source evidence and existing reviews; normal recall still uses `memory_context`.

## After finishing work

Before closing a ticket slice of work, leave evidence behind:

1. `memory_observe` — what changed and what validation you ran (ticket scope).
2. `memory_propose` — candidate lesson/constraint if something should recur (still needs UI confirm).

Do not leave a ticket with only a git commit and an empty memory scope.

## Vocabulary

Call `memory_catalog` for starter actions and needs. For new domains, pass your own `actions` and `needs` on the task.
