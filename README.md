# Context Lab

A runnable MVP for testing **when a memory should affect an agent's next decision**.

Includes a local browser interface, Python CLI, SQLite memory store, a minimal stdio
MCP server, optional model adapters, and a synthetic comparison suite. Python 3.10+
is required. The default application has **no third-party Python dependencies**.
Tested here with Python 3.12.

## Start in one command

Unzip the package, open a terminal in `context-lab`, and run:

```bash
python3 start.py
```

On Windows, use `python start.py` if that is your Python command. Open
**http://127.0.0.1:8765** in your browser. Press Ctrl+C in the terminal to stop.
For a busy port: `python3 start.py --port 8766`.

The launcher seeds an empty database with the fictional Fieldnote project. It never
overwrites existing records. Data is saved locally to `workspace/memory.sqlite3`.
The server listens only on localhost. It is a local experiment, not a hosted service.

## Try these five things

1. **Context builder → Background uploads after reconnect.** Compare what each arm
   retrieves. Expand “Why this memory?” and “records left out.” The targeted arm
   brings the offline constraint and the complete duplicate-prevention evidence.
2. **Duplicate prevention already verified.** Watch a formerly useful lesson become
   inapplicable. Unknown state is handled differently from a known exception.
3. **Team sharing changes an assumption.** The old architecture decision is retained,
   but its single-device assumption is flagged for reconsideration. The access policy
   remains missing instead of being invented.
4. **Conflicting retention records.** Neither contradictory value is treated as settled.
5. **Benchmark → Run comparison suite.** Inspect every case, including the two
   paraphrases the starter task rules miss. “Agent supplies task features” shows the
   intended integration path for such wording.

## What the three modes compare

| Mode | Available records | Selection |
|---|---|---|
| A: `retrieval` | Facts, events, decisions, constraints | BM25, optionally fused with real model embeddings |
| B: `lessons` | Same records plus confirmed lessons | Same retriever and content representation |
| C: `targeted` | Same records as B | Retriever plus action/need activation, applicability checks, complete dependency bundles, need coverage and budget selection |

All modes share project isolation, date filtering, candidate exclusion, supersession,
the same task features, the same formatting policy and the same context budget.
Their evidence-check footer uses the same declared-need assessor. The C arm also
spells out individual applicability checks in its context. These are bundled design
comparisons, not a causal isolation of each individual mechanism.

**The default retriever is lexical BM25, not hybrid semantic search.** Enabling an
embedding endpoint makes it hybrid using reciprocal rank fusion. There are no fake
or hash-based “semantic embeddings.”

## What is real, and what is a scaffold

- **Implemented:** persistence, immutable evidence, revision history, optimistic
  concurrency checks, project/date filtering, supersession, conditional activation,
  exceptions, dependency bundles, conflict reporting, estimated-token budgeting,
  run snapshots and feedback diagnosis.
- **Implemented, optional:** model-assisted task planning, drafting candidate lessons
  from a source, exact-excerpt validation and cached model embeddings.
- **Scaffold:** the demo's 20 memories and 28 scenarios are authored fixtures. The
  default task planner uses the inspectable patterns in `data/task_rules.json`.
- **Not implemented:** automatic verified learning, a nightly scheduler, live agent
  action execution, learned utility ranking, universal semantic conflict detection,
  or a proof that context is sufficient. A prompted draft is not a verified lesson.

The initial comparison is deliberately a context-selection experiment. It does not
claim an improvement in real agent task completion. See `results/benchmark.md` and
the complete per-case `results/benchmark.json` for the saved run and limitations.

## Use your own observations

For a clean store without fictional records:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 serve
```

1. Open **Evidence**. Set your project name and paste the original observation.
2. Open **Memories → New memory**. Link `source_ids` to the saved source ID.
3. Write a scoped claim, its conditions and expected effect. Keep tentative
   generalizations as `candidate`. Review the evidence before confirming them.
4. Test with a task that should activate the memory, one where it should stay quiet,
   and one where its validity is unknown.

You can also copy and adapt `examples/import.json`:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 import examples/import.json
```

