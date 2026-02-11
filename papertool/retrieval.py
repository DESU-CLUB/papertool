from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone

from papertool.config import PaperToolConfig, load_config
from papertool.db import PaperDB
from papertool.models import SearchHit
from papertool.rust_backend import RustBackendUnavailable, rank_quiz_papers as rust_rank_quiz_papers
from papertool.rust_backend import retrieve_hits as rust_retrieve_hits


STOP_WORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "for",
    "in",
    "on",
    "at",
    "is",
    "are",
    "be",
    "from",
    "with",
    "what",
    "how",
    "why",
    "when",
}

TOPIC_SEEDS = {
    "moe",
    "mamba",
    "attention",
    "transformer",
    "quantization",
    "rlhf",
    "multimodal",
    "diffusion",
    "reasoning",
    "agent",
    "retrieval",
    "inference",
    "compiler",
    "systems",
    "alignment",
}

QUEUE_BOOST = {
    "today": 1.0,
    "next": 0.75,
    "inbox": 0.45,
    "later": 0.2,
    "done": 0.05,
}


def fts_query_from_text(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    useful = [token for token in tokens if token not in STOP_WORDS and len(token) > 2]
    if not useful:
        useful = tokens[:3]
    if not useful:
        return "paper"
    return " OR ".join(dict.fromkeys(useful))


def _normalize(values: list[float], *, invert: bool = False) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-12:
        return [1.0 for _ in values]
    if invert:
        return [(hi - value) / (hi - lo) for value in values]
    return [(value - lo) / (hi - lo) for value in values]


def _query_topics(question: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9_]+", question.lower()))
    return {topic for topic in TOPIC_SEEDS if topic in words}


def _resolve_paper_filter(db: PaperDB, topic: str | None, community_id: str | None) -> list[str] | None:
    topic_ids = db.paper_ids_for_topic(topic, limit=5000) if topic else None
    community_ids = db.paper_ids_for_community(community_id, limit=5000) if community_id else None

    if topic_ids is None and community_ids is None:
        return None
    if topic_ids is None:
        return community_ids or []
    if community_ids is None:
        return topic_ids or []
    return [paper_id for paper_id in topic_ids if paper_id in set(community_ids)]


def _python_hybrid_retrieve(
    db: PaperDB,
    question: str,
    top_k: int = 6,
    *,
    topic: str | None = None,
    community_id: str | None = None,
) -> list[SearchHit]:
    query = fts_query_from_text(question)
    paper_filter = _resolve_paper_filter(db, topic=topic, community_id=community_id)

    raw_limit = max(top_k * 12, top_k)
    raw_hits = db.search_chunks(query=query, limit=raw_limit, paper_ids=paper_filter)
    if not raw_hits:
        return []

    # Keep best lexical hit per paper before hybrid reranking.
    best_by_paper: dict[str, SearchHit] = {}
    for hit in raw_hits:
        current = best_by_paper.get(hit.paper_id)
        if current is None or hit.score < current.score:
            best_by_paper[hit.paper_id] = hit

    deduped = list(best_by_paper.values())
    bm25_norm = _normalize([hit.score for hit in deduped], invert=True)

    paper_ids = [hit.paper_id for hit in deduped]
    features = db.paper_rank_features(paper_ids)
    citation_norm = _normalize([float(features.get(paper_id, {}).get("citation_degree", 0.0)) for paper_id in paper_ids])

    query_topics = _query_topics(question)
    if topic:
        query_topics.add(topic.strip().lower())

    reranked: list[SearchHit] = []
    for idx, hit in enumerate(deduped):
        f = features.get(hit.paper_id, {"queue_status": "inbox", "topics": {}})
        queue_status = str(f.get("queue_status") or "inbox").lower()
        queue_boost = QUEUE_BOOST.get(queue_status, QUEUE_BOOST["inbox"])

        topic_scores = f.get("topics") or {}
        matched_topics = [float(score) for label, score in topic_scores.items() if label in query_topics]
        topic_boost = max(matched_topics) if matched_topics else 0.0

        final_score = (
            0.62 * bm25_norm[idx]
            + 0.18 * citation_norm[idx]
            + 0.12 * topic_boost
            + 0.08 * queue_boost
        )
        reranked.append(
            SearchHit(
                paper_id=hit.paper_id,
                title=hit.title,
                path=hit.path,
                snippet=hit.snippet,
                score=float(final_score),
            )
        )

    reranked.sort(key=lambda item: item.score, reverse=True)
    return reranked[:top_k]


def _resolve_backend(config: PaperToolConfig) -> str:
    value = (config.retrieval_backend or "shadow").strip().lower()
    if value not in {"python", "shadow", "rust"}:
        return "shadow"
    return value


def _overlap_at_k(py_hits: list[SearchHit], rust_hits: list[SearchHit], k: int) -> float:
    if k <= 0:
        return 0.0
    py_ids = {hit.paper_id for hit in py_hits[:k]}
    rust_ids = {hit.paper_id for hit in rust_hits[:k]}
    if not py_ids and not rust_ids:
        return 1.0
    if not py_ids:
        return 0.0
    return len(py_ids & rust_ids) / max(len(py_ids), 1)


def retrieve(
    db: PaperDB,
    question: str,
    top_k: int = 6,
    *,
    topic: str | None = None,
    community_id: str | None = None,
    config: PaperToolConfig | None = None,
) -> list[SearchHit]:
    cfg = config or load_config()
    backend = _resolve_backend(cfg)

    if backend == "python":
        return _python_hybrid_retrieve(db, question, top_k=top_k, topic=topic, community_id=community_id)

    if backend == "rust":
        try:
            return rust_retrieve_hits(
                db,
                cfg.rust_index_dir,
                question,
                top_k=top_k,
                topic=topic,
                community_id=community_id,
            )
        except RustBackendUnavailable:
            return _python_hybrid_retrieve(db, question, top_k=top_k, topic=topic, community_id=community_id)

    # shadow mode: return Python result but capture parity telemetry against Rust.
    py_start = time.perf_counter()
    py_hits = _python_hybrid_retrieve(db, question, top_k=top_k, topic=topic, community_id=community_id)
    py_ms = (time.perf_counter() - py_start) * 1000.0

    rust_hits: list[SearchHit] = []
    rust_ms = -1.0
    rust_start = time.perf_counter()
    try:
        rust_hits = rust_retrieve_hits(
            db,
            cfg.rust_index_dir,
            question,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
        )
        rust_ms = (time.perf_counter() - rust_start) * 1000.0
    except RustBackendUnavailable:
        rust_ms = -1.0

    db.log_retrieval_shadow(
        query=question,
        top_k=top_k,
        python_hits_json=json.dumps(hits_to_dict(py_hits), ensure_ascii=True),
        rust_hits_json=json.dumps(hits_to_dict(rust_hits), ensure_ascii=True),
        overlap_at_k=_overlap_at_k(py_hits, rust_hits, top_k),
        py_ms=py_ms,
        rust_ms=rust_ms,
    )
    return py_hits


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fallback_rank_quiz_papers(db: PaperDB, count: int) -> list[dict[str, float | str]]:
    now = datetime.now(timezone.utc)
    rows = db.paper_activity()
    if not rows:
        return []
    ranked: list[dict[str, float | str]] = []
    features = db.paper_rank_features([str(row["id"]) for row in rows])
    for row in rows:
        paper_id = str(row["id"])
        ingested = _parse_iso(str(row["ingested_at"])) or now
        days_old = max((now - ingested).total_seconds() / 86400.0, 0.0)
        recency = 1.0 / (1.0 + days_old / 10.0)
        queue = QUEUE_BOOST.get(str(features.get(paper_id, {}).get("queue_status") or "inbox"), QUEUE_BOOST["inbox"])
        score = 0.8 * recency + 0.2 * queue
        ranked.append({"paper_id": paper_id, "score": float(score)})
    ranked.sort(key=lambda item: float(item["score"]), reverse=True)
    return ranked[: max(count, 1)]


def rank_quiz_papers(
    db: PaperDB,
    count: int,
    *,
    include_queue_boost: bool = True,
    diversify_by_topic: bool = True,
    config: PaperToolConfig | None = None,
) -> list[dict[str, float | str]]:
    cfg = config or load_config()
    if _resolve_backend(cfg) in {"shadow", "rust"}:
        try:
            return rust_rank_quiz_papers(
                db,
                cfg.rust_index_dir,
                count=count,
                include_queue_boost=include_queue_boost,
                diversify_by_topic=diversify_by_topic,
            )
        except RustBackendUnavailable:
            return _fallback_rank_quiz_papers(db, count)
    return _fallback_rank_quiz_papers(db, count)


def synthesize_answer(question: str, hits: list[SearchHit]) -> str:
    if not hits:
        return "No matching passages found yet. Ingest more papers or broaden the query."

    grouped: OrderedDict[str, list[SearchHit]] = OrderedDict()
    for hit in hits:
        grouped.setdefault(hit.paper_id, []).append(hit)

    lines: list[str] = [f"Question: {question}", ""]
    lines.append("Best matching evidence from your library:")
    for paper_hits in grouped.values():
        first = paper_hits[0]
        lines.append(f"- {first.title}")
        lines.append(f"  {first.snippet}")

    lines.append("")
    lines.append("Answer draft:")
    lines.append(
        "Based on the retrieved passages, the key explanation is in the snippets above. "
        "If you want a stronger answer, ask a narrower follow-up question (method, results, or assumptions)."
    )
    return "\n".join(lines)


def hits_to_dict(hits: list[SearchHit]) -> list[dict[str, object]]:
    return [asdict(hit) for hit in hits]
