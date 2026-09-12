# Context Lab hard gates

Call `memory_context` before:

1. After `memory_initiate` / allocate-ticket (or choosing an existing scope), before other work.
2. Before every `git commit` or `git push`.
3. Before any decision that depends on prior incidents, constraints, lessons, or project state.

Setup is not recall. Initializing a ticket does not load standing rules until `memory_context` runs.

Candidates never affect retrieval until confirmed in the local UI (`python3 start.py`).
Lab-wide writes (`project=__global__`) need explicit user approval and `confirm_global=true`; keep them sparse.
