from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from papertool.models import PaperRecord, SearchHit


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def initialize(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                ingested_at TEXT NOT NULL,
                mtime REAL NOT NULL,
                doi TEXT,
                arxiv_id TEXT,
                published_date TEXT,
                summary TEXT,
                full_text TEXT
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                UNIQUE (paper_id, chunk_index)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                content,
                paper_id UNINDEXED,
                tokenize='porter'
            );

            CREATE TABLE IF NOT EXISTS citations (
                source_paper_id TEXT NOT NULL,
                target_paper_id TEXT NOT NULL,
                reason TEXT,
                confidence REAL DEFAULT 0.8,
                PRIMARY KEY (source_paper_id, target_paper_id),
                FOREIGN KEY (source_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (target_paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS qa_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asked_at TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                paper_ids TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'mcp'
            );

            CREATE TABLE IF NOT EXISTS quiz_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                question_text TEXT NOT NULL,
                expected_answer TEXT NOT NULL,
                user_answer TEXT,
                score REAL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reading_queue (
                paper_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'inbox',
                priority REAL NOT NULL DEFAULT 1.0,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_planned_for TEXT,
                completed_at TEXT,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL,
                question_text TEXT NOT NULL,
                expected_answer TEXT NOT NULL,
                next_due_at TEXT NOT NULL,
                interval_days INTEGER NOT NULL DEFAULT 1,
                review_count INTEGER NOT NULL DEFAULT 0,
                last_score REAL,
                last_reviewed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (paper_id, question_text),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_papers_mtime ON papers(mtime);
            CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_paper_id);
            CREATE INDEX IF NOT EXISTS idx_queue_status ON reading_queue(status);
            CREATE INDEX IF NOT EXISTS idx_review_due ON review_cards(next_due_at);
            """
        )
        self._migrate_quiz_history()
        self.bootstrap_queue()
        self.conn.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _migrate_quiz_history(self) -> None:
        if not self._column_exists("quiz_history", "source"):
            self.conn.execute("ALTER TABLE quiz_history ADD COLUMN source TEXT NOT NULL DEFAULT 'daily'")

    def upsert_paper(self, paper: PaperRecord, full_text: str) -> None:
        payload = asdict(paper)
        payload["full_text"] = full_text
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO papers(id, title, path, ingested_at, mtime, doi, arxiv_id, published_date, summary, full_text)
            VALUES(:id, :title, :path, :ingested_at, :mtime, :doi, :arxiv_id, :published_date, :summary, :full_text)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                path=excluded.path,
                ingested_at=excluded.ingested_at,
                mtime=excluded.mtime,
                doi=excluded.doi,
                arxiv_id=excluded.arxiv_id,
                published_date=excluded.published_date,
                summary=excluded.summary,
                full_text=excluded.full_text
            """,
            payload,
        )
        self.conn.execute("DELETE FROM chunks WHERE paper_id = ?", (paper.id,))
        self.conn.execute("DELETE FROM chunk_fts WHERE paper_id = ?", (paper.id,))
        self.conn.execute("DELETE FROM citations WHERE source_paper_id = ?", (paper.id,))
        self.conn.execute(
            """
            INSERT OR IGNORE INTO reading_queue(paper_id, status, priority, added_at, updated_at)
            VALUES (?, 'inbox', 1.0, ?, ?)
            """,
            (paper.id, now, now),
        )
        self.conn.commit()

    def insert_chunks(self, paper_id: str, chunks: Iterable[str]) -> None:
        for idx, chunk in enumerate(chunks):
            cursor = self.conn.execute(
                "INSERT INTO chunks(paper_id, chunk_index, content) VALUES(?, ?, ?)",
                (paper_id, idx, chunk),
            )
            rowid = cursor.lastrowid
            self.conn.execute(
                "INSERT INTO chunk_fts(rowid, content, paper_id) VALUES(?, ?, ?)",
                (rowid, chunk, paper_id),
            )
        self.conn.commit()

    def list_papers(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT id, title, path, doi, arxiv_id, published_date, ingested_at FROM papers ORDER BY ingested_at DESC"
        ).fetchall()
        return list(rows)

    def get_paper(self, paper_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()

    def get_paper_by_path(self, path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM papers WHERE path = ?", (path,)).fetchone()

    def search_chunks(self, query: str, limit: int = 5) -> list[SearchHit]:
        sql = (
            "SELECT paper_id, snippet(chunk_fts, 0, '[', ']', ' ... ', 16) AS snippet, "
            "bm25(chunk_fts) AS rank FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        rows = self.conn.execute(sql, (query, limit)).fetchall()
        if not rows:
            return []

        paper_rows = {
            row["id"]: row
            for row in self.conn.execute(
                f"SELECT id, title, path FROM papers WHERE id IN ({','.join('?' * len(rows))})",
                [r["paper_id"] for r in rows],
            ).fetchall()
        }
        hits: list[SearchHit] = []
        for row in rows:
            paper = paper_rows.get(row["paper_id"])
            if not paper:
                continue
            hits.append(
                SearchHit(
                    paper_id=row["paper_id"],
                    title=paper["title"],
                    path=paper["path"],
                    snippet=row["snippet"],
                    score=float(row["rank"]),
                )
            )
        return hits

    def set_citations(self, source_paper_id: str, target_paper_ids: list[tuple[str, str, float]]) -> None:
        self.conn.execute("DELETE FROM citations WHERE source_paper_id = ?", (source_paper_id,))
        for target_id, reason, confidence in target_paper_ids:
            if source_paper_id == target_id:
                continue
            self.conn.execute(
                """
                INSERT OR IGNORE INTO citations(source_paper_id, target_paper_id, reason, confidence)
                VALUES (?, ?, ?, ?)
                """,
                (source_paper_id, target_id, reason, confidence),
            )
        self.conn.commit()

    def citation_edges(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT c.source_paper_id, c.target_paper_id, c.reason, c.confidence,
                       s.title AS source_title, t.title AS target_title
                FROM citations c
                JOIN papers s ON c.source_paper_id = s.id
                JOIN papers t ON c.target_paper_id = t.id
                """
            ).fetchall()
        )

    def log_qa(self, question: str, answer: str, paper_ids: list[str], channel: str = "mcp") -> None:
        self.conn.execute(
            "INSERT INTO qa_log(asked_at, question, answer, paper_ids, channel) VALUES (?, ?, ?, ?, ?)",
            (utc_now_iso(), question, answer, json.dumps(paper_ids), channel),
        )
        self.conn.commit()

    def save_quiz_question(
        self,
        *,
        question_id: str,
        paper_id: str,
        question_text: str,
        expected_answer: str,
        source: str = "daily",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO quiz_history(question_id, paper_id, created_at, question_text, expected_answer, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (question_id, paper_id, utc_now_iso(), question_text, expected_answer, source),
        )
        self.conn.commit()

    def get_quiz_question(self, question_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, question_id, paper_id, created_at, question_text, expected_answer, user_answer, score, source
            FROM quiz_history
            WHERE question_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (question_id,),
        ).fetchone()

    def update_quiz_answer(self, question_id: str, user_answer: str, score: float | None = None) -> sqlite3.Row | None:
        self.conn.execute(
            "UPDATE quiz_history SET user_answer = ?, score = ? WHERE question_id = ?",
            (user_answer, score, question_id),
        )
        self.conn.commit()
        row = self.get_quiz_question(question_id)
        if row and score is not None:
            self.schedule_or_update_review_card(
                paper_id=str(row["paper_id"]),
                question_text=str(row["question_text"]),
                expected_answer=str(row["expected_answer"]),
                score=float(score),
            )
        return row

    def paper_activity(self) -> list[sqlite3.Row]:
        sql = """
        SELECT p.id, p.title, p.ingested_at,
               MAX(q.created_at) AS last_quiz_at,
               AVG(CASE WHEN q.score IS NULL THEN NULL ELSE q.score END) AS avg_score,
               COUNT(q.id) AS quiz_count
        FROM papers p
        LEFT JOIN quiz_history q ON p.id = q.paper_id
        GROUP BY p.id
        """
        return list(self.conn.execute(sql).fetchall())

    def wrong_question_pool(self, limit: int = 200, wrong_threshold: float = 0.7) -> list[sqlite3.Row]:
        sql = """
        WITH latest_attempt AS (
            SELECT
                q.paper_id,
                q.question_text,
                q.expected_answer,
                q.created_at,
                q.score,
                ROW_NUMBER() OVER (
                    PARTITION BY q.paper_id, q.question_text
                    ORDER BY q.created_at DESC, q.id DESC
                ) AS rn
            FROM quiz_history q
        )
        SELECT la.paper_id, p.title AS paper_title, la.question_text, la.expected_answer, la.created_at
        FROM latest_attempt la
        JOIN papers p ON p.id = la.paper_id
        WHERE la.rn = 1
          AND la.score IS NOT NULL
          AND la.score < ?
        ORDER BY la.created_at DESC
        LIMIT ?
        """
        return list(self.conn.execute(sql, (wrong_threshold, limit)).fetchall())

    def bootstrap_queue(self) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO reading_queue(paper_id, status, priority, added_at, updated_at)
            SELECT p.id, 'inbox', 1.0, ?, ?
            FROM papers p
            """,
            (now, now),
        )
        self.conn.commit()

    def ensure_queue_entry(self, paper_id: str, status: str = "inbox") -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO reading_queue(paper_id, status, priority, added_at, updated_at)
            VALUES (?, ?, 1.0, ?, ?)
            """,
            (paper_id, status, now, now),
        )
        self.conn.commit()

    def queue_set_status(self, paper_id: str, status: str, priority: float | None = None) -> None:
        now = utc_now_iso()
        self.ensure_queue_entry(paper_id)
        if priority is None:
            self.conn.execute(
                "UPDATE reading_queue SET status = ?, updated_at = ? WHERE paper_id = ?",
                (status, now, paper_id),
            )
        else:
            self.conn.execute(
                "UPDATE reading_queue SET status = ?, priority = ?, updated_at = ? WHERE paper_id = ?",
                (status, priority, now, paper_id),
            )
        if status == "done":
            self.conn.execute(
                "UPDATE reading_queue SET completed_at = ? WHERE paper_id = ?",
                (now, paper_id),
            )
        self.conn.commit()

    def queue_list(self, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        self.bootstrap_queue()
        base = (
            "SELECT q.paper_id, q.status, q.priority, q.added_at, q.updated_at, q.last_planned_for, q.completed_at, "
            "p.title, p.path, p.ingested_at FROM reading_queue q JOIN papers p ON p.id = q.paper_id "
        )
        params: list[object] = []
        if status:
            base += "WHERE q.status = ? "
            params.append(status)
        base += "ORDER BY q.priority DESC, q.updated_at DESC LIMIT ?"
        params.append(limit)
        return list(self.conn.execute(base, params).fetchall())

    def plan_today(self, max_items: int = 3) -> list[sqlite3.Row]:
        self.bootstrap_queue()
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self.conn.execute(
            """
            SELECT q.paper_id, q.status, q.priority, q.last_planned_for, p.title, p.path, p.ingested_at
            FROM reading_queue q
            JOIN papers p ON p.id = q.paper_id
            WHERE q.status IN ('next', 'inbox', 'later')
            ORDER BY
              CASE q.status
                WHEN 'next' THEN 0
                WHEN 'inbox' THEN 1
                ELSE 2
              END ASC,
              q.priority DESC,
              p.ingested_at DESC
            LIMIT ?
            """,
            (max_items,),
        ).fetchall()

        now = utc_now_iso()
        for row in rows:
            self.conn.execute(
                """
                UPDATE reading_queue
                SET status = 'today', last_planned_for = ?, updated_at = ?
                WHERE paper_id = ?
                """,
                (today, now, row["paper_id"]),
            )
        self.conn.commit()
        return self.queue_list(status="today", limit=max_items)

    def paper_of_day(self) -> sqlite3.Row | None:
        self.bootstrap_queue()
        row = self.conn.execute(
            """
            SELECT q.paper_id, q.status, q.priority, p.title, p.path, p.summary, p.ingested_at
            FROM reading_queue q
            JOIN papers p ON p.id = q.paper_id
            WHERE q.status = 'today'
            ORDER BY q.priority DESC, q.updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return row

        planned = self.plan_today(max_items=1)
        if planned:
            return planned[0]
        return None

    def mark_done(self, paper_id: str) -> None:
        self.queue_set_status(paper_id, "done")

    def schedule_or_update_review_card(
        self,
        *,
        paper_id: str,
        question_text: str,
        expected_answer: str,
        score: float,
    ) -> None:
        now = datetime.now(timezone.utc)
        current = self.conn.execute(
            """
            SELECT id, interval_days, review_count
            FROM review_cards
            WHERE paper_id = ? AND question_text = ?
            """,
            (paper_id, question_text),
        ).fetchone()

        if current:
            prev_interval = int(current["interval_days"] or 1)
            if score < 0.7:
                interval = 1
            elif score >= 0.9:
                interval = min(prev_interval * 2, 30)
            else:
                interval = min(prev_interval + 2, 21)
            review_count = int(current["review_count"] or 0) + 1
            due = (now + timedelta(days=interval)).isoformat()
            self.conn.execute(
                """
                UPDATE review_cards
                SET expected_answer = ?,
                    next_due_at = ?,
                    interval_days = ?,
                    review_count = ?,
                    last_score = ?,
                    last_reviewed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    expected_answer,
                    due,
                    interval,
                    review_count,
                    score,
                    now.isoformat(),
                    now.isoformat(),
                    current["id"],
                ),
            )
        else:
            interval = 1 if score < 0.7 else 3
            due = (now + timedelta(days=interval)).isoformat()
            self.conn.execute(
                """
                INSERT INTO review_cards(
                    paper_id,
                    question_text,
                    expected_answer,
                    next_due_at,
                    interval_days,
                    review_count,
                    last_score,
                    last_reviewed_at,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    question_text,
                    expected_answer,
                    due,
                    interval,
                    1,
                    score,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        self.conn.commit()

    def due_review_cards(self, limit: int = 20) -> list[sqlite3.Row]:
        now = utc_now_iso()
        return list(
            self.conn.execute(
                """
                SELECT r.paper_id, p.title AS paper_title, r.question_text, r.expected_answer,
                       r.next_due_at, r.interval_days, r.review_count, r.last_score
                FROM review_cards r
                JOIN papers p ON p.id = r.paper_id
                WHERE r.next_due_at <= ?
                ORDER BY r.next_due_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        )

    def recent_qa(self, limit: int = 25) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT asked_at, question, answer, paper_ids, channel FROM qa_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        )
