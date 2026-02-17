from __future__ import annotations

import json
import hashlib
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from papertool.config import config_from_kwargs, dump_config, load_config
from papertool.couch_client import CouchClient
from papertool.ask_service import commit_or_confirm, prepare_ask_with_lock
from papertool.dashboard import build_medals_dashboard
from papertool.db import PaperDB
from papertool.graph import export_graph_html, export_graph_json, export_graph_mermaid
from papertool.ingest import citation_mentions_preview, ingest_folder, rebuild_citation_graph
from papertool.medals import link_repo_to_paper, recompute_all_medals
from papertool.planner import (
    due_review_questions,
    generate_micro_quiz_for_paper,
    paper_of_day_payload,
    queue_rows_to_dict,
    validate_queue_status,
)
from papertool.quiz import generate_daily_quiz
from papertool.resources import (
    link_resource_to_paper as link_resource_to_paper_rel,
    parse_topics_csv,
    related_resources_for_paper,
    tag_resource_topics,
)
from papertool.retrieval import hits_to_dict, retrieve
from papertool.rust_backend import build_clusters, build_index
from papertool.store import create_store
from papertool.url_import import import_result_to_dict, import_url_to_library

app = typer.Typer(help="PaperTool CLI")
graph_app = typer.Typer(help="Graph export commands")
queue_app = typer.Typer(help="Reading queue commands")
index_app = typer.Typer(help="Retrieval index commands")
cluster_app = typer.Typer(help="Clustering commands")
citations_app = typer.Typer(help="Citation enrichment and inspection commands")
sync_app = typer.Typer(help="Sync commands")
migrate_app = typer.Typer(help="Migration commands")
remote_app = typer.Typer(help="Remote API and worker commands")
goal_app = typer.Typer(help="Daily goal and streak commands")
medals_app = typer.Typer(help="Medal commands")
resource_app = typer.Typer(help="Resource bookmark and tagging commands")
app.add_typer(graph_app, name="graph")
app.add_typer(queue_app, name="queue")
app.add_typer(index_app, name="index")
app.add_typer(cluster_app, name="cluster")
app.add_typer(citations_app, name="citations")
app.add_typer(sync_app, name="sync")
app.add_typer(migrate_app, name="migrate")
app.add_typer(remote_app, name="remote")
app.add_typer(goal_app, name="goal")
app.add_typer(medals_app, name="medals")
app.add_typer(resource_app, name="resource")


def _db() -> tuple[PaperDB, object]:
    cfg = load_config()
    store = create_store(cfg)
    store.initialize()
    db = store.db
    setattr(db, "_papertool_store", store)
    return db, cfg


def _close_db(db: PaperDB) -> None:
    store = getattr(db, "_papertool_store", None)
    if store is not None:
        store.close()
        return
    db.close()


def _normalize_confirm_mode(value: str | None, default: str) -> str:
    mode = (value or default or "session").strip().lower()
    if mode not in {"session", "always", "never"}:
        raise typer.BadParameter("confirm mode must be one of: session, always, never")
    return mode


def _default_cli_session_id() -> str:
    workspace = str(Path.cwd().resolve())
    digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:16]
    return f"workspace:{digest}"


