"""SQLite persistence with immutable sources and optimistic memory revisions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


KINDS = {"fact", "constraint", "decision", "event", "lesson", "standing_rule"}
STATUSES = {"candidate", "confirmed", "retracted"}
# Reserved project for sparse lab-wide standing rules that apply to every project.
GLOBAL_PROJECT = "__global__"


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


def scope_layers(record):
    """Ordered scopes that apply while working in this task scope.

    Lab-wide globals first, then the project's baseline (ticket ''), then the
    exact ticket when one is set. Other tickets never inherit into each other.
    """
    project, ticket = scope_key(record)
    layers = [(GLOBAL_PROJECT, "")]
    if project != GLOBAL_PROJECT:
        layers.append((project, ""))
        if ticket:
            layers.append((project, ticket))
    return layers


def scope_covers(owner, dependent):
    """True when dependent may reference owner (same scope or an ancestor layer)."""
    return scope_key(owner) in set(scope_layers(dependent))


def layer_rank(record):
    project, ticket = scope_key(record)
    if project == GLOBAL_PROJECT:
        return 0
    if ticket == "":
        return 1
    return 2


def validate_memory(raw):
    if not isinstance(raw, dict):
        raise ValueError("Memory must be a JSON object")
    m = dict(raw)
    for key in ("id", "project", "title", "claim", "kind"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            raise ValueError(f"Memory requires nonempty {key}")
    m["project"], m["ticket"] = scope_key(m)
    if m["project"] == GLOBAL_PROJECT and m["ticket"]:
        raise ValueError("Lab-wide memories cannot carry a ticket")
    if m["project"] == GLOBAL_PROJECT and not m.pop("confirm_global", False):
        raise ValueError("Lab-wide memories require confirm_global=true after explicit user approval")
    if m["kind"] not in KINDS:
        raise ValueError("Unknown memory kind")
    if m["kind"] == "standing_rule" and m["ticket"]:
        raise ValueError("standing_rule memories must be lab-wide or project baseline (empty ticket)")
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
        if s["project"] == GLOBAL_PROJECT and s["ticket"]:
            raise ValueError("Lab-wide evidence cannot carry a ticket")
        if s["project"] == GLOBAL_PROJECT and not s.pop("confirm_global", False):
            raise ValueError("Lab-wide evidence requires confirm_global=true after explicit user approval")
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

    def put_knowledge_base(self, record):
        """Upsert a knowledge-base payload (documents included). Used for vault binding on project baseline."""
        if not isinstance(record, dict):
            raise ValueError("Knowledge base must be a JSON object")
        project, ticket = scope_key(record)
        payload = dict(record, project=project, ticket=ticket)
        with self.db:
            self.db.execute(
                "INSERT INTO knowledge_bases VALUES (?,?,?) ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
                (project, ticket, json.dumps(payload)))
        return {k: v for k, v in payload.items() if k != "documents"}

    def knowledge_bases(self):
        return [{k: v for k, v in json.loads(r[0]).items() if k != "documents"}
                for r in self.db.execute("SELECT payload FROM knowledge_bases ORDER BY project,ticket")]

    def documents(self, project, ticket=""):
        return (self.knowledge_base(project, ticket) or {}).get("documents", [])

    def source(self, sid):
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def sources(self, project=None, ticket=None):
        if ticket is not None and project is None:
            raise ValueError("ticket filter requires project")
        if project is not None and ticket is not None:
            project, ticket = scope_key({"project": project, "ticket": ticket})
            rows = self.db.execute("SELECT * FROM sources WHERE project=? AND ticket=? ORDER BY id", (project, ticket))
        elif project is not None:
            rows = self.db.execute("SELECT * FROM sources WHERE project=? ORDER BY id", (project.strip(),))
        else:
            rows = self.db.execute("SELECT * FROM sources ORDER BY id")
        return [dict(r) for r in rows]

    def memories(self, project=None, ticket=None):
        if ticket is not None and project is None:
            raise ValueError("ticket filter requires project")
        rows = self.db.execute("""SELECT m.* FROM memories m JOIN
          (SELECT id, MAX(version) AS v FROM memories GROUP BY id) x
          ON m.id=x.id AND m.version=x.v ORDER BY m.id""")
        out = [dict(json.loads(r["payload"]), version=r["version"], recorded_at=r["recorded_at"]) for r in rows]
        if project is not None and ticket is not None:
            key = scope_key({"project": project, "ticket": ticket})
            out = [m for m in out if scope_key(m) == key]
        elif project is not None:
            out = [m for m in out if m["project"] == project.strip()]
        return out

    def list_scope_rows(self):
        rows = {}

        def row(project, ticket):
            key = (project, ticket)
            if key not in rows:
                rows[key] = {"project": project, "ticket": ticket,
                             "label": ("Lab-wide" if project == GLOBAL_PROJECT else
                                       "Project baseline" if ticket == "" else ticket),
                             "memory_count": 0, "source_count": 0, "note_count": 0, "kb_initialized": False}
            return rows[key]

        for kb in self.knowledge_bases():
            project, ticket = scope_key(kb)
            entry = row(project, ticket)
            entry["kb_initialized"] = True
            entry["note_count"] = kb.get("note_count", 0)
        for source in self.sources():
            project, ticket = scope_key(source)
            row(project, ticket)["source_count"] += 1
        for memory in self.memories():
            project, ticket = scope_key(memory)
            row(project, ticket)["memory_count"] += 1
        row(GLOBAL_PROJECT, "")
        for project in {p for p, _ in rows}:
            row(project, "")
        # Lab-wide first, then projects alphabetically; baseline before tickets within a project.
        return sorted(rows.values(), key=lambda item: (
            item["project"] != GLOBAL_PROJECT, item["project"], item["ticket"] != "", item["ticket"]))

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
        prepared = []
        for raw in entries:
            item = dict(raw)
            old = self.memory(item["id"]) if isinstance(item.get("id"), str) else None
            # Revising an existing lab-wide memory does not re-ask for confirm_global.
            if old and old["project"] == GLOBAL_PROJECT and item.get("project") == GLOBAL_PROJECT:
                item["confirm_global"] = True
            prepared.append(item)
        validated = [validate_memory(m) for m in prepared]
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
                    if not scope_covers(s, m):
                        raise ValueError("Memory and evidence must share the same project/ticket or an ancestor layer")
                if m["quote"] and not any(m["quote"] in self.source(s)["body"] for s in m["source_ids"]):
                    raise ValueError("Evidence quote must be an exact excerpt from a linked source")
                for ref in m["depends_on"] + m["supersedes"]:
                    if ref not in proposed or not scope_covers(proposed[ref], m):
                        raise ValueError(f"Missing or cross-scope memory reference: {ref}")
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
