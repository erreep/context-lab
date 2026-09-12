"""SQLite persistence with immutable sources and optimistic memory revisions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


KINDS = {"fact", "constraint", "decision", "event", "lesson"}
STATUSES = {"candidate", "confirmed", "retracted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


def checked_date(value):
    date.fromisoformat(value)
    return value


def scope_key(record):
    project, ticket = record.get("project"), record.get("ticket", "")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("Scope requires a nonempty project")
    if not isinstance(ticket, str):
        raise ValueError("ticket must be text; omit it for project-only context")
    return project.strip(), ticket.strip()


def validate_memory(raw):
    if not isinstance(raw, dict):
        raise ValueError("Memory must be a JSON object")
    m = dict(raw)
    for key in ("id", "project", "title", "claim", "kind"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            raise ValueError(f"Memory requires nonempty {key}")
    m["project"], m["ticket"] = scope_key(m)
    if m["kind"] not in KINDS:
        raise ValueError("Unknown memory kind")
    m.setdefault("status", "candidate")
    if m["status"] not in STATUSES:
        raise ValueError("Unknown memory status")
    for key in ("source_ids", "depends_on", "supersedes", "topics", "need_tags"):
        m.setdefault(key, [])
        if not isinstance(m[key], list) or any(not isinstance(x, str) for x in m[key]):
            raise ValueError(f"{key} must be a list of strings")
        m[key] = list(dict.fromkeys(m[key]))
    if not m["source_ids"]:
        raise ValueError("Every memory needs at least one source_id")
    for key in ("rationale", "expected_effect", "quote"):
        m.setdefault(key, "")
        if not isinstance(m[key], str):
            raise ValueError(f"{key} must be text")
    m.setdefault("valid_from", date.today().isoformat())
    checked_date(m["valid_from"])
    if m.get("valid_until"):
        checked_date(m["valid_until"])
        if m["valid_until"] <= m["valid_from"]:
            raise ValueError("valid_until must be after valid_from (exclusive end)")
    for key in ("applies", "unless", "assumptions", "assertions"):
        m.setdefault(key, {})
        if not isinstance(m[key], dict):
            raise ValueError(f"{key} must be an object")
    allowed_applies = {"actions_any", "state_equals"}
    if set(m["applies"]) - allowed_applies:
        raise ValueError("applies supports actions_any and state_equals")
    acts = m["applies"].get("actions_any", [])
    eq = m["applies"].get("state_equals", {})
    if not isinstance(acts, list) or any(not isinstance(x, str) for x in acts):
        raise ValueError("actions_any must be a list of strings")
    if not isinstance(eq, dict):
        raise ValueError("state_equals must be an object")
    if m["id"] in m["depends_on"] + m["supersedes"]:
        raise ValueError("A memory cannot depend on or supersede itself")
    # Keep state predicates exact and inspectable; no evaluated expressions.
    for obj in (eq, m["unless"], m["assumptions"], m["assertions"]):
        if any(not isinstance(k, str) or isinstance(v, (list, dict)) for k, v in obj.items()):
            raise ValueError("State values and assertions must be JSON scalars")
    json.dumps(m, allow_nan=False)
    return m


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS memories (
            id TEXT NOT NULL, version INTEGER NOT NULL, recorded_at TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(id, version));
          CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS embeddings (
            key TEXT PRIMARY KEY, vector TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS knowledge_bases (
            project TEXT NOT NULL, ticket TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(project, ticket));
        """)
        # Existing stores predate ticket scope; their records remain project-only.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if "ticket" not in {r[1] for r in self.db.execute("PRAGMA table_info(sources)")}:
                self.db.execute("ALTER TABLE sources ADD COLUMN ticket TEXT NOT NULL DEFAULT ''")

    def close(self):
        self.db.close()

    def add_source(self, source):
        s = dict(source)
        for key in ("project", "title", "body"):
            if not isinstance(s.get(key), str) or not s[key].strip():
                raise ValueError(f"Source requires nonempty {key}")
        s["project"], s["ticket"] = scope_key(s)
        s.setdefault("id", new_id("src"))
        s.setdefault("created_at", now())
        s["sha256"] = hashlib.sha256(s["body"].encode()).hexdigest()
        old = self.source(s["id"])
        if old:
            if all(old[k] == s[k] for k in ("project", "ticket", "title", "body")):
                return old
            raise ValueError("Sources are immutable; create a new source ID")
        with self.db:
            self.db.execute("INSERT INTO sources (id,project,title,body,created_at,sha256,ticket) VALUES (?,?,?,?,?,?,?)",
                            tuple(s[k] for k in ("id", "project", "title", "body", "created_at", "sha256", "ticket")))
        return s

    def knowledge_base(self, project, ticket=""):
        row = self.db.execute("SELECT payload FROM knowledge_bases WHERE project=? AND ticket=?",
                              scope_key({"project": project, "ticket": ticket})).fetchone()
        return json.loads(row[0]) if row else None

    def knowledge_bases(self):
        return [{k: v for k, v in json.loads(r[0]).items() if k != "documents"}
                for r in self.db.execute("SELECT payload FROM knowledge_bases ORDER BY project,ticket")]

    def documents(self, project, ticket=""):
        return (self.knowledge_base(project, ticket) or {}).get("documents", [])

    def source(self, sid):
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def sources(self, project=None):
        if project:
            rows = self.db.execute("SELECT * FROM sources WHERE project=? ORDER BY id", (project,))
        else:
            rows = self.db.execute("SELECT * FROM sources ORDER BY id")
        return [dict(r) for r in rows]

    def memories(self):
        rows = self.db.execute("""SELECT m.* FROM memories m JOIN
          (SELECT id, MAX(version) AS v FROM memories GROUP BY id) x
          ON m.id=x.id AND m.version=x.v ORDER BY m.id""")
        return [dict(json.loads(r["payload"]), version=r["version"], recorded_at=r["recorded_at"]) for r in rows]

    def memory(self, mid):
        rows = self.revisions(mid)
        return rows[-1] if rows else None

    def revisions(self, mid):
        rows = self.db.execute("SELECT * FROM memories WHERE id=? ORDER BY version", (mid,))
        return [dict(json.loads(r["payload"]), version=r["version"], recorded_at=r["recorded_at"]) for r in rows]

    def put_memories(self, entries):
        """Atomically add/revise a batch. Existing IDs require expected_version."""
        if not isinstance(entries, list) or not entries:
            raise ValueError("memories must be a nonempty list")
        validated = [validate_memory(m) for m in entries]
        if len({m["id"] for m in validated}) != len(validated):
            raise ValueError("Duplicate IDs in memory batch")
        output = []
        try:
            self.db.execute("BEGIN IMMEDIATE")
            existing = {m["id"]: m for m in self.memories()}
            proposed = {**existing, **{m["id"]: m for m in validated}}
            for m in validated:
                for sid in m["source_ids"]:
                    s = self.source(sid)
                    if not s:
                        raise ValueError(f"Missing evidence source: {sid}")
                    if scope_key(s) != scope_key(m):
                        raise ValueError("Memory and evidence must belong to the same project and ticket")
                if m["quote"] and not any(m["quote"] in self.source(s)["body"] for s in m["source_ids"]):
                    raise ValueError("Evidence quote must be an exact excerpt from a linked source")
                for ref in m["depends_on"] + m["supersedes"]:
                    if ref not in proposed or scope_key(proposed[ref]) != scope_key(m):
                        raise ValueError(f"Missing or cross-project/ticket memory reference: {ref}")
                old = existing.get(m["id"])
                expected = m.pop("expected_version", None)
                if old and scope_key(old) != scope_key(m):
                    raise ValueError("A memory ID cannot move between projects or tickets; create a new ID")
                if old and expected != old["version"]:
                    raise ValueError(f"Revision conflict for {m['id']}; expected_version must be {old['version']}")
                if not old and expected not in (None, 0):
                    raise ValueError("New memory cannot specify an existing version")
            # Validate both reference graphs separately, including pre-existing nodes.
            for edge in ("depends_on", "supersedes"):
                visiting, done = set(), set()
                def walk(mid):
                    if mid in visiting:
                        raise ValueError(f"Cycle in {edge}")
                    if mid in done:
                        return
                    visiting.add(mid)
                    for child in proposed[mid].get(edge, []):
                        walk(child)
                    visiting.remove(mid)
                    done.add(mid)
                for mid in proposed:
                    walk(mid)
            for m in validated:
                old = existing.get(m["id"])
                version = old["version"] + 1 if old else 1
                timestamp = now()
                m.pop("version", None)
                m.pop("recorded_at", None)
                self.db.execute("INSERT INTO memories VALUES (?,?,?,?)",
                                (m["id"], version, timestamp, json.dumps(m)))
                output.append(dict(m, version=version, recorded_at=timestamp))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return output

    def save_run(self, packet):
        packet = dict(packet, run_id=new_id("run"))
        with self.db:
            self.db.execute("INSERT INTO runs VALUES (?,?,?)", (packet["run_id"], now(), json.dumps(packet)))
        return packet

    def run(self, rid):
        row = self.db.execute("SELECT payload FROM runs WHERE id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None

    def log_feedback(self, rid, memory_id, observation, note=""):
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("Feedback requires a nonempty memory_id")
        if not isinstance(note, str):
            raise ValueError("Feedback note must be text")
        packet = self.run(rid)
        if not packet:
            raise ValueError("Unknown run_id")
        if observation not in {"missed", "irrelevant", "stale", "helpful"}:
            raise ValueError("observation must be missed, irrelevant, stale, or helpful")
        trace = next((x for x in packet["trace"] if x["id"] == memory_id), None)
        selected = any(x["id"] == memory_id for x in packet["selected"])
        if observation == "stale":
            diagnosis = "validity_or_applicability"
        elif observation == "irrelevant":
            diagnosis = "selection_or_applicability" if selected else "not_supplied"
        elif observation == "helpful":
            diagnosis = "reported_helpful" if selected else "not_supplied"
        elif trace is None:
            diagnosis = "not_recorded_at_run"
        elif trace["stage"] == "excluded":
            diagnosis = "eligibility_or_applicability"
        elif selected:
            diagnosis = "supplied_but_reported_missed"
        elif trace["stage"] == "not_retrieved":
            diagnosis = "retrieval"
        else:
            diagnosis = "selection_or_budget"
        result = dict(id=new_id("fb"), run_id=rid, memory_id=memory_id,
                      observation=observation, diagnosis=diagnosis, note=note,
                      task=packet["task"], created_at=now(), status="reported_not_verified")
        with self.db:
            self.db.execute("INSERT INTO feedback VALUES (?,?,?)", (result["id"], result["created_at"], json.dumps(result)))
        return result

    def feedback(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM feedback ORDER BY created_at DESC")]

    def export(self):
        return {"schema_version": 2, "sources": self.sources(), "memories": self.memories(),
                "knowledge_bases": [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM knowledge_bases ORDER BY project,ticket")],
                "revisions": {m["id"]: self.revisions(m["id"]) for m in self.memories()}, "feedback": self.feedback()}

    def seed(self, filename):
        payload = json.loads(Path(filename).read_text())
        for s in payload["sources"]:
            self.add_source(s)
        existing = {m["id"] for m in self.memories()}
        fresh = [m for m in payload["memories"] if m["id"] not in existing]
        if fresh:
            self.put_memories(fresh)
        return {"added": len(fresh), "total": len(self.memories())}