@app.command()
def init(
    library_dir: Optional[str] = typer.Option(None, help="Path to folder containing papers"),
    db_path: Optional[str] = typer.Option(None, help="Path to SQLite DB"),
    retrieval_backend: str = typer.Option("shadow", help="python|shadow|rust"),
    rust_index_dir: Optional[str] = typer.Option(None, help="Path to Rust retrieval index dir"),
    cluster_mode: str = typer.Option("on_demand", help="Cluster update mode"),
    storage_backend: str = typer.Option("sqlite", help="sqlite|hybrid|couch"),
    remote_api_base_url: Optional[str] = typer.Option(None, help="Remote API base URL"),
    remote_api_token: Optional[str] = typer.Option(None, help="Remote API bearer token"),
    couchdb_url: Optional[str] = typer.Option(None, help="CouchDB base URL"),
    couchdb_db_meta: str = typer.Option("papertool_meta", help="CouchDB metadata database name"),
    couchdb_db_events: str = typer.Option("papertool_events", help="CouchDB events database name"),
    couchdb_db_jobs: str = typer.Option("papertool_jobs", help="CouchDB jobs database name"),
    minio_endpoint: Optional[str] = typer.Option(None, help="MinIO endpoint URL"),
    minio_bucket: str = typer.Option("papertool-files", help="MinIO bucket"),
    minio_access_key: Optional[str] = typer.Option(None, help="MinIO access key"),
    minio_secret_key: Optional[str] = typer.Option(None, help="MinIO secret key"),
    sync_enabled: bool = typer.Option(True, "--sync-enabled/--no-sync-enabled", help="Enable sync to remote store"),
    sync_pull_interval_sec: int = typer.Option(30, help="Default pull interval in seconds"),
    sync_push_interval_sec: int = typer.Option(30, help="Default push interval in seconds"),
    daily_goal: int = typer.Option(1, help="Daily paper completion+quiz goal"),
    goal_timezone: str = typer.Option("America/Los_Angeles", help="IANA timezone for streak/day boundaries"),
    ask_confirmation_mode: str = typer.Option("session", help="Ask confirm mode: session|always|never"),
    ask_session_ttl_sec: int = typer.Option(1800, help="Ask session lock TTL in seconds"),
    ask_cli_auto_session: bool = typer.Option(True, "--ask-cli-auto-session/--no-ask-cli-auto-session", help="Auto derive workspace session_id for CLI ask"),
    citation_refresh_on_import: bool = typer.Option(True, "--citation-refresh-on-import/--no-citation-refresh-on-import", help="Refresh citations after ingest/import"),
    citation_title_match_mode: str = typer.Option("conservative", help="Title match mode: conservative|balanced|aggressive"),
) -> None:
    root = Path.cwd()
    cfg = config_from_kwargs(
        root,
        {
            "library_dir": library_dir,
            "db_path": db_path,
            "retrieval_backend": retrieval_backend,
            "rust_index_dir": rust_index_dir,
            "cluster_mode": cluster_mode,
            "storage_backend": storage_backend,
            "remote_api_base_url": remote_api_base_url,
            "remote_api_token": remote_api_token,
            "couchdb_url": couchdb_url,
            "couchdb_db_meta": couchdb_db_meta,
            "couchdb_db_events": couchdb_db_events,
            "couchdb_db_jobs": couchdb_db_jobs,
            "minio_endpoint": minio_endpoint,
            "minio_bucket": minio_bucket,
            "minio_access_key": minio_access_key,
            "minio_secret_key": minio_secret_key,
            "sync_enabled": sync_enabled,
            "sync_pull_interval_sec": sync_pull_interval_sec,
            "sync_push_interval_sec": sync_push_interval_sec,
            "daily_goal": daily_goal,
            "goal_timezone": goal_timezone,
            "ask_confirmation_mode": ask_confirmation_mode,
            "ask_session_ttl_sec": ask_session_ttl_sec,
            "ask_cli_auto_session": ask_cli_auto_session,
            "citation_refresh_on_import": citation_refresh_on_import,
            "citation_title_match_mode": citation_title_match_mode,
        },
    )
    out = dump_config(cfg)
    cfg.library_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Wrote config: {out}")


@app.command()
def ingest(folder: Optional[str] = typer.Option(None, help="Paper folder; defaults to configured library_dir")) -> None:
    db, cfg = _db()
    try:
        target = Path(folder).expanduser().resolve() if folder else cfg.library_dir
        target.mkdir(parents=True, exist_ok=True)
        stats = ingest_folder(db, target, config=cfg)
        typer.echo(
            f"Scanned {stats.scanned} files, ingested {stats.ingested}, skipped {stats.skipped}."
        )
    finally:
        _close_db(db)


