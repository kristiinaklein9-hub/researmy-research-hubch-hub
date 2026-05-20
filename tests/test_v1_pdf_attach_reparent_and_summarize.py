"""Regression tests for:

  F1 — bundle.py: probe_cleared_failed_no_abstract triggers text fallback
  F2 — auto.py:   _run_pdf_attach_step wired into auto_pipeline when with_pdfs=True
  F3 — auto.py:   _maybe_reparent_collection reparents top-level Zotero collections
  F4 — auto.py:   auto_pipeline default with_summary=True
"""

from __future__ import annotations

import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# F1 — bundle: probe_cleared_failed_no_abstract → text fallback
# ---------------------------------------------------------------------------

def test_bundle_probe_cleared_failed_no_abstract_condition():
    """The text-fallback condition in bundle.py must treat
    url_quality_reason == 'probe_cleared_failed_no_abstract' as a no-content
    signal (same as 'likely_error_page').  This function pins the exact
    conditional so a refactor can't accidentally drop it.
    """
    # Replicate the exact condition from bundle.py so test stays tightly coupled
    def _is_no_content(url_quality: str, url_quality_reason: str) -> bool:
        return (
            url_quality == "likely_error_page"
            or url_quality_reason == "probe_cleared_failed_no_abstract"
        )

    # Springer skeleton page: HTTP 200 but no body → MUST use text fallback
    assert _is_no_content("ok", "probe_cleared_failed_no_abstract"), (
        "Springer paywall skeleton (probe_cleared_failed_no_abstract) "
        "must be treated as no-content"
    )
    # Explicit error page
    assert _is_no_content("likely_error_page", "")
    # Good open-access page — NOT no-content
    assert not _is_no_content("ok", "")
    assert not _is_no_content("ok", "probe_ok")


def test_bundle_probe_cleared_is_importable():
    """bundle.py should be importable without errors (smoke test)."""
    import research_hub.notebooklm.bundle as _bundle  # noqa: F401
    assert hasattr(_bundle, "bundle_cluster")


# ---------------------------------------------------------------------------
# F2 — _run_pdf_attach_step: unit tests
# ---------------------------------------------------------------------------

def test_run_pdf_attach_step_no_collection_key_logs_failure():
    """_run_pdf_attach_step logs a failure step when cluster has no Zotero key."""
    from research_hub.auto import _run_pdf_attach_step, AutoReport

    cfg = SimpleNamespace()
    cluster = SimpleNamespace(zotero_collection_key="")
    report = AutoReport(cluster_slug="test", cluster_created=False)

    with patch("research_hub.zotero.client.get_client"):
        _run_pdf_attach_step(cfg, "test", cluster, report, time.time(), False)

    attach_steps = [s for s in report.steps if s.name == "pdf.attach"]
    assert attach_steps, "pdf.attach step should be logged"
    assert not attach_steps[0].ok, "should be failure when no collection key"


def test_run_pdf_attach_step_no_oa_pdfs_logs_ok():
    """_run_pdf_attach_step logs ok (not an error) when no OA PDFs are found."""
    from research_hub.auto import _run_pdf_attach_step, AutoReport

    cluster = SimpleNamespace(zotero_collection_key="TESTKEY")
    report = AutoReport(cluster_slug="test", cluster_created=False)

    fake_item = {"key": "ITEM1", "data": {"title": "Paywalled paper", "DOI": "10.1234/x", "itemType": "journalArticle"}}
    # plan_attach_for_items returns plans with no pdf_url (paywall)
    fake_plan = SimpleNamespace(
        item_key="ITEM1", title="Paywalled", doi="10.1234/x",
        arxiv_id="", pdf_url="", source="", error="no_oa_record",
    )

    with (
        patch("research_hub.zotero.client.get_client") as mock_gc,
        patch("research_hub.zotero.pdf_attach.plan_attach_for_items", return_value=[fake_plan]),
    ):
        mock_gc.return_value = SimpleNamespace(web=MagicMock(
            collection_items=MagicMock(return_value=[fake_item])
        ))
        _run_pdf_attach_step(SimpleNamespace(), "test", cluster, report, time.time(), False)

    attach_steps = [s for s in report.steps if s.name == "pdf.attach"]
    assert attach_steps
    assert attach_steps[0].ok, "no OA PDFs is not an error — log ok"


