from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from papertool.models import PaperRecord, SearchHit


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_score_input(score: float) -> float:
    if score < 0:
        raise ValueError("score must be >= 0")
    if score > 10:
        raise ValueError("score must be <= 10")
    if score > 1:
        return float(score) / 10.0
    return float(score)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _compact_text(value: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def summarize_answer_text(answer: str, max_chars: int = 700) -> str:
    raw = (answer or "").strip()
    if not raw:
        return ""
    if "Answer draft:" in raw:
        raw = raw.split("Answer draft:", 1)[1].strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    filtered: list[str] = []
    for line in lines:
        if line.lower().startswith("question:"):
            continue
        if line.lower().startswith("best matching evidence"):
            continue
        filtered.append(line)
    merged = " ".join(filtered) if filtered else raw
    return _compact_text(merged, max_chars=max_chars)


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

            CREATE TABLE IF NOT EXISTS pending_ask_sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                channel TEXT NOT NULL,
                question TEXT NOT NULL,
                answer_preview TEXT NOT NULL,
                hits_json TEXT NOT NULL,
                paper_ids_json TEXT NOT NULL,
                status TEXT NOT NULL,
                confirmed_at TEXT,
                rejected_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ask_scope_locks (
                session_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                paper_ids_json TEXT NOT NULL,
                confirmed_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (session_id, channel)
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

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS goal_settings (
                id TEXT PRIMARY KEY,
                daily_goal INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_progress (
                day_key TEXT PRIMARY KEY,
                timezone TEXT NOT NULL,
                goal_target INTEGER NOT NULL,
                qualified_count INTEGER NOT NULL,
                goal_met INTEGER NOT NULL,
                streak_value INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_qualified_papers (
                day_key TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                qualified_at TEXT NOT NULL,
                PRIMARY KEY (day_key, paper_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_medals (
                paper_id TEXT PRIMARY KEY,
                bronze_awarded_at TEXT,
                silver_awarded_at TEXT,
                silver_active INTEGER NOT NULL DEFAULT 0,
                silver_revoked_at TEXT,
                gold_awarded_at TEXT,
                gold_repo_url TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_repo_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT NOT NULL,
                url TEXT NOT NULL,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                is_owner_valid INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                UNIQUE (paper_id, url),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS medal_events (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resource_topics (
                resource_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (resource_id, topic_id),
                FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE,
                FOREIGN KEY (topic_id) REFERENCES topic_catalog(topic_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_resource_links (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                link_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (paper_id, resource_id, link_type),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_papers_mtime ON papers(mtime);
            CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_paper_id);
            CREATE INDEX IF NOT EXISTS idx_pending_status_created ON pending_ask_sessions(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_pending_expires_at ON pending_ask_sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_scope_locks_expires ON ask_scope_locks(expires_at);
            CREATE INDEX IF NOT EXISTS idx_scope_locks_channel_last_used ON ask_scope_locks(channel, last_used_at);
            CREATE INDEX IF NOT EXISTS idx_queue_status ON reading_queue(status);
            CREATE INDEX IF NOT EXISTS idx_review_due ON review_cards(next_due_at);
            CREATE INDEX IF NOT EXISTS idx_topic_label ON topic_catalog(label);
            CREATE INDEX IF NOT EXISTS idx_paper_topic_paper ON paper_topic_scores(paper_id);
            CREATE INDEX IF NOT EXISTS idx_paper_topic_topic ON paper_topic_scores(topic_id);
            CREATE INDEX IF NOT EXISTS idx_comm_id ON citation_communities(community_id);
            CREATE INDEX IF NOT EXISTS idx_shadow_created ON retrieval_shadow_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_daily_goal_met ON daily_progress(goal_met, day_key);
            CREATE INDEX IF NOT EXISTS idx_qualified_day ON daily_qualified_papers(day_key);
            CREATE INDEX IF NOT EXISTS idx_medals_silver_active ON paper_medals(silver_active);
            CREATE INDEX IF NOT EXISTS idx_repo_links_paper ON paper_repo_links(paper_id);
            CREATE INDEX IF NOT EXISTS idx_resources_kind ON resources(kind);
            CREATE INDEX IF NOT EXISTS idx_resources_canonical_url ON resources(canonical_url);
            CREATE INDEX IF NOT EXISTS idx_resource_topics_resource ON resource_topics(resource_id);
            CREATE INDEX IF NOT EXISTS idx_resource_topics_topic ON resource_topics(topic_id);
            CREATE INDEX IF NOT EXISTS idx_paper_resource_links_paper ON paper_resource_links(paper_id);
            CREATE INDEX IF NOT EXISTS idx_paper_resource_links_resource ON paper_resource_links(resource_id);
            """
        )
        self._migrate_quiz_history()
        self._seed_topic_catalog()
        self._seed_goal_settings()
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

    def _seed_goal_settings(self) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO goal_settings(id, daily_goal, timezone, updated_at)
            VALUES ('default', 1, 'America/Los_Angeles', ?)
            """,
            (utc_now_iso(),),
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

    def get_papers_by_ids(self, paper_ids: list[str]) -> list[sqlite3.Row]:
        ids = [str(item).strip() for item in paper_ids if str(item).strip()]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM papers WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        row_map = {str(row["id"]): row for row in rows}
        ordered: list[sqlite3.Row] = []
        for paper_id in ids:
            row = row_map.get(paper_id)
            if row is not None:
                ordered.append(row)
        return ordered

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

    def search_papers_by_title_tokens(self, tokens: list[str], limit: int = 20) -> list[sqlite3.Row]:
        cleaned = []
        seen: set[str] = set()
        for token in tokens:
            value = re.sub(r"[^a-z0-9]+", "", str(token or "").lower())
            if len(value) < 2 or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        if not cleaned:
            return []
        where = " OR ".join("lower(title) LIKE ?" for _ in cleaned)
        params: list[object] = [f"%{token}%" for token in cleaned]
        params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT id, title, path, arxiv_id, ingested_at
            FROM papers
            WHERE {where}
            ORDER BY ingested_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return list(rows)

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
        question_summary = _compact_text(question, max_chars=280)
        answer_summary = summarize_answer_text(answer, max_chars=700)
        self.conn.execute(
            "INSERT INTO qa_log(asked_at, question, answer, paper_ids, channel) VALUES (?, ?, ?, ?, ?)",
            (utc_now_iso(), question_summary, answer_summary, json.dumps(paper_ids), channel),
        )
        self.conn.commit()

    def scope_hash_for_papers(self, paper_ids: list[str]) -> str:
        normalized = sorted({str(item).strip() for item in paper_ids if str(item).strip()})
        payload = "\n".join(normalized).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def create_pending_ask_session(
        self,
        *,
        channel: str,
        question: str,
        answer_preview: str,
        hits_json: str,
        paper_ids: list[str],
        ttl_seconds: int = 1800,
    ) -> str:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
        session_id = str(uuid.uuid4())
        unique_ids = list(dict.fromkeys(str(item).strip() for item in paper_ids if str(item).strip()))
        self.conn.execute(
            """
            INSERT INTO pending_ask_sessions(
                id, created_at, expires_at, channel, question, answer_preview, hits_json, paper_ids_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                session_id,
                now.isoformat(),
                expires_at.isoformat(),
                channel,
                question,
                answer_preview,
                hits_json,
                json.dumps(unique_ids, ensure_ascii=True),
            ),
        )
        self.conn.commit()
        return session_id

    def mark_pending_ask_session_status(self, session_id: str, status: str) -> None:
        if status not in {"pending", "confirmed", "rejected", "expired"}:
            raise ValueError("invalid pending ask status")
        now = utc_now_iso()
        if status == "confirmed":
            self.conn.execute(
                """
                UPDATE pending_ask_sessions
                SET status = ?, confirmed_at = ?, rejected_at = NULL
                WHERE id = ?
                """,
                (status, now, session_id),
            )
        elif status == "rejected":
            self.conn.execute(
                """
                UPDATE pending_ask_sessions
                SET status = ?, rejected_at = ?
                WHERE id = ?
                """,
                (status, now, session_id),
            )
        else:
            self.conn.execute(
                "UPDATE pending_ask_sessions SET status = ? WHERE id = ?",
                (status, session_id),
            )
        self.conn.commit()

    def expire_pending_ask_sessions(self) -> int:
        now = utc_now_iso()
        cursor = self.conn.execute(
            """
            UPDATE pending_ask_sessions
            SET status = 'expired'
            WHERE status = 'pending' AND expires_at < ?
            """,
            (now,),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def get_pending_ask_session(self, session_id: str, *, include_expired: bool = False) -> sqlite3.Row | None:
        self.expire_pending_ask_sessions()
        row = self.conn.execute(
            """
            SELECT id, created_at, expires_at, channel, question, answer_preview, hits_json, paper_ids_json,
                   status, confirmed_at, rejected_at
            FROM pending_ask_sessions
            WHERE id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        if not include_expired and str(row["status"]) == "expired":
            return None
        return row

    def expire_scope_locks(self) -> int:
        now = utc_now_iso()
        cursor = self.conn.execute(
            "DELETE FROM ask_scope_locks WHERE expires_at < ?",
            (now,),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def get_scope_lock(self, session_id: str, channel: str, *, include_expired: bool = False) -> sqlite3.Row | None:
        if not include_expired:
            self.expire_scope_locks()
        row = self.conn.execute(
            """
            SELECT session_id, channel, scope_hash, paper_ids_json, confirmed_at, last_used_at, expires_at
            FROM ask_scope_locks
            WHERE session_id = ? AND channel = ?
            LIMIT 1
            """,
            (session_id, channel),
        ).fetchone()
        if not row:
            return None
        if include_expired:
            return row
        expires = _parse_iso(str(row["expires_at"]))
        if expires and expires < datetime.now(timezone.utc):
            return None
        return row

    def upsert_scope_lock(self, session_id: str, channel: str, paper_ids: list[str], ttl_seconds: int = 1800) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
        unique_ids = sorted({str(item).strip() for item in paper_ids if str(item).strip()})
        scope_hash = self.scope_hash_for_papers(unique_ids)
        payload = json.dumps(unique_ids, ensure_ascii=True)
        self.conn.execute(
            """
            INSERT INTO ask_scope_locks(
                session_id, channel, scope_hash, paper_ids_json, confirmed_at, last_used_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, channel) DO UPDATE SET
                scope_hash = excluded.scope_hash,
                paper_ids_json = excluded.paper_ids_json,
                confirmed_at = excluded.confirmed_at,
                last_used_at = excluded.last_used_at,
                expires_at = excluded.expires_at
            """,
            (
                session_id,
                channel,
                scope_hash,
                payload,
                now.isoformat(),
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        self.conn.commit()
        return {
            "session_id": session_id,
            "channel": channel,
            "scope_hash": scope_hash,
            "paper_ids": unique_ids,
            "confirmed_at": now.isoformat(),
            "last_used_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    def refresh_scope_lock(self, session_id: str, channel: str, ttl_seconds: int = 1800) -> dict[str, Any] | None:
        row = self.get_scope_lock(session_id, channel, include_expired=False)
        if not row:
            return None
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(60, int(ttl_seconds)))
        self.conn.execute(
            """
            UPDATE ask_scope_locks
            SET last_used_at = ?, expires_at = ?
            WHERE session_id = ? AND channel = ?
            """,
            (now.isoformat(), expires_at.isoformat(), session_id, channel),
        )
        self.conn.commit()
        paper_ids = [str(item) for item in json.loads(str(row["paper_ids_json"]) or "[]")]
        return {
            "session_id": session_id,
            "channel": channel,
            "scope_hash": str(row["scope_hash"]),
            "paper_ids": paper_ids,
            "confirmed_at": str(row["confirmed_at"]),
            "last_used_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

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

    def quiz_prompts_for_paper(self, paper_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            WITH latest AS (
                SELECT
                    question_text,
                    source,
                    created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY question_text
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM quiz_history
                WHERE paper_id = ?
            )
            SELECT question_text, source, created_at
            FROM latest
            WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (paper_id, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_quiz_answer(self, question_id: str, user_answer: str, score: float | None = None) -> sqlite3.Row | None:
        normalized_score = normalize_score_input(score) if score is not None else None
        self.conn.execute(
            "UPDATE quiz_history SET user_answer = ?, score = ? WHERE question_id = ?",
            (user_answer, normalized_score, question_id),
        )
        self.conn.commit()
        row = self.get_quiz_question(question_id)
        if row and normalized_score is not None:
            self.schedule_or_update_review_card(
                paper_id=str(row["paper_id"]),
                question_text=str(row["question_text"]),
                expected_answer=str(row["expected_answer"]),
                score=float(normalized_score),
            )
            self.evaluate_paper_medals(str(row["paper_id"]))
        if row:
            goal = self.get_goal_settings()
            today = self.day_key_now(str(goal["timezone"]))
            self.recompute_all_medals(from_day=today)
        return row

    def get_goal_settings(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT daily_goal, timezone, updated_at
            FROM goal_settings
            WHERE id = 'default'
            """,
        ).fetchone()
        if not row:
            self._seed_goal_settings()
            row = self.conn.execute(
                "SELECT daily_goal, timezone, updated_at FROM goal_settings WHERE id = 'default'"
            ).fetchone()
        assert row is not None
        return {
            "daily_goal": int(row["daily_goal"]),
            "timezone": str(row["timezone"]),
            "updated_at": str(row["updated_at"]),
        }

    def set_goal_settings(self, daily_goal: int, timezone_name: str) -> dict[str, Any]:
        if daily_goal <= 0:
            raise ValueError("daily_goal must be positive")
        # Validate timezone eagerly.
        ZoneInfo(timezone_name)
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO goal_settings(id, daily_goal, timezone, updated_at)
            VALUES ('default', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                daily_goal = excluded.daily_goal,
                timezone = excluded.timezone,
                updated_at = excluded.updated_at
            """,
            (int(daily_goal), timezone_name, now),
        )
        self.conn.commit()
        return self.get_goal_settings()

    def _iso_to_day_key(self, value: str | None, timezone_name: str) -> str | None:
        dt = _parse_iso(value)
        if dt is None:
            return None
        tz = ZoneInfo(timezone_name)
        return dt.astimezone(tz).date().isoformat()

    def day_key_now(self, timezone_name: str) -> str:
        tz = ZoneInfo(timezone_name)
        return datetime.now(timezone.utc).astimezone(tz).date().isoformat()

    def qualified_paper_ids_for_day(self, day_key: str, timezone_name: str) -> list[str]:
        completed_rows = self.conn.execute(
            """
            SELECT paper_id, completed_at
            FROM reading_queue
            WHERE completed_at IS NOT NULL
            """
        ).fetchall()
        completed_today: set[str] = set()
        for row in completed_rows:
            if self._iso_to_day_key(str(row["completed_at"]), timezone_name) == day_key:
                completed_today.add(str(row["paper_id"]))
        if not completed_today:
            return []

        placeholders = ",".join("?" * len(completed_today))
        quiz_rows = self.conn.execute(
            f"""
            SELECT paper_id, created_at
            FROM quiz_history
            WHERE user_answer IS NOT NULL
              AND paper_id IN ({placeholders})
            """,
            tuple(completed_today),
        ).fetchall()
        qualified: set[str] = set()
        for row in quiz_rows:
            if self._iso_to_day_key(str(row["created_at"]), timezone_name) == day_key:
                qualified.add(str(row["paper_id"]))
        return sorted(qualified)

    def recompute_day_progress(self, day_key: str) -> dict[str, Any]:
        goal = self.get_goal_settings()
        timezone_name = str(goal["timezone"])
        daily_goal = int(goal["daily_goal"])
        qualified_ids = self.qualified_paper_ids_for_day(day_key, timezone_name)
        qualified_count = len(qualified_ids)
        goal_met = 1 if qualified_count >= daily_goal else 0

        prev_streak = 0
        prev_day = (datetime.fromisoformat(day_key).date() - timedelta(days=1)).isoformat()
        prev_row = self.conn.execute(
            "SELECT streak_value FROM daily_progress WHERE day_key = ?",
            (prev_day,),
        ).fetchone()
        if prev_row:
            prev_streak = int(prev_row["streak_value"] or 0)
        streak_value = prev_streak + 1 if goal_met else 0
        now = utc_now_iso()

        self.conn.execute("DELETE FROM daily_qualified_papers WHERE day_key = ?", (day_key,))
        for paper_id in qualified_ids:
            self.conn.execute(
                """
                INSERT INTO daily_qualified_papers(day_key, paper_id, qualified_at)
                VALUES (?, ?, ?)
                """,
                (day_key, paper_id, now),
            )
        self.conn.execute(
            """
            INSERT INTO daily_progress(
                day_key, timezone, goal_target, qualified_count, goal_met, streak_value, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day_key) DO UPDATE SET
                timezone = excluded.timezone,
                goal_target = excluded.goal_target,
                qualified_count = excluded.qualified_count,
                goal_met = excluded.goal_met,
                streak_value = excluded.streak_value,
                computed_at = excluded.computed_at
            """,
            (day_key, timezone_name, daily_goal, qualified_count, goal_met, streak_value, now),
        )
        self.conn.commit()
        return {
            "day_key": day_key,
            "timezone": timezone_name,
            "goal_target": daily_goal,
            "qualified_count": qualified_count,
            "goal_met": bool(goal_met),
            "streak_value": streak_value,
            "qualified_paper_ids": qualified_ids,
        }

    def recompute_streak(self, from_day: str | None = None) -> dict[str, Any]:
        rows = self.conn.execute(
            """
            SELECT day_key, goal_met
            FROM daily_progress
            WHERE (? IS NULL OR day_key >= ?)
            ORDER BY day_key ASC
            """,
            (from_day, from_day),
        ).fetchall()
        updated = 0
        prev_streak = 0
        prev_day = None
        for row in rows:
            day_key = str(row["day_key"])
            if prev_day is not None:
                expected = (datetime.fromisoformat(prev_day).date() + timedelta(days=1)).isoformat()
                if day_key != expected:
                    prev_streak = 0
            goal_met = int(row["goal_met"] or 0)
            streak_value = prev_streak + 1 if goal_met else 0
            self.conn.execute(
                "UPDATE daily_progress SET streak_value = ?, computed_at = ? WHERE day_key = ?",
                (streak_value, utc_now_iso(), day_key),
            )
            prev_streak = streak_value
            prev_day = day_key
            updated += 1
        self.conn.commit()
        return {"updated_days": updated}

    def record_medal_event(self, paper_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO medal_events(id, paper_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), paper_id, event_type, json.dumps(payload, ensure_ascii=True), utc_now_iso()),
        )
        self.conn.commit()

    def _ensure_paper_medal_row(self, paper_id: str) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO paper_medals(
                paper_id, silver_active, updated_at
            ) VALUES (?, 0, ?)
            """,
            (paper_id, utc_now_iso()),
        )
        self.conn.commit()

    def award_bronze_for_paper(self, paper_id: str, awarded_at: str | None = None) -> bool:
        self._ensure_paper_medal_row(paper_id)
        row = self.conn.execute(
            "SELECT bronze_awarded_at FROM paper_medals WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if row and row["bronze_awarded_at"]:
            return False
        ts = awarded_at or utc_now_iso()
        self.conn.execute(
            """
            UPDATE paper_medals
            SET bronze_awarded_at = ?, updated_at = ?
            WHERE paper_id = ?
            """,
            (ts, utc_now_iso(), paper_id),
        )
        self.conn.commit()
        self.record_medal_event(paper_id, "bronze_awarded", {"awarded_at": ts})
        return True

    def add_paper_repo_link(self, paper_id: str, url: str, owner: str, repo: str, is_owner_valid: bool) -> dict[str, Any]:
        now = utc_now_iso()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO paper_repo_links(paper_id, url, owner, repo, is_owner_valid, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (paper_id, url, owner, repo, 1 if is_owner_valid else 0, now),
        )
        self.conn.commit()
        self.record_medal_event(
            paper_id,
            "repo_linked",
            {"url": url, "owner": owner, "repo": repo, "is_owner_valid": bool(is_owner_valid)},
        )
        return {
            "paper_id": paper_id,
            "url": url,
            "owner": owner,
            "repo": repo,
            "is_owner_valid": bool(is_owner_valid),
        }

    def latest_review_score(self, paper_id: str) -> float | None:
        row = self.conn.execute(
            """
            SELECT score
            FROM quiz_history
            WHERE paper_id = ?
              AND source = 'review'
              AND score IS NOT NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            return None
        return float(row["score"])

    def evaluate_silver_medal(self, paper_id: str) -> dict[str, Any]:
        self._ensure_paper_medal_row(paper_id)
        row = self.conn.execute(
            """
            SELECT bronze_awarded_at, silver_awarded_at, silver_active
            FROM paper_medals
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            return {"paper_id": paper_id, "silver_active": False}
        if not row["bronze_awarded_at"]:
            return {"paper_id": paper_id, "silver_active": False}

        latest_score = self.latest_review_score(paper_id)
        if latest_score is None:
            return {"paper_id": paper_id, "silver_active": bool(row["silver_active"])}

        now = utc_now_iso()
        if latest_score >= 0.9:
            if int(row["silver_active"] or 0) == 1:
                return {"paper_id": paper_id, "silver_active": True}
            if row["silver_awarded_at"]:
                self.conn.execute(
                    """
                    UPDATE paper_medals
                    SET silver_active = 1,
                        updated_at = ?
                    WHERE paper_id = ?
                    """,
                    (now, paper_id),
                )
                self.conn.commit()
                self.record_medal_event(paper_id, "silver_reactivated", {"score": latest_score, "at": now})
            else:
                self.conn.execute(
                    """
                    UPDATE paper_medals
                    SET silver_awarded_at = ?,
                        silver_active = 1,
                        updated_at = ?
                    WHERE paper_id = ?
                    """,
                    (now, now, paper_id),
                )
                self.conn.commit()
                self.record_medal_event(paper_id, "silver_awarded", {"score": latest_score, "at": now})
            return {"paper_id": paper_id, "silver_active": True}

        # latest_score < 0.9
        if int(row["silver_active"] or 0) == 1:
            self.conn.execute(
                """
                UPDATE paper_medals
                SET silver_active = 0,
                    silver_revoked_at = ?,
                    updated_at = ?
                WHERE paper_id = ?
                """,
                (now, now, paper_id),
            )
            self.conn.commit()
            self.record_medal_event(paper_id, "silver_revoked", {"score": latest_score, "at": now})
        return {"paper_id": paper_id, "silver_active": False}

    def evaluate_gold_medal(self, paper_id: str) -> dict[str, Any]:
        self._ensure_paper_medal_row(paper_id)
        row = self.conn.execute(
            """
            SELECT bronze_awarded_at, gold_awarded_at
            FROM paper_medals
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            return {"paper_id": paper_id, "gold_awarded": False}
        if not row["bronze_awarded_at"]:
            return {"paper_id": paper_id, "gold_awarded": False}
        if row["gold_awarded_at"]:
            return {"paper_id": paper_id, "gold_awarded": True}

        link = self.conn.execute(
            """
            SELECT url
            FROM paper_repo_links
            WHERE paper_id = ? AND is_owner_valid = 1
            ORDER BY id ASC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        if not link:
            return {"paper_id": paper_id, "gold_awarded": False}
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE paper_medals
            SET gold_awarded_at = ?,
                gold_repo_url = ?,
                updated_at = ?
            WHERE paper_id = ?
            """,
            (now, str(link["url"]), now, paper_id),
        )
        self.conn.commit()
        self.record_medal_event(paper_id, "gold_awarded", {"url": str(link["url"]), "at": now})
        return {"paper_id": paper_id, "gold_awarded": True}

    def evaluate_paper_medals(self, paper_id: str) -> dict[str, Any]:
        self._ensure_paper_medal_row(paper_id)
        silver = self.evaluate_silver_medal(paper_id)
        gold = self.evaluate_gold_medal(paper_id)
        row = self.get_paper_medal(paper_id)
        return {
            "paper_id": paper_id,
            "silver_active": bool(silver.get("silver_active", False)),
            "gold_awarded": bool(gold.get("gold_awarded", False)),
            "medal": row,
        }

    def recompute_all_medals(self, from_day: str | None = None) -> dict[str, Any]:
        goal = self.get_goal_settings()
        timezone_name = str(goal["timezone"])
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name)).date()
        base_date: datetime.date = today
        if from_day:
            base_date = datetime.fromisoformat(from_day).date()
        else:
            completed = self.conn.execute(
                "SELECT MIN(completed_at) AS min_completed FROM reading_queue WHERE completed_at IS NOT NULL"
            ).fetchone()
            if completed and completed["min_completed"]:
                parsed = _parse_iso(str(completed["min_completed"]))
                if parsed:
                    base_date = parsed.astimezone(ZoneInfo(timezone_name)).date()

        self.conn.execute("DELETE FROM daily_qualified_papers WHERE day_key >= ?", (base_date.isoformat(),))
        self.conn.execute("DELETE FROM daily_progress WHERE day_key >= ?", (base_date.isoformat(),))
        self.conn.commit()

        cursor_date = base_date
        recomputed_days = 0
        while cursor_date <= today:
            day_key = cursor_date.isoformat()
            day_payload = self.recompute_day_progress(day_key)
            if day_payload["goal_met"]:
                for paper_id in day_payload["qualified_paper_ids"]:
                    self.award_bronze_for_paper(str(paper_id))
                    self.evaluate_paper_medals(str(paper_id))
            recomputed_days += 1
            cursor_date = cursor_date + timedelta(days=1)

        bronze_rows = self.conn.execute(
            "SELECT paper_id FROM paper_medals WHERE bronze_awarded_at IS NOT NULL"
        ).fetchall()
        for row in bronze_rows:
            self.evaluate_paper_medals(str(row["paper_id"]))

        return {"ok": True, "recomputed_days": recomputed_days, "from_day": base_date.isoformat(), "to_day": today.isoformat()}

    def get_paper_medal(self, paper_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT paper_id, bronze_awarded_at, silver_awarded_at, silver_active, silver_revoked_at, gold_awarded_at, gold_repo_url, updated_at
            FROM paper_medals
            WHERE paper_id = ?
            """,
            (paper_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "paper_id": str(row["paper_id"]),
            "bronze_awarded_at": row["bronze_awarded_at"],
            "silver_awarded_at": row["silver_awarded_at"],
            "silver_active": bool(row["silver_active"]),
            "silver_revoked_at": row["silver_revoked_at"],
            "gold_awarded_at": row["gold_awarded_at"],
            "gold_repo_url": row["gold_repo_url"],
            "updated_at": row["updated_at"],
        }

    def paper_repo_links(self, paper_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, paper_id, url, owner, repo, is_owner_valid, added_at
            FROM paper_repo_links
            WHERE paper_id = ?
            ORDER BY added_at DESC
            """,
            (paper_id,),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "paper_id": str(row["paper_id"]),
                "url": str(row["url"]),
                "owner": str(row["owner"]),
                "repo": str(row["repo"]),
                "is_owner_valid": bool(row["is_owner_valid"]),
                "added_at": str(row["added_at"]),
            }
            for row in rows
        ]

    def topic_id_for_label(self, label: str) -> str | None:
        normalized = label.strip().lower()
        if not normalized:
            return None
        row = self.conn.execute(
            "SELECT topic_id FROM topic_catalog WHERE lower(label) = ? LIMIT 1",
            (normalized,),
        ).fetchone()
        if not row:
            return None
        return str(row["topic_id"])

    def resource_by_canonical_url(self, canonical_url: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, kind, url, canonical_url, title, notes, created_at, updated_at
            FROM resources
            WHERE canonical_url = ?
            LIMIT 1
            """,
            (canonical_url,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, kind, url, canonical_url, title, notes, created_at, updated_at
            FROM resources
            WHERE id = ?
            LIMIT 1
            """,
            (resource_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def upsert_resource(
        self,
        *,
        kind: str,
        url: str,
        canonical_url: str,
        title: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        existing = self.resource_by_canonical_url(canonical_url)
        now = utc_now_iso()
        if existing:
            next_notes = notes if notes is not None else existing.get("notes")
            next_title = title or str(existing.get("title") or canonical_url)
            self.conn.execute(
                """
                UPDATE resources
                SET kind = ?, url = ?, title = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (kind, url, next_title, next_notes, now, str(existing["id"])),
            )
            self.conn.commit()
            out = self.get_resource(str(existing["id"]))
            assert out is not None
            return out

        resource_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO resources(
                id, kind, url, canonical_url, title, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resource_id, kind, url, canonical_url, title, notes, now, now),
        )
        self.conn.commit()
        out = self.get_resource(resource_id)
        assert out is not None
        return out

    def set_resource_topics(self, resource_id: str, topics: list[tuple[str, float, str]]) -> None:
        now = utc_now_iso()
        self.conn.execute("DELETE FROM resource_topics WHERE resource_id = ?", (resource_id,))
        for topic_id, score, source in topics:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO resource_topics(resource_id, topic_id, score, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (resource_id, topic_id, float(score), source, now),
            )
        self.conn.commit()

    def upsert_resource_topic(
        self,
        resource_id: str,
        topic_id: str,
        *,
        score: float = 1.0,
        source: str = "manual",
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO resource_topics(resource_id, topic_id, score, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (resource_id, topic_id, float(score), source, utc_now_iso()),
        )
        self.conn.commit()

    def resource_topics(self, resource_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT rt.resource_id, rt.topic_id, tc.label AS topic_label, rt.score, rt.source, rt.updated_at
            FROM resource_topics rt
            JOIN topic_catalog tc ON tc.topic_id = rt.topic_id
            WHERE rt.resource_id = ?
            ORDER BY rt.score DESC, tc.label ASC
            """,
            (resource_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_resources(self, kind: str | None = None, topic: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if topic:
            rows = self.conn.execute(
                """
                SELECT DISTINCT r.id, r.kind, r.url, r.canonical_url, r.title, r.notes, r.created_at, r.updated_at
                FROM resources r
                JOIN resource_topics rt ON rt.resource_id = r.id
                JOIN topic_catalog tc ON tc.topic_id = rt.topic_id
                WHERE (? IS NULL OR r.kind = ?)
                  AND lower(tc.label) = lower(?)
                ORDER BY r.updated_at DESC
                LIMIT ?
                """,
                (kind, kind, topic, max(1, limit)),
            ).fetchall()
            return [dict(row) for row in rows]

        rows = self.conn.execute(
            """
            SELECT id, kind, url, canonical_url, title, notes, created_at, updated_at
            FROM resources
            WHERE (? IS NULL OR kind = ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (kind, kind, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def link_paper_resource(self, paper_id: str, resource_id: str, link_type: str = "related") -> dict[str, Any]:
        allowed = {"related", "implementation", "update", "background"}
        normalized = link_type.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"link_type must be one of: {', '.join(sorted(allowed))}")
        row = self.conn.execute(
            """
            SELECT id, paper_id, resource_id, link_type, created_at
            FROM paper_resource_links
            WHERE paper_id = ? AND resource_id = ? AND link_type = ?
            LIMIT 1
            """,
            (paper_id, resource_id, normalized),
        ).fetchone()
        if row:
            return dict(row)
        link_id = str(uuid.uuid4())
        created_at = utc_now_iso()
        self.conn.execute(
            """
            INSERT INTO paper_resource_links(id, paper_id, resource_id, link_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (link_id, paper_id, resource_id, normalized, created_at),
        )
        self.conn.commit()
        out = self.conn.execute(
            """
            SELECT id, paper_id, resource_id, link_type, created_at
            FROM paper_resource_links
            WHERE id = ?
            LIMIT 1
            """,
            (link_id,),
        ).fetchone()
        assert out is not None
        return dict(out)

    def resource_links_for_paper(self, paper_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT prl.id, prl.paper_id, prl.resource_id, prl.link_type, prl.created_at,
                   r.kind AS resource_kind, r.title AS resource_title, r.url AS resource_url
            FROM paper_resource_links prl
            JOIN resources r ON r.id = prl.resource_id
            WHERE prl.paper_id = ?
            ORDER BY prl.created_at DESC
            LIMIT ?
            """,
            (paper_id, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def resource_links_for_resource(self, resource_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT prl.id, prl.paper_id, prl.resource_id, prl.link_type, prl.created_at,
                   p.title AS paper_title, p.path AS paper_path
            FROM paper_resource_links prl
            JOIN papers p ON p.id = prl.paper_id
            WHERE prl.resource_id = ?
            ORDER BY prl.created_at DESC
            LIMIT ?
            """,
            (resource_id, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def related_resources_for_paper(self, paper_id: str, limit: int = 20) -> list[dict[str, Any]]:
        direct = self.resource_links_for_paper(paper_id, limit=limit)
        seen_ids = {str(row["resource_id"]) for row in direct}
        if len(direct) >= limit:
            return direct[:limit]

        remaining = max(0, limit - len(direct))
        if remaining <= 0:
            return direct[:limit]

        overlap_rows = self.conn.execute(
            """
            SELECT r.id AS resource_id,
                   r.kind AS resource_kind,
                   r.title AS resource_title,
                   r.url AS resource_url,
                   MAX(rt.score * pts.score) AS overlap_score
            FROM paper_topic_scores pts
            JOIN resource_topics rt ON rt.topic_id = pts.topic_id
            JOIN resources r ON r.id = rt.resource_id
            WHERE pts.paper_id = ?
            GROUP BY r.id, r.kind, r.title, r.url
            ORDER BY overlap_score DESC, r.updated_at DESC
            LIMIT ?
            """,
            (paper_id, max(remaining * 4, remaining)),
        ).fetchall()
        out = direct[:]
        for row in overlap_rows:
            resource_id = str(row["resource_id"])
            if resource_id in seen_ids:
                continue
            out.append(
                {
                    "id": "",
                    "paper_id": paper_id,
                    "resource_id": resource_id,
                    "link_type": "related",
                    "created_at": "",
                    "resource_kind": row["resource_kind"],
                    "resource_title": row["resource_title"],
                    "resource_url": row["resource_url"],
                    "overlap_score": float(row["overlap_score"] or 0.0),
                }
            )
            seen_ids.add(resource_id)
            if len(out) >= limit:
                break
        return out[:limit]

    def recent_resources(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, kind, title, url, updated_at
            FROM resources
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def resource_topic_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT tc.label AS topic_label, COUNT(*) AS resource_count
            FROM resource_topics rt
            JOIN topic_catalog tc ON tc.topic_id = rt.topic_id
            GROUP BY tc.label
            ORDER BY resource_count DESC, tc.label ASC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def daily_progress_rows(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT day_key, timezone, goal_target, qualified_count, goal_met, streak_value, computed_at
            FROM daily_progress
            ORDER BY day_key DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def medal_overview(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT p.id AS paper_id,
                   p.title,
                   COALESCE(q.status, 'inbox') AS queue_status,
                   pm.bronze_awarded_at,
                   pm.silver_awarded_at,
                   COALESCE(pm.silver_active, 0) AS silver_active,
                   pm.silver_revoked_at,
                   pm.gold_awarded_at,
                   pm.gold_repo_url,
                   (
                     SELECT qh.score
                     FROM quiz_history qh
                     WHERE qh.paper_id = p.id
                       AND qh.source = 'review'
                       AND qh.score IS NOT NULL
                     ORDER BY qh.created_at DESC, qh.id DESC
                     LIMIT 1
                   ) AS latest_review_score
            FROM papers p
            LEFT JOIN paper_medals pm ON pm.paper_id = p.id
            LEFT JOIN reading_queue q ON q.paper_id = p.id
            ORDER BY p.ingested_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

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
        if status == "done":
            goal = self.get_goal_settings()
            day_key = self._iso_to_day_key(now, str(goal["timezone"])) or self.day_key_now(str(goal["timezone"]))
            self.recompute_all_medals(from_day=day_key)
            self.evaluate_paper_medals(paper_id)

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

    def set_sync_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_state(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now_iso()),
        )
        self.conn.commit()

    def get_sync_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        return str(row["value"])

    def sync_state_all(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT key, value, updated_at FROM sync_state ORDER BY key ASC").fetchall())
