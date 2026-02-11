use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::Value;
use tantivy::Index;

use crate::db;
use crate::indexer::{schema_and_fields, IndexFields};
use crate::types::{Hit, QuizRank};

const TOPIC_SEEDS: &[&str] = &[
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
];

fn queue_weight(status: &str) -> f64 {
    match status {
        "today" => 1.0,
        "next" => 0.75,
        "inbox" => 0.45,
        "later" => 0.2,
        "done" => 0.05,
        _ => 0.45,
    }
}

fn normalize(values: &[f64], invert: bool) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    let lo = values
        .iter()
        .fold(f64::INFINITY, |acc, val| if *val < acc { *val } else { acc });
    let hi = values
        .iter()
        .fold(f64::NEG_INFINITY, |acc, val| if *val > acc { *val } else { acc });
    if (hi - lo).abs() < 1e-12 {
        return vec![1.0; values.len()];
    }
    values
        .iter()
        .map(|v| {
            if invert {
                (hi - *v) / (hi - lo)
            } else {
                (*v - lo) / (hi - lo)
            }
        })
        .collect()
}

fn query_topics(query: &str, topic_filter: Option<&str>) -> HashSet<String> {
    let mut out = HashSet::new();
    let lower = query.to_lowercase();
    for token in lower.split(|c: char| !c.is_alphanumeric() && c != '_') {
        if TOPIC_SEEDS.contains(&token) {
            out.insert(token.to_string());
        }
    }
    if let Some(topic) = topic_filter {
        out.insert(topic.trim().to_lowercase());
    }
    out
}

fn extract_text(doc: &tantivy::TantivyDocument, field: tantivy::schema::Field) -> String {
    doc.get_first(field)
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

#[derive(Clone)]
struct RawHit {
    bm25: f64,
    paper_id: String,
    title: String,
    path: String,
    snippet: String,
}

pub fn retrieve(
    db_path: &str,
    index_dir: &str,
    query: &str,
    top_k: usize,
    topic: Option<&str>,
    community_id: Option<&str>,
) -> Result<Vec<Hit>, String> {
    let conn = db::open(db_path)?;

    let mut allowed: Option<HashSet<String>> = None;
    if let Some(topic_label) = topic {
        let ids = db::paper_ids_for_topic(&conn, topic_label)?;
        allowed = Some(ids.into_iter().collect());
    }
    if let Some(cid) = community_id {
        let ids = db::paper_ids_for_community(&conn, cid)?;
        let set: HashSet<String> = ids.into_iter().collect();
        allowed = match allowed {
            Some(existing) => Some(existing.intersection(&set).cloned().collect()),
            None => Some(set),
        };
    }

    let index = Index::open_in_dir(index_dir).map_err(|e| e.to_string())?;
    let reader = index.reader().map_err(|e| e.to_string())?;
    let searcher = reader.searcher();

    let (_schema_check, fields): (_, IndexFields) = schema_and_fields();
    let query_parser = QueryParser::for_index(&index, vec![fields.chunk_text, fields.title, fields.summary]);

    let parsed = query_parser
        .parse_query(query)
        .or_else(|_| query_parser.parse_query("paper"))
        .map_err(|e| e.to_string())?;

    let raw_limit = std::cmp::max(top_k.saturating_mul(12), top_k);
    let top_docs = searcher
        .search(&parsed, &TopDocs::with_limit(raw_limit))
        .map_err(|e| e.to_string())?;

    let mut best_by_paper: HashMap<String, RawHit> = HashMap::new();
    for (score, addr) in top_docs {
        let doc = searcher.doc::<tantivy::TantivyDocument>(addr).map_err(|e| e.to_string())?;
        let paper_id = extract_text(&doc, fields.paper_id);
        if paper_id.is_empty() {
            continue;
        }
        if let Some(ref allow) = allowed {
            if !allow.contains(&paper_id) {
                continue;
            }
        }
        let title = extract_text(&doc, fields.title);
        let path = extract_text(&doc, fields.path);
        let chunk = extract_text(&doc, fields.chunk_text);
        let snippet = if chunk.len() > 240 {
            chunk[..240].to_string()
        } else {
            chunk
        };

        let candidate = RawHit {
            bm25: score as f64,
            paper_id: paper_id.clone(),
            title,
            path,
            snippet,
        };
        match best_by_paper.get(&paper_id) {
            Some(existing) if existing.bm25 >= candidate.bm25 => {}
            _ => {
                best_by_paper.insert(paper_id, candidate);
            }
        }
    }

    let mut deduped: Vec<RawHit> = best_by_paper.into_values().collect();
    if deduped.is_empty() {
        return Ok(Vec::new());
    }

    let paper_ids: Vec<String> = deduped.iter().map(|hit| hit.paper_id.clone()).collect();
    let features = db::load_features(&conn, &paper_ids)?;
    let bm25_vals: Vec<f64> = deduped.iter().map(|hit| hit.bm25).collect();
    let bm25_norm = normalize(&bm25_vals, false);

    let citation_vals: Vec<f64> = paper_ids
        .iter()
        .map(|paper_id| features.get(paper_id).map(|f| f.citation_degree).unwrap_or(0.0))
        .collect();
    let citation_norm = normalize(&citation_vals, false);

    let q_topics = query_topics(query, topic);

    let mut out: Vec<Hit> = Vec::new();
    for (idx, hit) in deduped.drain(..).enumerate() {
        let feature = features.get(&hit.paper_id);
        let queue_boost = feature
            .map(|f| queue_weight(f.queue_status.as_str()))
            .unwrap_or(queue_weight("inbox"));

        let mut topic_boost = 0.0;
        if let Some(f) = feature {
            for (label, score) in &f.topics {
                if q_topics.contains(label) && *score > topic_boost {
                    topic_boost = *score;
                }
            }
        }

        let final_score = 0.62 * bm25_norm[idx]
            + 0.18 * citation_norm[idx]
            + 0.12 * topic_boost
            + 0.08 * queue_boost;

        out.push(Hit {
            paper_id: hit.paper_id,
            title: hit.title,
            path: hit.path,
            snippet: hit.snippet,
            score: final_score,
        });
    }

    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(Ordering::Equal));
    out.truncate(top_k);
    Ok(out)
}

