"""Migration job logic that does not need a database."""

from __future__ import annotations

import re
from pathlib import Path

import migrate

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_discover_returns_sql_files_in_name_order(tmp_path: Path) -> None:
    (tmp_path / "0002_b.sql").write_text("select 2")
    (tmp_path / "0001_a.sql").write_text("select 1")
    (tmp_path / "notes.txt").write_text("ignored")
    assert [p.name for p in migrate.discover(tmp_path)] == ["0001_a.sql", "0002_b.sql"]


def test_discover_handles_missing_directory(tmp_path: Path) -> None:
    assert migrate.discover(tmp_path / "missing") == []


def test_pending_skips_applied_versions(tmp_path: Path) -> None:
    files = [tmp_path / "0001_a.sql", tmp_path / "0002_b.sql", tmp_path / "0003_c.sql"]
    assert [p.stem for p in migrate.pending({"0001_a", "0003_c"}, files)] == ["0002_b"]


def test_real_migrations_are_numbered_and_unique() -> None:
    files = migrate.discover(MIGRATIONS)
    assert files, "at least one migration is expected"
    numbers = [f.stem[:4] for f in files]
    assert all(re.fullmatch(r"\d{4}", n) for n in numbers)
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)


def test_real_migrations_are_idempotent_by_construction() -> None:
    """Every CREATE must be guarded so re-running on an existing volume is safe."""
    for path in migrate.discover(MIGRATIONS):
        sql = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE\s+(TABLE|INDEX|EXTENSION)\s+(?!IF NOT EXISTS)", sql, re.I
        ):
            raise AssertionError(f"{path.name}: unguarded {match.group(0).strip()}")
        for match in re.finditer(r"CREATE\s+TYPE\s+(\w+)", sql, re.I):
            guard = f"typname = '{match.group(1)}'"
            assert guard in sql, f"{path.name}: CREATE TYPE {match.group(1)} lacks a pg_type guard"
