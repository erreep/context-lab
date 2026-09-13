"""Reproduce the deliberately synthetic fixtures. These are not a sealed holdout."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "context_lab" / "data"
sources, memories, cases = [], [], []


def source(sid, title, body, project="fieldnote"):
    sources.append(dict(id=sid, project=project, title=title, body=body, created_at="2026-01-01T00:00:00+00:00"))


def memory(mid, kind, title, claim, sid, actions, needs=(), **extra):
    memories.append(dict(id=mid, project=extra.pop("project", "fieldnote"), kind=kind, title=title,
        claim=claim, source_ids=[sid], status=extra.pop("status", "confirmed"),
        valid_from=extra.pop("valid_from", "2026-01-01"), applies=extra.pop("applies", {"actions_any": actions}),
        need_tags=list(needs), **extra))


source("src-product", "Product decision: disconnected work", "Core editing must work without any connection. Optional synchronization must not block local edits. This is a confirmed product requirement.")
memory("C-offline", "constraint", "Editing continues without a connection", "Core editing must work without any connection. Optional synchronization must not block local edits.", "src-product", ["sync", "sharing", "hosting", "deploy"], ["offline_mode"], assertions={"offline_mode": True}, quote="Core editing must work without any connection.")
source("src-storage", "Architecture decision: local store", "We chose SQLite for the local store because the first release runs on one device. This decision did not evaluate simultaneous editing across devices. Revisit the architecture if that assumption changes.")
memory("D-local", "decision", "SQLite selection and its original assumption", "SQLite is the local store. Its original selection did not evaluate simultaneous editing across devices.", "src-storage", ["storage", "sharing", "sync"], ["storage_assumption"], assumptions={"single_device": True}, rationale="The first release ran on one device.", expected_effect="Reconsider the architecture when introducing shared editing.")
source("src-incident", "Incident: duplicated edits after reconnect", "During reconnect, the upload worker retried an acknowledged request. The server created a second edit because it treated the delivery as a new operation. A regression test reproduced the duplicate.")
memory("E-duplicate", "event", "Duplicated edits after reconnect", "A retried upload created a second edit; the regression test reproduced it.", "src-incident", ["sync", "retry"], topics=["upload", "retry", "reconnect"], quote="A regression test reproduced the duplicate.")
memory("L-operation", "lesson", "Preserve operation identity across retry", "When a write can be delivered again, reuse its operation identity and check that the receiver rejects duplicate effects.", "src-incident", ["sync", "retry"], ["duplicate_prevention"], depends_on=["E-duplicate"], unless={"idempotency_verified": True}, rationale="Delivery can repeat even when the first response was lost.", expected_effect="Inspect duplicate prevention before enabling automatic retry.", topics=["idempotency", "upload", "retry"])
source("src-read", "Read-only endpoint retry experiment", "The read-only GET /status endpoint was safe to retry with bounded backoff. We did not test this approach for POST requests that create records.")
memory("L-read", "lesson", "Bounded retry for read-only status requests", "Read-only GET status requests can use bounded retry. This result says nothing about record-creating requests.", "src-read", ["retry"], ["duplicate_prevention"], applies={"actions_any": ["retry"], "state_equals": {"http_method": "GET"}}, assertions={"read_retry_safe": True}, expected_effect="Verify that the operation is read-only before reusing this procedure.")
source("src-host-old", "Original hosting restriction", "For the initial prototype, do not introduce any paid hosting service. This decision is limited to the initial prototype.")
memory("C-host-old", "constraint", "Initial prototype hosting restriction", "The initial prototype must not introduce paid hosting.", "src-host-old", ["hosting", "sharing"], ["hosting_policy"], assertions={"hosting_allowed": False})
source("src-host-new", "July decision: optional hosted relay", "The product owner approved an optional hosted relay from 1 July 2026. It must stay below the agreed monthly cap and cannot be required for local editing. This replaces the prototype ban on paid hosting.")
memory("C-host-current", "constraint", "Optional hosted relay approved", "An optional hosted relay is allowed within the agreed monthly cap; local editing must still work independently.", "src-host-new", ["hosting", "sharing"], ["hosting_policy"], valid_from="2026-07-01", supersedes=["C-host-old"], assertions={"hosting_allowed": True}, expected_effect="Check the current cap before selecting a service; no amount is established in this source.")
source("src-auth-old", "Initial login plan", "The prototype login plan used passwords. This was the initial plan, before the authentication review.")
memory("D-auth-old", "decision", "Original password login plan", "Use passwords for prototype login.", "src-auth-old", ["auth"], ["auth_policy"])
source("src-auth-new", "Authentication review", "From 15 March 2026, use email magic links for login. This explicitly replaces the password plan. Enterprise SSO has not been decided.")
memory("D-auth-current", "decision", "Email magic link login", "Use email magic links for login. Enterprise SSO remains undecided.", "src-auth-new", ["auth"], ["auth_policy"], valid_from="2026-03-15", supersedes=["D-auth-old"])
source("src-schema", "Incident: old client broke after column removal", "Removing the legacy title column broke installed desktop clients. A compatibility test reproduced the issue. For mixed client versions, add the replacement first and delay removal until old clients no longer depend on it.")
memory("E-schema", "event", "Old desktop clients broke after column removal", "A compatibility test reproduced an old-client failure after the legacy title column was removed.", "src-schema", ["migration"], topics=["schema", "column", "migration"])
memory("L-schema", "lesson", "Stage schema changes while older clients remain", "For mixed client versions, add the replacement first and delay removing fields still used by installed clients.", "src-schema", ["migration"], ["schema_compatibility"], depends_on=["E-schema"], applies={"actions_any": ["migration"], "state_equals": {"old_clients_present": True}}, expected_effect="Check the oldest supported client before removing the column.")
source("src-brand", "Fieldnote visual conventions", "Use deep green for primary buttons, an off-white canvas and restrained orange accents. Keep these visual conventions consistent in settings and editor screens.")
memory("C-brand", "constraint", "Fieldnote visual conventions", "Use deep green primary buttons, an off-white canvas and restrained orange accents.", "src-brand", ["ui"], ["brand_rule"])
source("src-release", "Release checklist", "Before a production release, run the offline editing check and the compatibility suite. A passing unit test suite alone is insufficient.")
memory("C-release", "constraint", "Release checks", "Before release, run offline editing and compatibility checks in addition to unit tests.", "src-release", ["deploy"], ["release_policy"])
source("src-search", "Privacy requirement for search", "Private note text may not be sent to a third-party search indexing service. Local indexing is permitted. This requirement also applies when the interface only sends excerpts.")
memory("C-search", "constraint", "Private note search remains local", "Private note text and excerpts must not be sent to third-party search indexes.", "src-search", ["search"], ["query_privacy"], expected_effect="Check where indexing processes note text.")
source("src-retention-a", "Retention proposal marked approved by operations", "Operations recorded deleted-note retention as 30 days. The document is marked approved, but no relationship to the product retention document is recorded.")
memory("C-retention-30", "constraint", "Operations retention record", "Operations recorded a 30-day deleted-note retention period.", "src-retention-a", ["delete"], ["retention_rule"], assertions={"retention_days": 30})
source("src-retention-b", "Retention record from product", "Product recorded deleted-note retention as 90 days. No explicit supersession of the operations document is recorded.")
memory("C-retention-90", "constraint", "Product retention record", "Product recorded a 90-day deleted-note retention period.", "src-retention-b", ["delete"], ["retention_rule"], assertions={"retention_days": 90})
source("src-candidate", "Unreviewed agent suggestion", "An agent suggested switching all persistence to a hosted database. Nobody has confirmed that suggestion.")
memory("D-unreviewed", "decision", "Unreviewed hosted database suggestion", "Switch all persistence to a hosted database.", "src-candidate", ["storage", "hosting"], ["storage_assumption"], status="candidate")
source("src-future", "Planned future release policy", "A proposed 2027 support window would require a different client compatibility suite. This policy is not effective before 1 January 2027.")
memory("C-future", "constraint", "Future compatibility suite", "Use the new 2027 compatibility suite for releases.", "src-future", ["deploy"], ["release_policy"], valid_from="2027-01-01")
source("src-ui-old", "Temporary launch styling", "A blue primary button was temporarily used only during the January launch experiment; that experiment ended on 1 February 2026.")
memory("C-expired", "constraint", "Temporary blue launch button", "Use a blue primary button during the temporary launch experiment.", "src-ui-old", ["ui"], ["brand_rule"], valid_until="2026-02-01")
source("src-shop", "Shopfront deployment architecture", "Shopfront requires online connectivity and uses a managed hosted database. This is a separate project from Fieldnote.", project="shopfront")
memory("S-hosting", "fact", "Shopfront hosted database", "Shopfront requires connectivity and uses a managed hosted database.", "src-shop", ["hosting", "storage", "deploy"], ["hosting_policy", "storage_assumption"], project="shopfront")


def case(cid, label, query, required=(), forbidden=(), relevant=(), state=None, gaps=None, category="implicit", as_of="2026-09-12", project="fieldnote", **task_fields):
    cases.append({"id": cid, "label": label, "category": category, "split": "illustrative",
        "task": dict(query=query, project=project, state=state or {}, as_of=as_of, **task_fields),
        "expected": {"required": list(required), "forbidden": list(forbidden),
                     "relevant": sorted(set(relevant) | set(required)), "gaps": gaps or {}}})


case("01", "Background uploads after reconnect", "Add background uploads that retry after reconnecting.", ["C-offline", "L-operation", "E-duplicate"], ["L-read", "C-brand"], ["D-local"], {"idempotency_verified": False, "http_method": "POST", "single_device": True})
case("02", "Read-only retry", "Add bounded retry to the read-only status request.", ["L-read"], ["L-operation"], ["E-duplicate"], {"http_method": "GET", "idempotency_verified": True})
case("03", "Write retry", "Add retry to the request that creates a note.", ["L-operation", "E-duplicate"], ["L-read"], state={"http_method": "POST", "idempotency_verified": False})
case("04", "Duplicate prevention already verified", "Change the retry backoff duration.", [], ["L-operation", "L-read"], ["E-duplicate"], {"http_method": "POST", "idempotency_verified": True}, category="negative")
case("05", "Exception state unknown", "Add retry to the request that creates a note.", ["L-operation", "E-duplicate"], ["L-read"], state={"http_method": "POST"}, gaps={"duplicate_prevention": "conditional"}, category="uncertainty")
case("06", "Team sharing changes an assumption", "Add team sharing with shared editing.", ["C-offline", "D-local"], ["C-host-old"], ["C-host-current"], {"single_device": False}, {"storage_assumption": "conditional", "access_policy": "missing"}, category="changed_assumption")
case("07", "Storage rationale", "Explain why this project uses SQLite storage.", ["D-local"], ["D-unreviewed"], state={"single_device": True}, category="explicit")
case("08", "Historical hosting restriction", "Can the prototype use a hosted service?", ["C-host-old", "C-offline"], ["C-host-current"], as_of="2026-02-01", category="temporal")
case("09", "Current hosting policy", "Can we choose a hosted relay for this project?", ["C-host-current", "C-offline"], ["C-host-old"], category="temporal")
case("10", "Current authentication", "Implement the login flow.", ["D-auth-current"], ["D-auth-old"], category="temporal")
case("11", "Historical authentication", "Explain the login plan as it stood in February.", ["D-auth-old"], ["D-auth-current"], as_of="2026-02-10", category="temporal")
case("12", "Schema removal with older clients", "Drop the legacy title column in the next schema migration.", ["L-schema", "E-schema"], state={"old_clients_present": True}, category="dependency")
case("13", "No older clients remain", "Drop the legacy title column in the schema migration.", [], ["L-schema"], ["E-schema"], {"old_clients_present": False}, category="negative")
case("14", "Client compatibility unknown", "Rename the title column in the schema.", ["L-schema", "E-schema"], state={}, gaps={"schema_compatibility": "conditional"}, category="uncertainty")
case("15", "An unrelated button change", "Change the primary button color in settings.", ["C-brand"], ["L-operation", "L-schema", "C-offline", "C-expired"], category="negative")
case("16", "Visual spacing", "Adjust the editor spacing and typography.", ["C-brand"], ["L-operation", "C-offline"], category="negative")
case("17", "Production release", "Prepare the production release.", ["C-release"], ["C-future"], ["C-offline"], category="explicit")
case("18", "Search feature with an implicit privacy constraint", "Add faster search to private notes.", ["C-search"], ["L-operation"], category="implicit")
case("19", "Conflicting retention records", "Implement deleted-note retention and scheduled purge.", ["C-retention-30", "C-retention-90"], gaps={"retention_rule": "conflicted"}, category="conflict")
case("20", "Cross-project isolation", "Explain the hosted database architecture.", ["S-hosting"], ["D-local", "C-offline", "C-host-current"], project="shopfront", category="scope")
case("21", "Combined search and hosting", "Add a hosted search index.", ["C-search", "C-host-current", "C-offline"], ["C-host-old"], category="composition")
case("22", "Combined storage and collaboration", "Rework the SQLite storage for collaboration across multiple devices.", ["D-local", "C-offline"], ["D-unreviewed"], ["C-host-current"], {"single_device": False}, {"access_policy": "missing", "storage_assumption": "conditional"}, category="composition")
case("23", "Unknown task wording", "Let two colleagues change the same document at once.", ["C-offline", "D-local"], state={"single_device": False}, category="planner_blindspot")
case("24", "Unknown disconnected-work wording", "Keep the editor usable on a flight with no signal.", ["C-offline"], category="planner_blindspot")
case("25", "Missing evidence in an empty project", "Implement the login flow.", [], [], [], {}, {"auth_policy": "missing"}, project="new-project", category="uncertainty")
case("26", "Agent supplies task features", "Let two colleagues change the same document at once.", ["C-offline", "D-local"], [], ["C-host-current"], {"single_device": False}, {"access_policy": "missing", "storage_assumption": "conditional"}, category="caller_plan", actions=["sharing"], needs=["offline_mode", "storage_assumption", "access_policy"])
case("27", "Evidence bundle required", "What did the old desktop clients teach us about migration?", ["L-schema", "E-schema"], state={"old_clients_present": True}, category="dependency")
case("28", "Mixed interface and synchronization task", "Add a sync status button and retry failed uploads.", ["C-brand", "C-offline", "L-operation", "E-duplicate"], ["L-read"], ["D-local"], {"single_device": True, "http_method": "POST", "idempotency_verified": False}, category="composition")

(HERE / "memories.json").write_text(json.dumps({"schema_version": 1, "synthetic": True, "sources": sources, "memories": memories}, indent=2) + "\n")
(HERE / "scenarios.json").write_text(json.dumps({"name": "Fieldnote: 28 authored context-selection cases", "cases": cases}, indent=2) + "\n")
print(f"Wrote {len(sources)} sources, {len(memories)} memories and {len(cases)} cases")
