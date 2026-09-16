"""Symlink-safe directory sandbox for Context Lab filesystem writes."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .schemas import AgentError


@dataclass(frozen=True)
class PinMetadata:
    path: str
    st_dev: int | None = None
    st_ino: int | None = None


def reject_dangling_dest(path: Path) -> None:
    """Refuse write-through on a dangling destination symlink."""
    if path.is_symlink() and not path.exists():
        raise AgentError(
            "symlink_escape",
            f"refusing dangling symlink {path}",
            field="path",
        )


def _validate_relative(relative: Path) -> Path:
    if relative.is_absolute():
        raise AgentError("symlink_escape", "path must be relative to pinned root", field="path")
    if relative.drive or ".." in relative.parts:
        raise AgentError("symlink_escape", "path escapes pinned root", field="path")
    return relative


class PinnedRoot:
    """Symlink-safe directory sandbox for all Context Lab filesystem writes."""

    def __init__(self, root: Path, *, dir_fd: int | None = None, pin: PinMetadata | None = None):
        self.root = root
        self._dir_fd = dir_fd
        self.pin = pin

    @classmethod
    def pin(cls, path: Path, *, stored: PinMetadata | None = None) -> PinnedRoot:
        """Resolve with strict directory check; open O_NOFOLLOW dir fd when available."""
        resolved = path.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise AgentError("symlink_escape", f"pinned path is not a directory: {resolved}", field="path")
        stat = resolved.stat()
        live = PinMetadata(path=str(resolved), st_dev=stat.st_dev, st_ino=stat.st_ino)
        if stored is not None and stored.st_dev is not None and stored.st_ino is not None:
            if stored.st_dev != live.st_dev or stored.st_ino != live.st_ino:
                raise AgentError(
                    "journal_home_stale",
                    "notes root changed since pin was recorded",
                    field="path",
                )
        dir_fd = None
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            dir_fd = os.open(str(resolved), flags)
        except OSError:
            dir_fd = None
        return cls(resolved, dir_fd=dir_fd, pin=live)

    def close(self) -> None:
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None

    def __enter__(self) -> PinnedRoot:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _reject_symlink_components(self, relative: Path) -> None:
        current = self.root
        for part in relative.parts:
            candidate = current / part
            if candidate.is_symlink():
                raise AgentError(
                    "symlink_escape",
                    f"refusing symlink component {candidate}",
                    field="path",
                )
            current = candidate

    def ensure_dir(self, relative: Path) -> None:
        """Create relative path under root without following symlinks in intermediates."""
        relative = _validate_relative(relative)
        if not relative.parts:
            return
        current = self.root
        for part in relative.parts:
            candidate = current / part
            if candidate.is_symlink():
                raise AgentError(
                    "symlink_escape",
                    f"refusing symlink component {candidate}",
                    field="path",
                )
            if candidate.exists():
                if not candidate.is_dir():
                    raise AgentError(
                        "symlink_escape",
                        f"not a directory: {candidate}",
                        field="path",
                    )
            else:
                candidate.mkdir()
            current = candidate

    def write_text(
        self,
        relative: Path,
        content: str,
        *,
        mode: int = 0o600,
        exclusive: bool = False,
    ) -> Path:
        """Same-dir temp + fsync + os.replace; reject escape and symlink components."""
        relative = _validate_relative(relative)
        self._reject_symlink_components(relative)
        dest = self.root / relative
        if exclusive and dest.exists():
            raise AgentError("already_exists", f"{dest} already exists", field="path")
        if dest.is_symlink():
            raise AgentError(
                "symlink_escape",
                f"refusing symlink destination {dest}",
                field="path",
            )
        parent = dest.parent
        if parent != self.root:
            self.ensure_dir(parent.relative_to(self.root))
        fd, tmp = tempfile.mkstemp(prefix=".pinned-", suffix=dest.suffix, dir=str(parent))
        try:
            os.chmod(tmp, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, dest)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return dest
