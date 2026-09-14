"""Once-per-scope setup and local Markdown/text snapshots. No vault writes or models."""
import hashlib
import json
import os
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .store import now, scope_key

# Lab-wide Obsidian/journal root (first OS touch for this DB). Not a retrieval layer.
LAB_VAULT_PROJECT = "__lab__"
JOURNAL_ROOT = "Context Lab"
SEED_NOTE = "README.md"
MAX_SEGMENT = 64
WIN_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})
NEXT_STEP = {
    "unavailable": "Pass knowledge.vault=<folder>, or 'none' to keep journaling off.",
    "vault_only_no_ticket": "Journaling needs a ticket; call memory_allocate_ticket.",
    "vault_only_taken": "Pass knowledge.path to bind a folder for this ticket explicitly.",
}


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


class Readiness(str, Enum):
    """Can this scope write a journal note right now? Derived, never stored."""

    unavailable = "unavailable"
    vault_only = "vault_only"
    ready = "ready"


@dataclass(frozen=True)
class JournalHome:
    """Where journaling for exactly one scope lives.

    notes is set iff readiness is ready. vault is set iff the lab vault is bound
    (ready+declined import is the one case with notes and no vault).
    """

    project: str
    ticket: str
    readiness: Readiness
    vault: Path | None
    notes: Path | None


def vault_segment(text):
    """One path segment from an agent-supplied scope name. Trust boundary."""
    text = unicodedata.normalize("NFC", str(text))
    cleaned = []
    for ch in text:
        if ch in '\\/<>:"|?*' or ord(ch) < 32:
            cleaned.append("-")
        else:
            cleaned.append(ch)
    segment = re.sub(r"-+", "-", "".join(cleaned)).strip(".- ")
    segment = segment[:MAX_SEGMENT].strip(".- ")
    if not segment or segment in {".", ".."} or segment.upper() in WIN_RESERVED:
        raise ValueError("unsafe vault segment")
    return segment


def next_step_for(home):
    if home.readiness is Readiness.unavailable:
        return NEXT_STEP["unavailable"]
    if not home.ticket:
        return NEXT_STEP["vault_only_no_ticket"]
    return NEXT_STEP["vault_only_taken"]


def journal_home(store, project, ticket=""):
    """Derive this scope's journal home. The single readiness predicate."""
    project, ticket = scope_key({"project": project, "ticket": ticket})
    binding = vault_binding(store)
    vault = Path(binding["path"]) if binding["state"] == "bound" and binding["path"] else None
    kb = store.knowledge_base(project, ticket)
    raw = kb.get("path") if kb else None
    if isinstance(raw, str) and raw.strip():
        notes = Path(raw).expanduser().resolve()
        return JournalHome(project, ticket, Readiness.ready, vault, notes)
    if binding["state"] != "bound":
        return JournalHome(project, ticket, Readiness.unavailable, None, None)
    return JournalHome(project, ticket, Readiness.vault_only, vault, None)


def seed_body(project, ticket):
    return (
        f"# {project} / {ticket}\n\n"
        "Ticket notes folder. Agents add plan, decision, progress, and handoff files under journal/.\n"
    )


def _path_taken(store, folder, project, ticket):
    # ponytail: O(scopes) scan of knowledge_bases(); unique index on path if this ever matters.
    target = folder.resolve()
    for kb in store.knowledge_bases():
        if kb.get("project") == LAB_VAULT_PROJECT:
            continue
        if (kb.get("project"), kb.get("ticket")) == (project, ticket):
            continue
        raw = kb.get("path")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            if Path(raw).expanduser().resolve() == target:
                return True
        except OSError:
            continue
    return False


def ensure_journal_home(store, project, ticket=""):
    """Move vault_only to ready when only a folder is missing. Returns (home, created)."""
    project, ticket = scope_key({"project": project, "ticket": ticket})
    home = journal_home(store, project, ticket)
    if home.readiness is Readiness.ready or home.readiness is not Readiness.vault_only or not ticket:
        return home, False
    try:
        folder = (home.vault / JOURNAL_ROOT / vault_segment(project) / vault_segment(ticket)).resolve()
        folder.relative_to(home.vault.resolve())
    except ValueError:
        return JournalHome(project, ticket, Readiness.vault_only, home.vault, None), False
    created = False
    try:
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True)
            created = True
        seed = folder / SEED_NOTE
        fd = os.open(str(seed), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, seed_body(project, ticket).encode())
        finally:
            os.close(fd)
        created = True
    except FileExistsError:
        pass
    except OSError:
        return JournalHome(project, ticket, Readiness.vault_only, home.vault, None), False
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if _path_taken(store, folder, project, ticket):
            return JournalHome(project, ticket, Readiness.vault_only, home.vault, None), False
        record = store.knowledge_base(project, ticket) or empty_kb_record(project, ticket)
        record["path"] = str(folder)
        store.db.execute(
            "INSERT INTO knowledge_bases VALUES (?,?,?) ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
            (project, ticket, json.dumps(record)))
    return JournalHome(project, ticket, Readiness.ready, home.vault, folder), created