@app.command("list")
def list_papers(limit: int = typer.Option(100, help="Maximum papers to print")) -> None:
    db, _cfg = _db()
    try:
        rows = db.list_papers()[:limit]
        for row in rows:
            typer.echo(f"{row['id'][:8]}  {row['title']}  ({row['path']})")
        typer.echo(f"Total: {len(rows)}")
    finally:
        _close_db(db)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(6, help="Number of passages to retrieve"),
    topic: Optional[str] = typer.Option(None, help="Optional topic filter label"),
    community_id: Optional[str] = typer.Option(None, help="Optional citation community filter"),
) -> None:
    db, cfg = _db()
    try:
        hits = retrieve(
            db,
            query,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
            config=cfg,
        )
        typer.echo(json.dumps(hits_to_dict(hits), ensure_ascii=True))
    finally:
        _close_db(db)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question about your paper library"),
    top_k: int = typer.Option(6, help="Number of passages to retrieve"),
    topic: Optional[str] = typer.Option(None, help="Optional topic filter label"),
    community_id: Optional[str] = typer.Option(None, help="Optional citation community filter"),
    paper_id: list[str] = typer.Option([], "--paper-id", help="Restrict ask to paper ID (repeatable)"),
    arxiv_id: list[str] = typer.Option([], "--arxiv-id", help="Restrict ask to arXiv ID (repeatable)"),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Optional ask session identifier"),
    confirm_mode: Optional[str] = typer.Option(None, "--confirm-mode", help="session|always|never"),
    confirm: Optional[str] = typer.Option(None, help="yes|no (required when confirmation is needed in non-interactive mode)"),
) -> None:
    db, cfg = _db()
    try:
        effective_mode = _normalize_confirm_mode(confirm_mode, getattr(cfg, "ask_confirmation_mode", "session"))
        effective_session_id = session_id
        if effective_mode == "session" and not effective_session_id and bool(getattr(cfg, "ask_cli_auto_session", True)):
            effective_session_id = _default_cli_session_id()

        prepared = prepare_ask_with_lock(
            db,
            cfg,  # type: ignore[arg-type]
            question=question,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
            explicit_paper_ids=list(paper_id),
            explicit_arxiv_ids=list(arxiv_id),
            channel="cli",
            session_id=effective_session_id,
            confirm_mode=effective_mode,
        )
        if not prepared.get("ok", False):
            typer.echo(str(prepared.get("message") or "Unable to resolve question scope."), err=True)
            candidates = prepared.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                typer.echo("Candidate papers:", err=True)
                for row in candidates[:8]:
                    if isinstance(row, dict):
                        typer.echo(
                            f"- {str(row.get('paper_id', ''))} :: {row.get('title', '')} "
                            f"(score={float(row.get('score', 0.0)):.2f})",
                            err=True,
                        )
            raise typer.Exit(code=2)

        selected = prepared.get("selected_papers", [])
        if isinstance(selected, list) and selected:
            typer.echo("Selected papers:")
            for row in selected:
                if isinstance(row, dict):
                    typer.echo(
                        f"- {str(row.get('paper_id', ''))[:8]} :: {row.get('title', '')} "
                        f"(score={float(row.get('score', 0.0)):.2f})"
                    )
            typer.echo("")

        answer_preview = str(prepared.get("answer_preview") or "")
        typer.echo(answer_preview)

        pending_id = str(prepared.get("pending_id"))
        if bool(prepared.get("auto_commit_eligible", False)):
            result = commit_or_confirm(
                db,
                cfg,  # type: ignore[arg-type]
                pending_id=pending_id,
                approve=True,
                session_id=effective_session_id,
                confirm_mode=effective_mode,
                channel="cli",
            )
            if not result.get("ok", False):
                typer.echo(
                    f"Auto-commit failed: {result.get('status', 'unknown_error')} (pending_id={pending_id})",
                    err=True,
                )
                raise typer.Exit(code=1)
            typer.echo(f"Auto-logged ask for session. pending_id={pending_id}")
            return

        normalized_confirm: Optional[bool] = None
        if confirm is not None:
            value = confirm.strip().lower()
            if value not in {"yes", "no"}:
                raise typer.BadParameter("--confirm must be 'yes' or 'no'")
            normalized_confirm = value == "yes"
        elif not sys.stdin.isatty():
            raise typer.BadParameter("Non-interactive mode requires --confirm yes|no when confirmation is required")

        approve = normalized_confirm if normalized_confirm is not None else typer.confirm(
            "Log this to selected papers?",
            default=False,
        )
        result = commit_or_confirm(
            db,
            cfg,  # type: ignore[arg-type]
            pending_id=pending_id,
            approve=approve,
            session_id=effective_session_id,
            confirm_mode=effective_mode,
            channel="cli",
        )
        if not approve:
            typer.echo(f"Skipped logging. pending_id={pending_id}")
            return
        if not result.get("ok", False):
            typer.echo(
                f"Failed to confirm ask session: {result.get('status', 'unknown_error')} (pending_id={pending_id})",
                err=True,
            )
            raise typer.Exit(code=1)
    finally:
        _close_db(db)


@app.command()
def quiz(count: int = typer.Option(5, help="How many questions to generate")) -> None:
    db, _cfg = _db()
    try:
        questions = generate_daily_quiz(db, count=count)
        if not questions:
            typer.echo("No papers found. Run `papertool ingest` first.")
            return
        for idx, question in enumerate(questions, start=1):
            typer.echo(f"[{idx}] {question.prompt}")
            typer.echo(f"    question_id={question.question_id}")
    finally:
        _close_db(db)


@app.command("today")
def today(count: int = typer.Option(3, help="How many papers to plan for today")) -> None:
    db, _cfg = _db()
    try:
        rows = db.plan_today(max_items=max(1, count))
        if not rows:
            typer.echo("No papers available to plan. Import or ingest papers first.")
            return
        for idx, item in enumerate(queue_rows_to_dict(rows), start=1):
            typer.echo(f"[{idx}] {item['title']} ({str(item['paper_id'])[:8]})")
            typer.echo(f"    status={item['status']} priority={item['priority']} path={item['path']}")
    finally:
        _close_db(db)


@app.command("paper-of-day")
def paper_of_day(
    include_quiz: bool = typer.Option(False, "--quiz/--no-quiz", help="Generate short quiz for this paper"),
    quiz_count: int = typer.Option(3, help="Quiz question count if --quiz is set"),
    show_resources: bool = typer.Option(False, "--show-resources/--no-show-resources", help="Show related resources"),
) -> None:
    db, _cfg = _db()
    try:
        payload = paper_of_day_payload(db)
        if not payload:
            typer.echo("No paper available. Add papers to queue or ingest/import first.")
            return
        typer.echo(f"Have you read this paper: {payload['title']} ({str(payload['paper_id'])[:8]})")
        typer.echo(f"Path: {payload['path']}")
        if show_resources:
            resources = related_resources_for_paper(db, str(payload["paper_id"]), limit=10)
            if resources:
                typer.echo("Related resources:")
                for item in resources:
                    typer.echo(
                        f"  - [{item.get('resource_kind', 'resource')}] {item.get('resource_title', '')} :: {item.get('resource_url', '')}"
                    )
            else:
                typer.echo("Related resources: none")
        if include_quiz:
            questions = generate_micro_quiz_for_paper(db, str(payload["paper_id"]), count=quiz_count)
            for idx, question in enumerate(questions, start=1):
                typer.echo(f"  Q{idx}. {question.prompt}")
                typer.echo(f"     question_id={question.question_id}")
    finally:
        _close_db(db)


