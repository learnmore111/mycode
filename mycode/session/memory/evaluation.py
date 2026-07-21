"""Deterministic replay metrics for staged memory rollout decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycode.session.memory.service import MemoryService


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expected_ids: set[str] = field(default_factory=set)
    forbidden_ids: set[str] = field(default_factory=set)
    agent: str | None = None


@dataclass(frozen=True)
class RetrievalMetrics:
    case_count: int
    recall_at_k: float
    mean_reciprocal_rank: float
    forbidden_adoption_rate: float
    evidence_completeness: float


def evaluate_retrieval(
    service: MemoryService,
    cases: list[RetrievalCase],
    *,
    k: int = 5,
) -> RetrievalMetrics:
    """Evaluate the transparent FTS/lexical baseline against replay labels."""
    if not cases:
        return RetrievalMetrics(0, 0.0, 0.0, 0.0, 0.0)

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    forbidden_hits = 0
    returned_count = 0
    complete_evidence = 0

    for case in cases:
        results = service.search(case.query, agent=case.agent, max_results=k)
        result_ids = [record.id for record in results]
        expected_hits = case.expected_ids.intersection(result_ids)
        recalls.append(len(expected_hits) / len(case.expected_ids) if case.expected_ids else 1.0)
        ranks = [result_ids.index(memory_id) + 1 for memory_id in expected_hits]
        reciprocal_ranks.append(1.0 / min(ranks) if ranks else 0.0)
        forbidden_hits += len(case.forbidden_ids.intersection(result_ids))
        returned_count += len(results)
        complete_evidence += sum(
            bool(record.source_kind and record.observed_at and (record.source_message_ids or record.evidence_refs))
            for record in results
        )

    return RetrievalMetrics(
        case_count=len(cases),
        recall_at_k=mean(recalls),
        mean_reciprocal_rank=mean(reciprocal_ranks),
        forbidden_adoption_rate=forbidden_hits / max(returned_count, 1),
        evidence_completeness=complete_evidence / max(returned_count, 1),
    )


__all__ = ["RetrievalCase", "RetrievalMetrics", "evaluate_retrieval"]