def require_journal_home(store, project, ticket=""):
    """ensure_journal_home, but the returned home is writable or this raises."""
    home, created = ensure_journal_home(store, project, ticket)
    if home.readiness is Readiness.ready and home.notes is not None:
        return home, created
    raise ValueError(next_step_for(home))


def announce_line(notes, *, vault, vault_auto_bound, folder_created):
    if not vault_auto_bound and not folder_created:
        return None
    bits = []
    if vault_auto_bound and vault is not None:
        bits.append(f"Auto-bound vault {vault}")
    if folder_created and notes is not None:
        rel = notes.relative_to(vault).as_posix() if vault is not None else str(notes)
        bits.append(("created " if bits else "Created ") + rel)
    if not bits:
        return None
    return " and ".join(bits) + ". Tell the user."


def obsidian_info(home, auto_detected=False, folder_created=False):
    """Always-on initiate field. journaling is ready|vault_only|unavailable."""
    info = {"journaling": home.readiness.value}
    if home.readiness is Readiness.ready:
        info["notes"] = str(home.notes)
        announce = announce_line(
            home.notes, vault=home.vault, vault_auto_bound=auto_detected, folder_created=folder_created)
        if announce:
            info["announce"] = announce
        return info
    info["next"] = next_step_for(home)
    if auto_detected and home.vault is not None:
        announce = announce_line(None, vault=home.vault, vault_auto_bound=True, folder_created=False)
        if announce:
            info["announce"] = announce
    return info


def journal_slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")[:48] or "entry"


def journal_digest(kind, title, body):
    return hashlib.sha256(f"{kind}\n{title.strip()}\n{body.strip()}".encode()).hexdigest()[:8]


def journal_note_path(notes, kind, title, body):
    """Resolve one journal file. Returns (dest, already_written)."""
    slug = journal_slug(title)
    digest = journal_digest(kind, title, body)
    journal_dir = notes / "journal"
    suffixed = journal_dir / f"{kind}-{slug}-{digest}.md"
    if journal_dir.is_dir():
        legacy = sorted(journal_dir.glob(f"{kind}-*-{slug}-{digest}.md"))
        if legacy:
            return legacy[0], True
        if suffixed.is_file():
            return suffixed, True
        base = journal_dir / f"{kind}-{slug}.md"
        if base.is_file():
            try:
                props, _ = split_frontmatter(base.read_text(encoding="utf-8-sig"))
            except OSError:
                props = {}
            if props.get("digest") == digest:
                return base, True
            return suffixed, False
    return journal_dir / f"{kind}-{slug}.md", False


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


def with_obsidian(result, store, project=None, auto_detected=False, folder_created=False):
    project = result.get("project", project or "")
    ticket = result.get("ticket", "")
    home = journal_home(store, project, ticket)
    out = dict(result)
    out["obsidian"] = obsidian_info(home, auto_detected=auto_detected, folder_created=folder_created)
    return out


CATEGORIES = {
    "requirements": r"requirement|acceptance|specification|user stor",
    "decisions": r"decision|\badr\b",
    "architecture": r"architect|design|schema",
    "testing": r"test|verif|\bqa\b",
    "investigation": r"investigat|debug|incident|troubleshoot",
    "setup": r"setup|install|getting started|configuration",
}


def split_frontmatter(body):
    """Parse simple YAML-ish frontmatter. Values stay strings; no nested objects."""
    if not body.startswith("---\n") and not body.startswith("---\r\n"):
        return {}, body
    # Find closing fence on its own line.
    match = re.search(r"\n---\s*\n", body[3:])
    if not match:
        return {}, body
    raw = body[3:3 + match.start()]
    rest = body[3 + match.end():]
    props = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        props[key] = value.strip().strip("\"'")
    return props, rest


