from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

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

            CREATE TABLE IF NOT EXISTS retrieval_shadow_log (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                query TEXT NOT NULL,
                top_k INTEGER NOT NULL,
                python_hits_json TEXT NOT NULL,
                rust_hits_json TEXT NOT NULL,
                overlap_at_k REAL NOT NULL,
                py_ms REAL NOT NULL,
                rust_ms REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topic_catalog (
                topic_id TEXT PRIMARY KEY,
                label TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_topic_scores (
                paper_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (paper_id, topic_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (topic_id) REFERENCES topic_catalog(topic_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS citation_communities (
                paper_id TEXT PRIMARY KEY,
                community_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS cluster_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                papers_processed INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_papers_mtime ON papers(mtime);
            CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_paper_id);
            CREATE INDEX IF NOT EXISTS idx_queue_status ON reading_queue(status);
            CREATE INDEX IF NOT EXISTS idx_review_due ON review_cards(next_due_at);
            CREATE INDEX IF NOT EXISTS idx_topic_label ON topic_catalog(label);
            CREATE INDEX IF NOT EXISTS idx_paper_topic_paper ON paper_topic_scores(paper_id);
            CREATE INDEX IF NOT EXISTS idx_paper_topic_topic ON paper_topic_scores(topic_id);
            CREATE INDEX IF NOT EXISTS idx_comm_id ON citation_communities(community_id);
            CREATE INDEX IF NOT EXISTS idx_shadow_created ON retrieval_shadow_log(created_at);
            """
        )
        self._migrate_quiz_history()
        self._seed_topic_catalog()
        self.bootstrap_queue()
        self.conn.commit()

    def _column_exists(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _migrate_quiz_history(self) -> None:
        if not self._column_exists("quiz_history", "source"):
            self.conn.execute("ALTER TABLE quiz_history ADD COLUMN source TEXT NOT NULL DEFAULT 'daily'")

    def _seed_topic_catalog(self) -> None:
        topics = [
            "moe",
            "mamba",
            "attention",
            "transformer",
            "quantization",
            "rlhf",
            "multimodal",
            "diffusion",
            "reasoning",
            "agent",
            "retrieval",
            "inference",
            "compiler",
            "systems",
            "alignment",
        ]
        now = utc_now_iso()
        for label in topics:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO topic_catalog(topic_id, label, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (f"seed:{label}", label, "seed", now),
            )

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

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> sqlite3.Row | None:
        normalized = arxiv_id.strip().lower().replace("arxiv:", "")
        base = re.sub(r"v\d+$", "", normalized)
        return self.conn.execute(
            """
            SELECT * FROM papers
            WHERE lower(arxiv_id) = ?
               OR lower(arxiv_id) = ?
               OR lower(arxiv_id) LIKE ?
            ORDER BY ingested_at DESC
            LIMIT 1
            """,
            (normalized, base, f"{base}v%"),
        ).fetchone()

    def set_paper_arxiv_id(self, paper_id: str, arxiv_id: str) -> None:
        normalized = arxiv_id.strip().lower().replace("arxiv:", "")
        base = re.sub(r"v\d+$", "", normalized)
        self.conn.execute("UPDATE papers SET arxiv_id = ? WHERE id = ?", (base, paper_id))
        self.conn.commit()

    def search_chunks(self, query: str, limit: int = 5, paper_ids: list[str] | None = None) -> list[SearchHit]:
        sql = (
            "SELECT paper_id, snippet(chunk_fts, 0, '[', ']', ' ... ', 16) AS snippet, "
            "bm25(chunk_fts) AS rank FROM chunk_fts WHERE chunk_fts MATCH ?"
        )
        params: list[object] = [query]
        if paper_ids is not None:
            if not paper_ids:
                return []
            sql += f" AND paper_id IN ({','.join('?' * len(paper_ids))})"
            params.extend(paper_ids)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
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

    def log_retrieval_shadow(
        self,
        *,
        query: str,
        top_k: int,
        python_hits_json: str,
        rust_hits_json: str,
        overlap_at_k: float,
        py_ms: float,
        rust_ms: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO retrieval_shadow_log(
                id, created_at, query, top_k, python_hits_json, rust_hits_json, overlap_at_k, py_ms, rust_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                utc_now_iso(),
                query,
                int(top_k),
                python_hits_json,
                rust_hits_json,
                float(overlap_at_k),
                float(py_ms),
                float(rust_ms),
            ),
        )
        self.conn.commit()

    def recent_shadow_logs(self, limit: int = 100) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT id, created_at, query, top_k, overlap_at_k, py_ms, rust_ms
                FROM retrieval_shadow_log
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def upsert_topic(self, label: str, source: str = "auto") -> str:
        normalized = label.strip().lower()
        if not normalized:
            raise ValueError("topic label is required")
        existing = self.conn.execute(
            "SELECT topic_id FROM topic_catalog WHERE lower(label) = ?",
            (normalized,),
        ).fetchone()
        if existing:
            return str(existing["topic_id"])
        topic_id = f"topic:{normalized}"
        self.conn.execute(
            """
            INSERT INTO topic_catalog(topic_id, label, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (topic_id, normalized, source, utc_now_iso()),
        )
        self.conn.commit()
        return topic_id

    def topic_labels(self) -> list[str]:
        rows = self.conn.execute("SELECT label FROM topic_catalog ORDER BY label ASC").fetchall()
        return [str(row["label"]) for row in rows]

    def clear_paper_topics(self) -> None:
        self.conn.execute("DELETE FROM paper_topic_scores")
        self.conn.commit()

    def replace_paper_topics(self, paper_id: str, topics: list[tuple[str, float]]) -> None:
        now = utc_now_iso()
        self.conn.execute("DELETE FROM paper_topic_scores WHERE paper_id = ?", (paper_id,))
        for topic_id, score in topics:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO paper_topic_scores(paper_id, topic_id, score, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, topic_id, float(score), now),
            )
        self.conn.commit()

    def clear_communities(self) -> None:
        self.conn.execute("DELETE FROM citation_communities")
        self.conn.commit()

    def set_paper_community(self, paper_id: str, community_id: str, score: float) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO citation_communities(paper_id, community_id, score, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (paper_id, community_id, float(score), utc_now_iso()),
        )
        self.conn.commit()

    def start_cluster_run(self, mode: str = "on_demand") -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO cluster_runs(run_id, started_at, status, mode, papers_processed)
            VALUES (?, ?, 'running', ?, 0)
            """,
            (run_id, utc_now_iso(), mode),
        )
        self.conn.commit()
        return run_id

    def finish_cluster_run(self, run_id: str, *, status: str, papers_processed: int) -> None:
        self.conn.execute(
            """
            UPDATE cluster_runs
            SET ended_at = ?, status = ?, papers_processed = ?
            WHERE run_id = ?
            """,
            (utc_now_iso(), status, int(papers_processed), run_id),
        )
        self.conn.commit()

    def paper_ids_for_topic(self, label: str, limit: int = 2000) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT pts.paper_id
            FROM paper_topic_scores pts
            JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
            WHERE lower(tc.label) = lower(?)
            ORDER BY pts.score DESC
            LIMIT ?
            """,
            (label.strip(), limit),
        ).fetchall()
        return [str(row["paper_id"]) for row in rows]

    def paper_ids_for_community(self, community_id: str, limit: int = 2000) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT paper_id
            FROM citation_communities
            WHERE community_id = ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (community_id.strip(), limit),
        ).fetchall()
        return [str(row["paper_id"]) for row in rows]

    def cluster_overview(self, cluster_type: str, limit: int = 50) -> list[sqlite3.Row]:
        kind = cluster_type.strip().lower()
        if kind == "topic":
            return list(
                self.conn.execute(
                    """
                    SELECT tc.label AS cluster_key, COUNT(*) AS paper_count, AVG(pts.score) AS avg_score
                    FROM paper_topic_scores pts
                    JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
                    GROUP BY tc.label
                    ORDER BY paper_count DESC, avg_score DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )
        if kind == "community":
            return list(
                self.conn.execute(
                    """
                    SELECT cc.community_id AS cluster_key, COUNT(*) AS paper_count, AVG(cc.score) AS avg_score
                    FROM citation_communities cc
                    GROUP BY cc.community_id
                    ORDER BY paper_count DESC, avg_score DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            )
        raise ValueError("cluster_type must be topic or community")

    def cluster_papers(self, *, topic: str | None = None, community_id: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if topic and community_id:
            return list(
                self.conn.execute(
                    """
                    SELECT p.id, p.title, p.path, p.ingested_at, pts.score AS cluster_score
                    FROM papers p
                    JOIN paper_topic_scores pts ON pts.paper_id = p.id
                    JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
                    JOIN citation_communities cc ON cc.paper_id = p.id
                    WHERE lower(tc.label) = lower(?) AND cc.community_id = ?
                    ORDER BY cluster_score DESC, p.ingested_at DESC
                    LIMIT ?
                    """,
                    (topic, community_id, limit),
                ).fetchall()
            )
        if topic:
            return list(
                self.conn.execute(
                    """
                    SELECT p.id, p.title, p.path, p.ingested_at, pts.score AS cluster_score
                    FROM papers p
                    JOIN paper_topic_scores pts ON pts.paper_id = p.id
                    JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
                    WHERE lower(tc.label) = lower(?)
                    ORDER BY cluster_score DESC, p.ingested_at DESC
                    LIMIT ?
                    """,
                    (topic, limit),
                ).fetchall()
            )
        if community_id:
            return list(
                self.conn.execute(
                    """
                    SELECT p.id, p.title, p.path, p.ingested_at, cc.score AS cluster_score
                    FROM papers p
                    JOIN citation_communities cc ON cc.paper_id = p.id
                    WHERE cc.community_id = ?
                    ORDER BY cluster_score DESC, p.ingested_at DESC
                    LIMIT ?
                    """,
                    (community_id, limit),
                ).fetchall()
            )
        return []

    def paper_rank_features(self, paper_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not paper_ids:
            return {}
        placeholders = ",".join("?" * len(paper_ids))
        features: dict[str, dict[str, Any]] = {
            paper_id: {"queue_status": "inbox", "citation_degree": 0.0, "topics": {}}
            for paper_id in paper_ids
        }
        rows = self.conn.execute(
            f"""
            SELECT p.id,
                   COALESCE(q.status, 'inbox') AS queue_status,
                   COALESCE(c.degree, 0) AS citation_degree
            FROM papers p
            LEFT JOIN reading_queue q ON q.paper_id = p.id
            LEFT JOIN (
                SELECT paper_id, COUNT(*) AS degree
                FROM (
                    SELECT source_paper_id AS paper_id FROM citations
                    UNION ALL
                    SELECT target_paper_id AS paper_id FROM citations
                ) z
                GROUP BY paper_id
            ) c ON c.paper_id = p.id
            WHERE p.id IN ({placeholders})
            """,
            paper_ids,
        ).fetchall()
        for row in rows:
            features[str(row["id"])] = {
                "queue_status": str(row["queue_status"] or "inbox"),
                "citation_degree": float(row["citation_degree"] or 0.0),
                "topics": {},
            }
        topic_rows = self.conn.execute(
            f"""
            SELECT pts.paper_id, tc.label, pts.score
            FROM paper_topic_scores pts
            JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
            WHERE pts.paper_id IN ({placeholders})
            """,
            paper_ids,
        ).fetchall()
        for row in topic_rows:
            pid = str(row["paper_id"])
            entry = features.setdefault(pid, {"queue_status": "inbox", "citation_degree": 0.0, "topics": {}})
            entry["topics"][str(row["label"])] = float(row["score"] or 0.0)
        return features

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