@app.command("complete-reading")
def complete_reading(
    paper_id: str = typer.Option(..., help="Paper ID to mark as done"),
    quiz_count: int = typer.Option(3, help="How many micro-quiz questions to generate"),
) -> None:
    db, _cfg = _db()
    try:
        paper = db.get_paper(paper_id)
        if not paper:
            raise typer.BadParameter(f"Paper not found: {paper_id}")
        db.mark_done(paper_id)
        questions = generate_micro_quiz_for_paper(db, paper_id, count=quiz_count)
        typer.echo(f"Marked done: {paper['title']}")
        for idx, question in enumerate(questions, start=1):
            typer.echo(f"  Q{idx}. {question.prompt}")
            typer.echo(f"     question_id={question.question_id}")
    finally:
        _close_db(db)


@app.command("review-due")
def review_due(count: int = typer.Option(5, help="How many due review questions to generate")) -> None:
    db, _cfg = _db()
    try:
        questions = due_review_questions(db, count=max(1, count))
        if not questions:
            typer.echo("No due reviews right now.")
            return
        for idx, question in enumerate(questions, start=1):
            typer.echo(f"[{idx}] {question.prompt}")
            typer.echo(f"    paper={question.paper_title} question_id={question.question_id}")
    finally:
        _close_db(db)


@app.command("submit-answer")
def submit_answer(
    question_id: str = typer.Option(..., help="Quiz question ID"),
    answer: str = typer.Option(..., help="Your answer"),
    score: Optional[float] = typer.Option(None, help="Self score from 0-1 or 0-10"),
) -> None:
    db, _cfg = _db()
    try:
        if score is not None and (score < 0 or score > 10):
            raise typer.BadParameter("score must be between 0 and 10")
        row = db.update_quiz_answer(question_id, answer, score)
        if not row:
            raise typer.BadParameter(f"Question not found: {question_id}")
        typer.echo(f"Saved answer for question_id={question_id}")
        if score is not None:
            typer.echo("Review schedule updated.")
    finally:
        _close_db(db)


@app.command("import-url")
def import_url(
    url: str = typer.Argument(..., help="URL to import (paper, repo, x post, or webpage)"),
    title: Optional[str] = typer.Option(None, help="Optional title override"),
    context_text: Optional[str] = typer.Option(None, help="Optional context to store with the capture"),
    topics: Optional[str] = typer.Option(None, help="Comma-separated topics (existing topic labels)"),
    link_paper_id: Optional[str] = typer.Option(None, help="Optional paper id to link resource to"),
    kind: Optional[str] = typer.Option(None, help="Optional kind override: x_post|blog|webpage|github|other|arxiv|pdf"),
) -> None:
    db, cfg = _db()
    try:
        result = import_url_to_library(
            db,
            cfg.library_dir,
            url,
            page_title=title,
            context_text=context_text,
            topics=parse_topics_csv(topics),
            link_paper_id=link_paper_id,
            kind_override=kind,
        )
        typer.echo(json.dumps(import_result_to_dict(result), ensure_ascii=True))
    finally:
        _close_db(db)


@resource_app.command("list")
def resource_list(
    kind: Optional[str] = typer.Option(None, help="Filter by kind"),
    topic: Optional[str] = typer.Option(None, help="Filter by topic label"),
    limit: int = typer.Option(100, help="Maximum rows"),
) -> None:
    db, _cfg = _db()
    try:
        rows = db.list_resources(kind=kind, topic=topic, limit=max(1, limit))
        typer.echo(json.dumps(rows, ensure_ascii=True))
    finally:
        _close_db(db)


@resource_app.command("show")
def resource_show(
    resource_id: str = typer.Option(..., help="Resource ID"),
) -> None:
    db, _cfg = _db()
    try:
        row = db.get_resource(resource_id)
        if not row:
            raise typer.BadParameter(f"Resource not found: {resource_id}")
        payload = {
            "resource": row,
            "topics": db.resource_topics(resource_id),
            "paper_links": db.resource_links_for_resource(resource_id),
        }
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@resource_app.command("tag")
def resource_tag(
    resource_id: str = typer.Option(..., help="Resource ID"),
    topics: str = typer.Option(..., help="Comma-separated topic labels"),
) -> None:
    db, _cfg = _db()
    try:
        row = db.get_resource(resource_id)
        if not row:
            raise typer.BadParameter(f"Resource not found: {resource_id}")
        tagged = tag_resource_topics(
            db,
            resource_id=resource_id,
            manual_topics=parse_topics_csv(topics),
            heuristic_text="",
        )
        typer.echo(json.dumps({"resource_id": resource_id, "topics": tagged}, ensure_ascii=True))
    finally:
        _close_db(db)


