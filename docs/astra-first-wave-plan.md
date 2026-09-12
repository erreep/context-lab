# Astra first-wave plan

Context Lab agents get budget-honest compact recall, explicit standing rules, and provenance-preserving promotion. The program enforces Astra priority order for coding agents and maintainers. PR ids in order are astra-p1, astra-p2, astra-p3, astra-p4, astra-p5.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator reviews and lands every PR. Owners stop at STACK-READY.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on her explicit go.
- [ ] On her go, arm a `/goal` with this exact text. "Run docs/astra-first-wave-plan.md through astra-p1 then astra-p2 then astra-p3 then astra-p4 then astra-p5 under autopilot-stack. A PR is verified only when its unit, live, and perf boxes are all checked. The operator merges. Done when Close the program is checked."
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/how/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/architect/SKILL.md`
- [ ] Arm the 30-minute audit tick. In a local session, a real terminal `/loop`. In a cloud root, a cloud-sleeper wake chain. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk and the armed /goal. Audit the operation against both and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then send the operator a status message, whether or not anything changed, with the queue table of PR, owner, state, and head SHA, the verdicts since the last tick, what merged, open operator gates, and blockers."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per PR with the full lifecycle the execution playbook names.
- [ ] Follow this dependency graph. Start dependent work only after its parent merges, or base it on the parent branch when the execution playbook stacks.
  - [ ] astra-p1 first from `main`.
  - [ ] astra-p2 after astra-p1.
  - [ ] astra-p3 after astra-p2.
  - [ ] astra-p4 after astra-p3.
  - [ ] astra-p5 after astra-p4.
- [ ] Hold the file boundaries. astra-p1 touches schemas, engine packing, mcp, agent_api, and tests for wire budget. astra-p2 touches engine selection and standing_rule validation. astra-p3 touches store promotion and workbench promote UI. astra-p4 touches provider, service, mcp wiring, and allocate_ticket. astra-p5 touches knowledge import and provider vocabulary.
- [ ] Hold the review gate. astra-p1 and astra-p3 change an agent or workbench interaction. They wait for the operator's review in chat with screenshots and a video before merge.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`; if `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every PR operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the PR ready, never draft, with `origin pr create --status open --base <base-branch>` or `gh pr create --base <base-branch>` according to the resolved forge. A stack child targets its parent branch.
- [ ] Run the repo's lint and typecheck once before the PR-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review.
- [ ] Triage every Bugbot and security-reviewer comment per `../references/bugbot-triage.md`.
- [ ] Rebase onto current trunk before babysit and again before the merge-ready report.

### Verdict and merge, for every PR

- [ ] At the merge-ready head SHA, run the swarm per `pstack/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the PR's **Verify, live** block. The perf lane from its **Verify, perf** block. One audit lane that reads the diff and the receipts and distrusts the PR body.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner. A new head gets a fresh swarm and a fresh verdict.
- [ ] Root appends the PR to the base-branch stack on a clean verdict. The operator lands the contiguous verified run bottom-up. Preserve patch-id rules from `playbooks/shipping.md`.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the PR head. Drive through `control-cli` from `cursor-team-kit` for MCP and CLI, and `control-ui` for the workbench where the PR touches it.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Start the local Context Lab DB seed if needed. Start the workbench with `python3 -m context_lab serve` when the PR needs UI. Wait for ready.
- [ ] Deliver input only through the control skill's commands. Name the read-only diagnostics.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Budget the full agent payload (astra-p1)

**Depends on.** None.

**Files.**

- [ ] Edit `context_lab/schemas.py`.
- [ ] Edit `context_lab/engine.py`.
- [ ] Edit `context_lab/mcp.py`.
- [ ] Edit `context_lab/agent_api.py`.
- [ ] Edit `context_lab/store.py` only if run persistence needs CompactView fields.
- [ ] Create `tests/test_wire_budget.py`.

**Build.**

- [ ] Add CompactView as the default MCP projection and measure the complete serialized tool result against budget. Keep full CompileResult on saved runs for TraceView. Drop excluded titles from the default agent payload.

**You see.**

