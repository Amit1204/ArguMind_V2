"""Per-request correlation context (request id) via contextvars.

Set once by the HTTP middleware; read by the log formatter so every line
carries the request id, and by error handlers so clients can quote it.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return str(uuid.uuid4())


def accept_request_id(candidate: str | None) -> str:
    """Honour a caller-supplied id when it is well formed; otherwise mint one."""
    if candidate and _VALID_REQUEST_ID.match(candidate.strip()):
        return candidate.strip()
    return new_request_id()


def bind_request(request_id: str) -> Token:
    return _request_id.set(request_id)


def clear_request(token: Token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()
