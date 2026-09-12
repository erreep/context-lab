"""Once-per-scope setup and local Markdown/text snapshots. No vault writes or models."""
import hashlib
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .store import now, scope_key

# Lab-wide Obsidian/journal root (first OS touch for this DB). Not a retrieval layer.
LAB_VAULT_PROJECT = "__lab__"


def allocate_ticket():
    # Same-second callers need a unique suffix (Astra collision report).
    return "work-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def empty_kb_record(project, ticket=""):
    timestamp = now()
    return {"project": project, "ticket": ticket, "path": None,
            "initialized_at": timestamp, "indexed_at": timestamp, "documents": [],
            "note_count": 0, "chunk_count": 0, "categories": {}, "skipped": {}}


def discover_obsidian_vaults():
    """Return [{path, ts, open}] from Obsidian's config, then shallow common folders.

    Prefer reading obsidian.json (O(vaults)) over grepping the disk.
    """
    found = {}
    config_paths = [
        Path.home() / "Library/Application Support/obsidian/obsidian.json",
        Path.home() / ".config/obsidian/obsidian.json",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        config_paths.append(Path(appdata) / "obsidian" / "obsidian.json")
    for config in config_paths:
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        vaults = data.get("vaults")
        if not isinstance(vaults, dict):
            continue
        for meta in vaults.values():
            if not isinstance(meta, dict):
                continue
            raw = meta.get("path")
            if not isinstance(raw, str) or not raw.strip():
                continue
            root = Path(raw).expanduser()
            if not root.is_dir():
                continue
            key = str(root.resolve())
            found[key] = {
                "path": key,
                "ts": int(meta.get("ts") or 0),
                "open": bool(meta.get("open")),
            }
    # Shallow fallback: one level under common parents looking for .obsidian/
    for parent in (Path.home() / "Documents" / "Vaults", Path.home() / "Documents", Path.home() / "Obsidian"):
        if not parent.is_dir():
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or not (child / ".obsidian").is_dir():
                continue
            key = str(child.resolve())
            found.setdefault(key, {"path": key, "ts": 0, "open": False})
    return sorted(found.values(), key=lambda v: (not v["open"], -v["ts"], v["path"]))


def pick_discovered_vault(vaults):
    if not vaults:
        return None
    open_ones = [v for v in vaults if v["open"]]
    return (open_ones or vaults)[0]["path"]


def vault_binding(store, project=None):
    """Lab-wide vault/journal root for this DB (ignores project; kept for call-site compat)."""
    baseline = store.knowledge_base(LAB_VAULT_PROJECT, "")
    if not baseline:
        return {"state": "undecided", "path": None}
    if baseline.get("vault_declined"):
        return {"state": "declined", "path": None}
    raw = baseline.get("vault_path")
    if isinstance(raw, str) and raw.strip():
        return {"state": "bound", "path": str(Path(raw).expanduser().resolve())}
    return {"state": "undecided", "path": None}


def obsidian_info(binding, auto_detected=False):
    """Always-on initiate field. Journaling available only when a journal root is bound."""
    if binding["state"] == "bound":
        reason = f"Obsidian vault / journal root bound at {binding['path']}."
        if auto_detected:
            reason = f"Auto-detected Obsidian vault at {binding['path']} and bound it for this machine."
        return {
            "journaling": "available",
            "reason": reason,
            "hint": "Session journals (opt-in) write under {vault}/Cl/{datetime}/. Importer skips Cl/.",
            "auto_detected": auto_detected,
        }
    if binding["state"] == "declined":
        return {
            "journaling": "unavailable",
            "reason": "No Obsidian vault or journal location provided, so Obsidian journaling is not available.",
            "hint": "To enable journaling later, pass knowledge.vault as a vault root or any folder where journals may be saved, or 'none' to keep journaling off.",
            "auto_detected": False,
        }
    return {
        "journaling": "unavailable",
        "reason": "No Obsidian vault found on this machine, so Obsidian journaling is not available.",
        "hint": "Provide knowledge.vault as your Obsidian vault root (or any folder for journals), or 'none' if you do not want journaling.",
        "auto_detected": False,
    }


def apply_vault_binding(store, vault, project=None):
    """Persist lab-wide vault/journal-root decision. vault is a path string or 'none'."""
    if not isinstance(vault, str) or not vault.strip():
        raise ValueError("knowledge.vault must be a nonempty path or 'none'")
    baseline = store.knowledge_base(LAB_VAULT_PROJECT, "") or empty_kb_record(LAB_VAULT_PROJECT, "")
    if vault.strip().lower() == "none":
        baseline["vault_path"] = None
        baseline["vault_declined"] = True
        baseline["vault_auto_detected"] = False
    else:
        root = Path(vault).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("knowledge.vault must be an existing directory (vault root or journal location)")
        baseline["vault_path"] = str(root)
        baseline["vault_declined"] = False
        baseline["vault_auto_detected"] = False
    store.put_knowledge_base(baseline)
    return vault_binding(store)


def ensure_lab_vault(store, vault_arg=None):
    """Resolve lab vault on first OS touch: explicit arg, else auto-detect, else undecided.

    Returns (binding, auto_detected). Explicit vault_arg always wins (attach after decline).
    """
    if vault_arg is not None:
        return apply_vault_binding(store, vault_arg), False
    binding = vault_binding(store)
    if binding["state"] != "undecided":
        return binding, False
    discovered = pick_discovered_vault(discover_obsidian_vaults())
    if discovered:
        baseline = store.knowledge_base(LAB_VAULT_PROJECT, "") or empty_kb_record(LAB_VAULT_PROJECT, "")
        baseline["vault_path"] = discovered
        baseline["vault_declined"] = False
        baseline["vault_auto_detected"] = True
        store.put_knowledge_base(baseline)
        return vault_binding(store), True
    return binding, False


def require_journal_available(store, project=None):
    binding = vault_binding(store)
    info = obsidian_info(binding)
    if info["journaling"] != "available":
        raise ValueError(info["reason"] + " " + info["hint"])
    return Path(binding["path"])


def with_obsidian(result, store, project=None, auto_detected=False):
    binding = vault_binding(store)
    out = dict(result)
    out["obsidian"] = obsidian_info(binding, auto_detected=auto_detected)
    if binding["path"]:
        out["vault_path"] = binding["path"]
    return out


CATEGORIES = {
    "requirements": r"requirement|acceptance|specification|user stor",
    "decisions": r"decision|\badr\b",
    "architecture": r"architect|design|schema",
    "testing": r"test|verif|\bqa\b",
    "investigation": r"investigat|debug|incident|troubleshoot",
    "setup": r"setup|install|getting started|configuration",
}


def sections(text):
    heading, lines, fence = "Overview", [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        title = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if title:
            if "".join(lines).strip():
                yield heading, "".join(lines)
            heading, lines = title[1], []
        lines.append(line)
    if "".join(lines).strip():
        yield heading, "".join(lines)


def chunks(text):
    start = 0
    while start < len(text):
        end = min(len(text), start + 1200)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if boundary > start:
                end = boundary + 1
        if text[start:end].strip():
            yield text[start:end].strip()
        start = end


def summary(record, status):
    return dict({k: v for k, v in record.items() if k != "documents"}, status=status)


def initiate(store, project, ticket="", path=None, empty=False, refresh=False):
    project, ticket = scope_key({"project": project, "ticket": ticket})
    if not isinstance(empty, bool) or not isinstance(refresh, bool):
        raise ValueError("empty and refresh must be booleans")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise ValueError("path must be a nonempty folder path")
    if empty and path is not None:
        raise ValueError("Choose a folder or an empty knowledge base")
    previous = store.knowledge_base(project, ticket)
    if previous and not refresh:
        return summary(previous, "already_initialized")
    if refresh and not previous:
        raise ValueError("Initialize this project/ticket before refreshing")
    if refresh and path is None and not empty:
        path = previous["path"]
        empty = path is None
    if path is None and not empty:
        return {"status": "needs_knowledge_base", "project": project, "ticket": ticket}

    timestamp = now()
    record = {"project": project, "ticket": ticket, "path": None,
              "initialized_at": previous["initialized_at"] if previous else timestamp,
              "indexed_at": timestamp, "documents": [], "note_count": 0,
              "chunk_count": 0, "categories": {}, "skipped": {}}
    sources, categories, skipped = [], Counter(), Counter()
    total_bytes = 0
    if path is not None:
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Choose a folder containing Markdown or text notes")
        record["path"] = str(root)

        def walk_error(error):
            raise error

        for directory, folders, files in os.walk(root, onerror=walk_error, followlinks=False):
            folders[:] = sorted(d for d in folders if not d.startswith(".")
                                and d not in {"node_modules", "Cl"}
                                and not (Path(directory) / d).is_symlink())
            for filename in sorted(files):
                file = Path(directory) / filename
                if filename.startswith(".") or file.is_symlink() or file.suffix.lower() not in {".md", ".markdown", ".txt"}:
                    skipped["hidden_symlink_or_unsupported"] += 1
                    continue
                # Do not follow references or links outside the selected folder.
                file.resolve(strict=True).relative_to(root)
                with file.open("rb") as stream:
                    data = stream.read(2_000_001)
                total_bytes += len(data)
                if len(data) > 2_000_000 or total_bytes > 20_000_000:
                    raise ValueError("Knowledge base exceeds 2 MB per note or 20 MB total; choose a smaller folder")
                try:
                    body = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise ValueError(f"Note is not UTF-8 text: {file.relative_to(root)}") from None
                if not body.strip():
                    skipped["empty"] += 1
                    continue
                relative = file.relative_to(root).as_posix()
                digest = hashlib.sha256(body.encode()).hexdigest()
                sid = "kb-src-" + hashlib.sha256(json.dumps([project, ticket, str(file), digest]).encode()).hexdigest()
                sources.append((sid, project, relative, body, timestamp, digest, ticket))
                for section_index, (heading, section) in enumerate(sections(body)):
                    # ponytail: folder/heading keyword categories; add reviewed labels if these prove too coarse.
                    category = next((name for label in (heading.lower(), relative.lower())
                                     for name, pattern in CATEGORIES.items() if re.search(pattern, label)), "reference")
                    for index, chunk in enumerate(chunks(section)):
                        mid = "kb-doc-" + hashlib.sha256(json.dumps([sid, section_index, index]).encode()).hexdigest()
                        record["documents"].append({"id": mid, "version": 1, "project": project, "ticket": ticket,
                            "kind": "document", "status": "indexed", "title": relative + " · " + heading,
                            "claim": chunk, "path": relative, "heading": heading, "category": category,
                            "topics": [category, relative, heading], "source_ids": [sid], "need_tags": [],
                            "valid_from": timestamp[:10], "recorded_at": timestamp})
                        categories[category] += 1
                if len(record["documents"]) > 20_000:
                    raise ValueError("Knowledge base exceeds 20,000 sections; choose a smaller folder")
        if not sources:
            raise ValueError("No nonempty Markdown/text notes found; choose another folder or start empty")

    record.update(note_count=len(sources), chunk_count=len(record["documents"]),
                  categories=dict(categories), skipped=dict(skipped))
    # Snapshot sources and mark initialization complete together, including concurrent calls.
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        current = store.knowledge_base(project, ticket)
        if current and not refresh:
            return summary(current, "already_initialized")
        if refresh and current != previous:
            raise ValueError("Knowledge base changed during refresh; retry")
        store.db.executemany(
            "INSERT OR IGNORE INTO sources (id,project,title,body,created_at,sha256,ticket) VALUES (?,?,?,?,?,?,?)", sources)
        store.db.execute("INSERT INTO knowledge_bases VALUES (?,?,?) ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
                         (project, ticket, json.dumps(record)))
    return summary(record, "refreshed" if refresh else "initialized")
