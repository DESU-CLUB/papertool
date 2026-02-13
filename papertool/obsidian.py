from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from papertool.config import PaperToolConfig


INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
REVIEW_START = "<!-- PAPERTOOL_REVIEW_PROMPTS_START -->"
REVIEW_END = "<!-- PAPERTOOL_REVIEW_PROMPTS_END -->"


def _slugify(name: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", name).strip()
    return cleaned[:120] or "untitled"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _append_markdown(path: Path, content: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + content, encoding="utf-8")


def _compact_text(value: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _sanitize_snippet(value: str) -> str:
    text = (value or "").replace("[", "").replace("]", "")
    text = re.sub(r"\s+", " ", text).strip()
    return _compact_text(text, 220)


def _replace_between_markers(text: str, start: str, end: str, replacement: str) -> str:
    start_idx = text.find(start)
    end_idx = text.find(end)
    if start_idx >= 0 and end_idx > start_idx:
        before = text[: start_idx + len(start)]
        after = text[end_idx:]
        return before + "\n" + replacement.rstrip() + "\n" + after
    block = f"\n## Review Prompts\n{start}\n{replacement.rstrip()}\n{end}\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block


def paper_note_path(config: PaperToolConfig, title: str) -> Path:
    if not config.obsidian_vault:
        raise ValueError("obsidian_vault not configured")
    papers_dir = config.obsidian_vault / config.obsidian_papers_dir
    _ensure_dir(papers_dir)
    return papers_dir / f"{_slugify(title)}.md"


def upsert_paper_note(
    config: PaperToolConfig,
    *,
    title: str,
    source_path: str,
    summary: str,
    doi: str | None,
    arxiv_id: str | None,
) -> Path:
    note_path = paper_note_path(config, title)
    if note_path.exists():
        return note_path
    now = datetime.now().isoformat(timespec="seconds")
    safe_title = title.replace('"', "")
    safe_source = source_path.replace('"', "")
    safe_doi = (doi or "").replace('"', "")
    safe_arxiv = (arxiv_id or "").replace('"', "")
    frontmatter = [
        "---",
        f'title: "{safe_title}"',
        f'source_path: "{safe_source}"',
        f'doi: "{safe_doi}"',
        f'arxiv_id: "{safe_arxiv}"',
        f'updated_at: "{now}"',
        "---",
        "",
        "## Summary",
        summary or "(No summary extracted yet)",
        "",
        "## Notes",
        "",
        "## Study Notes",
        "",
        "## Review Prompts",
        REVIEW_START,
        "_(No review prompts yet)_",
        REVIEW_END,
        "",
    ]
    note_path.write_text("\n".join(frontmatter), encoding="utf-8")
    return note_path


def append_qa_to_paper_note(
    config: PaperToolConfig,
    *,
    title: str,
    question: str,
    answer: str,
    evidence_snippets: list[str] | None = None,
) -> Path:
    note_path = paper_note_path(config, title)
    if not note_path.exists():
        upsert_paper_note(
            config,
            title=title,
            source_path="",
            summary="",
            doi=None,
            arxiv_id=None,
        )
    now = datetime.now().isoformat(timespec="seconds")
    question_summary = _compact_text(question, 240)
    answer_summary = _compact_text(answer, 500)
    evidence = [snippet for snippet in (evidence_snippets or []) if snippet.strip()]
    block = [
        f"### Study {now}",
        f"- Prompt: {question_summary}",
        f"- Takeaway: {answer_summary or '(No concise takeaway yet)'}",
    ]
    if evidence:
        block.append("- Evidence Focus:")
        for snippet in evidence[:3]:
            block.append(f"  - {_sanitize_snippet(snippet)}")
    block.extend(
        [
            "",
        ]
    )
    _append_markdown(note_path, "\n".join(block))
    return note_path


def sync_review_prompts_in_paper_note(config: PaperToolConfig, *, title: str, prompts: list[str]) -> Path:
    note_path = paper_note_path(config, title)
    if not note_path.exists():
        upsert_paper_note(
            config,
            title=title,
            source_path="",
            summary="",
            doi=None,
            arxiv_id=None,
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for prompt in prompts:
        value = _compact_text(prompt, 220)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    replacement = "\n".join(f"- {prompt}" for prompt in cleaned) if cleaned else "_(No review prompts yet)_"
    text = note_path.read_text(encoding="utf-8")
    updated = _replace_between_markers(text, REVIEW_START, REVIEW_END, replacement)
    note_path.write_text(updated, encoding="utf-8")
    return note_path


def append_quiz_entry_to_paper_note(
    config: PaperToolConfig,
    *,
    title: str,
    question: str,
    source: str,
    answered: bool,
    score: float | None,
) -> Path:
    note_path = paper_note_path(config, title)
    if not note_path.exists():
        upsert_paper_note(
            config,
            title=title,
            source_path="",
            summary="",
            doi=None,
            arxiv_id=None,
        )
    now = datetime.now().isoformat(timespec="seconds")
    status = "answered" if answered else "new"
    score_text = "" if score is None else f" (score={score:.2f})"
    block = [
        f"### Quiz {now}",
        f"- Question: {_compact_text(question, 240)}",
        f"- Source: {source}",
        f"- Status: {status}{score_text}",
        "- Expected Answer: [hidden for spaced repetition]",
        "",
    ]
    _append_markdown(note_path, "\n".join(block))
    return note_path


def append_qa_to_daily_note(
    config: PaperToolConfig,
    *,
    question: str,
    answer: str,
    paper_titles: list[str],
) -> Path:
    if not config.obsidian_vault:
        raise ValueError("obsidian_vault not configured")

    daily_dir = config.obsidian_vault / config.obsidian_daily_dir
    _ensure_dir(daily_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    path = daily_dir / f"{today}.md"
    tags = ", ".join(paper_titles) if paper_titles else "(no source paper matched)"
    question_summary = _compact_text(question, 240)
    answer_summary = _compact_text(answer, 500)
    content = [
        "## PaperTool Study Log",
        f"- Papers: {tags}",
        f"- Prompt: {question_summary}",
        f"- Takeaway: {answer_summary}",
        "",
    ]
    _append_markdown(path, "\n".join(content))
    return path
