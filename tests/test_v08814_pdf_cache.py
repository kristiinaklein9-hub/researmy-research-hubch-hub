"""v0.88.14 — content-hash cache for ``_extract_pdf``.

Re-importing the same PDF (rename, retry, cluster-move) re-paid the
pdfplumber walk cost every time. W4 audit flagged this as the #1 PDF
win: ~100% on re-import, ~30% on partial cluster re-runs.

This module verifies the cache by stubbing pdfplumber with a counter
so we never need a real PDF parse.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdfDoc:
    def __init__(self, pages: list[str]) -> None:
        self.pages = [_FakePage(p) for p in pages]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_pdfplumber(monkeypatch):
    """Stub pdfplumber.open so tests can count invocations + force
    deterministic text without writing a real PDF."""

    call_count = {"open": 0}
    pages_per_path: dict[str, list[str]] = {}

    def fake_open(path_str: str):
        call_count["open"] += 1
        pages = pages_per_path.get(path_str, ["page-text"])
        return _FakePdfDoc(pages)

    module = SimpleNamespace(open=fake_open)
    monkeypatch.setitem(sys.modules, "pdfplumber", module)
    return SimpleNamespace(call_count=call_count, pages_per_path=pages_per_path)


@pytest.fixture(autouse=True)
def _reset_cache_dir(monkeypatch):
    """Each test starts with caching DISABLED so we control it
    explicitly. Avoids leaking state between cases."""
    from research_hub import importer

    monkeypatch.setattr(importer, "_PDF_EXTRACT_CACHE_DIR", None)
    yield


# ---------------------------------------------------------------------------
# Cache-disabled path: behaves identically to pre-v0.88.14
# ---------------------------------------------------------------------------


def test_extract_pdf_no_cache_when_dir_unset(tmp_path: Path, fake_pdfplumber) -> None:
    from research_hub.importer import _extract_pdf

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake-bytes-1")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["page 1 body", "page 2 body"]

    out = _extract_pdf(pdf)
    assert "page 1 body" in out
    assert "page 2 body" in out
    assert fake_pdfplumber.call_count["open"] == 1

    # Second call also re-opens (no cache configured)
    _extract_pdf(pdf)
    assert fake_pdfplumber.call_count["open"] == 2


# ---------------------------------------------------------------------------
# Cache-enabled path: re-import same content is a hit
# ---------------------------------------------------------------------------


def test_extract_pdf_caches_by_content_hash(tmp_path: Path, fake_pdfplumber, monkeypatch) -> None:
    from research_hub.importer import _extract_pdf, set_pdf_extract_cache_dir

    cache_dir = tmp_path / "cache"
    set_pdf_extract_cache_dir(cache_dir)

    pdf = tmp_path / "first.pdf"
    pdf.write_bytes(b"%PDF-1.4\ncontent-A")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["the actual text"]

    # First call: cache miss → pdfplumber.open invoked
    out1 = _extract_pdf(pdf)
    assert out1 == "the actual text"
    assert fake_pdfplumber.call_count["open"] == 1
    # Cache file written
    cache_files = list((cache_dir).rglob("*.txt"))
    assert len(cache_files) == 1
    assert cache_files[0].read_text(encoding="utf-8") == "the actual text"

    # Second call on the SAME path: cache hit → pdfplumber NOT invoked again
    out2 = _extract_pdf(pdf)
    assert out2 == "the actual text"
    assert fake_pdfplumber.call_count["open"] == 1, "second call must hit cache"


def test_extract_pdf_cache_hits_after_rename(tmp_path: Path, fake_pdfplumber) -> None:
    """Moved/renamed file with same content: cache hit (key is sha256
    of bytes, not path)."""
    from research_hub.importer import _extract_pdf, set_pdf_extract_cache_dir

    cache_dir = tmp_path / "cache"
    set_pdf_extract_cache_dir(cache_dir)

    original = tmp_path / "original.pdf"
    original.write_bytes(b"identical-content")
    fake_pdfplumber.pages_per_path[str(original)] = ["body"]

    _extract_pdf(original)
    assert fake_pdfplumber.call_count["open"] == 1

    # Rename it (same bytes)
    renamed = tmp_path / "renamed_after_move.pdf"
    original.rename(renamed)
    fake_pdfplumber.pages_per_path[str(renamed)] = ["body"]  # registered for the new path

    out = _extract_pdf(renamed)
    assert out == "body"
    assert fake_pdfplumber.call_count["open"] == 1, (
        "rename-only must hit cache via content hash"
    )


def test_extract_pdf_cache_miss_on_content_change(tmp_path: Path, fake_pdfplumber) -> None:
    """Different content → different hash → cache MISS (correctly re-extracts)."""
    from research_hub.importer import _extract_pdf, set_pdf_extract_cache_dir

    cache_dir = tmp_path / "cache"
    set_pdf_extract_cache_dir(cache_dir)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\nversion-1")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["v1 text"]

    _extract_pdf(pdf)
    assert fake_pdfplumber.call_count["open"] == 1

    # Same path, different content → cache miss
    pdf.write_bytes(b"%PDF-1.4\nversion-2-different")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["v2 text"]
    out = _extract_pdf(pdf)
    assert out == "v2 text"
    assert fake_pdfplumber.call_count["open"] == 2, "content change must invalidate cache"


def test_extract_pdf_cache_write_failure_is_best_effort(
    tmp_path: Path, fake_pdfplumber, monkeypatch
) -> None:
    """If the cache write fails (read-only dir, disk full), extraction
    still returns text — caching is best-effort, not a hard dependency."""
    from research_hub import importer

    cache_dir = tmp_path / "cache"
    importer.set_pdf_extract_cache_dir(cache_dir)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["the body"]

    # Force write_text to raise
    real_write_text = Path.write_text

    def boom_write_text(self, *args, **kwargs):
        if "cache" in str(self):
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom_write_text)

    # Must not raise; must return the extracted text
    out = importer._extract_pdf(pdf)
    assert out == "the body"
    assert fake_pdfplumber.call_count["open"] == 1


def test_extract_pdf_cache_read_failure_falls_back_to_fresh_extract(
    tmp_path: Path, fake_pdfplumber, monkeypatch
) -> None:
    """Corrupt cache file → fresh extraction (not crash)."""
    from research_hub.importer import _extract_pdf, set_pdf_extract_cache_dir
    import hashlib

    cache_dir = tmp_path / "cache"
    set_pdf_extract_cache_dir(cache_dir)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\ncontent-X")
    digest = hashlib.sha256(b"%PDF-1.4\ncontent-X").hexdigest()
    cache_path = cache_dir / f"{digest}.txt"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("cached!", encoding="utf-8")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["fresh"]

    # Force read_text to raise
    real_read_text = Path.read_text

    def boom_read_text(self, *args, **kwargs):
        if "cache" in str(self):
            raise OSError("permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom_read_text)

    out = _extract_pdf(pdf)
    # Falls back to fresh pdfplumber walk → returns the "fresh" text,
    # not the unreadable "cached!" value
    assert out == "fresh"
    assert fake_pdfplumber.call_count["open"] == 1


def test_set_pdf_extract_cache_dir_none_disables_cache(
    tmp_path: Path, fake_pdfplumber
) -> None:
    """Passing None re-disables caching after it was enabled."""
    from research_hub.importer import _extract_pdf, set_pdf_extract_cache_dir

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fake_pdfplumber.pages_per_path[str(pdf)] = ["body"]

    set_pdf_extract_cache_dir(tmp_path / "cache")
    _extract_pdf(pdf)  # populates cache
    assert fake_pdfplumber.call_count["open"] == 1

    # Disable cache → next call re-opens even though content hash matches
    set_pdf_extract_cache_dir(None)
    _extract_pdf(pdf)
    assert fake_pdfplumber.call_count["open"] == 2
