"""Tests for the pre-upload URL quality guard (v0.95.0 / plan playful-humming-tiger).

All HTTP calls are mocked — no real network access in this suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared by bundle tests
# ---------------------------------------------------------------------------


def _note(
    path: Path,
    doi: str,
    title: str = "Paper",
    topic_cluster: str = "alpha",
    url: str = "",
    summarize_status: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f'title: "{title}"',
        f'doi: "{doi}"',
        f'url: "{url}"',
        f'topic_cluster: "{topic_cluster}"',
    ]
    if summarize_status:
        fm_lines.append(f'summarize_status: "{summarize_status}"')
    fm_lines += ["---", "", f"# {title}", ""]
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


class _StubCfg:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.logs = root / "logs"
        self.research_hub_dir = root / ".research_hub"


# ---------------------------------------------------------------------------
# 1. Classifier tests — mock _probe_url so no real network
# ---------------------------------------------------------------------------


def _make_probe_response(
    *,
    status_code: int = 200,
    headers: dict | None = None,
    body: str = "",
    final_url: str = "https://example.com/paper",
):
    """Build a fake requests.Response-style object for _probe_url mocking."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.url = final_url
    body_bytes = body.encode("utf-8")

    def _iter_content(chunk_size=8192):
        # yield body in one shot
        if body_bytes:
            yield body_bytes

    resp.iter_content = _iter_content
    return resp


def test_classify_cloudflare_block():
    from research_hub.notebooklm.url_quality import classify_url_source

    mock_resp = _make_probe_response(
        status_code=403,
        headers={"Cf-Mitigated": "challenge"},
        body="<html>Attention Required</html>",
        final_url="https://www.tandfonline.com/doi/full/10.1080/test",
    )
    with patch("requests.get", return_value=mock_resp):
        result = classify_url_source(
            "https://doi.org/10.1080/test",
            "done",
        )
    assert result.quality == "likely_error_page"
    assert result.reason == "cloudflare_block"


def test_classify_tf_cookie_wall():
    from research_hub.notebooklm.url_quality import classify_url_source

    mock_resp = _make_probe_response(
        status_code=200,
        body='<html><body><a href="/action/cookieAbsent">login</a></body></html>',
        final_url="https://www.tandfonline.com/action/cookieAbsent",
    )
    with patch("requests.get", return_value=mock_resp):
        result = classify_url_source(
            "https://doi.org/10.1080/test",
            "done",
        )
    assert result.quality == "likely_error_page"
    assert result.reason == "tf_cookie_wall"


def test_classify_elsevier_js_redirect():
    from research_hub.notebooklm.url_quality import classify_url_source

    small_body = "<html><head><title>Redirecting</title></head><body>...</body></html>"
    assert len(small_body.encode()) < 5_000
    mock_resp = _make_probe_response(
        status_code=200,
        body=small_body,
        final_url="https://linkinghub.elsevier.com/retrieve/pii/S12345",
    )
    with patch("requests.get", return_value=mock_resp):
        result = classify_url_source(
            "https://doi.org/10.1016/test",
            "done",
        )
    assert result.quality == "likely_error_page"
    assert result.reason == "elsevier_js_redirect"


def test_classify_springer_full_article_ok():
    """Springer returns a real article page — should classify as ok (probe clears metadata FP)."""
    from research_hub.notebooklm.url_quality import classify_url_source

    full_body = (
        "<html><body>"
        + '<div class="abstract-content" id="abstract">'
        + "This study examines... " * 300  # large enough body
        + "</div></body></html>"
    )
    mock_resp = _make_probe_response(
        status_code=200,
        body=full_body,
        final_url="https://link.springer.com/article/10.1007/test",
    )
    with patch("requests.get", return_value=mock_resp):
        result = classify_url_source(
            "https://doi.org/10.1007/test",
            "done",
        )
    assert result.quality == "ok"


def test_classify_arxiv_no_probe():
    """arXiv host → ok immediately, no probe needed."""
    from research_hub.notebooklm.url_quality import classify_url_source

    with patch("requests.get") as mock_get:
        result = classify_url_source(
            "https://arxiv.org/abs/2502.10978",
            "done",
        )
    mock_get.assert_not_called()
    assert result.quality == "ok"
    assert result.reason == "open_host"


