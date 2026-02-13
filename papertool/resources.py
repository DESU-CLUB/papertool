from __future__ import annotations

from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from papertool.db import PaperDB

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "ref",
}

RESOURCE_KINDS = {"x_post", "blog", "webpage", "github", "other"}


def canonicalize_resource_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ValueError("url is required")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
        if not path:
            path = "/"

    clean_params: list[tuple[str, str]] = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=False):
        if key.lower() in TRACKING_QUERY_KEYS:
            continue
        clean_params.append((key, val))
    clean_params.sort()
    query = urlencode(clean_params, doseq=True)
    return urlunparse((scheme, host, path, "", query, ""))


def parse_topics_csv(value: str | None) -> list[str]:
    if not value:
        return []
    items = [part.strip().lower() for part in value.replace(";", ",").split(",")]
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if item not in out:
            out.append(item)
    return out


def _normalize_topic_list(topics: Iterable[str]) -> list[str]:
    out: list[str] = []
    for topic in topics:
        value = topic.strip().lower()
        if value and value not in out:
            out.append(value)
    return out


def match_existing_topics_from_text(db: PaperDB, text: str) -> list[str]:
    blob = text.lower().strip()
    if not blob:
        return []
    tokens = set(blob.replace("/", " ").replace("-", " ").split())
    matches: list[str] = []
    for label in db.topic_labels():
        topic = label.strip().lower()
        if not topic:
            continue
        if topic in blob or topic in tokens:
            matches.append(topic)
    return matches


def upsert_resource(
    db: PaperDB,
    *,
    url: str,
    title: str,
    kind: str,
    notes: str | None = None,
) -> dict[str, object]:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in RESOURCE_KINDS:
        raise ValueError(f"invalid resource kind: {kind}")
    canonical_url = canonicalize_resource_url(url)
    final_title = title.strip() if title.strip() else canonical_url
    row = db.upsert_resource(
        kind=normalized_kind,
        url=url.strip(),
        canonical_url=canonical_url,
        title=final_title,
        notes=notes.strip() if notes else None,
    )
    return row


def tag_resource_topics(
    db: PaperDB,
    *,
    resource_id: str,
    manual_topics: Iterable[str] = (),
    heuristic_text: str | None = None,
) -> list[dict[str, object]]:
    manual = _normalize_topic_list(manual_topics)
    heuristic = match_existing_topics_from_text(db, heuristic_text or "")
    seen: set[str] = set()
    for label in manual:
        topic_id = db.topic_id_for_label(label)
        if not topic_id:
            continue
        db.upsert_resource_topic(resource_id, topic_id, score=1.0, source="manual")
        seen.add(topic_id)
    for label in heuristic:
        topic_id = db.topic_id_for_label(label)
        if not topic_id or topic_id in seen:
            continue
        db.upsert_resource_topic(resource_id, topic_id, score=0.6, source="heuristic")
    return db.resource_topics(resource_id)


def link_resource_to_paper(
    db: PaperDB,
    *,
    resource_id: str,
    paper_id: str,
    link_type: str = "related",
) -> dict[str, object]:
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError(f"paper not found: {paper_id}")
    resource = db.get_resource(resource_id)
    if not resource:
        raise ValueError(f"resource not found: {resource_id}")
    return db.link_paper_resource(paper_id=paper_id, resource_id=resource_id, link_type=link_type)


def related_resources_for_paper(db: PaperDB, paper_id: str, limit: int = 20) -> list[dict[str, object]]:
    return db.related_resources_for_paper(paper_id, limit=max(1, limit))