@resource_app.command("link")
def resource_link(
    resource_id: str = typer.Option(..., help="Resource ID"),
    paper_id: str = typer.Option(..., help="Paper ID"),
    type: str = typer.Option("related", "--type", help="related|implementation|update|background"),
) -> None:
    db, _cfg = _db()
    try:
        payload = link_resource_to_paper_rel(
            db,
            resource_id=resource_id,
            paper_id=paper_id,
            link_type=type,
        )
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@resource_app.command("links")
def resource_links(
    paper_id: str = typer.Option(..., help="Paper ID"),
    limit: int = typer.Option(20, help="Maximum rows"),
) -> None:
    db, _cfg = _db()
    try:
        rows = db.resource_links_for_paper(paper_id, limit=max(1, limit))
        typer.echo(json.dumps(rows, ensure_ascii=True))
    finally:
        _close_db(db)


@queue_app.command("list")
def queue_list(
    status: Optional[str] = typer.Option(None, help="Filter status: inbox|today|next|later|done"),
    limit: int = typer.Option(50, help="Maximum rows"),
) -> None:
    db, _cfg = _db()
    try:
        status_filter = validate_queue_status(status) if status else None
        rows = db.queue_list(status=status_filter, limit=limit)
        for row in queue_rows_to_dict(rows):
            typer.echo(f"{str(row['paper_id'])[:8]}  {row['status']}  {row['title']}")
        typer.echo(f"Total: {len(rows)}")
    finally:
        _close_db(db)


@queue_app.command("set")
def queue_set(
    paper_id: str = typer.Option(..., help="Paper ID"),
    status: str = typer.Option(..., help="inbox|today|next|later|done"),
    priority: Optional[float] = typer.Option(None, help="Optional priority value"),
) -> None:
    db, _cfg = _db()
    try:
        status_value = validate_queue_status(status)
        db.queue_set_status(paper_id, status_value, priority=priority)
        typer.echo(f"Updated queue: {paper_id} -> {status_value}")
    finally:
        _close_db(db)


@goal_app.command("set")
def goal_set(
    daily: int = typer.Option(..., "--daily", help="Daily goal count"),
    timezone: str = typer.Option("America/Los_Angeles", "--timezone", help="IANA timezone"),
) -> None:
    db, _cfg = _db()
    try:
        payload = db.set_goal_settings(daily, timezone)
        db.recompute_all_medals()
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@goal_app.command("status")
def goal_status() -> None:
    db, _cfg = _db()
    try:
        goal = db.get_goal_settings()
        today = db.day_key_now(str(goal["timezone"]))
        today_row = None
        for row in db.daily_progress_rows(limit=120):
            if str(row["day_key"]) == today:
                today_row = row
                break
        current_streak = int(today_row["streak_value"]) if today_row else 0
        longest_streak = max((int(row["streak_value"]) for row in db.daily_progress_rows(limit=3650)), default=0)
        typer.echo(
            json.dumps(
                {
                    "goal": goal,
                    "today": today,
                    "today_progress": today_row or {},
                    "current_streak": current_streak,
                    "longest_streak": longest_streak,
                },
                ensure_ascii=True,
            )
        )
    finally:
        _close_db(db)


@medals_app.command("link-repo")
def medals_link_repo(
    paper_id: str = typer.Option(..., help="Paper ID"),
    url: str = typer.Option(..., help="GitHub repository URL"),
) -> None:
    db, _cfg = _db()
    try:
        paper = db.get_paper(paper_id)
        if not paper:
            raise typer.BadParameter(f"Paper not found: {paper_id}")
        payload = link_repo_to_paper(db, paper_id, url)
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@medals_app.command("status")
def medals_status(
    paper_id: Optional[str] = typer.Option(None, help="Optional paper ID filter"),
    limit: int = typer.Option(100, help="Maximum rows"),
) -> None:
    db, _cfg = _db()
    try:
        if paper_id:
            payload = {
                "paper_id": paper_id,
                "medals": db.get_paper_medal(paper_id),
                "repo_links": db.paper_repo_links(paper_id),
            }
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        rows = db.medal_overview(limit=max(1, limit))
        typer.echo(json.dumps(rows, ensure_ascii=True))
    finally:
        _close_db(db)


@medals_app.command("recompute")
def medals_recompute(
    from_day: Optional[str] = typer.Option(None, "--from", help="YYYY-MM-DD start day"),
) -> None:
    db, _cfg = _db()
    try:
        result = recompute_all_medals(db, from_day=from_day)
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        _close_db(db)


@medals_app.command("dashboard")
def medals_dashboard(
    output: str = typer.Option(".papertool/medals.html", help="Output HTML path"),
) -> None:
    db, _cfg = _db()
    try:
        out = Path(output).expanduser().resolve()
        built = build_medals_dashboard(db, out)
        typer.echo(f"Dashboard written to: {built}")
    finally:
        _close_db(db)


@index_app.command("build")
def index_build() -> None:
    db, cfg = _db()
    try:
        result = build_index(db, cfg.rust_index_dir)
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        _close_db(db)


