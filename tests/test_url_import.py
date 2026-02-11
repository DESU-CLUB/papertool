from papertool.url_import import arxiv_abs_to_pdf, detect_resource_kind, normalize_input_url


def test_detect_resource_kind_for_known_sources() -> None:
    assert detect_resource_kind("https://arxiv.org/abs/2205.14135") == "arxiv"
    assert detect_resource_kind("https://example.com/paper.pdf") == "pdf"
    assert detect_resource_kind("https://github.com/owner/repo") == "github"
    assert detect_resource_kind("https://x.com/user/status/123") == "x_post"
    assert detect_resource_kind("https://example.com/post") == "webpage"


def test_arxiv_abs_to_pdf_conversion() -> None:
    assert arxiv_abs_to_pdf("https://arxiv.org/abs/2205.14135") == "https://arxiv.org/pdf/2205.14135.pdf"


def test_normalize_input_url_adds_https() -> None:
    assert normalize_input_url("example.com") == "https://example.com"
