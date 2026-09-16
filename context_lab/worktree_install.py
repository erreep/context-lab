"""Preflight → stage → atomic publish → scope bind last."""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .pinned_root import PinnedRoot, reject_dangling_dest
from .schemas import AgentError


@dataclass
class InstallTarget:
    relative: Path
    content: str
    merge: Literal["replace", "codex_block"] = "replace"
    mode: int = 0o644


@dataclass
class InstallReport:
    written: list[str] = field(default_factory=list)
    scope: dict | None = None
    git_requested: bool = False
    git_installed: bool = False
    git_message: str = ""
    failed: bool = False
    rolled_back: bool = False
    error: AgentError | None = None


class WorktreeInstall:
    """Preflight all destinations, publish via PinnedRoot, bind scope last."""

    def __init__(
        self,
        client: str,
        *,
        cwd: Path,
        project: str | None,
        ticket: str | None,
        db: str | None,
        git: bool,
        force: bool,
        targets: list[InstallTarget],
        preload_scope: dict | None = None,
    ):
        self.client = client
        self.cwd = cwd
        self.project = project
        self.ticket = ticket
        self.db = db
        self.git = git
        self.force = force
        self.targets = targets
        self.preload_scope = preload_scope
        self._pinned: PinnedRoot | None = None
        self._backups: dict[Path, str | None] = {}

    @classmethod
    def build(
        cls,
        client: str,
        *,
        cwd: Path,
        project: str | None,
        ticket: str | None,
        db: str | None,
        git: bool,
        force: bool,
    ) -> WorktreeInstall:
        from .hooks import (
            CONFIG_PATHS,
            CURSOR_RULES,
            CURSOR_RULES_PATH,
            HARNESS_CONFIGS,
            MCP_PATHS,
            _mcp_payload,
            load_scope,
            require_worktree,
        )

        if client not in HARNESS_CONFIGS:
            raise AgentError("validation", f"unknown client {client!r}", field="client")
        require_worktree(str(cwd))
        root = cwd.expanduser().resolve()

        preload_scope = None
        if project is None:
            preload_scope = load_scope(cwd=str(root))

        targets: list[InstallTarget] = []
        mcp_rel = Path(MCP_PATHS[client])
        if client == "codex":
            from .hooks import CODEX_MCP_BLOCK

            targets.append(
                InstallTarget(relative=mcp_rel, content=CODEX_MCP_BLOCK, merge="codex_block")
            )
        else:
            targets.append(
                InstallTarget(relative=mcp_rel, content=_mcp_payload(client), merge="replace")
            )

        hooks_rel = Path(CONFIG_PATHS[client])
        targets.append(
            InstallTarget(
                relative=hooks_rel,
                content=json.dumps(HARNESS_CONFIGS[client], indent=2) + "\n",
                merge="replace",
            )
        )

        if client == "cursor":
            targets.append(
                InstallTarget(relative=Path(CURSOR_RULES_PATH), content=CURSOR_RULES, merge="replace")
            )

        return cls(
            client,
            cwd=root,
            project=project,
            ticket=ticket,
            db=db,
            git=git,
            force=force,
            targets=targets,
            preload_scope=preload_scope,
        )

    def preflight(self) -> None:
        self._pinned = PinnedRoot.pin(self.cwd)
        for target in self.targets:
            dest = self.cwd / target.relative
            reject_dangling_dest(dest)
            self._pinned._reject_symlink_components(target.relative.parent)
            if dest.is_symlink():
                raise AgentError(
                    "symlink_escape",
                    f"refusing symlink destination {dest}",
                    field="path",
                )
            if dest.exists() and target.merge == "replace" and not self.force:
                raise AgentError(
                    "already_exists",
                    f"{dest} already exists; pass --force to overwrite",
                    hint="Remove it or re-run with --force",
                )
            if dest.exists() and target.merge == "codex_block":
                self._codex_preflight(dest)

    def _codex_preflight(self, path: Path) -> None:
        begin, end = "# BEGIN context-lab", "# END context-lab"
        text = path.read_text(encoding="utf-8")
        if begin in text and end in text:
            if not self.force:
                raise AgentError(
                    "already_exists",
                    f"{path} already has a context-lab MCP section; pass --force to replace",
                )
        elif text.strip() and not self.force:
            raise AgentError(
                "already_exists",
                f"{path} already exists; pass --force to append a context-lab MCP section",
            )

    def _codex_content(self, path: Path, block: str) -> str:
        begin, end = "# BEGIN context-lab", "# END context-lab"
        block = block if block.endswith("\n") else block + "\n"
        if not path.exists():
            return block
        text = path.read_text(encoding="utf-8")
        if begin in text and end in text:
            pre = text.split(begin, 1)[0].rstrip()
            post = text.split(end, 1)[1].lstrip("\n")
            merged = (pre + "\n\n" if pre else "") + block + (post if not post or post.startswith("\n") else "\n" + post)
            return merged
        sep = "" if not text or text.endswith("\n") else "\n"
        return text + sep + ("\n" if text.strip() else "") + block

    def _backup(self, relative: Path) -> None:
        dest = self.cwd / relative
        if relative in self._backups:
            return
        if dest.exists() and not dest.is_symlink():
            self._backups[relative] = dest.read_text(encoding="utf-8")
        else:
            self._backups[relative] = None

    def _publish(self, target: InstallTarget) -> Path:
        assert self._pinned is not None
        dest = self.cwd / target.relative
        self._backup(target.relative)
        if target.merge == "codex_block":
            content = self._codex_content(dest, target.content)
        else:
            content = target.content
        if target.relative.parent.parts:
            self._pinned.ensure_dir(target.relative.parent)
        return self._pinned.write_text(target.relative, content, mode=target.mode)

    def _rollback(self, published: list[Path]) -> None:
        for relative in reversed(published):
            dest = self.cwd / relative
            backup = self._backups.get(relative)
            if backup is None:
                if dest.exists() or dest.is_symlink():
                    dest.unlink(missing_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(backup, encoding="utf-8")

    def run(self) -> InstallReport:
        from .hooks import install_git, set_scope

        report = InstallReport(git_requested=self.git)
        published: list[Path] = []
        stage_dir: Path | None = None
        pinned: PinnedRoot | None = None
        try:
            self.preflight()
            pinned = self._pinned
            assert pinned is not None
            stage_dir = Path(tempfile.mkdtemp(prefix=".context-lab-install-", dir=str(self.cwd)))
            for target in self.targets:
                staged = stage_dir / target.relative.name
                staged.parent.mkdir(parents=True, exist_ok=True)
                if target.merge == "codex_block":
                    content = self._codex_content(self.cwd / target.relative, target.content)
                else:
                    content = target.content
                staged.write_text(content, encoding="utf-8")
                dest = self._publish(target)
                published.append(target.relative)
                report.written.append(str(dest.relative_to(self.cwd)))

            if self.project is not None:
                report.scope = set_scope(
                    self.project,
                    self.ticket or "",
                    db=self.db,
                    cwd=str(self.cwd),
                )
            else:
                report.scope = self.preload_scope

            if self.git:
                code = install_git(cwd=str(self.cwd))
                report.git_installed = code == 0
                report.git_message = (
                    "installed pre-commit lease gate"
                    if code == 0
                    else "not installed (existing hook or core.hooksPath; see stderr)"
                )
            else:
                report.git_message = "skipped (--no-git)"
        except AgentError as exc:
            report.failed = True
            report.error = exc
            if published:
                self._rollback(published)
                report.rolled_back = True
            raise
        except Exception:
            report.failed = True
            if published:
                self._rollback(published)
                report.rolled_back = True
            raise
        finally:
            if pinned is not None:
                pinned.close()
            if stage_dir is not None and stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)
        return report
