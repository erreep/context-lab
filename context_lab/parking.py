from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from .store import GLOBAL_PROJECT, new_id, now


class ParkingError(ValueError):
    pass


@dataclass(frozen=True)
class NewTicket:
    pass


@dataclass(frozen=True)
class ExistingTicket:
    ticket: str


Destination = NewTicket | ExistingTicket


def _item_id(prefix="park"):
    return new_id(prefix)


def _default_capture_key():
    return uuid.uuid4().hex


def _fingerprint(operation, item_id, **parts):
    payload = {"operation": operation, "item_id": item_id, **parts}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _row_to_item(row):
    item = {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "body": row["body"],
        "later": row["later"],
        "captured_while_ticket": row["captured_while_ticket"],
        "capture_key": row["capture_key"],
        "created_at": row["created_at"],
        "state": row["state"],
    }
    if row["state"] == "started":
        item["destination_ticket"] = row["destination_ticket"]
        item["source_id"] = row["source_id"]
        item["candidate_id"] = row["candidate_id"]
        item["decided_at"] = row["decided_at"]
    elif row["state"] == "dismissed":
        item["decided_at"] = row["decided_at"]
    return item


class ParkingLot:
    def __init__(self, store):
        self._store = store

    def capture(self, *, project, title, body, later="", captured_by, captured_while_ticket="", capture_key=None):
        project = (project or "").strip()
        if not project or project == GLOBAL_PROJECT:
            raise ParkingError("Parking requires a real project; __global__ is not allowed")
        if not isinstance(title, str) or not title.strip():
            raise ParkingError("title required")
        if not isinstance(body, str) or not body.strip():
            raise ParkingError("body required")
        later = (later or "").strip() if isinstance(later, str) else ""
        captured_by = (captured_by or "").strip() or "local"
        captured_while_ticket = (captured_while_ticket or "").strip()
        key = (capture_key or "").strip() or _default_capture_key()
        existing = self._store._parking_find_capture(project, captured_by, key)
        if existing:
            return _row_to_item(existing)
        row = {
            "id": _item_id(),
            "project": project,
            "title": title.strip(),
            "body": body.strip(),
            "later": later,
            "captured_by": captured_by,
            "captured_while_ticket": captured_while_ticket,
            "capture_key": key,
            "created_at": now(),
            "state": "parked",
        }
        self._store._parking_insert(row)
        return _row_to_item(row)

    def list(self, *, project, state="parked", limit=50):
        project = (project or "").strip()
        if not project:
            raise ParkingError("project required")
        rows = self._store._parking_list(project, state=state, limit=limit)
        return [_row_to_item(r) for r in rows]

    def count(self, project, *, state="parked"):
        return self._store._parking_count((project or "").strip(), state=state)

    def get(self, item_id):
        row = self._store._parking_get(item_id)
        if not row:
            raise ParkingError(f"Unknown parked item: {item_id}")
        return _row_to_item(row)

    def start(self, item_id, destination: Destination, *, command_id, reviewer="local"):
        command_id = (command_id or "").strip()
        if not command_id:
            raise ParkingError("command_id required")
        reviewer = (reviewer or "").strip() or "local"
        row = self._store._parking_get(item_id)
        if not row:
            raise ParkingError(f"Unknown parked item: {item_id}")
        if isinstance(destination, ExistingTicket):
            dest_ticket = destination.ticket.strip()
            if not dest_ticket:
                raise ParkingError("destination ticket required")
        else:
            dest_ticket = None
        new_ticket = isinstance(destination, NewTicket)
        fp = _fingerprint("start", item_id, new_ticket=new_ticket, ticket=dest_ticket or "")
        try:
            replay = self._store._parking_command_replay(command_id, item_id, "start", fp)
        except ValueError as e:
            raise ParkingError(str(e)) from e
        if replay:
            return json.loads(replay)
        if row["state"] == "started":
            raise ParkingError(
                f"Item already started on ticket {row['destination_ticket']}; "
                "use a new parked item or retract the candidate in review"
            )
        if row["state"] != "parked":
            raise ParkingError(f"Item is {row['state']}, not parked")
        try:
            return self._store._parking_start_transaction(
                row, new_ticket=new_ticket, dest_ticket=dest_ticket,
                command_id=command_id, fingerprint=fp, reviewer=reviewer,
            )
        except ValueError as e:
            raise ParkingError(str(e)) from e

    def dismiss(self, item_id, *, command_id, reviewer="local"):
        command_id = (command_id or "").strip()
        if not command_id:
            raise ParkingError("command_id required")
        reviewer = (reviewer or "").strip() or "local"
        row = self._store._parking_get(item_id)
        if not row:
            raise ParkingError(f"Unknown parked item: {item_id}")
        fp = _fingerprint("dismiss", item_id)
        try:
            replay = self._store._parking_command_replay(command_id, item_id, "dismiss", fp)
        except ValueError as e:
            raise ParkingError(str(e)) from e
        if replay:
            return _row_to_item(json.loads(replay))
        if row["state"] == "dismissed":
            return _row_to_item(row)
        if row["state"] == "started":
            raise ParkingError("Started items cannot be dismissed; retract the candidate in review")
        item = self._store._parking_dismiss_transaction(row, command_id=command_id, fingerprint=fp, reviewer=reviewer)
        return _row_to_item(item)


def compact_item(item):
    out = {
        "id": item["id"],
        "project": item["project"],
        "title": item["title"],
        "state": item["state"],
        "created_at": item["created_at"],
    }
    if item.get("later"):
        out["later"] = item["later"]
    if item["state"] == "started":
        out["destination_ticket"] = item["destination_ticket"]
        out["candidate_id"] = item["candidate_id"]
    return out


def triage_command(project):
    return {"command": f"context-lab parking list --project {project}"}