- [ ] A memory_context call with budget 1200 returns a CompactView whose measured size is at or under budget, and TraceView loaded by run_id still shows exclusions.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `tests/test_wire_budget.py` covers oversize full packet vs compact default. Run `python3 -m pytest tests/test_wire_budget.py tests/test_http.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the Astra 1200-budget recall at trunk and head. If trunk lacks the feature, record that and gate measured CompactView size plus the end state the user waits for. Save `p1-regression.png`. Pass when head CompactView is at or under budget and trunk overshoot is documented.
- [ ] Lane 2. Default MCP detail omits excluded candidate titles. Save `p1-no-excluded.png`. Pass when the agent JSON has no excluded title strings from the seeded corpus.
- [ ] Lane 3. Inspect or trace by run_id still returns exclusions. Save `p1-trace.png`. Pass when TraceView lists a budget_excluded or excluded stage.
- [ ] Lane 4. Prose-only mode still works for callers that request it. Save `p1-prose.png`. Pass when prose keys alone are returned and stay under budget.
- [ ] Lane 5. Dependency gaps and needs remain on CompactView. Save `p1-needs.png`. Pass when a missing need appears without full memory records.
- [ ] Lane 6. Workbench recall still opens the same run. Save `p1-workbench.png`. Pass when the UI shows the run_id from MCP.
- [ ] Lane 7. Empty selection still fits the envelope. Save `p1-empty.png`. Pass when warnings render under budget.
- [ ] Lane 8. Large selected set packs greedily without overshoot. Save `p1-greedy.png`. Pass when measured size stays under budget after packing.
- [ ] Lane 9. Feedback against a saved run still diagnoses stages. Save `p1-feedback.png`. Pass when feedback returns a non-empty diagnosis.
- [ ] Lane 10. CLI context text path remains usable. Save `p1-cli.png`. Pass when `--text` prints only context prose.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measured wire bytes of the default MCP memory_context result at trunk and head for the Astra 1200-budget fixture.
- [ ] Probe. Seed the fixture, call memory_context once at trunk and once at head, interleaved, logging wire bytes from the same estimator.
- [ ] Baseline. Record the trunk wire bytes first.
- [ ] Rule. Head wire bytes must be less than or equal to the requested budget. If trunk lacks CompactView, also require head absolute wire bytes under budget and end-to-end recall under 200 ms on the seeded fixture.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 2 screenshots into `docs/media/astra-p1-review-regression.png` and `docs/media/astra-p1-review-no-excluded.png`.
- [ ] Record a 30 to 60 second video of the change on a lane VM. Save it as `docs/media/astra-p1-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] Root appends astra-p1 to the stack. The operator lands it.

## Separate standing policy from ranked evidence (astra-p2)

**Depends on.** astra-p1.

**Files.**

- [ ] Edit `context_lab/engine.py`.
- [ ] Edit `context_lab/store.py`.
- [ ] Edit `context_lab/schemas.py` for standing_rule kind.
- [ ] Create `tests/test_selection_lanes.py`.

**Build.**

- [ ] Persist standing rules as kind standing_rule at lab-wide or project baseline only. Reserve policy budget with STANDING_MAX_BYTES. Rank EvidenceLane by usefulness without layer_rank. Delete the ancestor score floor.

**You see.**

