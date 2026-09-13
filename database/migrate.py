"""Forward-only SQL migrations for ArguMind.

Files in database/migrations/ are applied once each, in name order, inside a
transaction, and recorded in schema_migrations. Every file must be idempotent
(CREATE ... IF NOT EXISTS, guarded CREATE TYPE) so that re-running against a
volume that already has the objects is harmless.

Usage (inside the db-migrate container):  python migrate.py
Environment:  DATABASE_URL  (libpq URL)  MIGRATE_WAIT_SECONDS  (default 60)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    """All migration files in application order (lexical by file name)."""
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def pending(applied: set[str], files: list[Path]) -> list[Path]:
    """Migrations not yet recorded, preserving order."""
    return [p for p in files if p.stem not in applied]


def ensure_registry(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration; return the versions applied in this run."""
    ensure_registry(conn)
    todo = pending(applied_versions(conn), discover(migrations_dir))
    done: list[str] = []
    for path in todo:
        sql = path.read_text(encoding="utf-8")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            conn.commit()
        except Exception:
            conn.rollback()
            print(f"migration {path.stem} failed", file=sys.stderr)
            raise
        done.append(path.stem)
        print(f"applied {path.stem}")
    return done


def connect_with_retry(url: str, wait_seconds: float) -> psycopg.Connection:
    """The postgres container may accept connections a moment after its health check."""
    deadline = time.monotonic() + wait_seconds
    delay = 0.5
    while True:
        try:
            return psycopg.connect(url, connect_timeout=5)
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                raise SystemExit(
                    f"database not reachable after {wait_seconds:.0f}s: {exc}"
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    wait = float(os.environ.get("MIGRATE_WAIT_SECONDS", "60"))
    with connect_with_retry(url, wait) as conn:
        done = apply_migrations(conn)
        current = sorted(applied_versions(conn))
    if done:
        print(f"applied {len(done)} migration(s); schema at {current[-1]}")
    else:
        print(f"nothing to apply; schema at {current[-1] if current else 'empty'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
