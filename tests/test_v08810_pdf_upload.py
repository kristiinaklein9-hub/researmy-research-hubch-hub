"""v0.88.10 — fix NotebookLM PDF upload SDK kwarg + non-retryable classification.

Stage B reported "8 succeeded out of 14" but the most-recent NLM debug
log showed 0/5 PDF uploads succeeded — every attempt failed with
``SourcesAPI.add_file() got an unexpected keyword argument 'path'``.
notebooklm-py 0.4.x renamed the kwarg from ``path=`` to ``file_path=``.

These tests lock in:
1. `client.upload_source(file_path=...)` calls the SDK with `file_path=`
2. `_attempt_upload` short-circuits on `unexpected keyword argument`
   instead of burning 12 s on three pointless retries.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fix #1 — upload_source uses file_path= not path=
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_upstream(monkeypatch):
    """Stub out notebooklm-py's NotebookLMClient.from_storage so we never
    touch the network. Returns a recording fake whose .sources surface
    captures call kwargs."""

    class _RecordingSources:
        def __init__(self):
            self.add_file_calls: list[dict] = []
            self.add_url_calls: list[dict] = []

        async def add_file(self, notebook_id, **kwargs):
            self.add_file_calls.append({"notebook_id": notebook_id, **kwargs})
            return SimpleNamespace(title="recorded-file.pdf")

        async def add_url(self, notebook_id, **kwargs):
            self.add_url_calls.append({"notebook_id": notebook_id, **kwargs})
            return SimpleNamespace(title="recorded-url")

    class _FakeUpstream:
        def __init__(self, *, storage_path):
            self.auth = SimpleNamespace(
                cookies={}, cookie_jar=object(),
                csrf_token="csrf", session_id="sid",
                storage_path=storage_path,
            )
            self.sources = _RecordingSources()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def refresh_auth(self):
            return self.auth

    async def fake_from_storage(*, path, timeout=None, **kwargs):
        return _FakeUpstream(storage_path=str(path))

    monkeypatch.setattr(
        "notebooklm.NotebookLMClient.from_storage",
        staticmethod(fake_from_storage),
    )

    # Suppress save_cookies_to_storage during teardown
    import notebooklm.auth as upstream_auth
    monkeypatch.setattr(upstream_auth, "save_cookies_to_storage", lambda *a, **k: None)


def test_upload_source_passes_file_path_kwarg(tmp_path, fake_upstream):
    """v0.88.10 regression — must call SDK with `file_path=`, not `path=`."""
    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")

    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    try:
        result = client.upload_source("nb-id", file_path=pdf)
    finally:
        client.close()

    upstream_sources = client._client.sources
    assert len(upstream_sources.add_file_calls) == 1
    call = upstream_sources.add_file_calls[0]
    assert call["notebook_id"] == "nb-id"
    assert "file_path" in call, "kwarg must be file_path= per notebooklm-py 0.4.x signature"
    assert call["file_path"] == str(pdf)
    assert "path" not in call, "the legacy `path=` kwarg must NOT be passed"
    assert result.success is True
    assert result.source_kind == "pdf"


def test_upload_source_url_path_unchanged(tmp_path, fake_upstream):
    """URL upload path is unaffected — sanity guard only."""
    from research_hub.notebooklm.client import NotebookLMClient

    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    client = NotebookLMClient(state, headless=True, timeout_sec=10)
    try:
        result = client.upload_source("nb-id", url="https://example.org/paper.pdf")
    finally:
        client.close()

    sources = client._client.sources
    assert len(sources.add_url_calls) == 1
    assert sources.add_url_calls[0]["url"] == "https://example.org/paper.pdf"
    assert result.success is True


# ---------------------------------------------------------------------------
# Fix #2 — _attempt_upload short-circuits on non-retryable errors
# ---------------------------------------------------------------------------


def test_attempt_upload_skips_retry_on_typeerror_kwarg(tmp_path, monkeypatch):
    """The exact Stage B error: `unexpected keyword argument 'path'`.
    Must short-circuit after attempt 1 — NOT burn 3 retries × backoff."""
    from research_hub.notebooklm import upload as upload_mod

    log_path = tmp_path / "upload.log.jsonl"
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    # Fake client always returns the SDK kwarg-mismatch error.
    upload_attempts = {"count": 0}

    class _FakeClient:
        def upload_pdf(self, path):
            upload_attempts["count"] += 1
            from research_hub.notebooklm.client import UploadResult
            return UploadResult(
                source_kind="pdf",
                path_or_url=str(path),
                success=False,
                error="SourcesAPI.add_file() got an unexpected keyword argument 'path'",
            )

    # Cap sleep so the test stays fast even on a regression
    sleeps: list[float] = []
    monkeypatch.setattr(upload_mod.time, "sleep", lambda s: sleeps.append(s))

    entry = {"action": "pdf", "pdf_path": str(pdf), "doi": "10.1/x"}
    result = upload_mod._attempt_upload(_FakeClient(), entry, log_path, max_attempts=3)

    assert upload_attempts["count"] == 1, "non-retryable error must short-circuit after attempt 1"
    assert sleeps == [], "no backoff sleep should have fired on non-retryable"
    assert result is not None
    assert result.success is False
    assert "unexpected keyword argument" in result.error.lower()
    # Verify the upload_non_retryable line was logged for forensics
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert any("upload_non_retryable" in ln for ln in log_lines)


def test_attempt_upload_still_retries_on_transient_errors(tmp_path, monkeypatch):
    """Conservative guard: transient errors (5xx, network) still get the
    full retry budget. Don't regress to skipping retries on real flakes."""
    from research_hub.notebooklm import upload as upload_mod
    from research_hub.notebooklm.client import UploadResult

    log_path = tmp_path / "upload.log.jsonl"
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    attempts = {"count": 0}

    class _FakeFlakyClient:
        def upload_pdf(self, path):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return UploadResult(
                    source_kind="pdf", path_or_url=str(path),
                    success=False,
                    error="HTTP 503 Service Unavailable (transient backend hiccup)",
                )
            return UploadResult(
                source_kind="pdf", path_or_url=str(path),
                success=True, title="y.pdf",
            )

    monkeypatch.setattr(upload_mod.time, "sleep", lambda s: None)
    entry = {"action": "pdf", "pdf_path": str(pdf), "doi": "10.1/y"}
    result = upload_mod._attempt_upload(_FakeFlakyClient(), entry, log_path, max_attempts=3)

    assert attempts["count"] == 3, "transient errors must still get full retry budget"
    assert result is not None
    assert result.success is True


@pytest.mark.parametrize(
    "error_text,expected",
    [
        # SDK contract drift — non-retryable
        ("SourcesAPI.add_file() got an unexpected keyword argument 'path'", True),
        ("got multiple values for keyword argument 'notebook_id'", True),
        ("missing 1 required positional argument: 'file_path'", True),
        # Validation — non-retryable
        ("'application/banana' is not a valid mime type", True),
        ("invalid mime type for source", True),
        # Auth — non-retryable
        ("HTTP 401 Unauthorized", True),
        ("403 Forbidden: insufficient scope", True),
        ("404 Not Found: notebook does not exist", True),
        # Transient — retryable
        ("HTTP 503 Service Unavailable", False),
        ("connection timeout after 30s", False),
        ("rate limit exceeded; retry-after 60s", False),
        # Empty / None
        ("", False),
    ],
)
def test_is_non_retryable_classification(error_text: str, expected: bool) -> None:
    """The non-retryable classifier must be conservative — only matches
    a small known set so transient errors still get retried."""
    from research_hub.notebooklm.upload import _is_non_retryable

    assert _is_non_retryable(error_text) is expected, (
        f"Misclassified: {error_text!r} expected {expected}"
    )
