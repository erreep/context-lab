"""The built wheel must work without the source checkout."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from context_lab.engine import ROOT


class InstallSmokeTests(unittest.TestCase):
    def test_wheel_mcp_and_workbench(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(
                    ".git", ".venv", "build", "workspace", "*.egg-info", "__pycache__",
                    ".cursor", ".agents", "results",
                ),
            )
            wheel_dir = temp / "wheel"
            wheel_dir.mkdir()
            built = subprocess.run(
                [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheel_dir)],
                cwd=source,
                capture_output=True,
                text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            venv = temp / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            executable = venv / ("Scripts/context-lab.exe" if os.name == "nt" else "bin/context-lab")
            pip = venv / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
            subprocess.run([str(pip), "install", "--no-deps", str(next(wheel_dir.glob("*.whl")))], check=True, capture_output=True)
            home = temp / "home"
            home.mkdir()
            env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
            subprocess.run([str(executable), "demo"], check=True, capture_output=True, text=True, env=env)
            db = home / ".context-lab" / "memory.sqlite3"
            self.assertTrue(db.is_file())

            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"cwd": str(source), "task": {"project": "fieldnote", "query": "Change button color", "as_of": "2026-09-12"}}}},
            ]
            result = subprocess.run(
                [str(executable), "--db", str(db), "mcp"],
                input="\n".join(map(json.dumps, messages)) + "\n",
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout.splitlines()[-1])["result"]["isError"])

            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server = subprocess.Popen(
                [str(executable), "--db", str(db), "serve", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=1) as response:
                            self.assertIn("Context Lab", response.read().decode())
                        break
                    except urllib.error.URLError:
                        if server.poll() is not None or time.monotonic() >= deadline:
                            self.fail(server.stderr.read() or "installed workbench did not start")
                        time.sleep(0.05)
            finally:
                server.terminate()
                server.wait(timeout=5)
                server.stdout.close()
                server.stderr.close()


if __name__ == "__main__":
    unittest.main()
