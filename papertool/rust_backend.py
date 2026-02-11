from __future__ import annotations

import json
import re
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from papertool.db import PaperDB
from papertool.models import SearchHit


class RustBackendUnavailable(RuntimeError):
    pass


_NATIVE_MODULE: Any | None = None
_NATIVE_ERROR: str | None = None

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "into",
    "using",
    "paper",
    "study",
    "result",
    "results",
    "method",
    "methods",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_native() -> Any:
    global _NATIVE_MODULE, _NATIVE_ERROR
    if _NATIVE_MODULE is not None:
        return _NATIVE_MODULE
    if _NATIVE_ERROR is not None:
        raise RustBackendUnavailable(_NATIVE_ERROR)
    try:
        import papertool_retriever_native as native  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on local rust build
        _NATIVE_ERROR = f"rust backend unavailable: {exc}"
        raise RustBackendUnavailable(_NATIVE_ERROR) from exc
    _NATIVE_MODULE = native
    return native


def is_rust_available() -> bool:
    try:
        _load_native()
        return True
    except RustBackendUnavailable:
        return False


def _ensure_index_dir(index_dir: Path | None) -> Path:
    target = (index_dir or Path(".papertool/index/v1")).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_index(db: PaperDB, index_dir: Path | None, paper_id: str | None = None) -> dict[str, Any]:
    target = _ensure_index_dir(index_dir)
    try:
        native = _load_native()
        result = native.build_index(str(db.db_path), str(target), paper_id)
        if isinstance(result, dict):
            return result
        return {"ok": True, "backend": "rust", "indexed": int(result or 0), "index_dir": str(target)}
    except RustBackendUnavailable:
        chunk_count = int(db.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        manifest = {
            "ok": True,
            "backend": "python-fallback",
            "indexed": chunk_count,
            "paper_id": paper_id,
            "index_dir": str(target),
            "built_at": _utc_now(),
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest


def retrieve_hits(
    db: PaperDB,
    index_dir: Path | None,
    query: str,
    *,
    top_k: int = 6,
    topic: str | None = None,
    community_id: str | None = None,
) -> list[SearchHit]:
    target = _ensure_index_dir(index_dir)
    native = _load_native()
    payload = native.retrieve(str(db.db_path), str(target), query, int(top_k), topic, community_id)
    hits: list[SearchHit] = []
    for item in payload:
        hits.append(
            SearchHit(
                paper_id=str(item["paper_id"]),
                title=str(item["title"]),
                path=str(item["path"]),
                snippet=str(item["snippet"]),
                score=float(item["score"]),
            )
        )
    return hits


def rank_quiz_papers(
    db: PaperDB,
    index_dir: Path | None,
    *,
    count: int,
    include_queue_boost: bool = True,
    diversify_by_topic: bool = True,
) -> list[dict[str, Any]]:
    target = _ensure_index_dir(index_dir)
    native = _load_native()
    payload = native.rank_quiz_papers(
        str(db.db_path),
        str(target),
        int(count),
        bool(include_queue_boost),
        bool(diversify_by_topic),
    )
    out: list[dict[str, Any]] = []
    for item in payload:
        out.append(
            {
                "paper_id": str(item["paper_id"]),
                "score": float(item["score"]),
            }
        )
    return out


def _extract_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z][a-z0-9_\-]{2,24}", text.lower())
        if token not in _STOPWORDS and not token.isdigit()
    ]


def _fallback_build_clusters(db: PaperDB) -> dict[str, Any]:
    run_id = db.start_cluster_run(mode="on_demand")
    try:
        papers = db.conn.execute("SELECT id, title, summary, full_text FROM papers").fetchall()
        topic_labels = db.topic_labels()
        known_topics = set(topic_labels)

        corpus_counter: Counter[str] = Counter()
        texts_by_paper: dict[str, str] = {}
        for row in papers:
            text = " ".join(
                [
                    str(row["title"] or ""),
                    str(row["summary"] or ""),
                    str(row["full_text"] or "")[:5000],
                ]
            ).lower()
            texts_by_paper[str(row["id"])] = text
            corpus_counter.update(_extract_tokens(text))

        # Auto-expand with high-frequency terms that are not already known topics.
        for token, freq in corpus_counter.most_common(30):
            if freq < 3:
                continue
            if token in known_topics:
                continue
            db.upsert_topic(token, source="auto")
            known_topics.add(token)

        labels = db.topic_labels()
        for paper_id, text in texts_by_paper.items():
            token_counter = Counter(_extract_tokens(text))
            scored: list[tuple[str, float]] = []
            for label in labels:
                count = token_counter.get(label, 0)
                if count <= 0:
                    continue
                score = min(1.0, 0.35 + 0.2 * count)
                topic_id = db.upsert_topic(label, source="seed" if label in topic_labels else "auto")
                scored.append((topic_id, score))
            db.replace_paper_topics(paper_id, scored)

        # Weakly connected components on citation graph for community IDs.
        db.clear_communities()
        all_ids = [str(row["id"]) for row in papers]
        graph: dict[str, set[str]] = {paper_id: set() for paper_id in all_ids}
        for edge in db.citation_edges():
            source = str(edge["source_paper_id"])
            target = str(edge["target_paper_id"])
            graph.setdefault(source, set()).add(target)
            graph.setdefault(target, set()).add(source)

        seen: set[str] = set()
        community_idx = 0
        for root in all_ids:
            if root in seen:
                continue
            queue: deque[str] = deque([root])
            component: list[str] = []
            seen.add(root)
            while queue:
                node = queue.popleft()
                component.append(node)
                for nxt in graph.get(node, set()):
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    queue.append(nxt)

            community_id = f"comm:{community_idx}"
            community_idx += 1
            size = max(len(component), 1)
            for node in component:
                degree = len(graph.get(node, set()))
                score = min(1.0, (degree + 1) / max(size, 1))
                db.set_paper_community(node, community_id, score)

        db.finish_cluster_run(run_id, status="ok", papers_processed=len(all_ids))
        return {
            "ok": True,
            "backend": "python-fallback",
            "run_id": run_id,
            "papers_processed": len(all_ids),
            "topics_total": len(labels),
            "communities_total": community_idx,
        }
    except Exception:
        db.finish_cluster_run(run_id, status="failed", papers_processed=0)
        raise


def build_clusters(db: PaperDB, index_dir: Path | None) -> dict[str, Any]:
    target = _ensure_index_dir(index_dir)
    try:
        native = _load_native()
        result = native.build_clusters(str(db.db_path), str(target))
        if isinstance(result, dict):
            return result
        return {"ok": True, "backend": "rust", "result": result}
    except RustBackendUnavailable:
        return _fallback_build_clusters(db)
