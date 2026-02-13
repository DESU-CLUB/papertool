from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from papertool.db import PaperDB


@dataclass(slots=True)
class RepoLink:
    url: str
    owner: str
    repo: str
    is_owner_valid: bool


def parse_github_repo_link(url: str) -> RepoLink:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("GitHub URL must start with http:// or https://")
    host = parsed.netloc.lower()
    if host not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com repo URLs are allowed")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub repo URL must include owner and repo")
    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("Invalid GitHub repo URL")
    return RepoLink(
        url=url.strip(),
        owner=owner,
        repo=repo,
        is_owner_valid=(owner == "DESU-CLUB"),
    )


def recompute_day(db: PaperDB, day_key: str) -> dict[str, Any]:
    payload = db.recompute_day_progress(day_key)
    if payload["goal_met"]:
        for paper_id in payload["qualified_paper_ids"]:
            db.award_bronze_for_paper(str(paper_id))
            db.evaluate_paper_medals(str(paper_id))
    return payload


def recompute_streak(db: PaperDB, from_day: str | None = None) -> dict[str, Any]:
    return db.recompute_streak(from_day=from_day)


def evaluate_paper_medals(db: PaperDB, paper_id: str) -> dict[str, Any]:
    return db.evaluate_paper_medals(paper_id)


def recompute_all_medals(db: PaperDB, from_day: str | None = None) -> dict[str, Any]:
    return db.recompute_all_medals(from_day=from_day)


def link_repo_to_paper(db: PaperDB, paper_id: str, url: str) -> dict[str, Any]:
    repo = parse_github_repo_link(url)
    out = db.add_paper_repo_link(
        paper_id=paper_id,
        url=repo.url,
        owner=repo.owner,
        repo=repo.repo,
        is_owner_valid=repo.is_owner_valid,
    )
    db.evaluate_paper_medals(paper_id)
    return out
