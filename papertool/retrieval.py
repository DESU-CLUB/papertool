from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import asdict

from papertool.db import PaperDB
from papertool.models import SearchHit


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


def fts_query_from_text(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    useful = [token for token in tokens if token not in STOP_WORDS and len(token) > 2]
    if not useful:
        useful = tokens[:3]
    if not useful:
        return "paper"
    return " OR ".join(dict.fromkeys(useful))


def retrieve(db: PaperDB, question: str, top_k: int = 6) -> list[SearchHit]:
    query = fts_query_from_text(question)
    return db.search_chunks(query=query, limit=top_k)


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
