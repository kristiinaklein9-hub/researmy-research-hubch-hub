"""Wave 3: new search backends (Google Scholar + SSRN) + --field exposure.

Tests use mocked HTTP / scholarly so no real network calls are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Google Scholar backend
# ---------------------------------------------------------------------------


def test_google_scholar_skips_when_scholarly_not_installed(monkeypatch):
    """GoogleScholarBackend returns [] when scholarly is not installed."""
    import research_hub.search.google_scholar_backend as gs_mod

    monkeypatch.setattr(gs_mod, "_SCHOLARLY_AVAILABLE", False)
    backend = gs_mod.GoogleScholarBackend()
    results = backend.search("flood risk", limit=5)
    assert results == []


def test_google_scholar_converts_pub_to_search_result(monkeypatch):
    """GoogleScholarBackend._convert produces correct SearchResult from a pub dict."""
    import research_hub.search.google_scholar_backend as gs_mod

    monkeypatch.setattr(gs_mod, "_SCHOLARLY_AVAILABLE", True)

    pub = {
        "bib": {
            "title": "Flood Risk Under Climate Change",
            "pub_year": "2022",
            "author": "Smith, J and Doe, A",
            "abstract": "We study flood risk.",
            "venue": "Nature Climate Change",
        },
        "pub_url": "https://doi.org/10.1/x",
        "eprint_url": "",
        "num_citations": 42,
    }
    backend = gs_mod.GoogleScholarBackend()
    result = backend._convert(pub)
    assert result is not None
    assert result.title == "Flood Risk Under Climate Change"
    assert result.year == 2022
    assert "Smith" in result.authors[0]
    assert result.citation_count == 42
    assert result.source == "google-scholar"


def test_google_scholar_convert_returns_none_for_missing_title(monkeypatch):
    """GoogleScholarBackend._convert returns None when title is absent."""
    import research_hub.search.google_scholar_backend as gs_mod

    backend = gs_mod.GoogleScholarBackend()
    result = backend._convert({"bib": {}})
    assert result is None


def test_google_scholar_search_with_mock(monkeypatch):
    """GoogleScholarBackend.search calls scholarly and converts results."""
    import research_hub.search.google_scholar_backend as gs_mod

    fake_pub = {
        "bib": {
            "title": "Mocked Paper",
            "pub_year": "2023",
            "author": "Author One",
            "abstract": "Abstract text.",
            "venue": "ICLR",
        },
        "pub_url": "https://example.com/paper",
        "eprint_url": "",
        "num_citations": 10,
    }

    mock_scholarly = MagicMock()
    mock_scholarly.search_pubs.return_value = iter([fake_pub])

    monkeypatch.setattr(gs_mod, "_SCHOLARLY_AVAILABLE", True)
    monkeypatch.setattr(gs_mod, "_scholarly_mod", mock_scholarly)

    backend = gs_mod.GoogleScholarBackend(delay_seconds=0)
    results = backend.search("neural network", limit=5)
    assert len(results) == 1
    assert results[0].title == "Mocked Paper"


# ---------------------------------------------------------------------------
# SSRN backend
# ---------------------------------------------------------------------------


def test_ssrn_converts_paper_dict_to_search_result():
    """SsrnBackend._convert produces correct SearchResult from API dict."""
    from research_hub.search.ssrn_backend import SsrnBackend

    paper = {
        "title": "Behavioral Flood Adaptation",
        "date": "2023-04-15",
        "authors": [{"name": "Chen Wei"}, {"firstName": "Alice", "lastName": "Lee"}],
        "doi": "10.2139/ssrn.1234567",
        "abstract": "We model adaptive behavior under flood risk.",
        "abstract_id": "1234567",
        "downloads": 234,
    }
    backend = SsrnBackend()
    result = backend._convert(paper)
    assert result is not None
    assert result.title == "Behavioral Flood Adaptation"
    assert result.year == 2023
    assert "Chen Wei" in result.authors
    assert "Alice Lee" in result.authors
    assert result.doi == "10.2139/ssrn.1234567"
    assert result.citation_count == 234
    assert result.source == "ssrn"
    assert result.doc_type == "preprint"


def test_ssrn_convert_returns_none_for_missing_title():
    """SsrnBackend._convert returns None when title is absent."""
    from research_hub.search.ssrn_backend import SsrnBackend

    backend = SsrnBackend()
    assert backend._convert({}) is None
    assert backend._convert({"title": ""}) is None


def test_ssrn_search_returns_empty_on_http_error(monkeypatch):
    """SsrnBackend.search returns [] on non-200 HTTP response."""
    from research_hub.search.ssrn_backend import SsrnBackend
    import requests

    mock_resp = MagicMock()
    mock_resp.status_code = 503

    with patch("research_hub.search.ssrn_backend.requests.get", return_value=mock_resp):
        backend = SsrnBackend(delay_seconds=0)
        results = backend.search("behavioral economics", limit=5)
        assert results == []


def test_ssrn_search_returns_empty_on_network_error(monkeypatch):
    """SsrnBackend.search returns [] on network exception."""
    import requests as req_mod
    from research_hub.search.ssrn_backend import SsrnBackend

    with patch(
        "research_hub.search.ssrn_backend.requests.get",
        side_effect=req_mod.exceptions.ConnectionError("no internet"),
    ):
        backend = SsrnBackend(delay_seconds=0)
        results = backend.search("policy adoption", limit=5)
        assert results == []


def test_ssrn_search_parses_papers_key(monkeypatch):
    """SsrnBackend.search correctly parses {'papers': [...]} response shape."""
    from research_hub.search.ssrn_backend import SsrnBackend

    fake_paper = {
        "title": "Socio-Hydrology Study",
        "date": "2022-01-10",
        "authors": [{"name": "Wang Fang"}],
        "doi": "",
        "abstract": "Water and people.",
        "abstract_id": "99999",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"papers": [fake_paper]}

    with patch("research_hub.search.ssrn_backend.requests.get", return_value=mock_resp):
        backend = SsrnBackend(delay_seconds=0)
        results = backend.search("socio-hydrology", limit=5)
        assert len(results) == 1
        assert results[0].title == "Socio-Hydrology Study"
        assert results[0].year == 2022
        assert "Wang Fang" in results[0].authors


def test_ssrn_search_parses_results_key(monkeypatch):
    """SsrnBackend.search correctly parses {'results': [...]} response shape."""
    from research_hub.search.ssrn_backend import SsrnBackend

    fake_paper = {
        "title": "Policy Adoption Study",
        "date": "2021-06-01",
        "authors": [{"name": "Li Ming"}],
        "doi": "",
        "abstract": "Policy matters.",
        "abstract_id": "55555",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": [fake_paper]}

    with patch("research_hub.search.ssrn_backend.requests.get", return_value=mock_resp):
        backend = SsrnBackend(delay_seconds=0)
        results = backend.search("policy", limit=5)
        assert len(results) == 1
        assert results[0].title == "Policy Adoption Study"


def test_ssrn_search_parses_bare_list(monkeypatch):
    """SsrnBackend.search correctly parses a bare list API response."""
    from research_hub.search.ssrn_backend import SsrnBackend

    fake_paper = {
        "title": "Behavioral Economics",
        "date": "2020-03-10",
        "authors": [{"name": "Park Jae"}],
        "doi": "",
        "abstract": "People make decisions.",
        "abstract_id": "33333",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [fake_paper]

    with patch("research_hub.search.ssrn_backend.requests.get", return_value=mock_resp):
        backend = SsrnBackend(delay_seconds=0)
        results = backend.search("behavioral", limit=5)
        assert len(results) == 1
        assert results[0].title == "Behavioral Economics"


def test_google_scholar_runtime_error_returns_partial(monkeypatch):
    """GoogleScholarBackend returns partial results when generator raises RuntimeError."""
    import research_hub.search.google_scholar_backend as gs_mod

    def _generator_that_fails():
        yield {
            "bib": {
                "title": "First Paper",
                "pub_year": "2021",
                "author": "Author A",
                "abstract": "First.",
                "venue": "ICML",
            },
            "pub_url": "https://example.com/1",
            "eprint_url": "",
            "num_citations": 5,
        }
        raise RuntimeError("generator raised StopIteration")

    mock_scholarly = MagicMock()
    mock_scholarly.search_pubs.return_value = _generator_that_fails()

    monkeypatch.setattr(gs_mod, "_SCHOLARLY_AVAILABLE", True)
    monkeypatch.setattr(gs_mod, "_scholarly_mod", mock_scholarly)

    backend = gs_mod.GoogleScholarBackend(delay_seconds=0)
    results = backend.search("neural", limit=10)
    # Must return the 1 result collected before the RuntimeError, not []
    assert len(results) == 1
    assert results[0].title == "First Paper"


# ---------------------------------------------------------------------------
# Backend registry + presets
# ---------------------------------------------------------------------------


def test_backend_registry_includes_new_backends():
    """Both google-scholar and ssrn are in the _BACKEND_REGISTRY."""
    from research_hub.search.fallback import _BACKEND_REGISTRY

    assert "google-scholar" in _BACKEND_REGISTRY
    assert "ssrn" in _BACKEND_REGISTRY


def test_field_presets_include_ssrn_in_social():
    """FIELD_PRESETS['social'] includes ssrn backend."""
    from research_hub.search.fallback import FIELD_PRESETS

    assert "ssrn" in FIELD_PRESETS["social"]
    assert "ssrn" in FIELD_PRESETS["econ"]
    assert "ssrn" in FIELD_PRESETS["general"]


def test_field_presets_include_google_scholar_in_cs():
    """FIELD_PRESETS['cs'] includes google-scholar backend."""
    from research_hub.search.fallback import FIELD_PRESETS

    assert "google-scholar" in FIELD_PRESETS["cs"]
    assert "google-scholar" in FIELD_PRESETS["general"]


def test_auto_pipeline_prints_active_backends(monkeypatch, capsys, tmp_path):
    """auto_pipeline prints active backends at start of search step."""
    from research_hub import auto as auto_mod

    cfg = type("Cfg", (), {})()
    cfg.clusters_file = tmp_path / "clusters.yaml"
    cfg.research_hub_dir = tmp_path / ".research_hub"
    cfg.root = tmp_path
    cfg.raw = tmp_path / "raw"
    cfg.raw.mkdir()
    (cfg.raw / "x").mkdir()
    (cfg.raw / "x" / "p1.md").write_text("paper", encoding="utf-8")

    monkeypatch.setattr(auto_mod, "get_config", lambda: cfg)

    class _Reg:
        def __init__(self, *a, **kw):
            pass
        def get(self, slug):
            return type("C", (), {"slug": slug, "name": slug})()
        def create(self, **kw):
            return type("C", (), {"slug": kw["slug"], "name": kw.get("name", "")})()

    monkeypatch.setattr(auto_mod, "ClusterRegistry", _Reg)
    monkeypatch.setattr(auto_mod, "_run_search", lambda topic, **kw: [{"slug": "p1"}])
    monkeypatch.setattr(auto_mod, "run_pipeline", lambda **kw: 0)

    report = auto_mod.auto_pipeline(
        "test topic",
        max_papers=1,
        field="social",
        do_nlm=False,
        do_crystals=False,
        do_fit_check=False,
        print_progress=True,
    )
    out = capsys.readouterr().out
    assert "[search] backends:" in out
    assert "ssrn" in out or "openalex" in out
