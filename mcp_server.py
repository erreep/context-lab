"""Absolute-path launcher for MCP clients that do not support cwd.

Seeds the demo corpus only when the database has no sources and no memories.
Existing user data is never replaced.
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from context_lab.engine import ROOT
from context_lab.mcp import serve_mcp
from context_lab.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Context Lab stdio MCP server")
    parser.add_argument(
        "--db",
        default=str(ROOT / "workspace" / "memory.sqlite3"),
        help="SQLite path for sources, memories, runs and feedback",
    )
    args = parser.parse_args(argv)
    store = Store(args.db)
    if not store.memories() and not store.sources():
        store.seed(ROOT / "data" / "memories.json")
    serve_mcp(store)


if __name__ == "__main__":
    raise SystemExit(main())