@index_app.command("refresh")
def index_refresh(paper_id: Optional[str] = typer.Option(None, help="Optional single paper ID to refresh")) -> None:
    db, cfg = _db()
    try:
        result = build_index(db, cfg.rust_index_dir, paper_id=paper_id)
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        _close_db(db)


@cluster_app.command("build")
def cluster_build() -> None:
    db, cfg = _db()
    try:
        result = build_clusters(db, cfg.rust_index_dir)
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        _close_db(db)


@cluster_app.command("list")
def cluster_list(
    type: str = typer.Option("topic", "--type", help="topic|community"),
    limit: int = typer.Option(50, help="Maximum rows"),
) -> None:
    db, _cfg = _db()
    try:
        rows = db.cluster_overview(type, limit=max(1, limit))
        for row in rows:
            typer.echo(
                json.dumps(
                    {
                        "cluster_key": row["cluster_key"],
                        "paper_count": row["paper_count"],
                        "avg_score": row["avg_score"],
                    },
                    ensure_ascii=True,
                )
            )
        typer.echo(f"Total: {len(rows)}")
    finally:
        _close_db(db)


@cluster_app.command("papers")
def cluster_papers(
    topic: Optional[str] = typer.Option(None, help="Topic label"),
    community: Optional[str] = typer.Option(None, "--community", help="Community ID"),
    limit: int = typer.Option(100, help="Maximum rows"),
) -> None:
    if not topic and not community:
        raise typer.BadParameter("provide --topic or --community")
    db, _cfg = _db()
    try:
        rows = db.cluster_papers(topic=topic, community_id=community, limit=max(1, limit))
        for row in rows:
            typer.echo(
                json.dumps(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "path": row["path"],
                        "cluster_score": row["cluster_score"],
                    },
                    ensure_ascii=True,
                )
            )
        typer.echo(f"Total: {len(rows)}")
    finally:
        _close_db(db)


@citations_app.command("rebuild")
def citations_rebuild(
    paper_id: Optional[str] = typer.Option(None, help="Optional source paper ID to rebuild"),
) -> None:
    db, cfg = _db()
    try:
        result = rebuild_citation_graph(
            db,
            paper_ids=[paper_id] if paper_id else None,
            config=cfg,
        )
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        _close_db(db)


@citations_app.command("status")
def citations_status() -> None:
    db, _cfg = _db()
    try:
        payload = db.citation_status_summary()
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@citations_app.command("inspect")
def citations_inspect(
    paper_id: str = typer.Option(..., help="Paper ID to inspect"),
) -> None:
    db, cfg = _db()
    try:
        paper = db.get_paper(paper_id)
        if not paper:
            raise typer.BadParameter(f"Paper not found: {paper_id}")
        payload = db.citation_edges_for_paper(paper_id)
        payload["extracted_mentions_preview"] = citation_mentions_preview(
            db,
            paper_id=paper_id,
            mode=cfg.citation_title_match_mode,
            limit=80,
        )
        payload["paper"] = {
            "id": str(paper["id"]),
            "title": str(paper["title"]),
        }
        typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


@sync_app.command("run")
def sync_run(
    pull: bool = typer.Option(True, "--pull/--no-pull", help="Pull from remote into local cache"),
    push: bool = typer.Option(True, "--push/--no-push", help="Push local cache to remote"),
) -> None:
    cfg = load_config()
    store = create_store(cfg)
    try:
        store.initialize()
        result = store.sync_run(pull=pull, push=push)
        if pull and getattr(store, "db", None) is not None:
            try:
                recompute_all_medals(store.db)  # type: ignore[arg-type]
            except Exception:
                pass
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        store.close()


@sync_app.command("status")
def sync_status() -> None:
    cfg = load_config()
    store = create_store(cfg)
    try:
        store.initialize()
        result = store.sync_status()
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        store.close()