pub fn rank_quiz_papers(
    db_path: &str,
    count: usize,
    include_queue_boost: bool,
    diversify_by_topic: bool,
) -> Result<Vec<QuizRank>, String> {
    let conn = db::open(db_path)?;
    let papers = db::load_all_papers(&conn)?;
    if papers.is_empty() {
        return Ok(Vec::new());
    }

    let paper_ids: Vec<String> = papers.iter().map(|(id, _, _, _)| id.clone()).collect();
    let features = db::load_features(&conn, &paper_ids)?;

    let mut scored: Vec<(String, f64, Vec<String>)> = Vec::new();
    for (paper_id, title, summary, _full_text) in papers {
        let mut score = 0.5 + (title.len() as f64 / 300.0).min(0.3) + (summary.len() as f64 / 1000.0).min(0.2);
        let mut labels = Vec::new();
        if let Some(feat) = features.get(&paper_id) {
            if include_queue_boost {
                score += 0.25 * queue_weight(feat.queue_status.as_str());
            }
            score += 0.15 * feat.citation_degree.min(10.0) / 10.0;
            for (label, topic_score) in &feat.topics {
                labels.push(label.clone());
                score += 0.1 * *topic_score;
            }
        }
        scored.push((paper_id, score, labels));
    }

    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(Ordering::Equal));

    let mut out: Vec<QuizRank> = Vec::new();
    if diversify_by_topic {
        let mut seen_topics: HashSet<String> = HashSet::new();
        for (paper_id, score, labels) in &scored {
            let has_new_topic = labels.iter().any(|label| !seen_topics.contains(label));
            if has_new_topic {
                out.push(QuizRank {
                    paper_id: paper_id.clone(),
                    score: *score,
                });
                for label in labels {
                    seen_topics.insert(label.clone());
                }
                if out.len() >= count {
                    return Ok(out);
                }
            }
        }
    }

    for (paper_id, score, _labels) in scored {
        if out.iter().any(|item| item.paper_id == paper_id) {
            continue;
        }
        out.push(QuizRank { paper_id, score });
        if out.len() >= count {
            break;
        }
    }
    Ok(out)
}
