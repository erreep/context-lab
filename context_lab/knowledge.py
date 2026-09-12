"""Once-per-scope setup and local Markdown/text snapshots. No vault writes or models."""
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .store import now, scope_key


def allocate_ticket():
    return "work-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


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
