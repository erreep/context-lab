# Context Lab hard gates

## Automatic recall (Claude Code and Codex)

With worktree scope set (`python3 -m context_lab hook set-scope --project P --ticket T`), Claude Code and Codex inject a scoped CompactView on every prompt through the shared hook contract. SessionStart injects standing rules (project baseline and `__global__`) plus this gate text. Cursor does not get prompt injection. It keeps this always-apply rule, a `beforeShellExecution` deny for `git commit`, and the real Git hook.

Call `memory_context` (or rely on ambient inject where the harness supports it) before:

1. After `memory_initiate` / allocate-ticket (or choosing an existing scope), before other work.
2. Before every `git commit` or `git push`.
3. Before any decision that depends on prior incidents, constraints, lessons, or project state.

Setup is not recall. Initializing a ticket does not load standing rules until recall runs.

## Commit lease

Before `git commit`, run:

```text
python3 -m context_lab hook recall-for --purpose commit
```

That prints a CompactView and writes a short-lived lease bound to HEAD, the index tree, scope, and `run_id`. Install the verifier once with `python3 -m context_lab hook install-git` (refuses to overwrite an existing `pre-commit` or a set `core.hooksPath`).

Known limit: every hook runs `python3 -m context_lab`, which only imports when the worktree root is this repository. In any other repository the installed `pre-commit` fails with "No module named context_lab" and blocks every commit until packaging lands.

The Git `pre-commit` hook is the enforcement point. It checks the lease. A lease proves a retrieval event under those conditions, not that the agent understood the context. Client hooks (Claude PreToolUse, Cursor beforeShellExecution) are guardrails only, not a security boundary. If a formatter rewrites the index after recall (lint-staged style), run `recall-for` again.

Candidates never affect retrieval until confirmed in the local UI (`python3 start.py`).
Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`; keep them sparse.

Durable ticket evidence goes through `memory_journal` into the bound ticket folder. `Cl/` stays ephemeral and is skipped by the importer.