- [ ] A ticket recall keeps a standing_rule and a ticket-critical lesson while an unrelated baseline document is omitted when the budget is tight.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `tests/test_selection_lanes.py` covers standing reserve, fail-closed overflow, and cross-scope usefulness. Run `python3 -m pytest tests/test_selection_lanes.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Reproduce Astra stationery crowding at trunk and head. Save `p2-regression.png`. Pass when head keeps the ticket upload-retry constraint and trunk crowding is documented.
- [ ] Lane 2. standing_rule appears in PolicyLane. Save `p2-policy.png`. Pass when the CompactView policy list contains the tagged rule.
- [ ] Lane 3. Untagged baseline lesson competes in EvidenceLane only. Save `p2-evidence.png`. Pass when an unrelated baseline note can lose to a higher-scoring ticket lesson.
- [ ] Lane 4. Ticket-absent project recall no longer drops a baseline rule inconsistently. Save `p2-project-only.png`. Pass when the same standing_rule appears for project-only and ticket tasks.
- [ ] Lane 5. MandatoryPolicyOverflow fails closed when standing set exceeds allowance. Save `p2-overflow.png`. Pass when the tool returns a clear error and no silent drop.
- [ ] Lane 6. Bundle dependencies still admit atomically. Save `p2-bundle.png`. Pass when a depends_on chain is all-in or all-out.
- [ ] Lane 7. Candidates still never enter CompactView. Save `p2-candidate.png`. Pass when a candidate title is absent from CompactView and present in TraceView exclusions.
- [ ] Lane 8. Lab-wide standing_rule still loads for every project. Save `p2-global.png`. Pass when __global__ standing_rule is in PolicyLane.
- [ ] Lane 9. Workbench can inspect lane decisions for the run. Save `p2-trace-lanes.png`. Pass when TraceView shows policy vs evidence decisions.
- [ ] Lane 10. Existing confirmed tests still pass. Save `p2-suite.png`. Pass when `python3 -m pytest -q` exits 0.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of ticket-critical memories admitted under the Astra crowding fixture at trunk and head.
- [ ] Probe. Compile the crowding fixture at trunk and head, interleaved, logging admitted ticket memory ids.
- [ ] Baseline. Record trunk admitted ticket ids first.
- [ ] Rule. Head must admit the ticket-critical id that trunk dropped. Absolute compile latency under 50 ms on the fixture.

**Review gate.** None. astra-p2 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] Root appends astra-p2 to the stack. The operator lands it.

## Promote lessons with provenance (astra-p3)

**Depends on.** astra-p2.

**Files.**

- [ ] Edit `context_lab/store.py`.
- [ ] Edit `context_lab/service.py`.
- [ ] Edit `context_lab/static/index.html`.
- [ ] Create `tests/test_promote.py`.

**Build.**

- [ ] Add Application.promote or Store.promote that mints a project summary source, a project candidate lesson, and a ProvenanceLink with opaque origin refs. Idempotency defaults to origin memory, target scope, and claim hash.

**You see.**

- [ ] Promoting a ticket lesson yields a project candidate that cites only the summary source, and ticket raw notes stay unreachable from a project-only recall.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `tests/test_promote.py` covers success, isolation, and idempotent retry. Run `python3 -m pytest tests/test_promote.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Attempt baseline lesson citing ticket source at trunk and head. Save `p3-regression.png`. Pass when head promotion succeeds and trunk scope_covers rejection is documented.
- [ ] Lane 2. Workbench Promote action creates a candidate. Save `p3-ui.png`. Pass when pending_candidates increments on project baseline.
- [ ] Lane 3. Summary source is project-scoped. Save `p3-summary.png`. Pass when source.ticket is empty and project matches.
- [ ] Lane 4. ProvenanceLink stores opaque origin ids only. Save `p3-link.png`. Pass when no ticket body text appears in the link payload.
- [ ] Lane 5. Project recall after confirm returns the promoted lesson. Save `p3-recall.png`. Pass when the confirmed lesson appears without ticket sources.
- [ ] Lane 6. Sibling ticket still cannot read the origin ticket notes. Save `p3-isolation.png`. Pass when sibling memory_context omits origin ticket sources.
- [ ] Lane 7. Idempotent retry returns the same promotion ids. Save `p3-idempotent.png`. Pass when second promote equals first.
- [ ] Lane 8. Candidate still requires UI confirm before retrieval. Save `p3-gate.png`. Pass when unconfirmed promoted lesson is absent from CompactView.
- [ ] Lane 9. HTTP promote endpoint rejects wrong project. Save `p3-authz.png`. Pass when a mismatched project returns an error.
- [ ] Lane 10. Feedback still works on runs that used promoted lessons. Save `p3-feedback.png`. Pass when feedback accepts the run_id.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Wall time of one promote transaction at trunk (N/A) and head.
- [ ] Probe. Run promote twice at head interleaved with a no-op store read, logging milliseconds.
- [ ] Baseline. Record that trunk has no promote operation, then record head first-call latency.
- [ ] Rule. Head promote under 100 ms on a local SQLite fixture. Idempotent retry under 20 ms.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2 and lane 5 screenshots into `docs/media/astra-p3-review-ui.png` and `docs/media/astra-p3-review-recall.png`.
- [ ] Record a 30 to 60 second video of the change on a lane VM. Save it as `docs/media/astra-p3-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] Root appends astra-p3 to the stack. The operator lands it.

## Share model config and harden local-only (astra-p4)

**Depends on.** astra-p3.

**Files.**

- [ ] Edit `context_lab/provider.py`.
- [ ] Edit `context_lab/service.py`.
- [ ] Edit `context_lab/mcp.py`.
- [ ] Edit `context_lab/agent_api.py` or replace with `context_lab/app.py` per synthesis.
- [ ] Edit `context_lab/knowledge.py` for allocate_ticket suffix.
- [ ] Create `tests/test_server_config.py`.

**Build.**

- [ ] Load ServerConfig once for MCP, CLI, and HTTP. Wire planner and embeddings into the MCP compile path. Enforce local-only loopback hosts. Append a random suffix to allocate_ticket.

**You see.**

- [ ] With CONTEXT_LAB_BASE_URL and embedding model set, MCP memory_context reports a hybrid backend, and a non-loopback URL is rejected when local_only is true.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `tests/test_server_config.py` covers shared flags, local-only rejection, and ticket suffix uniqueness. Run `python3 -m pytest tests/test_server_config.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Call MCP context with embeddings env set at trunk and head. Save `p4-regression.png`. Pass when head backend is hybrid and trunk remains BM25-only as documented.
- [ ] Lane 2. CLI and MCP share the same ServerConfig. Save `p4-parity.png`. Pass when both report the same backend string for one env.
- [ ] Lane 3. local_only rejects a public HTTPS host. Save `p4-local.png`. Pass when open raises or compile refuses the host.
- [ ] Lane 4. Loopback URL is accepted under local_only. Save `p4-loopback.png`. Pass when Application.open succeeds for 127.0.0.1.
- [ ] Lane 5. Request payload cannot override local_only. Save `p4-no-override.png`. Pass when HTTP flags cannot enable a remote host.
- [ ] Lane 6. Two allocate_ticket calls in one second differ. Save `p4-ticket.png`. Pass when the two ids are unique.
- [ ] Lane 7. MCP without env stays lexical. Save `p4-lexical.png`. Pass when backend remains BM25 when env is unset.
- [ ] Lane 8. Workbench checkboxes still reflect model_available. Save `p4-ui-flags.png`. Pass when /api/info matches env.
- [ ] Lane 9. Draft endpoint still requires model config. Save `p4-draft.png`. Pass when draft fails clearly without CONTEXT_LAB_MODEL.
- [ ] Lane 10. Full pytest suite green. Save `p4-suite.png`. Pass when `python3 -m pytest -q` exits 0.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. MCP compile latency with embeddings disabled at trunk and head.
- [ ] Probe. Call memory_context ten times at trunk and head without embedding env, interleaved.
- [ ] Baseline. Record trunk median latency first.
- [ ] Rule. Head median within 20 percent of trunk median when embeddings are off. With embeddings on at head only, absolute latency under 2 s against a local stub.

