"""Run Context Lab from an installed plugin or source checkout."""
import shutil
import subprocess
import sys
from pathlib import Path

executable = shutil.which("context-lab")
if executable:
    raise SystemExit(subprocess.call([executable, *sys.argv[1:]]))

for parent in Path(__file__).resolve().parents:
    if (parent / "context_lab" / "__init__.py").is_file():
        sys.path.insert(0, str(parent))
        break

from context_lab.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