def test_classify_failed_no_abstract_probe_returns_likely_error_page():
    """failed_no_abstract + probe returns HTTP 200 → likely_error_page.

    Even when the probe gets HTTP 200 (e.g. Springer skeleton page that is
    large enough to escape the <10 KB body check), failed_no_abstract is
    reliable paywall evidence: the summarizer already tried and failed at
    ingest time. The reason is preserved as probe_cleared_failed_no_abstract
    for auditability, but quality is now likely_error_page so the bundle
    falls back to abstract text instead of uploading a URL NLM cannot read.
    """
    from research_hub.notebooklm.url_quality import classify_url_source

    article_text = "This study examines social simulation... " * 400
    abstract_body = (
        "<html><body>"
        + '<div class="abstract" id="abstract">'
        + article_text
        + "</div></body></html>"
    )
    mock_resp = _make_probe_response(
        status_code=200,
        body=abstract_body,
        final_url="https://link.springer.com/article/10.1007/s42001-026-00465-4",
    )
    with patch("requests.get", return_value=mock_resp):
        result = classify_url_source(
            "https://doi.org/10.1007/s42001-026-00465-4",
            "failed_no_abstract",
        )
    assert result.quality == "likely_error_page"
    assert result.reason == "probe_cleared_failed_no_abstract"


def test_classify_failed_no_abstract_probe_timeout_stays_error_page():
    """failed_no_abstract + probe raises Timeout → likely_error_page (metadata confirmed)."""
    from research_hub.notebooklm.url_quality import classify_url_source

    import requests as req

    with patch("requests.get", side_effect=req.Timeout("timed out")):
        result = classify_url_source(
            "https://doi.org/10.1080/test",
            "failed_no_abstract",
        )
    assert result.quality == "likely_error_page"
    assert result.reason == "failed_no_abstract"


def test_classify_probe_timeout_returns_unknown():
    """Probe exception/timeout → quality=unknown (fail-safe, never skipped)."""
    from research_hub.notebooklm.url_quality import classify_url_source

    import requests as req

    with patch("requests.get", side_effect=req.Timeout("timed out")):
        result = classify_url_source(
            "https://doi.org/10.1016/test",
            "done",
        )
    assert result.quality == "unknown"
    assert result.reason == "probe_exception"


# ---------------------------------------------------------------------------
# 2. Bundle integration tests
# ---------------------------------------------------------------------------


def test_bundle_failed_no_abstract_sets_url_quality(tmp_path):
    """A note with summarize_status=failed_no_abstract and no local PDF → entry
    url_quality=likely_error_page (probe IS called because pdf is None, and
    mocked to return likely_error_page confirming a genuinely bad URL)."""
    from research_hub.clusters import Cluster
    from research_hub.notebooklm.bundle import bundle_cluster
    from research_hub.notebooklm.url_quality import UrlQuality

    cfg = _StubCfg(tmp_path)
    cfg.raw.mkdir(parents=True)
    cfg.research_hub_dir.mkdir(parents=True)
    (cfg.root / "pdfs").mkdir()

    _note(
        cfg.raw / "alpha" / "bad.md",
        doi="10.1080/test",
        topic_cluster="alpha",
        summarize_status="failed_no_abstract",
        url="",
    )

    cluster = Cluster(slug="alpha", name="Alpha Cluster", obsidian_subfolder="alpha")

    # pdf is None → probe is enabled; mock _probe_url to return likely_error_page
    # (simulating a real Cloudflare-blocked T&F article)
    probe_result = UrlQuality("likely_error_page", "cloudflare_block", "HTTP 403 + Cf-Mitigated: challenge")
    with patch("research_hub.notebooklm.url_quality._probe_url", return_value=probe_result) as mock_probe:
        report = bundle_cluster(cluster, cfg)

    # Probe SHOULD be called (pdf is None path)
    mock_probe.assert_called_once()

    url_entries = [e for e in report.entries if e.url]
    assert len(url_entries) == 1
    entry = url_entries[0]
    assert entry.url_quality == "likely_error_page"
    assert entry.url_quality_reason == "failed_no_abstract"


