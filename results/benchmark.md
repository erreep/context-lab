# Context Lab: synthetic context-selection results

Fieldnote: 28 authored context-selection cases

Context budget: 1200 estimated tokens per arm.

| Strategy | Context cases passed | Required recall | Selection precision | Forbidden inclusions | Mean estimated tokens |
|---|---:|---:|---:|---:|---:|
| retrieval | 12/28 | 57.0% | 70.5% | 0 | 187 |
| lessons | 13/28 | 73.3% | 57.1% | 8 | 296 |
| targeted | 26/28 | 92.0% | 92.9% | 0 | 253 |

## Interpretation limits

- Synthetic, authored fixtures; not independent evidence of superiority.
- Scores measure annotated context selection and declared evidence gaps, not agent task success.
- The default planner is a transparent regex catalog and can miss paraphrases or negation.
- Default retrieval is BM25; embeddings and a model planner are optional and require a configured endpoint.
- Lessons are hand-authored unless you draft and review your own. No autonomous learning is claimed.
- All arms share date, project, candidate and supersession filters, task features and budget.
- Coverage labels remain outside retrieval. Fixtures are development examples, not a sealed holdout.
- Budget uses estimated tokens. Latency excludes ingestion; embedding cache state affects timing.

## Case-level failures

- **01 / retrieval:** missing=['C-offline', 'L-operation']; forbidden=[]; gap mismatches={}
- **01 / lessons:** missing=['C-offline']; forbidden=['L-read']; gap mismatches={}
- **02 / retrieval:** missing=['L-read']; forbidden=[]; gap mismatches={}
- **02 / lessons:** missing=[]; forbidden=['L-operation']; gap mismatches={}
- **03 / retrieval:** missing=['L-operation']; forbidden=[]; gap mismatches={}
- **03 / lessons:** missing=[]; forbidden=['L-read']; gap mismatches={}
- **04 / lessons:** missing=[]; forbidden=['L-operation', 'L-read']; gap mismatches={}
- **05 / retrieval:** missing=['L-operation']; forbidden=[]; gap mismatches={'duplicate_prevention': {'expected': 'conditional', 'actual': 'missing'}}
- **05 / lessons:** missing=[]; forbidden=['L-read']; gap mismatches={}
- **08 / retrieval:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **08 / lessons:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **09 / retrieval:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **09 / lessons:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **12 / retrieval:** missing=['L-schema']; forbidden=[]; gap mismatches={}
- **13 / lessons:** missing=[]; forbidden=['L-schema']; gap mismatches={}
- **14 / retrieval:** missing=['L-schema']; forbidden=[]; gap mismatches={'schema_compatibility': {'expected': 'conditional', 'actual': 'missing'}}
- **16 / retrieval:** missing=['C-brand']; forbidden=[]; gap mismatches={}
- **16 / lessons:** missing=['C-brand']; forbidden=[]; gap mismatches={}
- **21 / retrieval:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **21 / lessons:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **22 / retrieval:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **22 / lessons:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **23 / retrieval:** missing=['C-offline', 'D-local']; forbidden=[]; gap mismatches={}
- **23 / lessons:** missing=['C-offline', 'D-local']; forbidden=[]; gap mismatches={}
- **23 / targeted:** missing=['C-offline', 'D-local']; forbidden=[]; gap mismatches={}
- **24 / retrieval:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **24 / lessons:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **24 / targeted:** missing=['C-offline']; forbidden=[]; gap mismatches={}
- **26 / retrieval:** missing=['C-offline', 'D-local']; forbidden=[]; gap mismatches={'storage_assumption': {'expected': 'conditional', 'actual': 'not_supplied'}}
- **26 / lessons:** missing=['C-offline', 'D-local']; forbidden=[]; gap mismatches={'storage_assumption': {'expected': 'conditional', 'actual': 'not_supplied'}}
- **27 / retrieval:** missing=['L-schema']; forbidden=[]; gap mismatches={}
- **28 / retrieval:** missing=['C-brand', 'C-offline', 'L-operation']; forbidden=[]; gap mismatches={}
- **28 / lessons:** missing=['C-brand', 'C-offline']; forbidden=['L-read']; gap mismatches={}
