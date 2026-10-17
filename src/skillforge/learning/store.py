"""Learning session storage.

Sessions live in ``.skillforge/sessions/<id>.json`` inside the analysed
repository — separate from exported Agent Skills. They contain structured,
redacted evidence only (no raw logs, no secrets) and are intended to be
git-ignored.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from skillforge.errors import NotFoundError, SecurityRefusal
from skillforge.models import LearningSession
from skillforge.security.paths import safe_join
from skillforge.security.redaction import redact_text
from skillforge.utils.fs import atomic_write_text

SESSION_DIRNAME = ".skillforge"
SESSIONS_SUBDIR = "sessions"
_SESSION_ID_RE = re.compile(r"^[a-z0-9][A-Za-z0-9-]{5,80}$")


def new_session_id(now: datetime | None = None) -> str:
    """Time-ordered, unique, filesystem-safe session id."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"learn-{stamp}-{secrets.token_hex(3)}"


def sessions_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / SESSION_DIRNAME / SESSIONS_SUBDIR


def validate_session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.match(session_id):
        raise SecurityRefusal(
            f"invalid session id: {session_id!r}",
            hint="session ids look like learn-20261003T120000Z-a1b2c3",
        )
    return session_id


def session_path(repo_root: Path, session_id: str) -> Path:
    validate_session_id(session_id)
    return safe_join(sessions_dir(repo_root), f"{session_id}.json")


def save_session(session: LearningSession, repo_root: Path) -> Path:
    """Write the session JSON (redacted again as defence in depth)."""
    path = session_path(repo_root, session.id)
    payload = redact_text(session.model_dump_json(indent=2))
    atomic_write_text(path, payload + "\n")
    return path


def load_session(repo_root: Path, session_id: str) -> LearningSession:
    path = session_path(repo_root, session_id)
    if not path.is_file():
        raise NotFoundError(
            f"learning session not found: {session_id}",
            hint=f"expected at {path}",
        )
    import json

    return LearningSession.model_validate(json.loads(path.read_text(encoding="utf-8")))


def list_session_files(repo_root: Path) -> list[Path]:
    directory = sessions_dir(repo_root)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


__all__ = [
    "SESSIONS_SUBDIR",
    "SESSION_DIRNAME",
    "list_session_files",
    "load_session",
    "new_session_id",
    "save_session",
    "session_path",
    "sessions_dir",
    "validate_session_id",
]