def test_bundle_local_pdf_upgrades_likely_error_page_entry(tmp_path):
    """failed_no_abstract note + local PDF → entry.action upgraded to pdf."""
    from research_hub.clusters import Cluster
    from research_hub.notebooklm.bundle import bundle_cluster

    cfg = _StubCfg(tmp_path)
    cfg.raw.mkdir(parents=True)
    cfg.research_hub_dir.mkdir(parents=True)
    pdfs_dir = cfg.root / "pdfs"
    pdfs_dir.mkdir()

    doi = "10.1080/test"
    _note(
        cfg.raw / "alpha" / "bad.md",
        doi=doi,
        topic_cluster="alpha",
        summarize_status="failed_no_abstract",
    )
    # Create a local PDF that will match this DOI
    (pdfs_dir / "test.pdf").write_bytes(b"%PDF")

    cluster = Cluster(slug="alpha", name="Alpha Cluster", obsidian_subfolder="alpha")

    with patch("research_hub.notebooklm.url_quality._probe_url") as mock_probe:
        report = bundle_cluster(cluster, cfg)

    mock_probe.assert_not_called()

    # Entry should be upgraded to pdf, not url
    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.action == "pdf"
    assert entry.url_quality == "likely_error_page"
    assert entry.url_quality_reason == "failed_no_abstract"


def test_bundle_failed_no_abstract_probe_ok_no_abstract_in_note(tmp_path):
    """probe_cleared_failed_no_abstract note with NO abstract in note body → action=url (last resort).

    When probe returns ok but the note has no abstract text, the bundle cannot
    fall back to text. The URL is used as a last resort even though url_quality
    is likely_error_page — this is acceptable: trying the URL is better than
    skipping entirely. The key behavioural change from pre-fix is that
    url_quality is now likely_error_page, not ok.
    """
    from research_hub.clusters import Cluster
    from research_hub.notebooklm.bundle import bundle_cluster
    from research_hub.notebooklm.url_quality import UrlQuality

    cfg = _StubCfg(tmp_path)
    cfg.raw.mkdir(parents=True)
    cfg.research_hub_dir.mkdir(parents=True)
    (cfg.root / "pdfs").mkdir()

    _note(
        cfg.raw / "alpha" / "hashimoto.md",
        doi="10.1007/s42001-026-00465-4",
        topic_cluster="alpha",
        summarize_status="failed_no_abstract",
        url="",
    )

    cluster = Cluster(slug="alpha", name="Alpha Cluster", obsidian_subfolder="alpha")

    probe_result = UrlQuality("ok", "probe_ok", "HTTP 200")
    with patch("research_hub.notebooklm.url_quality._probe_url", return_value=probe_result):
        report = bundle_cluster(cluster, cfg)

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.url_quality == "likely_error_page"
    assert entry.url_quality_reason == "probe_cleared_failed_no_abstract"
    # No abstract in note → URL used as last resort
    assert entry.action == "url"


def test_bundle_probe_cleared_failed_no_abstract_falls_back_to_text(tmp_path):
    """Regression guard: probe_cleared_failed_no_abstract + abstract in note → action=text.

    A note with summarize_status=failed_no_abstract where the probe returns
    HTTP 200 (Springer-style paywall skeleton that the old code wrongly cleared
    to quality=ok) MUST use the abstract text as the NLM source, NOT upload the
    paywalled URL. NLM cannot bypass the paywall, so the text fallback is the
    only path that actually gets content indexed.
    """
    from research_hub.clusters import Cluster
    from research_hub.notebooklm.bundle import bundle_cluster
    from research_hub.notebooklm.url_quality import UrlQuality

    cfg = _StubCfg(tmp_path)
    cfg.raw.mkdir(parents=True)
    cfg.research_hub_dir.mkdir(parents=True)
    (cfg.root / "pdfs").mkdir()

    note_path = cfg.raw / "alpha" / "springer_paywall.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "\n".join([
            "---",
            'title: "Springer Paywall Paper"',
            'doi: "10.1007/s12345-026-00001-1"',
            'url: ""',
            'topic_cluster: "alpha"',
            'summarize_status: "failed_no_abstract"',
            "---",
            "",
            "# Springer Paywall Paper",
            "",
            "## Abstract",
            "",
            "This study examines flood risk adaptation using agent-based modelling "
            "to simulate household decision-making under uncertainty. We find that "
            "social networks and economic constraints jointly shape adaptive capacity.",
        ]),
        encoding="utf-8",
    )

    cluster = Cluster(slug="alpha", name="Alpha Cluster", obsidian_subfolder="alpha")

    # Probe returns ok (Springer 200 skeleton — big body, no blocked status)
    probe_result = UrlQuality("ok", "probe_ok", "HTTP 200")
    with patch("research_hub.notebooklm.url_quality._probe_url", return_value=probe_result):
        report = bundle_cluster(cluster, cfg)

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.url_quality == "likely_error_page", (
        f"expected likely_error_page, got {entry.url_quality!r} "
        f"(reason={entry.url_quality_reason!r})"
    )
    assert entry.url_quality_reason == "probe_cleared_failed_no_abstract"
    assert entry.action == "text", (
        f"expected action=text (abstract fallback), got {entry.action!r}"
    )
    assert "flood risk" in entry.text


