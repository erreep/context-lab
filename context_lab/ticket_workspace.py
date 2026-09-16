"""One ticket's notes volume: pin, reserve, publish, locked KB merge."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from . import knowledge as knowledge_mod
from .knowledge import (
    journal_digest,
    journal_note_path,
    journal_slug,
    note_source_and_docs,
    require_journal_home,
)
from .pinned_root import PinMetadata, PinnedRoot
from .store import Store, now, scope_key


@dataclass(frozen=True)
class JournalOutcome:
    relative_path: str
    index_status: Literal["indexed", "already_indexed", "reindexed"]
    source_id: str
    chunks: int | None = None


class TicketWorkspace:
    """Pin, reserve journal paths, publish via PinnedRoot, merge KB under lock."""

    def __init__(self, store: Store, root: PinnedRoot, project: str, ticket: str, notes_path: Path):
        self.store = store
        self.root = root
        self.project = project
        self.ticket = ticket
        self.notes_path = notes_path

    @classmethod
    def open(cls, store: Store, project: str, ticket: str = "") -> TicketWorkspace:
        project, ticket = scope_key({"project": project, "ticket": ticket})
        home, _created = require_journal_home(store, project, ticket)
        kb = store.knowledge_base(project, ticket)
        stored = None
        notes_pin = (kb or {}).get("notes_pin")
        if isinstance(notes_pin, dict):
            stored = PinMetadata(
                path=str(notes_pin.get("path", "")),
                st_dev=notes_pin.get("st_dev"),
                st_ino=notes_pin.get("st_ino"),
            )
        pinned = PinnedRoot.pin(home.notes, stored=stored)
        if pinned.pin:
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                kb = store.knowledge_base(project, ticket) or knowledge_mod.empty_kb_record(project, ticket)
                if not isinstance(kb.get("notes_pin"), dict):
                    record = dict(kb)
                    record["notes_pin"] = {
                        "path": pinned.pin.path,
                        "st_dev": pinned.pin.st_dev,
                        "st_ino": pinned.pin.st_ino,
                    }
                    store.db.execute(
                        "INSERT INTO knowledge_bases VALUES (?,?,?) "
                        "ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
                        (project, ticket, json.dumps(record)),
                    )
        return cls(store, pinned, project, ticket, home.notes)

    def close(self) -> None:
        self.root.close()

    def __enter__(self) -> TicketWorkspace:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def write_journal(self, kind: str, title: str, body: str) -> JournalOutcome:
        slug = journal_slug(title)
        digest = journal_digest(kind, title, body)
        legacy_dest, legacy_already = journal_note_path(self.notes_path, kind, title, body)
        legacy_rel = legacy_dest.relative_to(self.notes_path).as_posix() if legacy_already else None
        write_needed = not legacy_already
        index_status = "indexed"
        source_id = ""
        chunks: int | None = None
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            if legacy_rel:
                relative = self._ensure_reservation(kind, slug, digest, legacy_rel)
                write_needed = False
            else:
                relative, replay = self._reserve_path(kind, slug, digest)
                if replay:
                    write_needed = False
                elif (self.notes_path / relative).is_file():
                    write_needed = False
            if write_needed:
                created = datetime.now(timezone.utc)
                front = (
                    f"---\nkind: {kind}\nproject: {self.project}\nticket: {self.ticket}\n"
                    f"created_at: {created.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                    f"digest: {digest}\n---\n\n"
                    f"# {title.strip()}\n\n{body.strip()}\n"
                )
                self.root.ensure_dir(Path("journal"))
                self.root.write_text(Path(relative), front)
            merged = self._merge_index(relative)
            index_status = merged["status"]
            source_id = merged["source_id"]
            chunks = merged.get("chunks")
        return JournalOutcome(
            relative_path=relative,
            index_status=index_status,
            source_id=source_id,
            chunks=chunks,
        )

    def _ensure_reservation(self, kind: str, slug: str, digest: str, relative: str) -> str:
        self.store.db.execute(
            """INSERT OR IGNORE INTO journal_reservations
               (project, ticket, kind, slug, digest, relative_path, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (self.project, self.ticket, kind, slug, digest, relative, now()),
        )
        return relative

    def _reserve_path(self, kind: str, slug: str, digest: str) -> tuple[str, bool]:
        row = self.store.db.execute(
            """SELECT relative_path FROM journal_reservations
               WHERE project=? AND ticket=? AND kind=? AND slug=? AND digest=?""",
            (self.project, self.ticket, kind, slug, digest),
        ).fetchone()
        if row:
            return row[0], True
        existing = self.store.db.execute(
            """SELECT 1 FROM journal_reservations
               WHERE project=? AND ticket=? AND kind=? AND slug=? LIMIT 1""",
            (self.project, self.ticket, kind, slug),
        ).fetchone()
        relative = (
            f"journal/{kind}-{slug}-{digest}.md"
            if existing
            else f"journal/{kind}-{slug}.md"
        )
        self.store.db.execute(
            """INSERT INTO journal_reservations
               (project, ticket, kind, slug, digest, relative_path, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (self.project, self.ticket, kind, slug, digest, relative, now()),
        )
        return relative, False

    def _merge_index(self, relative: str) -> dict:
        """Re-read KB payload under lock, merge documents, upsert source."""
        kb = self.store.knowledge_base(self.project, self.ticket)
        if not kb or not isinstance(kb.get("path"), str) or not kb["path"].strip():
            raise ValueError("Ticket has no bound notes folder")
        root = Path(kb["path"]).expanduser().resolve(strict=True)
        file_path = (self.notes_path / relative).resolve(strict=True)
        file_path.relative_to(root)
        body = file_path.read_text(encoding="utf-8-sig")
        if not body.strip():
            raise ValueError("Note is empty")
        timestamp = now()
        source, documents, categories = note_source_and_docs(
            self.project, self.ticket, root, file_path, body, timestamp,
        )
        sid = source[0]
        kb = self.store.knowledge_base(self.project, self.ticket) or {}
        docs = list(kb.get("documents") or [])
        existing_ids = {d["id"] for d in docs}
        doc_ids = {d["id"] for d in documents}
        had_source = self.store.source(sid) is not None
        if had_source and doc_ids <= existing_ids:
            return {"status": "already_indexed", "source_id": sid, "path": relative}
        for doc in documents:
            if doc["id"] not in existing_ids:
                docs.append(doc)
        cats = Counter(kb.get("categories") or {})
        cats.update(categories)
        record = dict(kb)
        record["documents"] = docs
        record["categories"] = dict(cats)
        record["note_count"] = int(record.get("note_count") or 0) + (0 if had_source else 1)
        record["chunk_count"] = len(docs)
        record["indexed_at"] = timestamp
        if len(docs) > 20_000:
            raise ValueError("Knowledge base exceeds 20,000 sections; choose a smaller folder")
        self.store.db.execute(
            "INSERT OR IGNORE INTO sources (id,project,title,body,created_at,sha256,ticket) VALUES (?,?,?,?,?,?,?)",
            source,
        )
        self.store.db.execute(
            "INSERT INTO knowledge_bases VALUES (?,?,?) ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
            (self.project, self.ticket, json.dumps(record)),
        )
        status = "reindexed" if had_source else "indexed"
        return {"status": status, "source_id": sid, "path": relative, "chunks": len(documents)}
