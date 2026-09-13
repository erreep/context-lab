# Context Lab hard gates

## Opt-in automatic recall (Claude Code and Codex)

Prefer one local command: `context-lab install --client {claude|codex|cursor} --project P --ticket T`. That binds scope, writes MCP + client hooks, and installs the git lease. Claude Code and Codex then inject a scoped CompactView on every prompt (`SessionStart` + `UserPromptSubmit`). Cursor does **not** get prompt injection; install writes MCP, a `beforeShellExecution` git-commit gate, and `.cursor/rules/context-lab-memory.mdc` (soft recall contract). Scope, package, or plugin setup alone never enables hooks.

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

Install the console script once per machine, then opt a repository in with one local command:

```text
pipx install git+https://github.com/erreep/context-lab.git
cd /path/to/project
context-lab install --client claude --project P --ticket T
```

Use `--client codex` or `--client cursor` as needed. Codex still needs `[features] codex_hooks = true`. Cursor gets MCP + a git-commit shell gate + `.cursor/rules/context-lab-memory.mdc` (soft recall contract) — **no** ambient CompactView inject.

`install` binds scope, writes MCP, writes client hooks, and installs the git lease. Scope alone never enables hooks.

Machine-wide MCP only (tools everywhere; **no** ambient inject; **no** git lease):

```text
context-lab install --global --client {claude|codex|cursor}
```

Ambient CompactView and the commit lease still need per-repo `install --client … --project … --ticket …`. Lower-level tools remain: `hook print-config`, `hook install-git`, `hook set-scope`.

Context Lab does not ship active project hook files.

Inside this repository `python3 -m context_lab` works without installing. Portable configs use the installed `context-lab` command. The installed `pre-commit` script tries `context-lab` on PATH first, then the checkout path baked in at install time (GUI Git clients often run hooks with a stripped PATH). If neither works it blocks the commit and prints the install command; it never fails open. A human committing by hand runs `recall-for` like the agent does, or bypasses once with `git commit --no-verify`.

The Git `pre-commit` hook is the enforcement point. It checks the lease. A lease proves a retrieval event under those conditions, not that the agent understood the context. Client hooks (Claude PreToolUse, Cursor beforeShellExecution) are guardrails only, not a security boundary. If a formatter rewrites the index after recall (lint-staged style), run `recall-for` again.

Candidates never affect retrieval until confirmed in the local UI (`context-lab serve`).
Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`; keep them sparse.

Durable ticket evidence goes through `memory_journal` into the bound ticket folder. `Cl/` stays ephemeral and is skipped by the importer.