@sync_app.command("daemon")
def sync_daemon(
    pull_interval_sec: int = typer.Option(30, help="Auto-pull interval in seconds"),
) -> None:
    cfg = load_config()
    store = create_store(cfg)
    interval = max(1, pull_interval_sec)
    try:
        store.initialize()
        typer.echo(f"sync daemon running (pull every {interval}s)")
        while True:
            try:
                result = store.sync_run(pull=True, push=False)
                typer.echo(json.dumps(result, ensure_ascii=True))
            except Exception as exc:
                typer.echo(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("sync daemon stopped")
    finally:
        store.close()


@app.command("remote-health")
def remote_health() -> None:
    cfg = load_config()
    store = create_store(cfg)
    try:
        store.initialize()
        result = store.remote_health()
        typer.echo(json.dumps(result, ensure_ascii=True))
    finally:
        store.close()


@remote_app.command("health")
def remote_health_subcommand() -> None:
    remote_health()


@migrate_app.command("export-sqlite")
def migrate_export_sqlite(
    output: str = typer.Option(".papertool/migration-export.json", help="Output JSON path"),
) -> None:
    db, _cfg = _db()
    try:
        payload = {
            "papers": [dict(row) for row in db.conn.execute("SELECT * FROM papers").fetchall()],
            "chunks": [dict(row) for row in db.conn.execute("SELECT * FROM chunks").fetchall()],
            "citations": [dict(row) for row in db.conn.execute("SELECT * FROM citations").fetchall()],
            "reading_queue": [dict(row) for row in db.conn.execute("SELECT * FROM reading_queue").fetchall()],
            "quiz_history": [dict(row) for row in db.conn.execute("SELECT * FROM quiz_history").fetchall()],
            "review_cards": [dict(row) for row in db.conn.execute("SELECT * FROM review_cards").fetchall()],
            "topic_catalog": [dict(row) for row in db.conn.execute("SELECT * FROM topic_catalog").fetchall()],
            "paper_topic_scores": [dict(row) for row in db.conn.execute("SELECT * FROM paper_topic_scores").fetchall()],
            "citation_communities": [dict(row) for row in db.conn.execute("SELECT * FROM citation_communities").fetchall()],
            "qa_log": [dict(row) for row in db.conn.execute("SELECT * FROM qa_log").fetchall()],
            "goal_settings": [dict(row) for row in db.conn.execute("SELECT * FROM goal_settings").fetchall()],
            "daily_progress": [dict(row) for row in db.conn.execute("SELECT * FROM daily_progress").fetchall()],
            "daily_qualified_papers": [dict(row) for row in db.conn.execute("SELECT * FROM daily_qualified_papers").fetchall()],
            "paper_medals": [dict(row) for row in db.conn.execute("SELECT * FROM paper_medals").fetchall()],
            "paper_repo_links": [dict(row) for row in db.conn.execute("SELECT * FROM paper_repo_links").fetchall()],
            "medal_events": [dict(row) for row in db.conn.execute("SELECT * FROM medal_events").fetchall()],
            "resources": [dict(row) for row in db.conn.execute("SELECT * FROM resources").fetchall()],
            "resource_topics": [dict(row) for row in db.conn.execute("SELECT * FROM resource_topics").fetchall()],
            "paper_resource_links": [dict(row) for row in db.conn.execute("SELECT * FROM paper_resource_links").fetchall()],
        }
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        typer.echo(f"Exported: {path}")
    finally:
        _close_db(db)


@migrate_app.command("import-couch")
def migrate_import_couch(
    input: str = typer.Option(".papertool/migration-export.json", help="Input JSON path"),
) -> None:
    cfg = load_config()
    if not cfg.couchdb_url:
        raise typer.BadParameter("couchdb_url is required in config")
    payload = json.loads(Path(input).expanduser().resolve().read_text(encoding="utf-8"))
    client = CouchClient(cfg.couchdb_url)
    client.ensure_db(cfg.couchdb_db_meta)
    docs: list[dict[str, object]] = []

    def add_docs(rows: list[dict[str, object]], prefix: str, kind: str, id_keys: list[str]) -> None:
        for row in rows:
            doc_id = prefix + ":" + ":".join(str(row.get(key) or "") for key in id_keys)
            docs.append(
                {
                    "_id": doc_id,
                    "type": kind,
                    "updated_at": str(row.get("updated_at") or row.get("created_at") or row.get("ingested_at") or ""),
                    "data": row,
                }
            )

    add_docs(payload.get("papers", []), "paper", "paper", ["id"])
    add_docs(payload.get("chunks", []), "chunk", "chunk", ["id"])
    add_docs(payload.get("citations", []), "citation", "citation_edge", ["source_paper_id", "target_paper_id"])
    add_docs(payload.get("reading_queue", []), "queue", "queue_entry", ["paper_id"])
    add_docs(payload.get("quiz_history", []), "quiz", "quiz_entry", ["id"])
    add_docs(payload.get("review_cards", []), "review", "review_card", ["id"])
    add_docs(payload.get("topic_catalog", []), "topic_catalog", "topic_catalog", ["topic_id"])
    add_docs(payload.get("paper_topic_scores", []), "topic_score", "topic_score", ["paper_id", "topic_id"])
    add_docs(payload.get("citation_communities", []), "citation_community", "citation_community", ["paper_id"])
    add_docs(payload.get("qa_log", []), "qa_log", "qa_log", ["id"])
    add_docs(payload.get("goal_settings", []), "goal_settings", "goal_settings", ["id"])
    add_docs(payload.get("daily_progress", []), "daily_progress", "daily_progress", ["day_key"])
    add_docs(payload.get("daily_qualified_papers", []), "daily_qualified_papers", "daily_qualified_papers", ["day_key", "paper_id"])
    add_docs(payload.get("paper_medals", []), "paper_medals", "paper_medals", ["paper_id"])
    add_docs(payload.get("paper_repo_links", []), "paper_repo_links", "paper_repo_links", ["id"])
    add_docs(payload.get("medal_events", []), "medal_events", "medal_events", ["id"])
    add_docs(payload.get("resources", []), "resource", "resource", ["id"])
    add_docs(payload.get("resource_topics", []), "resource_topic", "resource_topic", ["resource_id", "topic_id"])
    add_docs(payload.get("paper_resource_links", []), "paper_resource_link", "paper_resource_link", ["id"])

    for doc in docs:
        client.upsert_doc(cfg.couchdb_db_meta, str(doc["_id"]), doc)  # type: ignore[arg-type]
    typer.echo(f"Imported {len(docs)} documents into {cfg.couchdb_db_meta}")


@migrate_app.command("verify")
def migrate_verify() -> None:
    db, cfg = _db()
    try:
        if not cfg.couchdb_url:
            raise typer.BadParameter("couchdb_url is required in config")
        client = CouchClient(cfg.couchdb_url)
        rows = client.all_docs(cfg.couchdb_db_meta, include_docs=True)
        doc_count = len([row for row in rows if isinstance(row.get("doc"), dict) and not str(row.get("id", "")).startswith("_")])
        local_counts = {
            "papers": db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
            "chunks": db.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "citations": db.conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0],
            "reading_queue": db.conn.execute("SELECT COUNT(*) FROM reading_queue").fetchone()[0],
            "paper_medals": db.conn.execute("SELECT COUNT(*) FROM paper_medals").fetchone()[0],
            "daily_progress": db.conn.execute("SELECT COUNT(*) FROM daily_progress").fetchone()[0],
            "paper_repo_links": db.conn.execute("SELECT COUNT(*) FROM paper_repo_links").fetchone()[0],
            "resources": db.conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
            "resource_topics": db.conn.execute("SELECT COUNT(*) FROM resource_topics").fetchone()[0],
            "paper_resource_links": db.conn.execute("SELECT COUNT(*) FROM paper_resource_links").fetchone()[0],
        }
        typer.echo(
            json.dumps(
                {
                    "remote_doc_count": int(doc_count),
                    "local_counts": {k: int(v) for k, v in local_counts.items()},
                },
                ensure_ascii=True,
            )
        )
    finally:
        _close_db(db)


