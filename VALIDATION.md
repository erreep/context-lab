# Validation record

Validated on Python 3.12.14 with SQLite 3.53.1 in the build environment.

## Completed checks

- 34 passing `unittest` checks across persistence, revisions, rollback, source
  provenance, scope, validity, supersession, conditional activation, dependency
  bundles, budget limits, conflict reporting and feedback diagnosis.
- HTTP integration checks start a real local server and exercise the page, example
  picker, three-arm comparison, source creation, candidate confirmation, revision
  retrieval, invalid-input handling and cross-origin rejection.
- MCP initialization, tool discovery, context calls and error handling were tested.
  A subprocess test exercised the newline-delimited stdio interface with a local client.
- The embedded browser JavaScript passed `node --check`.
- The comparison suite ran on all 28 authored fixtures. At a 1200-estimated-token
  budget, context cases passed: retrieval 12/28, lessons 13/28, targeted 26/28.
- A caller-supplied task profile demonstrated assumption invalidation and a missing
  access-policy requirement in an agent-ready packet.

## Limits of these checks

- The available cloud browser refused localhost navigation. Full rendered-browser
  interaction, screenshot and mobile visual checks could not be completed here.
  HTTP integration and JavaScript syntax checks do not replace visual browser QA.
- No live model endpoint was configured. Model planning, lesson drafting and embedding
  adapters were checked with mocks, not a paid or local inference service.
- No external agent client was connected. The MCP transport was tested locally;
  particular client configuration formats and behavior still need verification.
- The benchmark is synthetic and authored with the implementation. It measures
  selection against record-ID and gap-status annotations, not end-to-end task success.
  The two targeted misses are retained: unfamiliar paraphrases are not recognized by
  the starter regex planner. Independent real-task evaluation is the next step.

To reproduce:

```bash
python3 -m context_lab demo
python3 -m unittest discover -s tests -v
python3 -m context_lab benchmark --budget 1200
```
