"""Cache of source lookups: identical searches within the TTL hit no external API."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.sources.models import Source


class SourceCache(Protocol):
    def get(self, key: str) -> list[Source] | None: ...

    def set(
        self, key: str, kind: str, query: str, sources: list[Source], ttl: timedelta
    ) -> None: ...


def _dump(sources: list[Source]) -> str:
    return json.dumps([s.model_dump(mode="json") for s in sources])


def _load(payload: str | list) -> list[Source]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return [Source.model_validate(item) for item in data]


class MemorySourceCache:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._items: dict[str, tuple[datetime, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> list[Source] | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, payload = item
            if expires_at <= self._now():
                del self._items[key]
                return None
            return _load(payload)

    def set(self, key: str, kind: str, query: str, sources: list[Source], ttl: timedelta) -> None:
        with self._lock:
            self._items[key] = (self._now() + ttl, _dump(sources))


class DbSourceCache:
    """Backed by the `source_cache` table; survives restarts and is shared by replicas."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get(self, key: str) -> list[Source] | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    "SELECT payload FROM source_cache WHERE cache_key = :k AND expires_at > now()"
                ),
                {"k": key},
            ).first()
        return _load(row[0]) if row else None

    def set(self, key: str, kind: str, query: str, sources: list[Source], ttl: timedelta) -> None:
        with self._session_factory() as session:
            session.execute(
                text(
                    """
                    INSERT INTO source_cache
                        (cache_key, kind, query, payload, fetched_at, expires_at)
                    VALUES (:k, :kind, :q, CAST(:payload AS jsonb), now(), now() + :ttl)
                    ON CONFLICT (cache_key) DO UPDATE
                        SET payload = EXCLUDED.payload,
                            fetched_at = now(),
                            expires_at = EXCLUDED.expires_at
                    """
                ),
                {"k": key, "kind": kind, "q": query[:2000], "payload": _dump(sources), "ttl": ttl},
            )
            session.commit()