@app.command("bridge")
def bridge(
    host: str = typer.Option("127.0.0.1", help="Bridge host"),
    port: int = typer.Option(17345, help="Bridge port"),
) -> None:
    """Run local HTTP bridge for browser extensions and other URL capture clients."""
    from papertool.bridge_server import run_bridge_server

    run_bridge_server(host=host, port=port)


@app.command("remote-serve")
def remote_serve(
    host: str = typer.Option("0.0.0.0", help="Remote API bind host"),
    port: int = typer.Option(18443, help="Remote API bind port"),
) -> None:
    """Run remote API service for extension/mobile/other devices over Tailscale."""
    from papertool.server.api import run_api_server

    run_api_server(host=host, port=port)


@remote_app.command("serve")
def remote_serve_subcommand(
    host: str = typer.Option("0.0.0.0", help="Remote API bind host"),
    port: int = typer.Option(18443, help="Remote API bind port"),
) -> None:
    remote_serve(host=host, port=port)


@remote_app.command("worker")
def remote_worker(
    poll_interval_sec: int = typer.Option(5, help="Polling interval for queued capture jobs"),
) -> None:
    """Run background worker for queued /v1/captures jobs."""
    from papertool.server.worker import run_worker_loop

    run_worker_loop(poll_interval_sec=max(1, poll_interval_sec))


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start MCP stdio server for Claude Code / Codex."""
    from papertool.mcp_server import main as mcp_main

    mcp_main()


@graph_app.command("export")
def graph_export(
    output: str = typer.Option(".papertool/graph.json", help="Output file path"),
    format: str = typer.Option("json", help="One of: json, mermaid, html"),
) -> None:
    db, cfg = _db()
    try:
        rebuild_result = rebuild_citation_graph(db, config=cfg)
        if not bool(rebuild_result.get("ok", False)):
            typer.echo(
                f"Citation rebuild failed: {json.dumps(rebuild_result, ensure_ascii=True)}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(
            "Citation rebuild:"
            f" processed={int(rebuild_result.get('processed', 0))}"
            f" edges_set={int(rebuild_result.get('edges_set', 0))}"
        )

        out = Path(output).expanduser().resolve()
        fmt = format.lower()
        if fmt == "json":
            export_graph_json(db, out)
        elif fmt == "mermaid":
            export_graph_mermaid(db, out)
        elif fmt == "html":
            export_graph_html(db, out)
        else:
            raise typer.BadParameter("format must be one of: json, mermaid, html")
        typer.echo(f"Graph written to: {out}")
    finally:
        _close_db(db)


@app.command("recent-qa")
def recent_qa(limit: int = typer.Option(20, help="How many QA entries to print")) -> None:
    db, _cfg = _db()
    try:
        rows = db.recent_qa(limit)
        for row in rows:
            payload = {
                "asked_at": row["asked_at"],
                "question": row["question"],
                "answer": row["answer"],
                "paper_ids": json.loads(row["paper_ids"]),
                "channel": row["channel"],
            }
            typer.echo(json.dumps(payload, ensure_ascii=True))
    finally:
        _close_db(db)


if __name__ == "__main__":
    app()
