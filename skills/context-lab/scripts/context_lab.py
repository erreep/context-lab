"""Run the repository CLI from the installed skill, independent of the caller's cwd."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from context_lab.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
