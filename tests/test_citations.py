from papertool.citations import (
    extract_cited_identifiers,
    find_identifiers,
    link_citations,
    normalize_arxiv,
    normalize_doi,
)


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
