use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

mod cluster;
mod db;
mod indexer;
mod retriever;
mod types;

fn err_to_py(err: String) -> PyErr {
    PyRuntimeError::new_err(err)
}

#[pyfunction]
#[pyo3(signature = (db_path, index_dir, paper_id=None))]
fn build_index(py: Python<'_>, db_path: &str, index_dir: &str, paper_id: Option<&str>) -> PyResult<PyObject> {
    let indexed = indexer::build_index(db_path, index_dir, paper_id).map_err(err_to_py)?;
    let out = PyDict::new_bound(py);
    out.set_item("ok", true)?;
    out.set_item("backend", "rust")?;
    out.set_item("indexed", indexed)?;
    out.set_item("index_dir", index_dir)?;
    Ok(out.to_object(py))
}

#[pyfunction]
#[pyo3(signature = (db_path, index_dir, query, top_k, topic=None, community_id=None))]
fn retrieve(
    py: Python<'_>,
    db_path: &str,
    index_dir: &str,
    query: &str,
    top_k: usize,
    topic: Option<&str>,
    community_id: Option<&str>,
) -> PyResult<PyObject> {
    let hits = retriever::retrieve(db_path, index_dir, query, top_k, topic, community_id).map_err(err_to_py)?;
    let out = PyList::empty_bound(py);
    for hit in hits {
        let item = PyDict::new_bound(py);
        item.set_item("paper_id", hit.paper_id)?;
        item.set_item("title", hit.title)?;
        item.set_item("path", hit.path)?;
        item.set_item("snippet", hit.snippet)?;
        item.set_item("score", hit.score)?;
        out.append(item)?;
    }
    Ok(out.to_object(py))
}

#[pyfunction]
fn rank_quiz_papers(
    py: Python<'_>,
    db_path: &str,
    _index_dir: &str,
    count: usize,
    include_queue_boost: bool,
    diversify_by_topic: bool,
) -> PyResult<PyObject> {
    let rows = retriever::rank_quiz_papers(db_path, count, include_queue_boost, diversify_by_topic).map_err(err_to_py)?;
    let out = PyList::empty_bound(py);
    for row in rows {
        let item = PyDict::new_bound(py);
        item.set_item("paper_id", row.paper_id)?;
        item.set_item("score", row.score)?;
        out.append(item)?;
    }
    Ok(out.to_object(py))
}

#[pyfunction]
fn build_clusters(py: Python<'_>, db_path: &str, _index_dir: &str) -> PyResult<PyObject> {
    let (papers, topics, communities) = cluster::build_clusters(db_path).map_err(err_to_py)?;
    let out = PyDict::new_bound(py);
    out.set_item("ok", true)?;
    out.set_item("backend", "rust")?;
    out.set_item("papers_processed", papers)?;
    out.set_item("topics_total", topics)?;
    out.set_item("communities_total", communities)?;
    Ok(out.to_object(py))
}

#[pymodule]
fn papertool_retriever_native(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(build_index, m)?)?;
    m.add_function(wrap_pyfunction!(retrieve, m)?)?;
    m.add_function(wrap_pyfunction!(rank_quiz_papers, m)?)?;
    m.add_function(wrap_pyfunction!(build_clusters, m)?)?;
    Ok(())
}