**Review gate.** None. astra-p4 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] Root appends astra-p4 to the stack. The operator lands it.

## Enrich import and action matching (astra-p5)

**Depends on.** astra-p4.

**Files.**

- [ ] Edit `context_lab/knowledge.py`.
- [ ] Edit `context_lab/provider.py`.
- [ ] Edit `context_lab/engine.py` for UnknownParaphrase vs KnownIncompatible.
- [ ] Create `tests/test_import_structure.py`.

**Build.**

- [ ] Preserve heading ancestry, links, and properties on import chunks. Derive vocabulary from confirmed memories plus catalog. Distinguish unrecognized paraphrase from known-incompatible action so paraphrase does not auto-disqualify evidence.

**You see.**

- [ ] An imported note keeps heading_path on its document record, and a paraphrased query no longer marks a useful lesson inapplicable solely for unrecognized action text.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `tests/test_import_structure.py` covers heading_path retention and action match variants. Run `python3 -m pytest tests/test_import_structure.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Import a nested heading note at trunk and head. Save `p5-regression.png`. Pass when head stores heading_path and trunk flat heading is documented.
- [ ] Lane 2. Wikilinks appear on the chunk record. Save `p5-links.png`. Pass when outbound_links is nonempty for a linked note.
- [ ] Lane 3. Frontmatter properties survive import. Save `p5-props.png`. Pass when properties contain a seeded key.
- [ ] Lane 4. Vocabulary includes a need_tag from a confirmed memory. Save `p5-vocab.png`. Pass when catalog union lists that tag.
- [ ] Lane 5. UnknownParaphrase does not force inapplicable. Save `p5-paraphrase.png`. Pass when a useful lesson stays eligible.
- [ ] Lane 6. KnownIncompatible still excludes. Save `p5-incompatible.png`. Pass when an explicit mismatch stays out.
- [ ] Lane 7. Condition proposals stay candidates for review. Save `p5-propose.png`. Pass when proposed applies fields are status candidate only.
- [ ] Lane 8. Refresh import is idempotent on unchanged files. Save `p5-refresh.png`. Pass when chunk_count is stable across refresh.
- [ ] Lane 9. Cl session folders remain skipped. Save `p5-skip-cl.png`. Pass when Cl paths are absent from documents.
- [ ] Lane 10. Full pytest suite green. Save `p5-suite.png`. Pass when `python3 -m pytest -q` exits 0.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Import wall time for a 20-note fixture at trunk and head.
- [ ] Probe. Run initiate --refresh on the fixture at trunk and head, interleaved.
- [ ] Baseline. Record trunk import milliseconds first.
- [ ] Rule. Head import within 25 percent of trunk. Absolute under 2 s for the 20-note fixture.

**Review gate.** None. astra-p5 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] Root appends astra-p5 to the stack. The operator lands it.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the report the execution playbook names.

## Appendix A. Prototype evidence

Astra already reproduced wire overshoot (context 615 vs JSON 2384 under budget 1200), ancestor crowding of ticket evidence, and scope_covers rejection of project lessons citing ticket sources at commit 7264cf2. No new prototype branch was cut for those three. Open product prefs remain unproven. Default policy allowance size, auto-tag vs manual standing_rule migration, and whether MandatoryPolicyOverflow returns a tiny error view or fails the tool call.

## Appendix B. Alternatives rejected

Slimmer FULL_KEYS with the same prose budget. Lost because wire size stays dishonest. Layer quotas. Lost because scope would still act as importance. Copying ticket sources into project scope. Lost because provenance collapses. Candidate-1 stage modules (envelope, standing, selector). Lost to temporal decomposition and pass-through agent_api. Full NewType hierarchy without standing_rule kind graft. Lost as excess persistence churn.

## Appendix C. Risks

Wire meter drift if MCP wrapper shape changes (astra-p1). Standing_rule migration misses unlabeled gates (astra-p2). Promotion summaries too thin for later tickets (astra-p3). Local stub embeddings hide real latency (astra-p4). Richer import increases document pool noise before vocabulary derivation lands (astra-p5). Evidence freshness research is intentionally out of this program.

## Appendix D. Links and reading list

Synthesized design at `/tmp/arena-ticket999/synthesis/design.md`. Arena candidates under `/tmp/arena-ticket999/candidate-1` and `candidate-2`. Cross-judge at `/tmp/arena-ticket999/cross-judge.md`. Grounding at `/tmp/arena-ticket999/grounding/how-synthesized.md`. Run `pstack/skills/how/SKILL.md` again before astra-p2 if engine selection drifts. Run `pstack/skills/interrogate/SKILL.md` on astra-p1 CompactView shape if the operator contests the breaking MCP JSON change. Trail per `pstack/skills/show-me-your-work/SKILL.md` during execution.
