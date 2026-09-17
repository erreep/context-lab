# Context Lab

Context Lab is a local memory lab for testing when a memory should affect an agent's next decision. You run it on your machine, store evidence and scoped memories in SQLite, and build task-targeted context packets at recall time. It is an experiment, not a hosted service and not production-ready.

You get two surfaces that share one database:

- **Agent integration (primary).** A stdio MCP server with tools for setup, recall, evidence, journalling, promotion, scope, and feedback.
- **Review inbox (human).** Localhost UI to confirm waiting candidates in the Memories column. **Lab rules** opens lab-wide writes. **Lab → Agent preview** toggles the agent preview column.

The runtime uses the Python standard library only. Python 3.11+ is required. There is no PyPI release yet; install directly from GitHub with `pipx` or run a clone.

## Why Context Lab

Long-lived agent memory has two separate problems: preserving information, and selecting the right information for the next decision. Context Lab focuses on the second problem.

Evidence and reviewed memories live in SQLite. Recall layers them by scope and builds a compact packet for a specific task. Proposed lessons do not become active just because an agent wrote them. A human confirms candidates in the workbench first.

## Install

```bash
pipx install git+https://github.com/erreep/context-lab.git
context-lab demo
context-lab serve
```

Open **http://127.0.0.1:8765**. Confirm waiting candidates in the browser. That is the human loop. The default database is `~/.context-lab/memory.sqlite3`; override it with `--db PATH` or `CONTEXT_LAB_DB`. To update a GitHub installation, run `pipx upgrade context-lab`.

For development from a clone:

```bash
git clone https://github.com/erreep/context-lab
cd context-lab
python3 -m pip install -e .
context-lab demo
context-lab serve
```

On Windows, use `python` if that is your Python command. Open **http://127.0.0.1:8765**. Press Ctrl+C to stop. For a busy port: `context-lab serve --port 8766`.

`serve` binds localhost only. `demo` seeds an empty database once with a fictional corpus. Existing records are never overwritten.