Sources are committed individually; the memory batch is atomic. If the memory batch
fails validation, already-imported immutable sources remain available. Importing an
existing memory requires `expected_version`; it never silently replaces it.

## Memory schema and semantics

```json
{
  "id": "lesson-retry-1",
  "project": "my-project",
  "kind": "lesson",
  "status": "confirmed",
  "title": "Preserve operation identity",
  "claim": "Repeated delivery needs duplicate prevention.",
  "rationale": "The earlier request was delivered twice.",
  "expected_effect": "Inspect duplicate prevention before adding retries.",
  "source_ids": ["existing-source-id"],
  "valid_from": "2026-09-12",
  "applies": {"actions_any": ["retry", "sync"]},
  "unless": {"idempotency_verified": true},
  "need_tags": ["duplicate_prevention"],
  "depends_on": ["existing-incident-memory-id"]
}
```

- `kind`: `fact`, `constraint`, `decision`, `event`, or `lesson`.
- `status`: `candidate`, `confirmed`, or `retracted`. Confirmation records a review
  decision; it does not prove a claim true.
- `applies.actions_any`: at least one action must match. An empty list imposes no
  action restriction; it does not force retrieval on every task.
- `applies.state_equals`: all exact state predicates must hold. Missing/null state
  produces a conditional result, not a guessed value.
- `unless`: a conjunction of exception predicates. If all hold, exclude the memory.
  If an exception could apply but its state is unknown, mark the memory conditional.
- `assumptions`: unknown or changed assumptions cause reconsideration rather than
  deleting the historical decision.
- `need_tags`: declared information needs supported by the record. Coverage is a
  metadata check, not semantic entailment or a guarantee of completeness.
- `assertions`: scalar claims such as `{"retention_days": 30}`. Different active
  values for the same assertion key flag a conflict for relevant declared needs.
  This will not discover contradictions expressed only in prose.
- `depends_on`: other memory records that must accompany this record in the targeted
  arm. Missing, retracted or expired support blocks the dependent lesson. Dependencies
  are evidence, so their own action activation need not match the new task.
- `supersedes`: retires earlier memory IDs from the replacement's effective date.
  Retirement is transitive and persists after replacement expiry/retraction, avoiding
  accidental resurrection. Create a new explicit replacement to restore an old rule.
- `valid_from` is inclusive; optional `valid_until` is exclusive. `as_of` queries use
  effective dates and the latest stored revisions. They do **not** reconstruct what
  was known at an earlier ingestion time; this is not full bitemporal storage.
- `quote`, when present, must occur verbatim in a linked source. Matching an excerpt
  verifies provenance, not that the lesson's generalization follows from it.
- Every revision stores a new version. Pass `expected_version` to update existing IDs.
  IDs cannot move projects. Dependency and supersession cycles are rejected.

Budget units are `ceil(UTF-8 bytes / 4)`, not provider token counts. The complete
emitted context, including gaps and warnings, must fit. Evidence bundles are withheld
instead of being partially truncated. A very large task/header can require increasing
the budget. The selector is a transparent greedy heuristic, not a learned optimizer.

## Initialize a ticket knowledge base once

The `context-lab` skill in `skills/context-lab` guides setup through the agent's
question picker (or a conversational question). Select the skill and write
`/initiate`; in Codex CLI you can mention it as `$context-lab /initiate`.
The skill asks for the project/ticket identity if missing, checks saved setup,
then asks for an existing notes folder or an empty knowledge base only when needed.

The same operation is available through `memory_initiate` or the CLI:

```bash
# Check first; returns needs_knowledge_base or already_initialized.
python3 -m context_lab initiate --project my-project --ticket PROJ-123
# Import the selected folder once, including subfolders.
python3 -m context_lab initiate --project my-project --ticket PROJ-123 --path '/path/to/Obsidian/ticket notes'
# Alternatively, record an empty setup.
python3 -m context_lab initiate --project my-project --ticket PROJ-456 --empty
# Rescan only when explicitly requested; reuses the saved folder.
python3 -m context_lab initiate --project my-project --ticket PROJ-123 --refresh
```

Setup is stored in SQLite under the exact project/ticket pair, so it survives
agent conversations and restarts. Ordinary initiation never rescans an existing
setup, even if a path is supplied again. A failed import does not mark setup
complete; a failed refresh preserves the previous searchable snapshot.

The importer reads UTF-8 `.md`, `.markdown` and `.txt` files without modifying
them. It splits notes at Markdown headings and into bounded excerpts, assigns
keyword categories from paths/headings, and uses the existing BM25 retriever.
Hidden files, symlinks and unsupported formats are skipped; links are not followed.
Limits are 2 MB per note, 20 MB total and 20,000 excerpts per selected folder.
Categorization is a local heuristic, not model-based semantic classification.

Imported excerpts are `document` references with `indexed` status, kept separately
from reviewed memories. They can be retrieved immediately, but are not confirmed
facts or lessons and do not count as declared-need coverage. Original evidence
snapshots remain available after refresh. Learned lessons still require review.

Pass `ticket` alongside `project` in context tasks, source reads, observations and
proposed memories. All retrieval strategies filter by this exact scope before
ranking, conflict checks and dependency traversal. Omitting `ticket` means
project-only records, **not all tickets**; project-only material is not implicitly
shared into a ticket. Choose only the folder you want available for that ticket.
The UI's saved-knowledge-base selector fills both fields for context previews.
Use a separate agent conversation per ticket to avoid carrying old chat messages.

For complete recovery, retain the SQLite database: it includes setup and searchable
snapshots. JSON export includes those snapshots for inspection, but JSON import
supports sources/memories only and rejects exports containing knowledge bases.

## Connect an agent

**CLI:** have an agent write a task object, then invoke:

```bash
python3 -m context_lab context --task examples/agent-task.json --text
```

Without `--text`, the response includes selection traces, need statuses and a `run_id`.
Supply project state from inspected evidence. Do not infer “false” from a missing key.
For arbitrary domains, provide your own `actions` and `needs`, or extend the catalog.

**MCP / Cursor agents:** `.cursor/mcp.json` starts the stdio server on
`workspace/memory.sqlite3`. An empty database is seeded with the demo corpus on first
launch; existing records are never replaced. Enable the `context-lab` MCP server in
Cursor if prompted. Rule `.cursor/rules/context-lab-memory.mdc` and skill
`.cursor/skills/context-lab-memory` tell agents when to recall, observe, propose, and
give feedback.

For other clients, copy `examples/mcp-config.json` and keep the `${workspaceFolder}`
paths or substitute absolutes. The launcher works without a client-specific working
directory.

Exposed tools:

| Tool | Purpose |
|---|---|
| `memory_initiate` | Check, initialize once, or explicitly refresh a project/ticket knowledge base |
| `memory_catalog` | Discover the starter action/need vocabulary |
| `memory_context` | Build a compact, targeted packet and save a run snapshot |
| `memory_source` | Read original evidence within a project |
| `memory_observe` | Append an observation source |
| `memory_propose` | Add candidate memories for review |
| `memory_feedback` | Report usefulness or a failure and record its likely stage |

Suggested agent instruction:

> Before a decision that depends on project history, call memory_context with the
> next action, known current state and project. Read linked sources when a condition
> or rationale matters. Resolve consequential gaps. After an observed outcome, record
> the evidence. Propose generalizations as candidates and report missed context using
> the run_id. A previous agent's suggestion is not a confirmed project change.