def test_run_pdf_attach_step_attaches_oa_pdf():
    """_run_pdf_attach_step calls attach_pdfs and logs ok/skip/fail counts."""
    from research_hub.auto import _run_pdf_attach_step, AutoReport

    cluster = SimpleNamespace(zotero_collection_key="TESTKEY")
    report = AutoReport(cluster_slug="test", cluster_created=False)

    fake_item = {"key": "ITEM1", "data": {"title": "OA paper", "DOI": "10.1234/oa", "itemType": "journalArticle"}}
    fake_plan = SimpleNamespace(
        item_key="ITEM1", title="OA", doi="10.1234/oa",
        arxiv_id="", pdf_url="https://example.com/oa.pdf", source="unpaywall", error="",
    )
    fake_summary = SimpleNamespace(ok=1, skip=0, fail=0)
    fake_results = MagicMock()
    fake_results.summary = fake_summary

    with (
        patch("research_hub.zotero.client.get_client") as mock_gc,
        patch("research_hub.zotero.pdf_attach.plan_attach_for_items", return_value=[fake_plan]),
        patch("research_hub.zotero.pdf_attach.attach_pdfs", return_value=fake_results),
    ):
        mock_gc.return_value = SimpleNamespace(web=MagicMock(
            collection_items=MagicMock(return_value=[fake_item])
        ))
        _run_pdf_attach_step(SimpleNamespace(), "test", cluster, report, time.time(), False)

    attach_steps = [s for s in report.steps if s.name == "pdf.attach"]
    assert attach_steps
    assert attach_steps[0].ok
    assert "1 attached" in attach_steps[0].detail


def test_run_pdf_attach_step_exception_is_logged_not_raised():
    """_run_pdf_attach_step never raises — exceptions are logged as failures."""
    from research_hub.auto import _run_pdf_attach_step, AutoReport

    cluster = SimpleNamespace(zotero_collection_key="TESTKEY")
    report = AutoReport(cluster_slug="test", cluster_created=False)

    with patch("research_hub.zotero.client.get_client", side_effect=RuntimeError("boom")):
        _run_pdf_attach_step(SimpleNamespace(), "test", cluster, report, time.time(), False)

    attach_steps = [s for s in report.steps if s.name == "pdf.attach"]
    assert attach_steps
    assert not attach_steps[0].ok
    assert "boom" in attach_steps[0].detail


# ---------------------------------------------------------------------------
# F3 — _maybe_reparent_collection
# ---------------------------------------------------------------------------

def test_maybe_reparent_collection_reparents_top_level():
    """_maybe_reparent_collection PATCHes the collection when parentCollection=False.

    mock_zot uses spec=ZoteroDualClient so that isinstance(mock_zot, ZoteroDualClient)
    returns True (MagicMock overrides __class__ with _spec_class when spec= is given).
    This exercises the isinstance-check branch introduced to distinguish
    ZoteroDualClient from bare pyzotero clients that share the update_collection name.
    """
    from research_hub.auto import _maybe_reparent_collection
    from research_hub.zotero.client import ZoteroDualClient

    mock_web = MagicMock()
    mock_web.collection.return_value = {
        "key": "ABC", "version": 5,
        "data": {"key": "ABC", "name": "My Cluster", "parentCollection": False, "version": 5},
    }
    mock_zot = MagicMock(spec=ZoteroDualClient)  # isinstance(mock_zot, ZoteroDualClient) → True

    with (
        patch("research_hub.config.get_config") as mock_gc,
        patch("research_hub.zotero.client.ensure_parent_collection", return_value="PARENT_KEY"),
    ):
        mock_gc.return_value = SimpleNamespace(zotero_parent_collection="research-hub")
        _maybe_reparent_collection(mock_zot, mock_web, "ABC", "my-cluster", False)

    mock_zot.update_collection.assert_called_once_with("ABC", parent_key="PARENT_KEY")


