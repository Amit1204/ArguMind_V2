"""Run persistence: an in-memory store for tests and the CLI, PostgreSQL for the service."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.evidence.models import Claim
from app.graph.model import Edge
from app.pipeline.schemas import RunUsage, StageRecord
from app.sources.models import Source


@dataclass
class RunRow:
    id: str
    question: str
    status: str
    request_id: str | None = None
    client_id: str | None = None
    outcome_reason: str | None = None
    answer: str | None = None
    confidence: float | None = None
    iteration_count: int = 0
    usage: RunUsage = field(default_factory=RunUsage)
    latency_ms: int | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class RunStore(Protocol):
    def create_run(self, question: str, request_id: str | None, client_id: str | None) -> str: ...
    def mark_running(self, run_id: str) -> None: ...
    def save_stage(self, run_id: str, stage: StageRecord) -> None: ...
    def save_sources(self, run_id: str, sources: list[Source]) -> None: ...
    def save_claims(self, run_id: str, claims: list[Claim]) -> None: ...
    def replace_edges(self, run_id: str, edges: list[Edge]) -> None: ...
    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        answer: str | None,
        confidence: float | None,
        outcome_reason: str | None,
        iteration_count: int,
        usage: RunUsage,
        latency_ms: int,
        error: str | None = None,
    ) -> None: ...
    def get_run(self, run_id: str) -> RunRow | None: ...
    def get_stages(self, run_id: str) -> list[StageRecord]: ...
    def get_sources(self, run_id: str) -> list[Source]: ...
    def get_claims(self, run_id: str) -> list[Claim]: ...
    def get_edges(self, run_id: str) -> list[Edge]: ...
    def list_runs(self, limit: int, offset: int) -> list[RunRow]: ...
    def count_runs(self) -> int: ...


# ----------------------------------------------------------------------------- memory


class MemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRow] = {}
        self._stages: dict[str, list[StageRecord]] = {}
        self._sources: dict[str, dict[str, Source]] = {}
        self._claims: dict[str, dict[str, Claim]] = {}
        self._edges: dict[str, list[Edge]] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def create_run(self, question: str, request_id: str | None, client_id: str | None) -> str:
        run_id = str(uuid.uuid4())
        with self._lock:
            self._runs[run_id] = RunRow(
                id=run_id,
                question=question,
                status="queued",
                request_id=request_id,
                client_id=client_id,
            )
            self._order.append(run_id)
            self._stages[run_id] = []
            self._sources[run_id] = {}
            self._claims[run_id] = {}
            self._edges[run_id] = []
        return run_id

    def mark_running(self, run_id: str) -> None:
        with self._lock:
            self._runs[run_id].status = "running"

    def save_stage(self, run_id: str, stage: StageRecord) -> None:
        with self._lock:
            self._stages[run_id].append(stage)

    def save_sources(self, run_id: str, sources: list[Source]) -> None:
        with self._lock:
            for s in sources:
                self._sources[run_id].setdefault(s.source_id, s)

    def save_claims(self, run_id: str, claims: list[Claim]) -> None:
        with self._lock:
            for c in claims:
                self._claims[run_id].setdefault(c.claim_id, c)

    def replace_edges(self, run_id: str, edges: list[Edge]) -> None:
        with self._lock:
            self._edges[run_id] = list(edges)

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        answer: str | None,
        confidence: float | None,
        outcome_reason: str | None,
        iteration_count: int,
        usage: RunUsage,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        with self._lock:
            row = self._runs[run_id]
            row.status = status
            row.answer = answer
            row.confidence = confidence
            row.outcome_reason = outcome_reason
            row.iteration_count = iteration_count
            row.usage = usage
            row.latency_ms = latency_ms
            row.error = error
            row.finished_at = datetime.now(UTC)

    def get_run(self, run_id: str) -> RunRow | None:
        return self._runs.get(run_id)

    def get_stages(self, run_id: str) -> list[StageRecord]:
        return list(self._stages.get(run_id, []))

    def get_sources(self, run_id: str) -> list[Source]:
        return list(self._sources.get(run_id, {}).values())

    def get_claims(self, run_id: str) -> list[Claim]:
        return list(self._claims.get(run_id, {}).values())

    def get_edges(self, run_id: str) -> list[Edge]:
        return list(self._edges.get(run_id, []))

    def list_runs(self, limit: int, offset: int) -> list[RunRow]:
        ids = list(reversed(self._order))[offset : offset + limit]
        return [self._runs[i] for i in ids]

    def count_runs(self) -> int:
        return len(self._runs)


# --------------------------------------------------------------------------- postgres


class DbRunStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sf = session_factory

    def create_run(self, question: str, request_id: str | None, client_id: str | None) -> str:
        import hashlib

        with self._sf() as s:
            run_id = s.execute(
                text(
                    """
                    INSERT INTO runs (request_id, client_id, question, question_hash, status)
                    VALUES (:rid, :cid, :q, :h, 'queued') RETURNING id
                    """
                ),
                {
                    "rid": request_id,
                    "cid": client_id,
                    "q": question,
                    "h": hashlib.sha256(" ".join(question.lower().split()).encode()).hexdigest(),
                },
            ).scalar_one()
            s.commit()
        return str(run_id)

    def mark_running(self, run_id: str) -> None:
        with self._sf() as s:
            s.execute(
                text("UPDATE runs SET status = 'running', started_at = now() WHERE id = :id"),
                {"id": run_id},
            )
            s.commit()

    def save_stage(self, run_id: str, stage: StageRecord) -> None:
        with self._sf() as s:
            s.execute(
                text(
                    """
                    INSERT INTO run_stages (run_id, name, attempt, status, started_at, finished_at,
                        duration_ms, llm_calls, input_tokens, output_tokens, detail, error)
                    VALUES (:run_id, :name, :attempt, :status, :started_at, :finished_at,
                        :duration_ms, :llm_calls, :input_tokens, :output_tokens,
                        CAST(:detail AS jsonb), :error)
                    """
                ),
                {
                    **stage.model_dump(exclude={"detail"}),
                    "run_id": run_id,
                    "detail": json.dumps(stage.detail, default=str),
                },
            )
            s.commit()

    def save_sources(self, run_id: str, sources: list[Source]) -> None:
        if not sources:
            return
        with self._sf() as s:
            for src in sources:
                s.execute(
                    text(
                        """
                        INSERT INTO sources (run_id, source_id, kind, title, url, authors,
                            published_year, summary, authority, sub_question_index)
                        VALUES (:run_id, :source_id, :kind, :title, :url, CAST(:authors AS jsonb),
                            :published_year, :summary, :authority, :sub_question_index)
                        ON CONFLICT (run_id, source_id) DO NOTHING
                        """
                    ),
                    {
                        "run_id": run_id,
                        "source_id": src.source_id,
                        "kind": src.kind.value,
                        "title": src.title,
                        "url": str(src.url),
                        "authors": json.dumps(src.authors),
                        "published_year": src.published_year,
                        "summary": src.summary,
                        "authority": src.authority,
                        "sub_question_index": src.sub_question_index,
                    },
                )
            s.commit()

    def save_claims(self, run_id: str, claims: list[Claim]) -> None:
        if not claims:
            return
        with self._sf() as s:
            for c in claims:
                s.execute(
                    text(
                        """
                        INSERT INTO claims (run_id, claim_id, source_id, sub_question_index, text,
                            stance, confidence, evidence_type)
                        VALUES (:run_id, :claim_id, :source_id, :sub_question_index, :text,
                            :stance, :confidence, :evidence_type)
                        ON CONFLICT (run_id, claim_id) DO NOTHING
                        """
                    ),
                    {**c.model_dump(mode="json"), "run_id": run_id},
                )
            s.commit()

    def replace_edges(self, run_id: str, edges: list[Edge]) -> None:
        with self._sf() as s:
            s.execute(text("DELETE FROM citation_edges WHERE run_id = :run_id"), {"run_id": run_id})
            for e in edges:
                s.execute(
                    text(
                        """
                        INSERT INTO citation_edges (run_id, source_node, target_node, edge_type,
                            weight, explanation)
                        VALUES (:run_id, :source_node, :target_node, :edge_type, :weight,
                            :explanation)
                        ON CONFLICT (run_id, source_node, target_node, edge_type) DO NOTHING
                        """
                    ),
                    {**e.model_dump(mode="json"), "run_id": run_id},
                )
            s.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        answer: str | None,
        confidence: float | None,
        outcome_reason: str | None,
        iteration_count: int,
        usage: RunUsage,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        with self._sf() as s:
            s.execute(
                text(
                    """
                    UPDATE runs SET status = CAST(:status AS run_status), answer = :answer,
                        confidence = :confidence, outcome_reason = :outcome_reason,
                        iteration_count = :iteration_count, llm_calls = :llm_calls,
                        input_tokens = :input_tokens, output_tokens = :output_tokens,
                        estimated_cost_usd = :cost, latency_ms = :latency_ms, error = :error,
                        finished_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "answer": answer,
                    "confidence": confidence,
                    "outcome_reason": outcome_reason,
                    "iteration_count": iteration_count,
                    "llm_calls": usage.llm_calls,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost": usage.estimated_cost_usd,
                    "latency_ms": latency_ms,
                    "error": error,
                },
            )
            s.commit()

    _RUN_COLUMNS = """
        id, request_id, client_id, question, status, outcome_reason, answer, confidence,
        iteration_count, llm_calls, input_tokens, output_tokens, estimated_cost_usd,
        latency_ms, error, started_at, finished_at
    """

    @staticmethod
    def _row(r) -> RunRow:  # noqa: ANN001 - sqlalchemy Row
        return RunRow(
            id=str(r.id),
            request_id=r.request_id,
            client_id=r.client_id,
            question=r.question,
            status=str(r.status),
            outcome_reason=r.outcome_reason,
            answer=r.answer,
            confidence=float(r.confidence) if r.confidence is not None else None,
            iteration_count=r.iteration_count,
            usage=RunUsage(
                llm_calls=r.llm_calls,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                estimated_cost_usd=float(r.estimated_cost_usd),
            ),
            latency_ms=r.latency_ms,
            error=r.error,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )

    def get_run(self, run_id: str) -> RunRow | None:
        with self._sf() as s:
            row = s.execute(
                text(f"SELECT {self._RUN_COLUMNS} FROM runs WHERE id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": run_id},
            ).first()
        return self._row(row) if row else None

    def get_stages(self, run_id: str) -> list[StageRecord]:
        with self._sf() as s:
            rows = s.execute(
                text(
                    """
                    SELECT name, attempt, status, started_at, finished_at, duration_ms, llm_calls,
                        input_tokens, output_tokens, detail, error
                    FROM run_stages WHERE run_id = CAST(:id AS uuid) ORDER BY id
                    """
                ),
                {"id": run_id},
            ).all()
        return [StageRecord(**dict(r._mapping)) for r in rows]

    def get_sources(self, run_id: str) -> list[Source]:
        with self._sf() as s:
            rows = s.execute(
                text(
                    """
                    SELECT source_id, kind, title, url, authors, published_year, summary, authority,
                        sub_question_index
                    FROM sources WHERE run_id = CAST(:id AS uuid) ORDER BY id
                    """
                ),
                {"id": run_id},
            ).all()
        return [Source(**{**dict(r._mapping), "authority": float(r.authority)}) for r in rows]

    def get_claims(self, run_id: str) -> list[Claim]:
        with self._sf() as s:
            rows = s.execute(
                text(
                    """
                    SELECT claim_id, source_id, sub_question_index, text, stance, confidence,
                        evidence_type
                    FROM claims WHERE run_id = CAST(:id AS uuid) ORDER BY id
                    """
                ),
                {"id": run_id},
            ).all()
        return [Claim(**{**dict(r._mapping), "confidence": float(r.confidence)}) for r in rows]

    def get_edges(self, run_id: str) -> list[Edge]:
        with self._sf() as s:
            rows = s.execute(
                text(
                    """
                    SELECT source_node, target_node, edge_type, weight, explanation
                    FROM citation_edges WHERE run_id = CAST(:id AS uuid) ORDER BY id
                    """
                ),
                {"id": run_id},
            ).all()
        return [Edge(**{**dict(r._mapping), "weight": float(r.weight)}) for r in rows]

    def list_runs(self, limit: int, offset: int) -> list[RunRow]:
        with self._sf() as s:
            rows = s.execute(
                text(
                    f"SELECT {self._RUN_COLUMNS} FROM runs ORDER BY started_at DESC "  # noqa: S608
                    "LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset},
            ).all()
        return [self._row(r) for r in rows]

    def count_runs(self) -> int:
        with self._sf() as s:
            return int(s.execute(text("SELECT COUNT(*) FROM runs")).scalar_one())
