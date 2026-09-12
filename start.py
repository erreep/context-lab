"""One-command local demo launcher. Existing user data is never replaced."""
import argparse
from context_lab.engine import ROOT
from context_lab.server import serve
from context_lab.store import Store

parser = argparse.ArgumentParser(description="Start the local Context Lab demo")
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--db", default=str(ROOT / "workspace/memory.sqlite3"))
args = parser.parse_args()
store = Store(args.db)
if not store.memories() and not store.sources():
    store.seed(ROOT / "data/memories.json")
# Best-effort: mirror sparse lab-wide standing rules into local Mem0 when available.
try:
    from context_lab.mem0_bridge import sync_lab_wide
    sync_lab_wide(store)
except Exception:
    pass
store.close()
serve(args.db, port=args.port)
