from __future__ import annotations

from pathlib import Path

from papertool.ingest import extract_title


def test_extract_title_skips_conference_banner_and_joins_uppercase_lines() -> None:
    text = """
arXiv:2412.06464v3  [cs.CL]  6 Mar 2025
Published as a conference paper at ICLR 2025
GATED DELTA NETWORKS :
IMPROVING MAMBA 2 WITH DELTA RULE
Songlin Yang
MIT CSAIL
"""
    title = extract_title(Path("2412.06464.pdf"), text)
    assert title == "GATED DELTA NETWORKS: IMPROVING MAMBA 2 WITH DELTA RULE"


def test_extract_title_falls_back_to_stem_when_no_candidate_line() -> None:
    text = """
arXiv:2412.06464v3
Abstract
Code: https://github.com/example/repo
"""
    title = extract_title(Path("my_paper-1.pdf"), text)
    assert title == "my paper 1"
