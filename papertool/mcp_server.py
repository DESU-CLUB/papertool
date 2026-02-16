from __future__ import annotations

import json
from pathlib import Path

from papertool.ask_service import commit_or_confirm, get_scope_lock_status, prepare_ask_with_lock
from papertool.config import load_config
from papertool.dashboard import build_medals_dashboard
from papertool.medals import link_repo_to_paper, recompute_all_medals
from papertool.graph import export_graph_json
from papertool.ingest import citation_mentions_preview, rebuild_citation_graph
from papertool.planner import (
    due_review_questions,
    generate_micro_quiz_for_paper,
    paper_of_day_payload,
    queue_rows_to_dict,
    validate_queue_status,
)
from papertool.quiz import generate_daily_quiz, quiz_to_dict
from papertool.resources import (
    link_resource_to_paper,
    parse_topics_csv,
    related_resources_for_paper,
    tag_resource_topics,
)
from papertool.retrieval import hits_to_dict, retrieve
from papertool.rust_backend import build_clusters, build_index
from papertool.store import create_store
from papertool.url_import import import_result_to_dict, import_url_to_library

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - dependency optional until installed
    FastMCP = None  # type: ignore[assignment]


class Runtime:
    def __init__(self) -> None:
        self.config = load_config()
        self.store = create_store(self.config)
        self.store.initialize()
        self.db = self.store.db


runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global runtime
    if runtime is None:
        runtime = Runtime()
    return runtime


