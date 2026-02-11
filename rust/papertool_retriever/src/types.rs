use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Hit {
    pub paper_id: String,
    pub title: String,
    pub path: String,
    pub snippet: String,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuizRank {
    pub paper_id: String,
    pub score: f64,
}

#[derive(Debug, Clone)]
pub struct ChunkDoc {
    pub chunk_id: i64,
    pub paper_id: String,
    pub title: String,
    pub path: String,
    pub chunk_text: String,
    pub summary: String,
    pub ingested_at: String,
    pub queue_status: String,
}

#[derive(Debug, Clone)]
pub struct PaperFeature {
    pub queue_status: String,
    pub citation_degree: f64,
    pub topics: Vec<(String, f64)>,
}
