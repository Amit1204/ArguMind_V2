"""Structured (JSON) logging with request correlation.

LOG_FORMAT=json (default) emits one JSON object per line with timestamp, level,
logger, message, service, request_id, plus any `extra={...}` fields.
LOG_FORMAT=text keeps the classic single-line format for local reading.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.observability.context import get_request_id

# Attributes every LogRecord has; anything else was passed via `extra=`.
_STANDARD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    }
)  # fmt: skip


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service,
        }
        if request_id := get_request_id():
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s%(correlation)s - %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        rid = get_request_id()
        record.correlation = f" [request_id={rid[:8]}]" if rid else ""
        return super().format(record)


_handler: logging.Handler | None = None


def configure_logging(level: str, fmt: str = "json", service: str = "argumind-backend") -> None:
    """Route every logger (including uvicorn's) through one structured root handler.

    Idempotent: calling it again (tests build several apps) replaces only the
    handler this module installed, leaving other handlers (pytest's) alone.
    """
    global _handler
    root = logging.getLogger()
    if _handler is not None and _handler in root.handlers:
        root.removeHandler(_handler)
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())
    root.addHandler(_handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # The request middleware writes its own access line with request id and latency;
    # uvicorn's unstructured one would duplicate it in JSON mode.
    logging.getLogger("uvicorn.access").setLevel(
        logging.WARNING if fmt == "json" else level.upper()
    )