The MVP exposes a minimal newline-delimited stdio MCP tool subset, based on the
[2025-11-25 tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
It does not implement Streamable HTTP, roots, resources, sampling or task extensions.
Requests were checked with a local JSON-RPC client; compatibility with your particular
agent client still needs testing.

## Optional models

Use an endpoint accepting chat-completions-style JSON and, optionally, embeddings.
No model account or network request is needed for the default demo. Set variables in
your own terminal; do not put credentials in source files or the browser UI.

```bash
export CONTEXT_LAB_BASE_URL=http://127.0.0.1:8000/v1
export CONTEXT_LAB_MODEL=your-chat-model
export CONTEXT_LAB_EMBEDDING_MODEL=your-embedding-model
# If the endpoint requires authentication, set CONTEXT_LAB_API_KEY privately.
python3 start.py
```

Model controls become available in the UI when configured. The selected source text,
tasks and/or indexed memory text are sent to the configured endpoint when those
features are invoked. The adapter expects `choices[0].message.content` containing
JSON and `data[].embedding` with ordered indexes. Provider-specific APIs may need a
small adapter. Live model behavior was not evaluated in the delivered default run;
adapter validation and caching were checked with mocks.

```bash
python3 -m context_lab draft --source src-incident
python3 -m context_lab context --task examples/task.json --model-planner --embeddings
```

Drafting returns candidate JSON without saving it. Exact source excerpts are checked;
review the scope, assumptions and exceptions before importing. No reward learning,
automatic promotion or background distillation scheduler is running.

## Run and extend evaluation

```bash
python3 -m context_lab demo
python3 -m context_lab benchmark --budget 1200
python3 -m unittest discover -s tests -v
```

Each case in `data/scenarios.json` separates `task` from `expected`. Only the task is
sent to retrieval. Labels specify required, relevant and forbidden memory IDs, plus
expected gap statuses. A context case passes when required records are present,
forbidden records are absent, and the expected gap statuses match. Additional
irrelevant records lower precision but do not by themselves fail a case. Recall is
macro-averaged over cases with required records. Empty selections count as precision
1 only when no records are required, otherwise 0. These conventions are explicit
so small sample metrics are not mistaken for universal measures.

The supplied cases are development illustrations. For meaningful evidence, freeze a
version of the memory/rules, collect new real tasks before observing outputs, annotate
required context independently, and compare the same agent/model/tool settings across
arms. Evaluate actual outcomes and constraint violations, not just memory citations.
Keep a separate holdout and report latency/cost including extraction, model planning
and embedding cache conditions. Do not train rules on your final test cases.

For a custom suite:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 benchmark --suite my-cases.json --out results/my-results.json
```

The UI benchmark runs the bundled suite against the currently opened store. Its
fictional labels are not meaningful for a store containing only your own data.

## Persistence and feedback

Use the UI's **Export data**, or:

```bash
python3 -m context_lab export --out my-context-backup.json
```

Exports include original sources, current records, revisions and feedback. Saved
SQLite databases also retain full run snapshots. JSON import restores current records,
not the revision/run history; retain the database for a complete backup. Stop the
server before copying the database and its accompanying WAL files.

Feedback diagnoses capture, retrieval, selection/budget, applicability/validity, or
“supplied but reported missed” using the saved run. Feedback is user/agent-reported,
not a causal assessment and not an automatically accepted test label.

## Design references

This prototype combines established patterns; no research novelty or production
readiness is claimed. Design inspiration includes:

- [ReasoningBank](https://arxiv.org/abs/2509.25140): experience distillation.
- [ACE](https://arxiv.org/abs/2510.04618): incremental context updates.
- [Sufficient Context](https://arxiv.org/abs/2411.06037): distinguish relevance from sufficiency.
- [TriggerBench](https://arxiv.org/abs/2606.23459): remembering latent constraints at the right time.

Our first experiment is intentionally smaller: does explicit applicability and
dependency-preserving context assembly improve selection on a task with known needs?
