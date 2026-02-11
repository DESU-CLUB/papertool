from __future__ import annotations

import json

from papertool.config import load_config
from papertool.db import PaperDB
from papertool.graph import export_graph_json
from papertool.obsidian import append_qa_to_daily_note, append_qa_to_paper_note, upsert_paper_note
from papertool.planner import (
    due_review_questions,
    generate_micro_quiz_for_paper,
    paper_of_day_payload,
    queue_rows_to_dict,
    validate_queue_status,
)
from papertool.quiz import generate_daily_quiz, quiz_to_dict
from papertool.retrieval import hits_to_dict, retrieve, synthesize_answer
from papertool.url_import import import_result_to_dict, import_url_to_library

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - dependency optional until installed
    FastMCP = None  # type: ignore[assignment]


class Runtime:
    def __init__(self) -> None:
        self.config = load_config()
        self.db = PaperDB(self.config.db_path)
        self.db.initialize()


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
    def search_papers(query: str, top_k: int = 6) -> dict[str, object]:
        """Search paper content and return matching passages."""
        rt = get_runtime()
        hits = retrieve(rt.db, query, top_k=top_k)
        return {
            "query": query,
            "hits": hits_to_dict(hits),
        }

    @mcp.tool()
    def ask_papers(question: str, top_k: int = 6, save_to_obsidian: bool = True) -> dict[str, object]:
        """Ask a question against your paper library and get an evidence-grounded answer."""
        rt = get_runtime()
        hits = retrieve(rt.db, question, top_k=top_k)
        answer = synthesize_answer(question, hits)
        paper_ids = list(dict.fromkeys(hit.paper_id for hit in hits))
        rt.db.log_qa(question, answer, paper_ids=paper_ids, channel="mcp")

        notes_written: list[str] = []
        if save_to_obsidian and rt.config.obsidian_vault:
            paper_titles: list[str] = []
            for paper_id in paper_ids:
                paper = rt.db.get_paper(paper_id)
                if not paper:
                    continue
                paper_titles.append(paper["title"])
                upsert_paper_note(
                    rt.config,
                    title=paper["title"],
                    source_path=paper["path"],
                    summary=paper["summary"] or "",
                    doi=paper["doi"],
                    arxiv_id=paper["arxiv_id"],
                )
                note = append_qa_to_paper_note(
                    rt.config,
                    title=paper["title"],
                    question=question,
                    answer=answer,
                )
                notes_written.append(str(note))

            if paper_titles:
                daily = append_qa_to_daily_note(
                    rt.config,
                    question=question,
                    answer=answer,
                    paper_titles=paper_titles,
                )
                notes_written.append(str(daily))

        return {
            "question": question,
            "answer": answer,
            "sources": hits_to_dict(hits),
            "notes_written": notes_written,
        }

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
        """Store an answer to a quiz question, with optional self-assigned score [0-1]."""
        rt = get_runtime()
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
    def import_resource(url: str, title: str | None = None, context_text: str | None = None) -> dict[str, object]:
        """Import a URL (paper, GitHub repo, X post, webpage) into the library and ingest it."""
        rt = get_runtime()
        result = import_url_to_library(
            rt.db,
            rt.config.library_dir,
            url,
            page_title=title,
            context_text=context_text,
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
    def paper_of_day(include_quiz: bool = False, quiz_count: int = 3) -> dict[str, object]:
        """Get one paper to read now; optionally generate a short micro-quiz."""
        rt = get_runtime()
        payload = paper_of_day_payload(rt.db)
        if not payload:
            return {"ok": False, "error": "no_paper_available"}
        out: dict[str, object] = {"ok": True, "paper": payload}
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

else:
    mcp = None


def main() -> None:
    if not mcp:
        raise RuntimeError("MCP library is not installed. Run `pip install -e .` first.")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