# ---------------------------------------------------------------------------
# 3. Upload integration tests — mock NLM client
# ---------------------------------------------------------------------------


def _write_bundle(research_hub_dir: Path, cluster_slug: str, entries: list[dict]) -> None:
    bundle_dir = research_hub_dir / "bundles" / f"{cluster_slug}-20260512T000000Z"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps({"entries": entries}), encoding="utf-8"
    )


def _upload_cfg(tmp_path: Path) -> SimpleNamespace:
    hub = tmp_path / ".research_hub"
    hub.mkdir()
    from research_hub.clusters import Cluster, ClusterRegistry

    cfg = SimpleNamespace(research_hub_dir=hub, clusters_file=hub / "clusters.yaml")
    return cfg


def _mock_nlm_client(monkeypatch):
    """Patch NotebookLMClient.from_storage with a fake that records uploads."""
    import notebooklm

    uploads = []

    class _FakeSources:
        async def add_file(self, notebook_id: str, file_path: str):
            uploads.append(("file", notebook_id, file_path))
            return SimpleNamespace(title=Path(file_path).name)

        async def add_url(self, notebook_id: str, url: str):
            uploads.append(("url", notebook_id, url))
            return SimpleNamespace(title=url)

    class _FakeNotebooks:
        async def list(self):
            return []

        async def create(self, title: str):
            return SimpleNamespace(
                id="nb-1",
                title=title,
                url="https://notebooklm.google.com/notebook/nb-1",
            )

    class _FakeClient:
        def __init__(self):
            self.notebooks = _FakeNotebooks()
            self.sources = _FakeSources()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _from_storage(**kwargs):
        return _FakeClient()

    monkeypatch.setattr(notebooklm.NotebookLMClient, "from_storage", staticmethod(_from_storage))
    return uploads


def test_upload_skips_likely_error_page_no_pdf(monkeypatch, tmp_path):
    """likely_error_page + action=url + no PDF → skipped + report.errors has pre_upload_likely_error_page."""
    from research_hub.clusters import Cluster, ClusterRegistry
    from research_hub.notebooklm.upload import upload_cluster

    uploads = _mock_nlm_client(monkeypatch)
    monkeypatch.setattr("research_hub.notebooklm.upload.time.sleep", lambda _: None)

    cfg = _upload_cfg(tmp_path)
    cluster = Cluster(slug="alpha", name="Alpha")
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters[cluster.slug] = cluster
    registry.save()

    _write_bundle(
        cfg.research_hub_dir,
        "alpha",
        [
            {
                "action": "url",
                "url": "https://doi.org/10.1080/bad",
                "doi": "10.1080/bad",
                "title": "Bad Paper",
                "url_quality": "likely_error_page",
                "url_quality_reason": "failed_no_abstract",
                "url_quality_signal": "metadata tier",
            }
        ],
    )

    with patch("research_hub.notebooklm.upload.validate_uploaded_sources") as mock_validate:
        mock_validate.return_value = SimpleNamespace(
            suspicious=[], suspicious_count=0, warning_text=lambda: ""
        )
        report = upload_cluster(cluster, cfg)

    # No actual upload happened
    assert not any(u[0] == "url" for u in uploads)
    # Error was recorded
    assert len(report.errors) == 1
    err = report.errors[0]
    assert err["error"] == "pre_upload_likely_error_page"
    assert "10.1080/bad" in err["source"]


