"""v0.88.11 polish — three small in-scope fixes from the post-Stage-B audit.

1. **Brief mirror archive-header strip** — v0.88.3 added `## TL;DR` to the
   front of brief mirrors but left the legacy NotebookLM archive header
   block (cluster-name H1 + Source:/Downloaded:/Sources:/Saved briefings:
   metadata) in place. Result: every brief showed the same metadata
   above and below the cluster pointer, wasting iPhone screen real estate.
2. **NLM heartbeat refresh between shards** — v0.88.7 persisted rotated
   cookies on close(), but a 200+-source upload session holds one
   client open the entire time. Without a heartbeat between shards
   the second/third shard hits the auth wall mid-flight.
3. **`_find_pdf_for_doi` O(P²) → O(P)** — `bundle_cluster` paid one
   `rglob("*.pdf")` directory walk per paper. 49 papers × 49 walks at
   ~80 PDFs each is wasted CPU + I/O. Memoize the index once per bundle.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fix 1 — brief mirror archive-header strip
# ---------------------------------------------------------------------------


def test_strip_archive_header_drops_metadata_block() -> None:
    """The exact shape produced by notebooklm-py's downloader: cluster
    H1 + 4 metadata lines + blank + synthesis H1. Strip everything up
    to the synthesis H1."""
    from research_hub.notebooklm.download import _strip_archive_header

    body = (
        "# LLM for Human-Water Systems\n"
        "Source: https://notebooklm.google.com/notebook/abc\n"
        "Downloaded: 20260513T041410Z\n"
        "Sources: 12\n"
        "Saved briefings: Briefing Doc\n"
        "\n"
        "# Comparative Analysis of AI-Driven Frameworks\n"
        "\n"
        "### 1. Thematic Synthesis\n"
        "The current paradigm in flood forecasting is...\n"
    )
    out = _strip_archive_header(body)
    assert "Source: https://" not in out
    assert "Downloaded: 20260513" not in out
    assert "Sources: 12" not in out
    assert "Saved briefings:" not in out
    assert out.startswith("# Comparative Analysis")
    # Real synthesis content is preserved
    assert "current paradigm in flood forecasting" in out


def test_strip_archive_header_no_op_when_shape_unfamiliar() -> None:
    """If the body doesn't match the archive shape, return verbatim
    (defensive — never accidentally drop user content)."""
    from research_hub.notebooklm.download import _strip_archive_header

    cases = [
        # No leading H1
        "Just some prose without a heading.\n",
        # H1 but no metadata lines after — looks like real synthesis
        "# Synthesis title\n\nReal content here.\n",
        # H1 + metadata but never a second H1 — bail out conservatively
        "# Title\nSource: x\nDownloaded: y\n",
        # Empty input
        "",
    ]
    for body in cases:
        assert _strip_archive_header(body) == body


def test_strip_archive_header_tolerates_extra_metadata_keys() -> None:
    """Some downloads also include `Notebook:` / `Generated:` lines —
    the strip should skip them too."""
    from research_hub.notebooklm.download import _strip_archive_header

    body = (
        "# Cluster X\n"
        "Source: https://x\n"
        "Notebook: my-notebook\n"
        "Generated: 2026-05-14\n"
        "Sources: 8\n"
        "\n"
        "# Real Synthesis Title\n"
        "Body text.\n"
    )
    out = _strip_archive_header(body)
    assert "Source: https://x" not in out
    assert "Notebook: my-notebook" not in out
    assert "Generated:" not in out
    assert out.startswith("# Real Synthesis Title")


# ---------------------------------------------------------------------------
# Fix 2 — NLM heartbeat refresh between shards
# ---------------------------------------------------------------------------


def test_upload_cluster_shards_calls_refresh_between_shards(
    tmp_path: Path, monkeypatch
) -> None:
    """When more than one shard is uploaded, `client.refresh_and_save()`
    must be called between them so rotated Google cookies persist
    mid-session — protecting power users from auth expiry on long
    uploads."""
    # We don't run the real shard pipeline (too much wiring); instead
    # we directly verify the integration point in upload.py: the call
    # site after `shard_cache[shard_name] = sorted(uploaded_sources)`
    # invokes `refresh_and_save` if available.
    import research_hub.notebooklm.upload as upload_mod

    # Re-read source to check the new call site is present and ordered
    # correctly. Codifies the v0.88.11 wiring as a structural test —
    # cheap, no network, regression-proof against accidental removal.
    src = Path(upload_mod.__file__).read_text(encoding="utf-8")
    assert "shard_cache[shard_name]" in src
    assert "refresh_and_save" in src
    # And that the refresh is called after the shard cache assignment
    # but before the next shard iteration completes:
    cache_idx = src.find("shard_cache[shard_name] = sorted(uploaded_sources)")
    refresh_idx = src.find("refresh_and_save", cache_idx)
    assert refresh_idx > cache_idx, (
        "v0.88.11: refresh_and_save() must be called AFTER each shard's "
        "uploaded_sources is committed, so a refresh failure doesn't lose "
        "the upload-success bookkeeping."
    )


def test_refresh_and_save_failure_is_swallowed(tmp_path: Path) -> None:
    """The heartbeat is best-effort. If refresh_and_save raises, the
    upload that just succeeded must not be poisoned. Verify the
    try/except wrapper exists in source."""
    import research_hub.notebooklm.upload as upload_mod

    src = Path(upload_mod.__file__).read_text(encoding="utf-8")
    # Find the refresh block and check it's inside try/except
    refresh_block_start = src.find("refresh = getattr(client, \"refresh_and_save\"")
    assert refresh_block_start > 0, "v0.88.11 heartbeat block missing"
    nearby = src[refresh_block_start : refresh_block_start + 500]
    assert "try:" in nearby
    assert "except Exception:" in nearby


# ---------------------------------------------------------------------------
# Fix 3 — bundle.py PDF lookup memoize (O(P²) → O(P))
# ---------------------------------------------------------------------------


def test_find_pdf_for_doi_uses_provided_index(tmp_path: Path) -> None:
    """Caller-provided pdf_index must be used INSTEAD of rglob, so the
    cluster-wide bundle pays one filesystem walk, not 49."""
    from research_hub.notebooklm.bundle import _find_pdf_for_doi

    pdfs_dir = tmp_path / "pdfs"
    pdfs_dir.mkdir()
    # Put a "real" PDF on disk
    real_pdf = pdfs_dir / "10.1234_xyz.pdf"
    real_pdf.write_bytes(b"%PDF-1.4")

    # Caller-provided index: an empty list. If memoize is honored,
    # the empty index gives no DOI-tail match → returns None even
    # though the PDF physically exists. (Exact-name match still works.)
    result_with_empty = _find_pdf_for_doi(pdfs_dir, "10.1234/something-else", pdf_index=[])
    assert result_with_empty is None, "empty pdf_index must short-circuit the rglob"

    # Caller-provided index with the real PDF: tail-substring match works
    result_with_index = _find_pdf_for_doi(
        pdfs_dir, "10.1234/xyz", pdf_index=[real_pdf]
    )
    assert result_with_index == real_pdf

    # No pdf_index → falls back to rglob (default behaviour preserved)
    result_default = _find_pdf_for_doi(pdfs_dir, "10.1234/xyz")
    assert result_default == real_pdf


def test_find_pdf_by_author_year_uses_provided_index(tmp_path: Path) -> None:
    """Same memoize pattern for the author-year fallback."""
    from research_hub.notebooklm.bundle import _find_pdf_by_author_year

    pdfs_dir = tmp_path / "pdfs"
    pdfs_dir.mkdir()
    paper = pdfs_dir / "Smith_2025_paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    # Empty index short-circuits
    assert _find_pdf_by_author_year(
        pdfs_dir, "Smith, John", "2025", pdf_index=[]
    ) is None

    # Provided index works
    found = _find_pdf_by_author_year(
        pdfs_dir, "Smith, John", "2025", pdf_index=[paper]
    )
    assert found == paper


def test_bundle_cluster_builds_index_once(tmp_path: Path, monkeypatch) -> None:
    """Verify `bundle_cluster` calls `pdfs_dir.rglob("*.pdf")` AT MOST
    once per bundle, not once per paper. Drops cluster-wide cost from
    O(P²) to O(P).

    Implementation check: count `rglob` calls during bundle_cluster
    by patching Path.rglob with a counter."""
    from research_hub.notebooklm import bundle as bundle_mod
    from research_hub.notebooklm.bundle import bundle_cluster

    # Build a minimal vault layout
    vault = tmp_path / "vault"
    raw = vault / "raw" / "demo"
    raw.mkdir(parents=True)
    pdfs_dir = vault / "pdfs"
    pdfs_dir.mkdir()
    # 3 fake paper notes
    for i in range(3):
        (raw / f"paper-{i}.md").write_text(
            f"---\ntitle: Paper {i}\ndoi: 10.1000/{i}\nauthors: A\nyear: 2025\n---\n# x\n",
            encoding="utf-8",
        )
    # 2 fake PDFs in the index
    (pdfs_dir / "10.1000_0.pdf").write_bytes(b"%PDF")
    (pdfs_dir / "Smith_2025.pdf").write_bytes(b"%PDF")

    cfg = SimpleNamespace(
        root=vault,
        raw=vault / "raw",
        research_hub_dir=vault / ".research_hub",
    )
    cfg.research_hub_dir.mkdir(parents=True, exist_ok=True)
    cluster = SimpleNamespace(slug="demo", name="Demo")

    rglob_call_count = {"n": 0}
    original_rglob = Path.rglob

    def counting_rglob(self, pattern):
        if str(self).endswith("pdfs"):
            rglob_call_count["n"] += 1
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", counting_rglob)

    bundle_cluster(cluster, cfg)

    # v0.88.11: should be exactly 1 rglob call, not 3 (one per paper)
    # or 6 (one for DOI + one for author-year fallback per paper).
    assert rglob_call_count["n"] == 1, (
        f"bundle_cluster paid {rglob_call_count['n']} rglob calls for 3 papers — "
        f"v0.88.11 memoize regression. Expected exactly 1 (built once at top of loop)."
    )
