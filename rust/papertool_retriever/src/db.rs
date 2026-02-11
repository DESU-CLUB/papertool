use std::collections::HashMap;

use rusqlite::{params, Connection};

use crate::types::{ChunkDoc, PaperFeature};

pub fn open(db_path: &str) -> Result<Connection, String> {
    Connection::open(db_path).map_err(|e| e.to_string())
}

pub fn load_chunk_docs(conn: &Connection) -> Result<Vec<ChunkDoc>, String> {
    let mut stmt = conn
        .prepare(
            "
            SELECT c.id, c.paper_id, p.title, p.path,
                   c.content,
                   COALESCE(p.summary, ''),
                   COALESCE(p.ingested_at, ''),
                   COALESCE(q.status, 'inbox')
            FROM chunks c
            JOIN papers p ON p.id = c.paper_id
            LEFT JOIN reading_queue q ON q.paper_id = p.id
            ORDER BY c.id ASC
            ",
        )
        .map_err(|e| e.to_string())?;

    let rows = stmt
        .query_map([], |row| {
            Ok(ChunkDoc {
                chunk_id: row.get(0)?,
                paper_id: row.get(1)?,
                title: row.get(2)?,
                path: row.get(3)?,
                chunk_text: row.get(4)?,
                summary: row.get(5)?,
                ingested_at: row.get(6)?,
                queue_status: row.get(7)?,
            })
        })
        .map_err(|e| e.to_string())?;

    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn load_all_papers(conn: &Connection) -> Result<Vec<(String, String, String, String)>, String> {
    let mut stmt = conn
        .prepare("SELECT id, title, COALESCE(summary,''), COALESCE(full_text,'') FROM papers ORDER BY ingested_at DESC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn citation_edges(conn: &Connection) -> Result<Vec<(String, String)>, String> {
    let mut stmt = conn
        .prepare("SELECT source_paper_id, target_paper_id FROM citations")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn topic_labels(conn: &Connection) -> Result<Vec<String>, String> {
    let mut stmt = conn
        .prepare("SELECT label FROM topic_catalog ORDER BY label ASC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn upsert_topic(conn: &Connection, label: &str, source: &str) -> Result<String, String> {
    let normalized = label.trim().to_lowercase();
    if normalized.is_empty() {
        return Err("topic label is required".to_string());
    }
    let mut stmt = conn
        .prepare("SELECT topic_id FROM topic_catalog WHERE lower(label)=lower(?) LIMIT 1")
        .map_err(|e| e.to_string())?;
    let existing: Result<String, _> = stmt.query_row([normalized.as_str()], |row| row.get(0));
    if let Ok(topic_id) = existing {
        return Ok(topic_id);
    }

    let topic_id = format!("topic:{}", normalized);
    conn.execute(
        "INSERT OR IGNORE INTO topic_catalog(topic_id, label, source, created_at) VALUES (?, ?, ?, datetime('now'))",
        params![topic_id, normalized, source],
    )
    .map_err(|e| e.to_string())?;
    Ok(format!("topic:{}", normalized))
}

pub fn clear_topics(conn: &Connection) -> Result<(), String> {
    conn.execute("DELETE FROM paper_topic_scores", [])
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn replace_paper_topics(conn: &Connection, paper_id: &str, topics: &[(String, f64)]) -> Result<(), String> {
    conn.execute("DELETE FROM paper_topic_scores WHERE paper_id = ?", [paper_id])
        .map_err(|e| e.to_string())?;
    let now = chrono_now_iso();
    for (topic_id, score) in topics {
        conn.execute(
            "INSERT OR REPLACE INTO paper_topic_scores(paper_id, topic_id, score, updated_at) VALUES (?, ?, ?, ?)",
            params![paper_id, topic_id, score, now],
        )
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn clear_communities(conn: &Connection) -> Result<(), String> {
    conn.execute("DELETE FROM citation_communities", [])
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn set_community(conn: &Connection, paper_id: &str, community_id: &str, score: f64) -> Result<(), String> {
    conn.execute(
        "INSERT OR REPLACE INTO citation_communities(paper_id, community_id, score, updated_at) VALUES (?, ?, ?, datetime('now'))",
        params![paper_id, community_id, score],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn start_cluster_run(conn: &Connection, run_id: &str, mode: &str) -> Result<(), String> {
    conn.execute(
        "INSERT INTO cluster_runs(run_id, started_at, status, mode, papers_processed) VALUES (?, datetime('now'), 'running', ?, 0)",
        params![run_id, mode],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn finish_cluster_run(conn: &Connection, run_id: &str, status: &str, papers_processed: i64) -> Result<(), String> {
    conn.execute(
        "UPDATE cluster_runs SET ended_at = datetime('now'), status = ?, papers_processed = ? WHERE run_id = ?",
        params![status, papers_processed, run_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

fn chrono_now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format!("{}", now)
}

pub fn paper_ids_for_topic(conn: &Connection, topic: &str) -> Result<Vec<String>, String> {
    let mut stmt = conn
        .prepare(
            "
            SELECT pts.paper_id
            FROM paper_topic_scores pts
            JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
            WHERE lower(tc.label) = lower(?)
            ORDER BY pts.score DESC
            ",
        )
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([topic], |row| row.get::<_, String>(0))
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn paper_ids_for_community(conn: &Connection, community_id: &str) -> Result<Vec<String>, String> {
    let mut stmt = conn
        .prepare("SELECT paper_id FROM citation_communities WHERE community_id = ? ORDER BY score DESC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([community_id], |row| row.get::<_, String>(0))
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows {
        out.push(row.map_err(|e| e.to_string())?);
    }
    Ok(out)
}

pub fn load_features(conn: &Connection, paper_ids: &[String]) -> Result<HashMap<String, PaperFeature>, String> {
    if paper_ids.is_empty() {
        return Ok(HashMap::new());
    }

    let mut out: HashMap<String, PaperFeature> = HashMap::new();

    let placeholders = vec!["?"; paper_ids.len()].join(",");
    let sql = format!(
        "
        SELECT p.id, COALESCE(q.status, 'inbox') AS queue_status, COALESCE(c.degree, 0) AS citation_degree
        FROM papers p
        LEFT JOIN reading_queue q ON q.paper_id = p.id
        LEFT JOIN (
            SELECT paper_id, COUNT(*) AS degree FROM (
                SELECT source_paper_id AS paper_id FROM citations
                UNION ALL
                SELECT target_paper_id AS paper_id FROM citations
            ) z
            GROUP BY paper_id
        ) c ON c.paper_id = p.id
        WHERE p.id IN ({})
        ",
        placeholders
    );

    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let params: Vec<&str> = paper_ids.iter().map(|s| s.as_str()).collect();
    let rows = stmt
        .query_map(rusqlite::params_from_iter(params), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, f64>(2)?,
            ))
        })
        .map_err(|e| e.to_string())?;

    for row in rows {
        let (paper_id, queue_status, citation_degree) = row.map_err(|e| e.to_string())?;
        out.insert(
            paper_id,
            PaperFeature {
                queue_status,
                citation_degree,
                topics: Vec::new(),
            },
        );
    }

    let topic_sql = format!(
        "
        SELECT pts.paper_id, tc.label, pts.score
        FROM paper_topic_scores pts
        JOIN topic_catalog tc ON tc.topic_id = pts.topic_id
        WHERE pts.paper_id IN ({})
        ",
        placeholders
    );
    let mut topic_stmt = conn.prepare(&topic_sql).map_err(|e| e.to_string())?;
    let topic_rows = topic_stmt
        .query_map(rusqlite::params_from_iter(paper_ids.iter().map(|s| s.as_str())), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, f64>(2)?,
            ))
        })
        .map_err(|e| e.to_string())?;

    for row in topic_rows {
        let (paper_id, label, score) = row.map_err(|e| e.to_string())?;
        if let Some(feature) = out.get_mut(&paper_id) {
            feature.topics.push((label, score));
        }
    }

    Ok(out)
}