def test_upload_include_suspect_urls_uploads_anyway(monkeypatch, tmp_path):
    """--include-suspect-urls → suspect URL is uploaded + warning in report.errors."""
    from research_hub.clusters import Cluster, ClusterRegistry
    from research_hub.notebooklm.upload import upload_cluster

    uploads = _mock_nlm_client(monkeypatch)
    monkeypatch.setattr("research_hub.notebooklm.upload.time.sleep", lambda _: None)

    cfg = _upload_cfg(tmp_path)
    cluster = Cluster(slug="alpha", name="Alpha")
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters[cluster.slug] = cluster
    registry.save()

    _write_bundle(
        cfg.research_hub_dir,
        "alpha",
        [
            {
                "action": "url",
                "url": "https://doi.org/10.1080/suspect",
                "doi": "10.1080/suspect",
                "title": "Suspect Paper",
                "url_quality": "likely_error_page",
                "url_quality_reason": "failed_no_abstract",
                "url_quality_signal": "metadata tier",
            }
        ],
    )

    with patch("research_hub.notebooklm.upload.validate_uploaded_sources") as mock_validate:
        mock_validate.return_value = SimpleNamespace(
            suspicious=[], suspicious_count=0, warning_text=lambda: ""
        )
        report = upload_cluster(cluster, cfg, include_suspect_urls=True)

    # URL was actually uploaded
    assert any(u[0] == "url" for u in uploads)
    # Warning still recorded
    assert len(report.errors) == 1
    assert "warning" in report.errors[0]["error"]


def test_upload_ok_and_unknown_upload_normally(monkeypatch, tmp_path):
    """ok and unknown url_quality entries are uploaded without being filtered."""
    from research_hub.clusters import Cluster, ClusterRegistry
    from research_hub.notebooklm.upload import upload_cluster

    uploads = _mock_nlm_client(monkeypatch)
    monkeypatch.setattr("research_hub.notebooklm.upload.time.sleep", lambda _: None)

    cfg = _upload_cfg(tmp_path)
    cluster = Cluster(slug="alpha", name="Alpha")
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters[cluster.slug] = cluster
    registry.save()

    _write_bundle(
        cfg.research_hub_dir,
        "alpha",
        [
            {
                "action": "url",
                "url": "https://arxiv.org/abs/2502.10978",
                "doi": "10.48550/arxiv.2502.10978",
                "title": "arXiv Paper",
                "url_quality": "ok",
                "url_quality_reason": "open_host",
                "url_quality_signal": "host arxiv.org in open allowlist",
            },
            {
                "action": "url",
                "url": "https://doi.org/10.9999/unknown",
                "doi": "10.9999/unknown",
                "title": "Unknown Quality Paper",
                "url_quality": "unknown",
                "url_quality_reason": "probe_exception",
                "url_quality_signal": "timed out",
            },
        ],
    )

    with patch("research_hub.notebooklm.upload.validate_uploaded_sources") as mock_validate:
        mock_validate.return_value = SimpleNamespace(
            suspicious=[], suspicious_count=0, warning_text=lambda: ""
        )
        report = upload_cluster(cluster, cfg)

    url_uploads = [u for u in uploads if u[0] == "url"]
    assert len(url_uploads) == 2
    # No pre-upload errors
    assert not any(
        err.get("error") == "pre_upload_likely_error_page" for err in report.errors
    )


# ---------------------------------------------------------------------------
# 4. CLI parser test
# ---------------------------------------------------------------------------


def test_cli_parser_include_suspect_urls(tmp_path):
    """--include-suspect-urls flag is recognized by the CLI parser."""
    from research_hub.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["notebooklm", "upload", "--cluster", "alpha", "--include-suspect-urls"]
    )
    assert args.include_suspect_urls is True


def test_cli_parser_include_suspect_urls_default_false(tmp_path):
    """--include-suspect-urls defaults to False."""
    from research_hub.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["notebooklm", "upload", "--cluster", "alpha"])
    assert args.include_suspect_urls is False