For a clean store without demo data:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 serve
```

## How it works

1. You choose a project and, when relevant, an exact ticket.
2. An agent recalls context for the decision it is about to make.
3. The agent can read linked evidence, record observations, and propose candidate memories.
4. You review candidates in the local workbench.
5. Confirmed memories can affect later targeted recall in the same scope.
6. The agent can report feedback against the saved recall run.

Recall layers scopes in this order:

```text
__global__ lab rules → project baseline → exact ticket
```

Sibling tickets stay isolated. Omitting `ticket` means project baseline, not every ticket. Lab-wide writes are rare and require explicit user approval plus `confirm_global=true`.

## Agent integration

MCP is the primary integration path. After installation, point your agent client at the canonical stdio command:

```json
{
  "mcpServers": {
    "context-lab": {
      "command": "context-lab",
      "args": ["mcp"]
    }
  }
}
```

Copy `examples/mcp-config.json` into your client config (or paste the JSON above). Enable the `context-lab` MCP server when prompted. Workflow guidance for agents lives in `plugins/context-lab/skills/context-lab/`.

### Codex plugin

The repo marketplace bundles the Context Lab workflow and MCP command. Install the Python executable first, then add and install the plugin:

```bash
pipx install git+https://github.com/erreep/context-lab.git
codex plugin marketplace add erreep/context-lab
codex plugin add context-lab@context-lab
```

The plugin intentionally calls `context-lab mcp`; it does not hide or duplicate the local Python prerequisite. Installing the package or plugin does **not** enable hooks.

Hooks are opt-in per repository. Prefer the one-shot local installer:

```bash
cd /path/to/project
context-lab install --client codex --project my-project --ticket PROJ-123
```

That binds scope, writes MCP config, writes client hooks, and installs the git commit lease. Use `--client claude` or `--client cursor` for those hosts. To bind the project baseline without a ticket, omit `--ticket` or pass `--ticket ""`. Rebind later with `context-lab hook set-scope --project P --ticket T`.

| Client | Ambient context | What install writes |
|---|---|---|
| `claude` | `SessionStart` standing CompactView; `UserPromptSubmit` identity only | `.mcp.json`, `.claude/settings.json`, git lease |
| `codex` | `SessionStart` standing CompactView; `UserPromptSubmit` identity only (needs `[features] codex_hooks = true`) | `.codex/config.toml`, `.codex/hooks.json`, git lease |
| `cursor` | **No** — Cursor has no prompt injection | `.cursor/mcp.json`, `.cursor/hooks.json` (git gate only), `.cursor/rules/context-lab-memory.mdc`, git lease |

Scope alone never enables hooks. `install` is what enables them. Advanced/manual path: `hook set-scope`, `hook install-git`, and `hook print-config` still work. This repository does not ship active client hook files.

Machine-wide MCP (tools available in every project, **no** ambient inject, **no** git lease):

```bash
context-lab install --global --client claude   # ~/.claude.json mcpServers
context-lab install --global --client codex    # ~/.codex/config.toml
context-lab install --global --client cursor   # ~/.cursor/mcp.json (+ soft-contract rules)
```

`--global` only wires MCP (and Cursor’s soft-contract rules). The `SessionStart` CompactView, prompt identity signal, and commit lease still require a per-repo `install --client … --project … --ticket …`.


### Agent contract

Runtime source: MCP `initialize.instructions` (`GATE_TEXT`).

1. Before `git commit`, run `context-lab hook recall-for --purpose commit`.
2. Soft clients call `memory_context(cwd, task, since=held_place)` after scope bind and before history-dependent work. Ambient clients receive standing context only at `SessionStart`.
3. Candidates stay inert until confirmed in the workbench. Propose one sharp ticket-scoped `title`+`claim`+`source_ids` per outcome. Unrelated later-fixes use `memory_park`, not propose/journal/baseline.
4. Lab-wide writes need explicit user approval and `confirm_global=true`.

Setup is not recall.

### Suggested agent loop

1. **Scope** once (`memory_initiate` / `memory_allocate_ticket` as needed).
2. **Recall** — soft clients call `memory_context(cwd, task, since=held_place)` after bind and before history-dependent work. Ambient clients receive standing context at `SessionStart` and call `memory_context` for focused queries.
3. **Read evidence** when a condition matters (`memory_source`).
4. **Record** observations (`memory_observe`); propose one sharp ticket-scoped candidate (`memory_propose`: title+claim+source_ids).
5. **Commit** — `context-lab hook recall-for --purpose commit` before `git commit`.
6. **Feedback** optional via `run_id` (`memory_feedback`).

Suggested instruction block for agent prompts:

> Soft clients: call memory_context with cwd, task, and the optional last place after scope bind and before history-dependent decisions. Ambient clients receive standing context at SessionStart only. Before git commit, run recall-for. After outcomes, observe evidence and propose one durable ticket-scoped candidate. Candidates stay inert until UI confirm. A prior agent suggestion is not a confirmed project change.


### MCP tools

| Tool | Purpose |
|---|---|
| `memory_initiate` | Check, initialize once, or explicitly refresh a project/ticket knowledge base |
| `memory_allocate_ticket` | Mint a work-unit ticket id (`work-YYYYMMDD-HHMMSS` UTC) when the user has notes but no ticket yet |
| `memory_catalog` | List starter action and need vocabulary for task features |
| `memory_context` | Build a targeted context packet from layered scopes; saves a run snapshot |
| `memory_inspect_run` | Load the detailed trace for a saved recall run |
| `memory_source` | Read immutable evidence by ID within scope |
| `memory_observe` | Append an observation source (evidence only) |
| `memory_propose` | Store structured candidate memories for human review |
| `memory_feedback` | Report helpful/missed/irrelevant/stale and diagnose pipeline stage |
| `memory_promote` | Promote a confirmed ticket memory to a project-baseline candidate |
| `memory_journal` | Write and immediately index a durable ticket journal entry |
| `memory_park` | Park an unrelated later-fix for human triage; excluded from recall and the confirm inbox |

### Agent recall vs evaluation

Agents call `memory_context`. That always runs the **targeted** selector: retriever plus action/need activation, applicability checks, dependency bundles, need coverage, and budget selection.

The offline benchmark compares three bundled arms on the same records and task features:

| Arm | What differs |
|---|---|
| A: retrieval | Facts, events, decisions, constraints; BM25 (optionally hybrid with real embeddings) |
| B: lessons | Same as A plus confirmed lessons |
| C: targeted | Same records as B plus activation, applicability, dependencies, and need-aware selection |

These arms are evaluation comparisons. MCP does not expose a strategy picker. The arms share project isolation, date filtering, candidate exclusion, supersession, formatting, and budget policy. The comparison does not isolate each mechanism causally.

The default retriever is lexical BM25, not hybrid semantic search. Enabling a real embedding endpoint makes retrieval hybrid via reciprocal rank fusion. There are no fake or hash-based "semantic embeddings."

### CLI fallback

Without MCP, write a task JSON and invoke:

```bash
python3 -m context_lab context --task examples/agent-task.json --text
```

Without `--text`, the response includes selection traces, need statuses, and a `run_id`. Supply project state from inspected evidence. Do not infer `false` from a missing key. For arbitrary domains, provide your own `actions` and `needs`, or extend the catalog.

### MCP transport

The server implements a minimal newline-delimited stdio JSON-RPC tool subset based on the [2025-11-25 MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools). It does not implement Streamable HTTP, roots, resources, sampling, or task extensions. Test compatibility with your particular client.

## Human UI

The browser opens as a review inbox for the current workspace and work item. The header uses **Workspace** and **Work item**; MCP and CLI still use `--project` and `--ticket`.

The **Memories** column lists waiting, confirmed, and retracted items for the selected workspace. **Lab → Agent preview** toggles the agent preview column only. Lab-wide rules live under the **Lab rules** button, not in the inbox column.

### Layer stack

Browse waiting and settled items in the Memories column. The stack shows Workspace and Work item layers for the current selection. Sibling work items never mix. Use **Filter** above the stack to narrow the list.

### Review inbox

Confirm waiting items on the review desk. That is the default human surface. Open **Lab → Agent preview** when you need agent preview, task JSON fields, or compare methods.

The desk is a human review surface, not CompactView: title and claim first, technical fields under an advanced section. Evidence opens in place on the card. Confirmation records a review decision. It does not prove a claim true. Only confirmed records enter targeted recall (plus indexed document excerpts as reference text).

Keyboard: `j`/`k` move, `a` confirm, `x` retract. Prefer one sharp ticket-scoped candidate per observed outcome; title and claim should stand alone without JSON chrome.

### Later (parking lot)

While bound to a ticket, agents can park unrelated fixes with `memory_park` (project-only; no ticket argument). Parked bodies never enter recall or the confirm inbox. The workbench header shows **Later (N)** beside the waiting badge; open it to start a parked item on a new or existing ticket (which writes one unconfirmed candidate there) or dismiss it. Human triage also works through `context-lab parking list|show|start|dismiss`.

### Agent preview

Hidden until you open **Lab → Agent preview**. Choose workspace, work item, and task features, then preview the context packet an agent would receive. Compare methods shows the three evaluation arms side by side for debugging. It is not the primary agent path.

### Lab menu

**Lab rules** is the sole surface for lab-wide candidates and writes (lab-wide requires explicit user approval). The **Lab** menu holds Agent preview, the bundled benchmark runner, and export. Use a separate agent conversation per work item to avoid carrying old chat context.

## Memory model

Context Lab separates immutable evidence (sources) from scoped memory records (facts, constraints, decisions, events, lessons). Recall is selection at decision time, not a full dump of everything ever written.

### Scopes and layering

1. **Lab-wide** (`project=__global__`, empty ticket): rare standing rules. Agents must ask the user and pass `confirm_global=true` before writing them.
2. **Project baseline** (project set, empty ticket): durable project knowledge.
3. **Exact ticket** (project + ticket id): work-unit-specific notes and memories.

Omitting `ticket` means project baseline only (plus lab-wide), not all tickets. Pass `ticket` alongside `project` in context tasks, source reads, observations, and proposed memories.

### Candidate gate

| Status | Retrieval behavior |
|---|---|
| `candidate` | Visible in workbench; excluded from agent recall |
| `confirmed` | Eligible for targeted selection (subject to applicability) |
| `retracted` | Excluded |

Imported ticket-note excerpts are indexed documents, not confirmed facts or lessons. They can be retrieved as references but do not count as declared-need coverage.

### Retrieval and non-guarantees

- BM25 by default. Hybrid mode requires a configured embedding endpoint returning real vectors.
- Need coverage checks metadata tags, not semantic entailment or completeness.
- Applicability uses declared actions, state predicates, and exceptions. Unknown state yields conditional results, not guessed values.
- Conflicts flag contradictory assertion keys for relevant needs. Prose-only contradictions are not detected.
- Budget uses `ceil(UTF-8 bytes / 4)` units, not provider token counts. The selector is a transparent greedy heuristic, not a learned optimizer. Evidence bundles are withheld rather than partially truncated.

### Memory record shape

```json
{
  "id": "lesson-retry-1",
  "project": "my-project",
  "ticket": "PROJ-123",
  "kind": "lesson",
  "status": "candidate",
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

Field notes:

- **`kind`:** `fact`, `constraint`, `decision`, `event`, or `lesson`.
- **`status`:** `candidate`, `confirmed`, or `retracted`.
- **`applies.actions_any`:** at least one action must match; an empty list imposes no action restriction.
- **`applies.state_equals`:** all predicates must hold; missing state is conditional, not false.
- **`unless`:** conjunction of exception predicates; unknown exception state marks the memory conditional.
- **`need_tags`:** declared information needs this record supports.
- **`depends_on`:** companion records required in targeted recall; missing or retracted support blocks the dependent lesson.
- **`supersedes`:** retires earlier IDs from the replacement's effective date (transitive).
- **`valid_from` / `valid_until`:** inclusive start, exclusive end. Date queries use effective dates and latest revisions. This is not full bitemporal storage.
- **`quote`:** when present, must occur verbatim in a linked source (provenance check, not proof of generalization).
- Updates require `expected_version`. IDs cannot change projects.

### Add your own observations

1. Save evidence in the layer stack or via MCP `memory_observe`.
2. Propose scoped memories linked to `source_ids` (`memory_propose` or the workbench), keeping tentative claims as `candidate`.
3. Confirm accurate candidates on the review desk.
4. Test recall with tasks that should activate the memory, stay quiet, or mark validity unknown.

Bulk import:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 import examples/import.json
```

Sources commit individually; the memory batch is atomic. Importing an existing memory requires `expected_version`.

## Ticket knowledge base

Ticket setup is vault-first and agent-driven. There is no UI initiate/import. Use MCP or CLI.

### Initiate once per project/ticket

On first touch, Context Lab tries to auto-detect an Obsidian vault from local Obsidian config (and shallow common folders). If found, it binds the vault and may set `obsidian.announce`. If none is found, `memory_initiate` returns `needs_obsidian_vault` until you pass `knowledge.vault` as a folder path or `"none"`. `obsidian.journaling` is `ready`, `vault_only`, or `unavailable`. `mode=auto` or `empty` with a bound vault and a ticket creates `{vault}/Context Lab/{project}/{ticket}/`. An imported ticket folder stays `ready` even when the vault was declined.

Prefer `knowledge={ "mode": "auto" }` (or `import`, `empty`, `reuse`) over ad-hoc flags.

If you want ticket-scoped notes but have no ticket yet, call `memory_allocate_ticket` (or `python3 -m context_lab allocate-ticket`) to mint `work-YYYYMMDD-HHMMSS` UTC, then initiate with that ticket id.

CLI equivalents:

```bash
# First project touch: pass --vault PATH or --no-vault if auto-detect is not enough.
python3 -m context_lab initiate --project my-project --ticket PROJ-123

# Import a notes folder once (includes subfolders).
python3 -m context_lab initiate --project my-project --ticket PROJ-123 --path '/path/to/ticket-notes'

# Record an empty setup.
python3 -m context_lab initiate --project my-project --ticket PROJ-456 --empty

# Rescan only when explicitly requested.
python3 -m context_lab initiate --project my-project --ticket PROJ-123 --refresh
```

Setup is stored in SQLite under the exact project/ticket pair. Ordinary initiation never rescans an existing setup, even if a path is supplied again. A failed import does not mark setup complete. A failed refresh preserves the previous searchable snapshot.

### Importer behavior

Reads UTF-8 `.md`, `.markdown`, and `.txt` without modifying source files. Splits notes at Markdown headings into bounded excerpts, assigns keyword categories from paths and headings, and indexes with the same BM25 retriever. Skips hidden files, symlinks, and unsupported formats. Does not follow links. `memory_journal` writes durable plan, decision, progress, and handoff notes under the ticket folder and indexes them immediately.

Limits: 2 MB per note, 20 MB total, 20,000 excerpts per selected folder. Categorization is a local heuristic, not model-based classification.

For complete recovery, retain the SQLite database. JSON export includes snapshots for inspection, but JSON import supports sources and memories only and rejects exports containing knowledge bases.

## What is implemented vs scaffold

**Implemented**

- Persistence, immutable evidence, revision history, optimistic concurrency
- Project and date filtering, supersession, conditional activation, exceptions
- Dependency bundles, conflict reporting, estimated-token budgeting
- Run snapshots and feedback diagnosis
- Targeted context assembly via MCP and CLI
- Vault-aware initiate and ticket note import

**Implemented, optional**

- Model-assisted task planning and drafting candidate lessons from a source
- Exact-excerpt validation and cached model embeddings when an endpoint is configured

**Scaffold**

- Demo corpus (20 memories, 28 scenarios) and default task patterns in `context_lab/data/task_rules.json`
- Bundled benchmark cases in `context_lab/data/scenarios.json`

**Not implemented**

- Automatic verified learning, nightly scheduler, or background distillation
- Live agent action execution or learned utility ranking
- Universal semantic conflict detection or proof of context sufficiency
- UI-side initiate/import, MCP strategy selection, or a published PyPI release

A prompted draft is not a verified lesson. The bundled benchmark is a context-selection experiment. It does not claim improvement in real agent task completion.

## Optional models

Configure a chat-completions-style HTTP endpoint and, optionally, embeddings. No model account is required for the default demo.

```bash
export CONTEXT_LAB_BASE_URL=http://127.0.0.1:8000/v1
export CONTEXT_LAB_MODEL=your-chat-model
export CONTEXT_LAB_EMBEDDING_MODEL=your-embedding-model
# If the endpoint requires authentication:
export CONTEXT_LAB_API_KEY=your-key
context-lab serve
```

Set variables in your own terminal. Do not put credentials in source files or the browser UI. Model controls appear in the UI when configured. The adapter expects `choices[0].message.content` JSON and ordered `data[].embedding` vectors.

```bash
python3 -m context_lab draft --source src-incident
python3 -m context_lab context --task examples/task.json --model-planner --embeddings
```

Drafting returns candidate JSON without saving it. Use the UI "Draft lessons" control or `memory_propose` to save reviewed candidates. No reward learning, automatic promotion, or background scheduler runs.

## Evaluation

The bundled benchmark compares the three arms on authored scenarios. Use it to inspect selection behavior, not as proof your agent improved.

```bash
python3 -m context_lab demo
python3 -m context_lab benchmark --budget 1200
python3 -m unittest discover -s tests -v
```

Custom suite:

```bash
python3 -m context_lab --db workspace/my-memory.sqlite3 benchmark \
  --suite my-cases.json --out results/my-results.json
```

Each case separates `task` from `expected` labels. Metrics follow explicit conventions in the codebase so small samples are not mistaken for universal measures. Benchmark output defaults to `results/` under the current directory (gitignored). Pass `--out` to choose another path.

The UI benchmark runs the bundled suite against the open store. Fictional labels are not meaningful for a store containing only your own data.

## Local token-usage estimates

MCP and hook processes automatically record payload estimates in their existing SQLite database. No extra model calls, dependencies, or fields in tool responses are added. The meter stores counts and scope metadata, not request/response bodies.

```bash
context-lab usage --project context-lab --ticket 768
context-lab usage --project context-lab    # all this project's tickets + baseline
context-lab usage --json                  # all projects, including unassigned traffic
```

Use the same database as your MCP/worktree scope. For an existing checkout database:

```bash
python3 -m context_lab --db workspace/memory.sqlite3 usage --project context-lab --ticket 768
```

Counts use **ceil(UTF-8 bytes / 4)** separately for each request and response, not actual tokenizer counts or billed usage. MCP requests include the tool name and arguments; responses include returned text, including tool errors. Hooks count `SessionStart` standing context and the printed `recall-for` context. The `UserPromptSubmit` identity signal does not open a memory store or record usage. Initialization instructions and tool definitions are tracked separately as unassigned setup traffic. Unknown tools and malformed transport messages are excluded.

Reports break down event counts and request/response estimates by project, ticket, channel, and operation. `--ticket ""` selects only the project's baseline; `--ticket` requires `--project`. Calls with only a run/memory ID use that record's scope. Mixed-scope batches and calls without a known scope stay unassigned, visible only in the unfiltered report. Injections are charged to the active ticket, even when recalling baseline rules.

Restart/reconnect existing MCP processes after updating. Hook injections also require a configured worktree scope: `context-lab hook set-scope --project P --ticket T --db /path/to/memory.sqlite3`. Counting starts with updated processes; old traffic is not backfilled. Compare before/after report totals to measure a task slice. These are cumulative local payload estimates, **not net token savings or cost**: they exclude protocol framing, skill loading, client history replay, cache effects, direct CLI/UI operations (except `hook` output), and any model's own usage. Reading a report does not add meter events. Preserve the SQLite database to retain counters; JSON memory exports do not include them. A failed counter write warns on stderr without failing an otherwise completed tool call.

## CLI reference

| Command | Purpose |
|---|---|
| `context-lab demo` | Seed the synthetic demo corpus without replacing existing memories |
| `context-lab serve [--port] [--db]` | Serve the workbench UI |
| `context-lab mcp` | Start the stdio MCP server |
| `context-lab initiate …` | Ticket knowledge-base setup (`--vault` / `--no-vault`, `--path`, `--empty`, `--refresh`) |
| `context-lab allocate-ticket` | Mint a generated ticket id |
| `context-lab context --task … [--strategy …] [--budget …] [--text] [--embeddings] [--model-planner]` | Build a context packet |
| `context-lab import …` / `export …` | JSON batch IO |
| `context-lab draft --source …` | Model-assisted candidate draft |
| `context-lab benchmark …` | Run the comparison suite |
| `context-lab usage [--project …] [--ticket …] [--json]` | Report locally estimated MCP and hook token traffic |
| `context-lab scope bind\|unbind\|show\|list` | Bind the current git branch to a project/ticket |
| `context-lab hook …` | Opt-in harness hooks (set-scope, recall-for, git gate) |

`python3 -m context_lab …` is equivalent after `pip install -e .` or a package install.

## Persistence and backup

Export from the Lab menu or:

```bash
python3 -m context_lab export --out my-context-backup.json
```

Exports include sources, current records, revisions, and feedback. SQLite also retains full run snapshots and ticket setup. JSON import restores current records, not revision or run history. Retain the database for a complete backup. Stop the server before copying the database and WAL files.

Feedback diagnoses capture, retrieval, selection/budget, applicability/validity, or "supplied but reported missed" using the saved run. Feedback is user- or agent-reported, not verified ground truth.

## Project status

This repository can be installed from source through its `pyproject.toml`, but it has no published PyPI release or `LICENSE` file yet. Review the license gap before redistributing or depending on it as a library.

## Design references

This prototype combines established patterns. No research novelty is claimed. Inspiration includes [ReasoningBank](https://arxiv.org/abs/2509.25140), [ACE](https://arxiv.org/abs/2510.04618), [Sufficient Context](https://arxiv.org/abs/2411.06037), and [TriggerBench](https://arxiv.org/abs/2606.23459). The first experiment asks a narrower question: does explicit applicability and dependency-preserving context assembly improve selection on tasks with known needs?
