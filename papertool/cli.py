from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from papertool.config import config_from_kwargs, dump_config, load_config
from papertool.couch_client import CouchClient
from papertool.db import PaperDB
from papertool.graph import export_graph_html, export_graph_json, export_graph_mermaid
from papertool.ingest import ingest_folder
from papertool.obsidian import append_qa_to_paper_note, upsert_paper_note
from papertool.planner import (
    due_review_questions,
    generate_micro_quiz_for_paper,
    paper_of_day_payload,
    queue_rows_to_dict,
    validate_queue_status,
)
from papertool.quiz import generate_daily_quiz
from papertool.retrieval import hits_to_dict, retrieve, synthesize_answer
from papertool.rust_backend import build_clusters, build_index
from papertool.store import create_store
from papertool.url_import import import_result_to_dict, import_url_to_library

app = typer.Typer(help="PaperTool CLI")
graph_app = typer.Typer(help="Graph export commands")
note_app = typer.Typer(help="Note commands")
queue_app = typer.Typer(help="Reading queue commands")
index_app = typer.Typer(help="Retrieval index commands")
cluster_app = typer.Typer(help="Clustering commands")
sync_app = typer.Typer(help="Sync commands")
migrate_app = typer.Typer(help="Migration commands")
remote_app = typer.Typer(help="Remote API and worker commands")
app.add_typer(graph_app, name="graph")
app.add_typer(note_app, name="note")
app.add_typer(queue_app, name="queue")
app.add_typer(index_app, name="index")
app.add_typer(cluster_app, name="cluster")
app.add_typer(sync_app, name="sync")
app.add_typer(migrate_app, name="migrate")
app.add_typer(remote_app, name="remote")


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


@app.command()
def init(
    library_dir: Optional[str] = typer.Option(None, help="Path to folder containing papers"),
    db_path: Optional[str] = typer.Option(None, help="Path to SQLite DB"),
    obsidian_vault: Optional[str] = typer.Option(None, help="Path to Obsidian vault"),
    obsidian_papers_dir: str = typer.Option("Papers", help="Folder for paper notes inside vault"),
    obsidian_daily_dir: str = typer.Option("Daily", help="Folder for daily notes inside vault"),
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
) -> None:
    root = Path.cwd()
    cfg = config_from_kwargs(
        root,
        {
            "library_dir": library_dir,
            "db_path": db_path,
            "obsidian_vault": obsidian_vault,
            "obsidian_papers_dir": obsidian_papers_dir,
            "obsidian_daily_dir": obsidian_daily_dir,
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
        stats = ingest_folder(db, target)
        typer.echo(
            f"Scanned {stats.scanned} files, ingested {stats.ingested}, skipped {stats.skipped}."
        )
    finally:
        _close_db(db)


@app.command()
def list(limit: int = typer.Option(100, help="Maximum papers to print")) -> None:
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
    save_notes: bool = typer.Option(True, "--save-notes/--no-save-notes", help="Persist Q&A in Obsidian"),
) -> None:
    db, cfg = _db()
    try:
        hits = retrieve(
            db,
            question,
            top_k=top_k,
            topic=topic,
            community_id=community_id,
            config=cfg,
        )
        answer = synthesize_answer(question, hits)
        paper_ids = list(dict.fromkeys(hit.paper_id for hit in hits))
        db.log_qa(question, answer, paper_ids=paper_ids, channel="cli")

        if save_notes and cfg.obsidian_vault:
            for paper_id in paper_ids:
                paper = db.get_paper(paper_id)
                if not paper:
                    continue
                upsert_paper_note(
                    cfg,
                    title=paper["title"],
                    source_path=paper["path"],
                    summary=paper["summary"] or "",
                    doi=paper["doi"],
                    arxiv_id=paper["arxiv_id"],
                )
                append_qa_to_paper_note(
                    cfg,
                    title=paper["title"],
                    question=question,
                    answer=answer,
                )

        typer.echo(answer)
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
) -> None:
    db, _cfg = _db()
    try:
        payload = paper_of_day_payload(db)
        if not payload:
            typer.echo("No paper available. Add papers to queue or ingest/import first.")
            return
        typer.echo(f"Have you read this paper: {payload['title']} ({str(payload['paper_id'])[:8]})")
        typer.echo(f"Path: {payload['path']}")
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
    score: Optional[float] = typer.Option(None, help="Self score from 0 to 1"),
) -> None:
    db, _cfg = _db()
    try:
        if score is not None and (score < 0 or score > 1):
            raise typer.BadParameter("score must be between 0 and 1")
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
) -> None:
    db, cfg = _db()
    try:
        result = import_url_to_library(
            db,
            cfg.library_dir,
            url,
            page_title=title,
            context_text=context_text,
        )
        typer.echo(json.dumps(import_result_to_dict(result), ensure_ascii=True))
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
    db, _cfg = _db()
    try:
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


@note_app.command("add")
def note_add(
    paper_id: str = typer.Option(..., help="Paper ID"),
    text: str = typer.Option(..., help="Note text to append"),
) -> None:
    db, cfg = _db()
    try:
        if not cfg.obsidian_vault:
            raise typer.BadParameter("obsidian_vault not set in papertool.toml")
        paper = db.get_paper(paper_id)
        if not paper:
            raise typer.BadParameter(f"Paper not found: {paper_id}")
        upsert_paper_note(
            cfg,
            title=paper["title"],
            source_path=paper["path"],
            summary=paper["summary"] or "",
            doi=paper["doi"],
            arxiv_id=paper["arxiv_id"],
        )
        note_path = append_qa_to_paper_note(
            cfg,
            title=paper["title"],
            question="Manual note",
            answer=text,
        )
        typer.echo(f"Updated note: {note_path}")
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
