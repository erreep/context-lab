"""Hermetic git subprocess helpers for tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_env(home: str | Path, extra: dict | None = None) -> dict:
    home = str(Path(home).resolve())
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "SSH_AUTH_SOCK")
    }
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
    env["GIT_CONFIG_VALUE_0"] = "false"
    env["GIT_CONFIG_KEY_1"] = "tag.gpgsign"
    env["GIT_CONFIG_VALUE_1"] = "false"
    if extra:
        env.update(extra)
    return env


def run_git(
    cwd: str | Path,
    *args: str,
    home: str | Path,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=text,
        env=git_env(home, env_extra),
    )
