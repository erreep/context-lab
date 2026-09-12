---
name: context-lab-memory
description: >-
  Use Context Lab for agent memory and recall via MCP. Apply when the user asks
  to remember, recall, look up project lessons, build task context, record an
  observation, propose a candidate lesson, or give feedback on retrieved memory.
---

# Context Lab memory

Use the `context-lab` MCP server. Do not invent project history when these tools are available.

## Recall before a decision

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
| Suggest a lesson for human review | `memory_propose` |
| Report helpful / missed / irrelevant / stale | `memory_feedback` |

Proposed memories stay `candidate` and cannot affect retrieval until confirmed in the local UI (`python3 start.py`).

## Vocabulary

Call `memory_catalog` for starter actions and needs. For new domains, pass your own `actions` and `needs` on the task.
