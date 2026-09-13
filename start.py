"""One-command local demo launcher. Existing user data is never replaced."""
import argparse
from context_lab.engine import DATA_ROOT, DEFAULT_DB
from context_lab.server import serve
from context_lab.store import Store

parser = argparse.ArgumentParser(description="Start the local Context Lab demo")
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--db", default=str(DEFAULT_DB))
args = parser.parse_args()
store = Store(args.db)
if not store.memories() and not store.sources():
    store.seed(DATA_ROOT / "memories.json")
store.close()
serve(args.db, port=args.port)
