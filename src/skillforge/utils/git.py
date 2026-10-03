"""Read-only Git metadata collection.

SkillForge never runs Git commands from the analysed repository; only a fixed
set of ``git`` plumbing commands is executed, with ``shell=False`` and a
timeout. Remote URLs are reduced to their host so credentials embedded in a
remote URL can never leak into logs or generated skills.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class GitInfo:
    """Small, safe summary of the repository's Git state."""

    available: bool = False
    in_work_tree: bool = False
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    host: str | None = None
    worktree_root: str | None = None

    @property
    def summary(self) -> str:
        if not self.in_work_tree:
            return "not a git work tree"
        parts = []
        if self.branch:
            parts.append(f"branch {self.branch}")
        if self.commit:
            parts.append(f"commit {self.commit}")
        if self.dirty is not None:
            parts.append("dirty" if self.dirty else "clean")
        return ", ".join(parts) if parts else "git work tree"


def _run_git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _host_from_remote(url: str) -> str | None:
    """Extract only the host from an SSH or HTTPS remote URL."""
    url = url.strip()
    if not url:
        return None
    if "://" in url:
        try:
            host = urlsplit(url).hostname
        except ValueError:
            return None
        return host.lower() if host else None
    # scp-like syntax: git@github.com:org/repo.git
    if "@" in url:
        _, _, remainder = url.partition("@")
        host, separator, _ = remainder.partition(":")
        if separator and host and "/" not in host:
            return host.lower()
    return None


def read_git_info(root: Path) -> GitInfo:
    """Collect Git metadata for ``root`` without mutating anything."""
    if _run_git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return GitInfo(available=_git_available(), in_work_tree=False)
    commit = _run_git(root, "rev-parse", "--short=12", "HEAD")
    branch = _run_git(root, "branch", "--show-current") or None
    status = _run_git(root, "status", "--porcelain", "--untracked-files=no")
    remote = _run_git(root, "remote", "get-url", "origin")
    worktree_root = _run_git(root, "rev-parse", "--show-toplevel")
    return GitInfo(
        available=True,
        in_work_tree=True,
        commit=commit or None,
        branch=branch,
        dirty=(bool(status) if status is not None else None),
        host=_host_from_remote(remote) if remote else None,
        worktree_root=worktree_root or None,
    )


def _git_available() -> bool:
    try:
        completed = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
