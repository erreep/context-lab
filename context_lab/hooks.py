"""Worktree-scoped harness hooks: scope, ambient inject, session-start, git lease (later)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import agent_api
from .engine import ROOT
from .schemas import GATE_TEXT, AgentError, wire_dumps
from .store import Store

INJECT_BUDGET = 400
SCOPE_HINT = "Run: python3 -m context_lab hook set-scope --project P --ticket T"


def _git(args, cwd=None, check=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise AgentError(
            "not_a_worktree",
            (result.stderr or result.stdout or "git failed").strip().splitlines()[-1]
            if (result.stderr or result.stdout) else "git failed",
            hint=SCOPE_HINT,
        )
    return result


def git_path(name, cwd=None):
    out = _git(["rev-parse", "--git-path", name], cwd=cwd).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return path.resolve()


def require_worktree(cwd=None):
    _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return cwd or os.getcwd()


def default_db():
    # ponytail: db identity is the resolved path in v1; upgrade to a stored id in knowledge_bases.
    return str((ROOT / "workspace" / "memory.sqlite3").resolve())


def scope_path(cwd=None):
    return git_path("context-lab", cwd=cwd) / "scope.json"


def load_scope(cwd=None):
    path = scope_path(cwd=cwd)
    if not path.is_file():
        raise AgentError("missing_scope", "No context-lab scope for this worktree", hint=SCOPE_HINT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AgentError("missing_scope", f"Invalid scope.json: {e}", hint=SCOPE_HINT) from e
    for key in ("project", "ticket", "db"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise AgentError("missing_scope", f"scope.json missing {key}", hint=SCOPE_HINT)
    # ponytail: db identity is the resolved path in v1; upgrade to a stored id in knowledge_bases.
    data["db"] = str(Path(data["db"]).expanduser().resolve())
    return data


def set_scope(project, ticket, db=None, cwd=None):
    require_worktree(cwd)
    # ponytail: db identity is the resolved path in v1; upgrade to a stored id in knowledge_bases.
    resolved_db = str(Path(db or default_db()).expanduser().resolve())
    path = scope_path(cwd=cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"project": project.strip(), "ticket": ticket.strip(), "db": resolved_db}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _read_hook_stdin(raw=None):
    text = sys.stdin.read() if raw is None else raw
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("hook stdin must be a JSON object")
    return data


def _cwd_from_payload(payload, fallback=None):
    cwd = payload.get("cwd") or fallback or os.getcwd()
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("cwd required")
    return cwd


def inject(payload=None, event_name="UserPromptSubmit"):
    payload = payload if payload is not None else _read_hook_stdin()
    cwd = _cwd_from_payload(payload)
    try:
        scope = load_scope(cwd=cwd)
    except AgentError as e:
        print(e.hint or SCOPE_HINT, file=sys.stderr)
        return 0
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str):
        prompt = str(prompt)
    store = Store(scope["db"])
    try:
        view = agent_api.context(
            store,
            {"query": prompt, "project": scope["project"], "ticket": scope["ticket"]},
            budget=INJECT_BUDGET,
            detail="agent",
        )
    finally:
        store.close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": wire_dumps(view),
        }
    }, ensure_ascii=False))
    return 0


def session_start(payload=None):
    payload = payload if payload is not None else _read_hook_stdin()
    cwd = _cwd_from_payload(payload)
    try:
        scope = load_scope(cwd=cwd)
    except AgentError as e:
        print(e.hint or SCOPE_HINT, file=sys.stderr)
        return 0
    store = Store(scope["db"])
    try:
        # Baseline + __global__ only: omit ticket so ticket decisions stay out.
        view = agent_api.context(
            store,
            {
                "query": "standing rules and hard gates for this session",
                "project": scope["project"],
            },
            budget=INJECT_BUDGET,
            detail="agent",
        )
    finally:
        store.close()
    text = GATE_TEXT.strip() + "\n\n" + wire_dumps(view)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }, ensure_ascii=False))
    return 0


def build_parser(sub):
    hook = sub.add_parser("hook", help="Harness hooks: scope, inject, session-start")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    scope_p = hook_sub.add_parser("set-scope", help="Bind project/ticket/db to this worktree")
    scope_p.add_argument("--project", required=True)
    scope_p.add_argument("--ticket", required=True)
    scope_p.add_argument("--db", default=None)
    hook_sub.add_parser("inject", help="UserPromptSubmit ambient CompactView injection")
    hook_sub.add_parser("session-start", help="SessionStart standing rules + gate text")
    return hook


def dispatch(args):
    if args.hook_command == "set-scope":
        print(json.dumps(set_scope(args.project, args.ticket, db=args.db), indent=2))
        return 0
    if args.hook_command == "inject":
        return inject()
    if args.hook_command == "session-start":
        return session_start()
    raise SystemExit(f"unknown hook command: {args.hook_command}")
