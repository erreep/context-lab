# Context Lab gates

Runtime contract: MCP `initialize.instructions` (`context_lab.schemas.GATE_TEXT`). Keep this file thin.

## Arm the worktree

```text
context-lab install --client {claude|codex|cursor} --project P --ticket T
```

That binds scope, writes MCP + client hooks, and installs the git lease. Scope/plugin alone never enables hooks.

| Capability | Clients | Meaning |
|---|---|---|
| Ambient | Claude, Codex | Standing context injected on SessionStart/prompt |
| Soft | Cursor | No inject; agent must call `memory_context` |

Machine-wide MCP only: `context-lab install --global --client …` (no ambient, no lease). Per-repo install still required for inject/lease.

## Commit lease

```text
python3 -m context_lab hook recall-for --purpose commit
```

`pre-commit` verifies the lease (retrieval under bound git state, not comprehension). Client shell gates are guardrails only.

## Candidates

Never affect retrieval until confirmed in the local UI (`context-lab serve`). Must-tier (lab-wide, baseline, constraints, standing rules) is one-at-a-time; ticket batch-tier may multi-confirm. Propose durable title+claim linked to real `source_ids`.
Human surface is the review inbox URL from `context-lab serve`.
