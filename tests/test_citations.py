from papertool.citations import (
    ReferenceCandidate,
    build_reference_preview,
    extract_cited_identifiers,
    extract_reference_candidates,
    extract_reference_entries,
    find_identifiers,
    link_citations,
    match_reference_titles_to_local_papers,
    normalize_arxiv,
    normalize_doi,
)
from papertool.ingest import _merge_citation_links


def test_find_identifiers_detects_doi_and_arxiv() -> None:
    text = "We build on 10.1145/12345.67890 and arXiv:2401.01234v2"
    dois, arxivs = find_identifiers(text)
    assert normalize_doi("10.1145/12345.67890") in dois
    assert normalize_arxiv("2401.01234v2") in arxivs


def test_extract_cited_identifiers_prefers_reference_section() -> None:
    text = "Intro with 10.1111/skip.1\n\nReferences\n[1] doi:10.2222/use.2"
    dois, _arxivs = extract_cited_identifiers(text)
    assert "10.2222/use.2" in dois


def test_link_citations_matches_known_ids() -> None:
    cited = ({"10.1000/xyz"}, {"2401.00001"})
    links = link_citations(
        cited,
        doi_to_paper={"10.1000/xyz": "paper-a"},
        arxiv_to_paper={"2401.00001": "paper-b"},
    )
    targets = {target for target, _reason, _confidence in links}
    assert "paper-a" in targets
    assert "paper-b" in targets


def test_extract_reference_entries_merges_wrapped_lines() -> None:
    text = """
Introduction text.

References
[1] Tri Dao, Daniel Fu, and Christopher Re.
FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness.
In NeurIPS, 2022.
[2] Another entry starts here.
"""
    entries = extract_reference_entries(text)
    assert len(entries) >= 2
    assert "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness" in entries[0]


def test_conservative_title_match_links_local_paper() -> None:
    text = """
References
[5] Tri Dao, Daniel Y. Fu, Stefano Ermon, Atri Rudra, and Christopher Re.
FlashAttention: Fast and memory-efficient exact attention with IO-awareness.
In Advances in Neural Information Processing Systems, 2022.
"""
    candidates = extract_reference_candidates(text)
    local_papers = [
        {"id": "fa1", "title": "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness", "published_date": "2022"},
        {"id": "other", "title": "Open-Sora: Democratizing Efficient Video Production for All", "published_date": "2024"},
    ]
    links = match_reference_titles_to_local_papers(
        candidates,
        local_papers,
        mode="conservative",
        source_paper_id="fa2",
    )
    assert any(target == "fa1" and reason.startswith("title:") for target, reason, _conf in links)
    assert not any(target == "other" for target, _reason, _conf in links)


def test_citation_merge_priority_prefers_id_based_reason() -> None:
    merged = _merge_citation_links(
        [
            ("paper-a", "title:flashattention fast attention", 0.82),
            ("paper-a", "doi:10.1000/xyz", 0.95),
        ]
    )
    assert merged == [("paper-a", "doi:10.1000/xyz", 0.95)]


def test_build_reference_preview_includes_best_match() -> None:
    candidates = [
        ReferenceCandidate(
            raw_entry="[5] FlashAttention: Fast and memory-efficient exact attention with IO-awareness.",
            title="FlashAttention: Fast and memory-efficient exact attention with IO-awareness",
            year=2022,
            dois=set(),
            arxiv_ids=set(),
        )
    ]
    local_papers = [
        {
            "id": "fa1",
            "title": "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
            "published_date": "2022",
            "doi": "",
            "arxiv_id": "2205.14135",
            "title_variants": [
                "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
            ],
        }
    ]
    preview = build_reference_preview(candidates, local_papers, source_paper_id="fa2", mode="conservative", limit=10)
    assert len(preview) == 1
    assert preview[0]["extracted_title"].startswith("FlashAttention:")
    best = preview[0]["best_match"]
    assert isinstance(best, dict)
    assert best["target_paper_id"] == "fa1"
    assert str(best["reason"]).startswith("title:")
