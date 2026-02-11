use std::fs;
use std::path::Path;

use tantivy::schema::{Field, Schema, STORED, TEXT};
use tantivy::{doc, Index};

use crate::db;

#[derive(Clone, Copy)]
pub struct IndexFields {
    pub chunk_id: Field,
    pub paper_id: Field,
    pub title: Field,
    pub path: Field,
    pub chunk_text: Field,
    pub summary: Field,
    pub ingested_at: Field,
    pub queue_status: Field,
}

pub fn schema_and_fields() -> (Schema, IndexFields) {
    let mut schema_builder = Schema::builder();
    let chunk_id = schema_builder.add_i64_field("chunk_id", STORED);
    let paper_id = schema_builder.add_text_field("paper_id", TEXT | STORED);
    let title = schema_builder.add_text_field("title", TEXT | STORED);
    let path = schema_builder.add_text_field("path", STORED);
    let chunk_text = schema_builder.add_text_field("chunk_text", TEXT | STORED);
    let summary = schema_builder.add_text_field("summary", TEXT | STORED);
    let ingested_at = schema_builder.add_text_field("ingested_at", STORED);
    let queue_status = schema_builder.add_text_field("queue_status", STORED);
    let schema = schema_builder.build();
    (
        schema,
        IndexFields {
            chunk_id,
            paper_id,
            title,
            path,
            chunk_text,
            summary,
            ingested_at,
            queue_status,
        },
    )
}

pub fn build_index(db_path: &str, index_dir: &str, _paper_id: Option<&str>) -> Result<usize, String> {
    let conn = db::open(db_path)?;
    let rows = db::load_chunk_docs(&conn)?;

    let target = Path::new(index_dir);
    if target.exists() {
        fs::remove_dir_all(target).map_err(|e| e.to_string())?;
    }
    fs::create_dir_all(target).map_err(|e| e.to_string())?;

    let (schema, fields) = schema_and_fields();
    let index = Index::create_in_dir(target, schema).map_err(|e| e.to_string())?;
    let mut writer = index.writer(40_000_000).map_err(|e| e.to_string())?;

    for row in &rows {
        writer.add_document(doc!(
            fields.chunk_id => row.chunk_id,
            fields.paper_id => row.paper_id.clone(),
            fields.title => row.title.clone(),
            fields.path => row.path.clone(),
            fields.chunk_text => row.chunk_text.clone(),
            fields.summary => row.summary.clone(),
            fields.ingested_at => row.ingested_at.clone(),
            fields.queue_status => row.queue_status.clone(),
        ))
        .map_err(|e| e.to_string())?;
    }

    writer.commit().map_err(|e| e.to_string())?;
    Ok(rows.len())
}
