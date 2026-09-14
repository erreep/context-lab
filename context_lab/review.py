"""Start or reuse the local review UI. URL strings exist only after a matching health check."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO
from urllib.parse import urlencode

REVIEW_HOST = "127.0.0.1"
REVIEW_PORT = 8765
SERVICE_NAME = "context-lab"
_READY_WAIT_S = 3.0
_PROBE_TIMEOUT_S = 0.4


class ServerOrigin(str, Enum):
    STARTED = "started"
    REUSED = "reused"


class BrowserOrigin(str, Enum):
    OPENED = "opened"
    MANUAL = "manual"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ReviewRoute:
    project: str
    ticket: str = ""


@dataclass(frozen=True)
class ReviewReady:
    url: str
    origin: ServerOrigin
    browser: BrowserOrigin

    @property
    def exit_code(self) -> int:
        return 0


@dataclass(frozen=True)
class ReviewUnavailable:
    reason: str
    retry_command: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return 1


ReviewOutcome = ReviewReady | ReviewUnavailable


def database_id(db_path: str | Path) -> str:
    resolved = str(Path(db_path).expanduser().resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:16]


def health_payload(db_path: str | Path) -> dict:
    return {"service": SERVICE_NAME, "database": database_id(db_path)}


def review_url(port: int, route: ReviewRoute | None = None) -> str:
    base = f"http://{REVIEW_HOST}:{port}/"
    if route is None or not route.project:
        return base
    query = {"project": route.project}
    if route.ticket:
        query["ticket"] = route.ticket
    return base + "?" + urlencode(query)


def retry_argv(db_path: str | Path, route: ReviewRoute | None = None) -> tuple[str, ...]:
    argv = ["context-lab", "--db", str(Path(db_path).expanduser()), "review"]
    if route and route.project:
        argv.extend(["--project", route.project])
        if route.ticket:
            argv.extend(["--ticket", route.ticket])
    return tuple(argv)


def _candidate_ports(db_path: str | Path, preferred: int = REVIEW_PORT) -> tuple[int, ...]:
    digest = int(database_id(db_path)[:4], 16)
    extras = tuple(REVIEW_PORT + 1 + ((digest + i) % 20) for i in range(5))
    ordered = (preferred,) + tuple(p for p in extras if p != preferred)
    return ordered


def _probe(port: int, expected_id: str) -> bool:
    req = urllib.request.Request(
        f"http://{REVIEW_HOST}:{port}/api/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
        return False
    return isinstance(body, dict) and body.get("service") == SERVICE_NAME and body.get("database") == expected_id


def _find_live(db_path: str | Path, preferred: int = REVIEW_PORT) -> int | None:
    expected = database_id(db_path)
    for port in _candidate_ports(db_path, preferred):
        if _probe(port, expected):
            return port
    return None


def _spawn(db_path: str | Path, port: int) -> subprocess.Popen:
    log_dir = Path(db_path).expanduser().resolve().parent
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "review-serve.log"
    log_f = open(log_path, "a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "context_lab", "--db", str(Path(db_path).expanduser()), "serve", "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        log_f.close()


def _wait_live(db_path: str | Path, port: int, timeout: float = _READY_WAIT_S) -> bool:
    expected = database_id(db_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _probe(port, expected):
            return True
        time.sleep(0.05)
    return False


def open_review(
    db_path: str | Path,
    route: ReviewRoute | None = None,
    *,
    open_browser: bool = True,
    preferred_port: int = REVIEW_PORT,
) -> ReviewOutcome:
    """Reuse or start a matching local serve, then return a verified URL outcome."""
    db_path = Path(db_path).expanduser()
    live = _find_live(db_path, preferred_port)
    origin = ServerOrigin.REUSED
    port = live
    if port is None:
        port = preferred_port
        try:
            _spawn(db_path, port)
        except OSError as e:
            return ReviewUnavailable(reason=str(e), retry_command=retry_argv(db_path, route))
        if not _wait_live(db_path, port):
            # Bind race: another matching server may have won.
            port = _find_live(db_path, preferred_port)
            if port is None:
                return ReviewUnavailable(
                    reason="review UI did not become ready",
                    retry_command=retry_argv(db_path, route),
                )
            origin = ServerOrigin.REUSED
        else:
            origin = ServerOrigin.STARTED
            # Confirm identity in case something else bound the port first.
            if not _probe(port, database_id(db_path)):
                adopted = _find_live(db_path, preferred_port)
                if adopted is None:
                    return ReviewUnavailable(
                        reason="port is busy with a different review database or service",
                        retry_command=retry_argv(db_path, route),
                    )
                port = adopted
                origin = ServerOrigin.REUSED

    url = review_url(port, route)
    browser = BrowserOrigin.SKIPPED
    if open_browser:
        try:
            browser = BrowserOrigin.OPENED if webbrowser.open(url) else BrowserOrigin.MANUAL
        except Exception:
            browser = BrowserOrigin.MANUAL
    return ReviewReady(url=url, origin=origin, browser=browser)


def format_outcome(outcome: ReviewOutcome) -> str:
    if isinstance(outcome, ReviewReady):
        lines = [f"  Review UI: live at {outcome.url}"]
        if outcome.browser == BrowserOrigin.OPENED:
            lines.append("  Browser open requested.")
        elif outcome.browser == BrowserOrigin.MANUAL:
            lines.append("  Open this URL in a browser.")
        return "\n".join(lines)
    cmd = " ".join(outcome.retry_command)
    return (
        f"  Review UI: not running ({outcome.reason}).\n"
        f"  Run: {cmd}"
    )


def print_install_handoff(
    db_path: str | Path,
    route: ReviewRoute | None = None,
    *,
    out: TextIO | None = None,
) -> ReviewOutcome:
    """Install Next block: spawn only on a TTY; otherwise print the review command."""
    out = out or sys.stdout
    if not out.isatty() or os.environ.get("CONTEXT_LAB_REVIEW") == "0":
        cmd = " ".join(retry_argv(db_path, route))
        print(f"  Review UI: start with `{cmd}` (then open the URL it prints).", file=out)
        return ReviewUnavailable(reason="non-interactive install", retry_command=retry_argv(db_path, route))
    outcome = open_review(db_path, route, open_browser=True)
    print(format_outcome(outcome), file=out)
    return outcome


def inbox_state(store, project: str | None = None, ticket: str | None = None) -> dict:
    """Derive empty-inbox copy from store rows. No persistent first-run flag."""
    if project is None:
        memories = store.memories()
        sources = store.sources()
    else:
        memories = store.memories(project, ticket if ticket is not None else "")
        sources = store.sources(project, ticket if ticket is not None else "")
    waiting = sum(1 for m in memories if m.get("status") == "candidate")
    settled = sum(1 for m in memories if m.get("status") in {"confirmed", "retracted"})
    source_n = len(sources)
    if waiting > 0:
        stage = "waiting"
        headline = f"{waiting} waiting to confirm"
        body = "Confirm candidates before they affect recall."
    elif settled == 0 and source_n == 0:
        stage = "fresh"
        headline = "You're set up. Nothing to review yet."
        body = (
            "Agents propose lessons here when they learn something. "
            "Each stays out of recall until you confirm it. "
            "An empty inbox is normal on a new lab. "
            "Optional: run `context-lab demo` and reload to see sample candidates."
        )
    else:
        stage = "caught_up"
        headline = "Inbox zero."
        body = (
            f"This database has {settled} settled memories and {source_n} sources. "
            "New candidates appear here as agents propose them."
        )
    return {
        "stage": stage,
        "waiting": waiting,
        "settled": settled,
        "sources": source_n,
        "headline": headline,
        "body": body,
    }
