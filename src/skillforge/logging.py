"""Structured logging, tracing, and log redaction.

Rules:

* Logs go to **stderr** so ``--json`` output on stdout stays machine-readable.
* Every log record passes through a redaction filter.
* Stages are traced with :class:`Tracer` spans so durations can be reported in
  ``--json`` output and, later, exported to an LLM/generation inspector.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from skillforge.security.redaction import redact_text

LOGGER_NAME = "skillforge"

_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class RedactingFilter(logging.Filter):
    """Redact secrets from log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: redact_text(str(value)) for key, value in record.args.items()
                    }
                else:
                    record.args = tuple(redact_text(str(value)) for value in record.args)
        except Exception:  # pragma: no cover - logging must never raise
            record.msg = "[log redaction failed]"
            record.args = ()
        return True


class PlainFormatter(logging.Formatter):
    """Compact ``level message`` formatter with optional context fields."""

    def __init__(self, *, show_context: bool = False) -> None:
        super().__init__("%(levelname)s %(message)s")
        self.show_context = show_context

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if not self.show_context:
            return base
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        if not extras:
            return base
        rendered = " ".join(f"{key}={value}" for key, value in sorted(extras.items()))
        return f"{base} [{rendered}]"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for CI logs and log shipping."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_") or key in payload:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    verbosity: int = 0,
    debug: bool = False,
    json_mode: bool = False,
    stream: TextIO | None = None,
    log_file: Path | None = None,
) -> None:
    """Configure the ``skillforge`` logger. Idempotent."""
    if debug or verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    output = stream if stream is not None else sys.stderr
    handler = logging.StreamHandler(output)
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter() if json_mode else PlainFormatter(show_context=debug))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(RedactingFilter())
        logger.addHandler(file_handler)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger of the ``skillforge`` namespace."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


# --------------------------------------------------------------------- tracing


@dataclass
class Span:
    """A recorded pipeline stage."""

    name: str
    duration_ms: float
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "attributes": self.attributes,
        }


class Tracer:
    """Collects spans for one command invocation."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        started = time.perf_counter()
        span = Span(name=name, duration_ms=0.0, attributes=dict(attributes))
        try:
            yield span
        except Exception:
            span.status = "error"
            raise
        finally:
            span.duration_ms = (time.perf_counter() - started) * 1000
            self.spans.append(span)

    def to_dict(self) -> dict[str, Any]:
        return {"spans": [span.to_dict() for span in self.spans]}

    def total_ms(self) -> float:
        return round(sum(span.duration_ms for span in self.spans), 3)


class NullTracer(Tracer):
    """Tracer used when the caller did not provide one."""

    def __init__(self) -> None:
        super().__init__()

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        started = time.perf_counter()
        span = Span(name=name, duration_ms=0.0, attributes=dict(attributes))
        try:
            yield span
        except Exception:
            span.status = "error"
            raise
        finally:
            span.duration_ms = (time.perf_counter() - started) * 1000
            # Deliberately not recorded.

    def to_dict(self) -> dict[str, Any]:
        return {"spans": []}


_active_tracer: contextvars.ContextVar[Tracer | None] = contextvars.ContextVar(
    "skillforge_tracer", default=None
)
_fallback = NullTracer()


def use_tracer(tracer: Tracer) -> contextvars.Token[Tracer | None]:
    """Install ``tracer`` as the active tracer for the current context."""
    return _active_tracer.set(tracer)


def reset_tracer(token: contextvars.Token[Tracer | None]) -> None:
    _active_tracer.reset(token)


def current_tracer() -> Tracer:
    return _active_tracer.get() or _fallback


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[Span]:
    """Record a span on the active tracer and log at debug level."""
    logger = get_logger("trace")
    started = time.perf_counter()
    with current_tracer().span(name, **attributes) as span:
        try:
            yield span
        finally:
            duration = (time.perf_counter() - started) * 1000
            logger.debug("stage finished", extra={"stage": name, "duration_ms": round(duration, 2)})