def extract_links(text):
    wiki = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", text)
    md = [target for _, target in re.findall(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)]
    return list(dict.fromkeys([*wiki, *md]))


def sections(text):
    """Yield (heading_path, heading, section_text). heading_path is ancestry including heading."""
    stack = []  # (level, title)
    lines, fence = [], None
    path, heading = ["Overview"], "Overview"
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        title = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if title:
            if "".join(lines).strip():
                yield path, heading, "".join(lines)
            level = len(title[1])
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title[2]))
            path = [name for _, name in stack]
            heading = title[2]
            lines = []
        lines.append(line)
    if "".join(lines).strip():
        yield path, heading, "".join(lines)


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


def note_source_and_docs(project, ticket, root, file, body, timestamp):
    """Build one importer source row and document chunks for a note body."""
    relative = file.relative_to(root).as_posix()
    properties, content = split_frontmatter(body)
    digest = hashlib.sha256(body.encode()).hexdigest()
    sid = "kb-src-" + hashlib.sha256(json.dumps([project, ticket, str(file), digest]).encode()).hexdigest()
    source = (sid, project, relative, body, timestamp, digest, ticket)
    documents = []
    categories = Counter()
    for section_index, (heading_path, heading, section) in enumerate(sections(content)):
        # ponytail: folder/heading keyword categories; add reviewed labels if these prove too coarse.
        category = next((name for label in (heading.lower(), relative.lower())
                         for name, pattern in CATEGORIES.items() if re.search(pattern, label)), "reference")
        outbound_links = extract_links(section)
        for index, chunk in enumerate(chunks(section)):
            mid = "kb-doc-" + hashlib.sha256(json.dumps([sid, section_index, index]).encode()).hexdigest()
            title = heading if heading != "Overview" else Path(relative).stem
            documents.append({
                "id": mid, "version": 1, "project": project, "ticket": ticket,
                "kind": "document", "status": "indexed", "title": title,
                "claim": chunk, "path": relative, "heading": heading,
                "heading_path": heading_path, "outbound_links": outbound_links,
                "properties": properties, "category": category,
                "topics": [category, relative, heading, *heading_path[:3]],
                "source_ids": [sid], "need_tags": [],
                "valid_from": timestamp[:10], "recorded_at": timestamp,
            })
            categories[category] += 1
    return source, documents, categories


def index_ticket_file(store, project, ticket, file_path):
    """Index one file under a bound ticket folder. Idempotent via content-addressed source id."""
    project, ticket = scope_key({"project": project, "ticket": ticket})
    kb = store.knowledge_base(project, ticket)
    if not kb or not isinstance(kb.get("path"), str) or not kb["path"].strip():
        raise ValueError("Ticket has no bound notes folder")
    root = Path(kb["path"]).expanduser().resolve(strict=True)
    file = Path(file_path).expanduser().resolve(strict=True)
    file.relative_to(root)
    body = file.read_text(encoding="utf-8-sig")
    if not body.strip():
        raise ValueError("Note is empty")
    timestamp = now()
    source, documents, categories = note_source_and_docs(project, ticket, root, file, body, timestamp)
    sid = source[0]
    if store.source(sid):
        return {"status": "already_indexed", "source_id": sid, "path": source[2]}
    record = dict(kb)
    docs = list(record.get("documents") or [])
    existing = {d["id"] for d in docs}
    for doc in documents:
        if doc["id"] not in existing:
            docs.append(doc)
    cats = Counter(record.get("categories") or {})
    cats.update(categories)
    record["documents"] = docs
    record["categories"] = dict(cats)
    record["note_count"] = int(record.get("note_count") or 0) + 1
    record["chunk_count"] = len(docs)
    record["indexed_at"] = timestamp
    if len(docs) > 20_000:
        raise ValueError("Knowledge base exceeds 20,000 sections; choose a smaller folder")
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO sources (id,project,title,body,created_at,sha256,ticket) VALUES (?,?,?,?,?,?,?)",
            source)
        store.db.execute(
            "INSERT INTO knowledge_bases VALUES (?,?,?) ON CONFLICT(project,ticket) DO UPDATE SET payload=excluded.payload",
            (project, ticket, json.dumps(record)))
    return {"status": "indexed", "source_id": sid, "path": source[2], "chunks": len(documents)}


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
                source, documents, cats = note_source_and_docs(
                    project, ticket, root, file, body, timestamp)
                sources.append(source)
                record["documents"].extend(documents)
                categories.update(cats)
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