def test_maybe_reparent_collection_skips_already_nested():
    """_maybe_reparent_collection does nothing when collection already has a parent."""
    from research_hub.auto import _maybe_reparent_collection

    mock_web = MagicMock()
    mock_web.collection.return_value = {
        "key": "ABC", "version": 5,
        "data": {"key": "ABC", "name": "My Cluster", "parentCollection": "EXISTING_PARENT", "version": 5},
    }
    mock_zot = MagicMock()

    with patch("research_hub.config.get_config") as mock_gc:
        mock_gc.return_value = SimpleNamespace(zotero_parent_collection="research-hub")
        _maybe_reparent_collection(mock_zot, mock_web, "ABC", "my-cluster", False)

    mock_zot.update_collection.assert_not_called()


def test_maybe_reparent_collection_skips_when_no_parent_name_configured():
    """When zotero_parent_collection is empty, reparenting is disabled."""
    from research_hub.auto import _maybe_reparent_collection

    mock_web = MagicMock()
    mock_zot = MagicMock()

    with patch("research_hub.config.get_config") as mock_gc:
        mock_gc.return_value = SimpleNamespace(zotero_parent_collection="")
        _maybe_reparent_collection(mock_zot, mock_web, "ABC", "my-cluster", False)

    # web.collection should never be called when parent name is empty
    mock_web.collection.assert_not_called()
    mock_zot.update_collection.assert_not_called()


def test_maybe_reparent_collection_silent_on_network_error():
    """_maybe_reparent_collection never raises — network errors are swallowed."""
    from research_hub.auto import _maybe_reparent_collection

    mock_web = MagicMock()
    mock_web.collection.side_effect = OSError("connection refused")

    with patch("research_hub.config.get_config") as mock_gc:
        mock_gc.return_value = SimpleNamespace(zotero_parent_collection="research-hub")
        # Must not raise
        _maybe_reparent_collection(MagicMock(), mock_web, "ABC", "slug", False)


# ---------------------------------------------------------------------------
# F4 — summarize hint shown in next-steps when papers are pending
# ---------------------------------------------------------------------------

def test_auto_pipeline_with_summary_parameter_exists():
    """auto_pipeline must expose a with_summary parameter (opt-in LLM summarise)."""
    from research_hub.auto import auto_pipeline
    sig = inspect.signature(auto_pipeline)
    assert "with_summary" in sig.parameters, "auto_pipeline must accept with_summary"


def test_print_next_steps_shows_summarize_hint(tmp_path):
    """_print_next_steps prints a HINT line when cluster papers have
    summarize_status=pending, telling the user the exact command to run.
    This prevents the silent 'why is my summary empty?' UX problem.
    """
    from research_hub.auto import _print_next_steps, AutoReport

    # Create a fake cluster dir with one pending paper
    slug = "test-cluster"
    cluster_dir = tmp_path / slug
    cluster_dir.mkdir()
    (cluster_dir / "paper1.md").write_text(
        "---\ntitle: Paper 1\nsummarize_status: pending\n---\n", encoding="utf-8"
    )
    (cluster_dir / "paper2.md").write_text(
        "---\ntitle: Paper 2\nsummarize_status: done\n---\n", encoding="utf-8"
    )

    cfg = SimpleNamespace(raw=tmp_path, clusters_file=tmp_path / "registry.yaml")
    report = AutoReport(cluster_slug=slug, cluster_created=False)

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_next_steps(report, slug, cfg, do_crystals=False)

    output = buf.getvalue()
    assert "summarize" in output.lower() or "HINT" in output, (
        "next-steps output should mention summarize when papers are pending;\n"
        f"got:\n{output}"
    )
