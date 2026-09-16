"""SQLite user_version migrations for Store and branch-scope registry."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from context_lab.scope import (
    BRANCH_BINDING_SCHEMA_VERSION,
    BranchBindingRegistry,
    BranchScopes,
    MemoryScope,
)
from context_lab.store import STORE_SCHEMA_VERSION, Store


def _store_user_version(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _branch_user_version(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


class StoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_store_sets_user_version(self):
        path = str(self.root / "fresh.sqlite3")
        store = Store(path)
        try:
            self.assertEqual(_store_user_version(path), STORE_SCHEMA_VERSION)
            self.assertIn("ticket", {r[1] for r in store.db.execute("PRAGMA table_info(sources)")})
            tables = {
                r[0]
                for r in store.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("journal_reservations", tables)
        finally:
            store.close()

    def test_legacy_sources_without_ticket_migrates(self):
        legacy = str(self.root / "legacy.sqlite3")
        with sqlite3.connect(legacy) as db:
            db.execute(
                """CREATE TABLE sources (
                   id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                   body TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL)"""
            )
            db.execute(
                "INSERT INTO sources VALUES ('old','course','Old note','Old evidence','2026-01-01','hash')"
            )
        store = Store(legacy)
        try:
            self.assertEqual(_store_user_version(legacy), STORE_SCHEMA_VERSION)
            self.assertEqual(store.source("old")["body"], "Old evidence")
            self.assertEqual(store.source("old")["ticket"], "")
            tables = {
                r[0]
                for r in store.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("journal_reservations", tables)
        finally:
            store.close()

    def test_pre_journal_user_version_two_migrates(self):
        """Stores stamped at v2 (pre-journal) gain journal_reservations via v3."""
        path = str(self.root / "v2.sqlite3")
        with sqlite3.connect(path) as db:
            db.executescript("""
              CREATE TABLE sources (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL,
                ticket TEXT NOT NULL DEFAULT '');
              PRAGMA user_version = 2;
            """)
        store = Store(path)
        try:
            self.assertEqual(_store_user_version(path), STORE_SCHEMA_VERSION)
            tables = {
                r[0]
                for r in store.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("journal_reservations", tables)
        finally:
            store.close()

    def test_legacy_full_schema_without_ticket_migrates(self):
        legacy = str(self.root / "v1.sqlite3")
        with sqlite3.connect(legacy) as db:
            db.executescript("""
              CREATE TABLE sources (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL);
              CREATE TABLE memories (
                id TEXT NOT NULL, version INTEGER NOT NULL, recorded_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(id, version));
              CREATE TABLE parked_items (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, later TEXT NOT NULL DEFAULT '',
                captured_by TEXT NOT NULL, captured_while_ticket TEXT NOT NULL DEFAULT '',
                capture_key TEXT NOT NULL, created_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('parked', 'started', 'dismissed')),
                destination_ticket TEXT, source_id TEXT, candidate_id TEXT, decided_at TEXT,
                UNIQUE (project, captured_by, capture_key),
                CHECK (
                  (state = 'started' AND destination_ticket IS NOT NULL AND source_id IS NOT NULL AND candidate_id IS NOT NULL)
                  OR (state != 'started' AND destination_ticket IS NULL AND source_id IS NULL AND candidate_id IS NULL)
                ));
            """)
            db.execute(
                "INSERT INTO sources VALUES ('keep','app','t','b','2026-01-01','h')"
            )
        store = Store(legacy)
        try:
            self.assertEqual(_store_user_version(legacy), STORE_SCHEMA_VERSION)
            self.assertEqual(store.source("keep")["ticket"], "")
        finally:
            store.close()


class BranchBindingMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.db = str(self.root / "memory.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def _legacy_registry_path(self):
        common = self.repo / ".git"
        common.mkdir(parents=True, exist_ok=True)
        return common / "context-lab" / "branch-bindings.sqlite3"

    def test_fresh_registry_sets_user_version(self):
        registry = BranchBindingRegistry(self.repo / ".git")
        try:
            self.assertEqual(
                _branch_user_version(registry._path),
                BRANCH_BINDING_SCHEMA_VERSION,
            )
            sql = registry._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='branch_bindings'",
            ).fetchone()[0]
            self.assertNotIn("trim(ticket)", sql)
        finally:
            registry.close()

    def test_legacy_nonempty_ticket_check_migrates(self):
        path = self._legacy_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE repository_config (
                  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                  memory_db TEXT NOT NULL
                );
                CREATE TABLE branch_bindings (
                  branch_ref TEXT PRIMARY KEY,
                  project TEXT NOT NULL CHECK (trim(project) <> ''),
                  ticket TEXT NOT NULL CHECK (trim(ticket) <> ''),
                  updated_at TEXT NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO repository_config (singleton, memory_db) VALUES (1, ?)",
                (self.db,),
            )
            conn.execute(
                "INSERT INTO branch_bindings VALUES ('refs/heads/main','app','T-1','2026-01-01T00:00:00Z')",
            )
        registry = BranchBindingRegistry(self.repo / ".git")
        try:
            self.assertEqual(_branch_user_version(path), BRANCH_BINDING_SCHEMA_VERSION)
            binding = registry.get("refs/heads/main")
            self.assertEqual(binding.scope, MemoryScope(project="app", ticket="T-1"))
        finally:
            registry.close()

    def test_baseline_bind_after_legacy_shape(self):
        path = self._legacy_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE repository_config (
                  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                  memory_db TEXT NOT NULL
                );
                CREATE TABLE branch_bindings (
                  branch_ref TEXT PRIMARY KEY,
                  project TEXT NOT NULL CHECK (trim(project) <> ''),
                  ticket TEXT NOT NULL CHECK (trim(ticket) <> ''),
                  updated_at TEXT NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO repository_config (singleton, memory_db) VALUES (1, ?)",
                (self.db,),
            )
        import subprocess

        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "lab@example.com"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Lab"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )
        baseline = MemoryScope(project="app", ticket="")
        resolved = BranchScopes.bind_current(str(self.repo), baseline, database=self.db)
        self.assertEqual(resolved.scope, baseline)
        self.assertEqual(_branch_user_version(path), BRANCH_BINDING_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
