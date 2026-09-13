# Context Lab hard gates

## Opt-in automatic recall (Claude Code and Codex)

Only after the user explicitly installs client hooks and sets worktree scope (`python3 -m context_lab hook set-scope --project P --ticket T`) do Claude Code and Codex inject a scoped CompactView on every prompt through the shared hook contract. SessionStart injects standing rules (project baseline and `__global__`) plus this gate text. Cursor does not get prompt injection. Its optional config supplies a `beforeShellExecution` deny for `git commit`; the Git hook is installed separately. Scope, package, or plugin setup alone never enables hooks.

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

## Using Context Lab from another repository

Install the console script once per machine. To explicitly opt a repository into hooks, generate and install the relevant config:

```text
pipx install git+https://github.com/erreep/context-lab.git
context-lab hook print-config claude      # writes the JSON for .claude/settings.json
context-lab hook print-config codex       # .codex/hooks.json (needs [features] codex_hooks = true)
context-lab hook print-config cursor      # .cursor/hooks.json
```

Context Lab does not ship active project hook files. Generating a config and placing it at the printed path is the client-hook opt-in; `context-lab hook install-git` is a separate Git-hook opt-in.

Inside this repository `python3 -m context_lab` works without installing. Portable configs use the installed `context-lab` command. The installed `pre-commit` script tries `context-lab` on PATH first, then the checkout path baked in at install time (GUI Git clients often run hooks with a stripped PATH). If neither works it blocks the commit and prints the install command; it never fails open. A human committing by hand runs `recall-for` like the agent does, or bypasses once with `git commit --no-verify`.

The Git `pre-commit` hook is the enforcement point. It checks the lease. A lease proves a retrieval event under those conditions, not that the agent understood the context. Client hooks (Claude PreToolUse, Cursor beforeShellExecution) are guardrails only, not a security boundary. If a formatter rewrites the index after recall (lint-staged style), run `recall-for` again.

Candidates never affect retrieval until confirmed in the local UI (`python3 start.py`).
Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`; keep them sparse.

Durable ticket evidence goes through `memory_journal` into the bound ticket folder. `Cl/` stays ephemeral and is skipped by the importer.