if FastMCP:
    mcp = FastMCP("papertool")

    @mcp.tool()
    def list_papers(limit: int = 100) -> dict[str, object]:
        """List papers currently in the library."""
        rt = get_runtime()
        rows = rt.db.list_papers()[:limit]
        papers = [
            {
                "id": row["id"],
                "title": row["title"],
                "path": row["path"],
                "doi": row["doi"],
                "arxiv_id": row["arxiv_id"],
                "published_date": row["published_date"],
                "ingested_at": row["ingested_at"],
            }
            for row in rows
        ]
        return {"count": len(papers), "papers": papers}

    @mcp.tool()
    def search_papers(
        query: str,
        top_k: int = 6,
        topic: str | None = None,
        community_id: str | None = None,
    ) -> dict[str, object]:
        """Search paper content and return matching passages."""
        rt = get_runtime()
        hits = retrieve(
            rt.db,
            query,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
            config=rt.config,
        )
        return {
            "query": query,
            "topic": topic,
            "community_id": community_id,
            "hits": hits_to_dict(hits),
        }

    @mcp.tool()
    def ask_papers_prepare(
        question: str,
        top_k: int = 6,
        paper_ids: list[str] | None = None,
        arxiv_ids: list[str] | None = None,
        topic: str | None = None,
        community_id: str | None = None,
        session_id: str | None = None,
        confirm_mode: str | None = None,
    ) -> dict[str, object]:
        """Prepare an ask session and return preview evidence without writing logs or notes."""
        rt = get_runtime()
        try:
            prepared = prepare_ask_with_lock(
                rt.db,
                rt.config,
                question=question,
                top_k=top_k,
                topic=topic,
                community_id=community_id,
                explicit_paper_ids=paper_ids or [],
                explicit_arxiv_ids=arxiv_ids or [],
                channel="mcp",
                session_id=session_id,
                confirm_mode=confirm_mode,
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_confirm_mode", "message": str(exc)}
        if not prepared.get("ok", False):
            return {
                "ok": False,
                "error": "ambiguous_scope",
                "message": prepared.get("message", "Could not resolve question scope."),
                "candidates": prepared.get("candidates", []),
            }
        return {
            "ok": True,
            "requires_confirmation": bool(prepared.get("requires_confirmation", True)),
            "pending_id": prepared["pending_id"],
            "question": prepared["question"],
            "topic": topic,
            "community_id": community_id,
            "paper_ids": prepared["paper_ids"],
            "selected_papers": prepared["selected_papers"],
            "answer_preview": prepared["answer_preview"],
            "sources": prepared["sources"],
            "confirm_mode": prepared.get("confirm_mode"),
            "session_id": prepared.get("session_id"),
            "scope_changed": prepared.get("scope_changed", False),
            "previous_paper_ids": prepared.get("previous_paper_ids", []),
            "new_paper_ids": prepared.get("new_paper_ids", []),
            "auto_commit_eligible": prepared.get("auto_commit_eligible", False),
        }

    @mcp.tool()
    def ask_papers_confirm(
        pending_id: str,
        approve: bool,
        final_answer: str | None = None,
        session_id: str | None = None,
        confirm_mode: str | None = None,
    ) -> dict[str, object]:
        """Confirm or reject a prepared ask session."""
        rt = get_runtime()
        try:
            result = commit_or_confirm(
                rt.db,
                rt.config,
                pending_id=pending_id,
                approve=approve,
                answer_override=final_answer,
                session_id=session_id,
                confirm_mode=confirm_mode,
                channel="mcp",
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_confirm_mode", "message": str(exc)}
        return dict(result)

    @mcp.tool()
    def ask_papers(
        question: str,
        top_k: int = 6,
        final_answer: str | None = None,
        topic: str | None = None,
        community_id: str | None = None,
        paper_ids: list[str] | None = None,
        arxiv_ids: list[str] | None = None,
        session_id: str | None = None,
        confirm_mode: str | None = None,
    ) -> dict[str, object]:
        """Single-call ask with session-scoped confirmation behavior."""
        prepared = ask_papers_prepare(
            question=question,
            top_k=top_k,
            paper_ids=paper_ids,
            arxiv_ids=arxiv_ids,
            topic=topic,
            community_id=community_id,
            session_id=session_id,
            confirm_mode=confirm_mode,
        )
        if not prepared.get("ok", False):
            return prepared
        if bool(prepared.get("auto_commit_eligible", False)):
            rt = get_runtime()
            committed = commit_or_confirm(
                rt.db,
                rt.config,
                pending_id=str(prepared["pending_id"]),
                approve=True,
                answer_override=final_answer,
                session_id=session_id,
                confirm_mode=str(prepared.get("confirm_mode") or confirm_mode or rt.config.ask_confirmation_mode),
                channel="mcp",
            )
            committed["auto_committed"] = True
            committed["requires_confirmation"] = False
            return dict(committed)
        prepared["message"] = "Call ask_papers_confirm with pending_id to approve or reject logging."
        return prepared

    @mcp.tool()
    def ask_scope_lock_status(session_id: str, channel: str = "mcp") -> dict[str, object]:
        """Inspect current ask scope lock for a session id."""
        rt = get_runtime()
        return get_scope_lock_status(rt.db, session_id=session_id, channel=channel)

    @mcp.tool()
    def get_daily_quiz(count: int = 5) -> dict[str, object]:
        """Generate daily quiz questions weighted toward recently-ingested papers."""
        rt = get_runtime()
        questions = generate_daily_quiz(rt.db, count=count)
        return {
            "count": len(questions),
            "questions": quiz_to_dict(questions),
        }

    @mcp.tool()
    def submit_quiz_answer(question_id: str, user_answer: str, score: float | None = None) -> dict[str, object]:
        """Store an answer to a quiz question, with optional score [0-1 or 0-10]."""
        rt = get_runtime()
        if score is not None and (score < 0 or score > 10):
            return {"ok": False, "error": "score_out_of_range", "question_id": question_id}
        row = rt.db.update_quiz_answer(question_id, user_answer, score)
        return {"ok": row is not None, "question_id": question_id, "score": score}

    @mcp.tool()
    def citation_graph() -> dict[str, object]:
        """Return citation graph payload and also persist graph JSON in .papertool/graph.json."""
        rt = get_runtime()
        output = rt.config.db_path.parent / "graph.json"
        export_graph_json(rt.db, output)
        payload = json.loads(output.read_text(encoding="utf-8"))
        return {
            "graph": payload,
            "saved_to": str(output),
        }

    @mcp.tool()
    def rebuild_citations(paper_id: str | None = None) -> dict[str, object]:
        """Rebuild citations for all papers or one paper."""
        rt = get_runtime()
        return rebuild_citation_graph(
            rt.db,
            paper_ids=[paper_id] if paper_id else None,
            config=rt.config,
        )

    @mcp.tool()
    def citation_status() -> dict[str, object]:
        """Return citation edge totals with reason and confidence breakdown."""
        rt = get_runtime()
        return rt.db.citation_status_summary()

    @mcp.tool()
    def paper_citations(paper_id: str) -> dict[str, object]:
        """Inspect incoming and outgoing citation edges for one paper."""
        rt = get_runtime()
        paper = rt.db.get_paper(paper_id)
        if not paper:
            return {"ok": False, "error": "paper_not_found", "paper_id": paper_id}
        payload = rt.db.citation_edges_for_paper(paper_id)
        payload["extracted_mentions_preview"] = citation_mentions_preview(
            rt.db,
            paper_id=paper_id,
            mode=rt.config.citation_title_match_mode,
            limit=80,
        )
        payload["ok"] = True
        payload["paper"] = {"id": str(paper["id"]), "title": str(paper["title"])}
        return payload

    @mcp.tool()
    def import_resource(
        url: str,
        title: str | None = None,
        context_text: str | None = None,
        topics: list[str] | None = None,
        paper_id: str | None = None,
        kind: str | None = None,
    ) -> dict[str, object]:
        """Import a URL (paper, GitHub repo, X post, webpage) into the library and ingest it."""
        rt = get_runtime()
        result = import_url_to_library(
            rt.db,
            rt.config.library_dir,
            url,
            page_title=title,
            context_text=context_text,
            topics=topics,
            link_paper_id=paper_id,
            kind_override=kind,
        )
        return import_result_to_dict(result)

    @mcp.tool()
    def import_resources(urls: list[str]) -> dict[str, object]:
        """Bulk import URLs into the library."""
        rt = get_runtime()
        imported: list[dict[str, object]] = []
        failed: list[dict[str, str]] = []
        for url in urls:
            try:
                result = import_url_to_library(rt.db, rt.config.library_dir, url)
                imported.append(import_result_to_dict(result))
            except Exception as exc:
                failed.append({"url": url, "error": str(exc)})
        return {"imported": imported, "failed": failed}

    @mcp.tool()
    def add_resource(
        url: str,
        title: str | None = None,
        notes: str | None = None,
        topics: list[str] | None = None,
        paper_id: str | None = None,
        kind: str | None = None,
    ) -> dict[str, object]:
        """Save a metadata-only resource bookmark and optionally link it to a paper."""
        rt = get_runtime()
        result = import_url_to_library(
            rt.db,
            rt.config.library_dir,
            url,
            page_title=title,
            context_text=notes,
            topics=topics or [],
            link_paper_id=paper_id,
            kind_override=kind,
        )
        return import_result_to_dict(result)

    @mcp.tool()
    def list_resources(kind: str | None = None, topic: str | None = None, limit: int = 100) -> dict[str, object]:
        """List saved resources with optional kind/topic filters."""
        rt = get_runtime()
        rows = rt.db.list_resources(kind=kind, topic=topic, limit=max(1, limit))
        return {"count": len(rows), "resources": rows}

    @mcp.tool()
    def resource_details(resource_id: str) -> dict[str, object]:
        """Get one resource with topics and linked papers."""
        rt = get_runtime()
        row = rt.db.get_resource(resource_id)
        if not row:
            return {"ok": False, "error": "resource_not_found", "resource_id": resource_id}
        return {
            "ok": True,
            "resource": row,
            "topics": rt.db.resource_topics(resource_id),
            "paper_links": rt.db.resource_links_for_resource(resource_id),
        }

    @mcp.tool()
    def tag_resource(resource_id: str, topics: list[str] | str) -> dict[str, object]:
        """Tag a resource with existing topic labels."""
        rt = get_runtime()
        if not rt.db.get_resource(resource_id):
            return {"ok": False, "error": "resource_not_found", "resource_id": resource_id}
        labels = parse_topics_csv(topics) if isinstance(topics, str) else [str(item) for item in topics]
        tagged = tag_resource_topics(
            rt.db,
            resource_id=resource_id,
            manual_topics=labels,
            heuristic_text="",
        )
        return {"ok": True, "resource_id": resource_id, "topics": tagged}

    @mcp.tool()
    def link_resource(resource_id: str, paper_id: str, link_type: str = "related") -> dict[str, object]:
        """Link a resource to a paper for reading enrichment."""
        rt = get_runtime()
        try:
            link = link_resource_to_paper(
                rt.db,
                resource_id=resource_id,
                paper_id=paper_id,
                link_type=link_type,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "link": link}

    @mcp.tool()
    def paper_resources(paper_id: str, limit: int = 20) -> dict[str, object]:
        """List resources linked or related by topic for a paper."""
        rt = get_runtime()
        if not rt.db.get_paper(paper_id):
            return {"ok": False, "error": "paper_not_found", "paper_id": paper_id}
        rows = related_resources_for_paper(rt.db, paper_id, limit=max(1, limit))
        return {"ok": True, "paper_id": paper_id, "count": len(rows), "resources": rows}

    @mcp.tool()
    def build_retrieval_index(paper_id: str | None = None) -> dict[str, object]:
        """Build or refresh the Rust retrieval index."""
        rt = get_runtime()
        return build_index(rt.db, rt.config.rust_index_dir, paper_id=paper_id)

    @mcp.tool()
    def build_clusters_index() -> dict[str, object]:
        """Build topic and citation-community clusters on-demand."""
        rt = get_runtime()
        return build_clusters(rt.db, rt.config.rust_index_dir)

    @mcp.tool()
    def clusters_overview(type: str = "topic", limit: int = 50) -> dict[str, object]:
        """List topic or citation-community cluster buckets."""
        rt = get_runtime()
        rows = rt.db.cluster_overview(type, limit=max(1, limit))
        return {
            "type": type,
            "count": len(rows),
            "clusters": [
                {
                    "cluster_key": row["cluster_key"],
                    "paper_count": row["paper_count"],
                    "avg_score": row["avg_score"],
                }
                for row in rows
            ],
        }

    @mcp.tool()
    def cluster_papers(topic: str | None = None, community_id: str | None = None, limit: int = 100) -> dict[str, object]:
        """List papers in a topic cluster or citation community."""
        rt = get_runtime()
        rows = rt.db.cluster_papers(topic=topic, community_id=community_id, limit=max(1, limit))
        return {
            "topic": topic,
            "community_id": community_id,
            "count": len(rows),
            "papers": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "path": row["path"],
                    "cluster_score": row["cluster_score"],
                }
                for row in rows
            ],
        }

    @mcp.tool()
    def sync_status() -> dict[str, object]:
        """Return sync backend status and last pull/push timestamps."""
        rt = get_runtime()
        return rt.store.sync_status()

    @mcp.tool()
    def sync_now(pull: bool = True, push: bool = True) -> dict[str, object]:
        """Run an immediate sync cycle against remote backend."""
        rt = get_runtime()
        return rt.store.sync_run(pull=pull, push=push)

    @mcp.tool()
    def remote_health() -> dict[str, object]:
        """Check configured remote API and CouchDB health."""
        rt = get_runtime()
        return rt.store.remote_health()

    @mcp.tool()
    def queue_overview(status: str | None = None, limit: int = 50) -> dict[str, object]:
        """List reading queue items."""
        rt = get_runtime()
        status_filter = validate_queue_status(status) if status else None
        rows = rt.db.queue_list(status=status_filter, limit=max(1, limit))
        return {"count": len(rows), "items": queue_rows_to_dict(rows)}

    @mcp.tool()
    def queue_set(paper_id: str, status: str, priority: float | None = None) -> dict[str, object]:
        """Set queue status (inbox|today|next|later|done) and optional priority for a paper."""
        rt = get_runtime()
        status_value = validate_queue_status(status)
        rt.db.queue_set_status(paper_id, status_value, priority=priority)
        return {"ok": True, "paper_id": paper_id, "status": status_value, "priority": priority}

    @mcp.tool()
    def plan_today(max_items: int = 3) -> dict[str, object]:
        """Auto-select a small daily reading list and move those papers to 'today'."""
        rt = get_runtime()
        rows = rt.db.plan_today(max_items=max(1, max_items))
        return {"count": len(rows), "items": queue_rows_to_dict(rows)}

    @mcp.tool()
    def paper_of_day(include_quiz: bool = False, quiz_count: int = 3, show_resources: bool = False) -> dict[str, object]:
        """Get one paper to read now; optionally generate a short micro-quiz."""
        rt = get_runtime()
        payload = paper_of_day_payload(rt.db)
        if not payload:
            return {"ok": False, "error": "no_paper_available"}
        out: dict[str, object] = {"ok": True, "paper": payload}
        if show_resources:
            out["resources"] = related_resources_for_paper(rt.db, str(payload["paper_id"]), limit=20)
        if include_quiz:
            questions = generate_micro_quiz_for_paper(rt.db, str(payload["paper_id"]), count=max(1, quiz_count))
            out["quiz"] = quiz_to_dict(questions)
        return out

    @mcp.tool()
    def complete_reading(paper_id: str, quiz_count: int = 3) -> dict[str, object]:
        """Mark paper as done and generate a short post-read micro-quiz."""
        rt = get_runtime()
        paper = rt.db.get_paper(paper_id)
        if not paper:
            return {"ok": False, "error": "paper_not_found", "paper_id": paper_id}
        rt.db.mark_done(paper_id)
        questions = generate_micro_quiz_for_paper(rt.db, paper_id, count=max(1, quiz_count))
        return {
            "ok": True,
            "paper_id": paper_id,
            "title": paper["title"],
            "quiz": quiz_to_dict(questions),
        }

    @mcp.tool()
    def due_reviews(count: int = 5) -> dict[str, object]:
        """Generate currently due spaced-review questions."""
        rt = get_runtime()
        questions = due_review_questions(rt.db, count=max(1, count))
        return {"count": len(questions), "questions": quiz_to_dict(questions)}

    @mcp.tool()
    def set_daily_goal(daily_goal: int, timezone: str = "America/Los_Angeles") -> dict[str, object]:
        """Set daily paper goal and timezone for streak tracking."""
        rt = get_runtime()
        payload = rt.db.set_goal_settings(int(daily_goal), timezone)
        recompute_all_medals(rt.db)
        return {"ok": True, "goal": payload}

    @mcp.tool()
    def goal_status() -> dict[str, object]:
        """Return current daily goal settings and streak summary."""
        rt = get_runtime()
        goal = rt.db.get_goal_settings()
        rows = rt.db.daily_progress_rows(limit=3650)
        today = rt.db.day_key_now(str(goal["timezone"]))
        today_row = next((row for row in rows if str(row["day_key"]) == today), None)
        current_streak = int(today_row["streak_value"]) if today_row else 0
        longest_streak = max((int(row["streak_value"]) for row in rows), default=0)
        return {
            "goal": goal,
            "today": today,
            "today_progress": today_row or {},
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        }

    @mcp.tool()
    def link_paper_repo(paper_id: str, url: str) -> dict[str, object]:
        """Link a GitHub repo to a paper; DESU-CLUB owner links can award Gold."""
        rt = get_runtime()
        paper = rt.db.get_paper(paper_id)
        if not paper:
            return {"ok": False, "error": "paper_not_found", "paper_id": paper_id}
        payload = link_repo_to_paper(rt.db, paper_id, url)
        return {"ok": True, "link": payload, "medals": rt.db.get_paper_medal(paper_id)}

    @mcp.tool()
    def paper_medals(paper_id: str) -> dict[str, object]:
        """Get medal state and linked repos for a paper."""
        rt = get_runtime()
        return {
            "paper_id": paper_id,
            "medals": rt.db.get_paper_medal(paper_id),
            "repo_links": rt.db.paper_repo_links(paper_id),
        }

    @mcp.tool()
    def medals_overview(limit: int = 100) -> dict[str, object]:
        """List medal status for papers."""
        rt = get_runtime()
        rows = rt.db.medal_overview(limit=max(1, limit))
        return {"count": len(rows), "items": rows}

    @mcp.tool()
    def build_medals_dashboard(output_path: str | None = None) -> dict[str, object]:
        """Generate local HTML dashboard for streaks and medals."""
        rt = get_runtime()
        out = rt.config.db_path.parent / "medals.html" if not output_path else Path(output_path).expanduser().resolve()
        built = build_medals_dashboard(rt.db, out)
        return {"ok": True, "output": str(built)}

    @mcp.tool()
    def recompute_medals(from_day: str | None = None) -> dict[str, object]:
        """Recompute streak and medal state from a day onward."""
        rt = get_runtime()
        result = recompute_all_medals(rt.db, from_day=from_day)
        return {"ok": True, "result": result}

else:
    mcp = None


def main() -> None:
    if not mcp:
        raise RuntimeError("MCP library is not installed. Run `pip install -e .` first.")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
