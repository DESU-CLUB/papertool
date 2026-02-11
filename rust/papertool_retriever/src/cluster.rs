use std::collections::{HashMap, HashSet, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::db;

const STOPWORDS: &[&str] = &[
    "the", "and", "for", "with", "from", "that", "this", "into", "paper", "result", "method",
];

fn run_id() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("run-{}", now)
}

fn tokenize(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_alphanumeric() && c != '_')
        .filter(|t| t.len() >= 3 && !STOPWORDS.contains(t))
        .map(|t| t.to_string())
        .collect()
}

pub fn build_clusters(db_path: &str) -> Result<(usize, usize, usize), String> {
    let conn = db::open(db_path)?;
    let papers = db::load_all_papers(&conn)?;

    let rid = run_id();
    db::start_cluster_run(&conn, &rid, "on_demand")?;

    // Topic clustering.
    let mut known = db::topic_labels(&conn)?;
    let mut vocab: HashMap<String, usize> = HashMap::new();
    for (_paper_id, title, summary, full_text) in &papers {
        let text = format!("{} {} {}", title, summary, full_text.chars().take(4000).collect::<String>());
        for token in tokenize(&text) {
            *vocab.entry(token).or_insert(0) += 1;
        }
    }

    for (token, freq) in &vocab {
        if *freq >= 3 && !known.contains(token) {
            let _ = db::upsert_topic(&conn, token, "auto")?;
            known.push(token.clone());
        }
    }

    db::clear_topics(&conn)?;
    for (paper_id, title, summary, full_text) in &papers {
        let text = format!("{} {} {}", title, summary, full_text.chars().take(8000).collect::<String>()).to_lowercase();
        let tokens = tokenize(&text);
        let mut counts: HashMap<String, usize> = HashMap::new();
        for token in &tokens {
            *counts.entry(token.clone()).or_insert(0) += 1;
        }
        let mut scored: Vec<(String, f64)> = Vec::new();
        for label in &known {
            let c = counts.get(label).copied().unwrap_or(0);
            if c == 0 {
                continue;
            }
            let topic_id = db::upsert_topic(&conn, label, "auto")?;
            let score = (0.35 + 0.2 * c as f64).min(1.0);
            scored.push((topic_id, score));
        }
        db::replace_paper_topics(&conn, paper_id, &scored)?;
    }

    // Citation communities via weakly-connected components.
    db::clear_communities(&conn)?;
    let edges = db::citation_edges(&conn)?;
    let mut graph: HashMap<String, HashSet<String>> = HashMap::new();
    for (paper_id, _title, _summary, _full_text) in &papers {
        graph.entry(paper_id.clone()).or_default();
    }
    for (source, target) in edges {
        graph.entry(source.clone()).or_default().insert(target.clone());
        graph.entry(target).or_default().insert(source);
    }

    let mut seen: HashSet<String> = HashSet::new();
    let mut community_count = 0usize;
    for (paper_id, _title, _summary, _full_text) in &papers {
        if seen.contains(paper_id) {
            continue;
        }
        let cid = format!("comm:{}", community_count);
        community_count += 1;

        let mut q: VecDeque<String> = VecDeque::new();
        q.push_back(paper_id.clone());
        seen.insert(paper_id.clone());

        let mut component: Vec<String> = Vec::new();
        while let Some(node) = q.pop_front() {
            component.push(node.clone());
            if let Some(neighbors) = graph.get(&node) {
                for nxt in neighbors {
                    if seen.contains(nxt) {
                        continue;
                    }
                    seen.insert(nxt.clone());
                    q.push_back(nxt.clone());
                }
            }
        }

        let size = component.len().max(1) as f64;
        for node in component {
            let degree = graph.get(&node).map(|n| n.len()).unwrap_or(0) as f64;
            let score = ((degree + 1.0) / size).min(1.0);
            db::set_community(&conn, &node, &cid, score)?;
        }
    }

    db::finish_cluster_run(&conn, &rid, "ok", papers.len() as i64)?;
    Ok((papers.len(), known.len(), community_count))
}
