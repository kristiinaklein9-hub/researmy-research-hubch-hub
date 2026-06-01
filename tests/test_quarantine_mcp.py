"""FUNC-1: quarantine must be reachable from the MCP surface.

The fit-check gate quarantines rejected candidates, and when an auto run ends
with 0 ingested it tells the caller to "run quarantine list". Previously that
existed ONLY as a CLI command, so an MCP agent -- the stated primary audience --
was handed a dead-end hint for a tool it did not have. These tests lock the
new MCP tools to the existing authenticity backend.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from research_hub import mcp_server
from research_hub.authenticity import QUARANTINE_DIR


def _cfg_with_quarantine(tmp_path):
    rh = tmp_path / ".research_hub"
    qdir = rh / QUARANTINE_DIR / "agents"
    qdir.mkdir(parents=True)
    (qdir / "p1.json").write_text(
        json.dumps(
            {
                "cluster": "agents",
                "slug": "p1",
                "layer": "fit_check",
                "reason": "off-topic (score 2)",
                "date": "2026-05-30",
                "raw_candidate": {"title": "Paper 1", "slug": "p1", "doi": "10.1/p1"},
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(research_hub_dir=rh, root=tmp_path), qdir


def test_mcp_list_quarantine_surfaces_rejected_candidates(tmp_path, monkeypatch):
    cfg, _qdir = _cfg_with_quarantine(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    result = mcp_server.list_quarantine(cluster_slug="agents")

    assert result["ok"] is True
    assert result["count"] == 1
    row = result["quarantined"][0]
    assert row["slug"] == "p1"
    assert row["cluster"] == "agents"
    assert "off-topic" in row["reason"]


def test_mcp_show_quarantine_returns_full_record(tmp_path, monkeypatch):
    cfg, _qdir = _cfg_with_quarantine(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    result = mcp_server.show_quarantine(slug="p1", cluster_slug="agents")

    assert result["ok"] is True
    assert result["slug"] == "p1"
    assert result["raw_candidate"]["title"] == "Paper 1"


def test_mcp_show_quarantine_unknown_slug_is_not_a_vault_error(tmp_path, monkeypatch):
    # an unknown slug must not be mislabeled "vault not initialized" (the backend
    # raises FileNotFoundError for both cases; the tool must disambiguate).
    cfg, _qdir = _cfg_with_quarantine(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    result = mcp_server.show_quarantine(slug="does-not-exist", cluster_slug="agents")

    assert result["ok"] is False
    assert "vault not initialized" not in result["error"]
    assert "available slugs" in result.get("hint", "")


def test_mcp_quarantine_tools_are_registered_and_mapped():
    # the three tools exist on the module (registered via mcp.tool() at import)
    for name in ("list_quarantine", "show_quarantine", "restore_quarantine"):
        assert callable(getattr(mcp_server, name))


def test_mcp_restore_quarantine_removes_record_and_requeues_candidate(tmp_path, monkeypatch):
    # Functional: restoring a false-positive must (1) remove the quarantine
    # record from disk and (2) re-add the raw candidate to papers_input.json so
    # the next ingest picks it up. (Previously this tool had only a
    # registration assertion -- no behavioural coverage.)
    cfg, qdir = _cfg_with_quarantine(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    quarantine_file = qdir / "p1.json"
    assert quarantine_file.exists()  # precondition

    result = mcp_server.restore_quarantine(slug="p1", cluster_slug="agents")

    assert result["ok"] is True
    assert result["cluster"] == "agents"
    assert result["slug"] == "p1"
    # (1) the quarantine record is gone
    assert not quarantine_file.exists()
    assert mcp_server.list_quarantine(cluster_slug="agents")["count"] == 0
    # (2) the candidate is back in papers_input.json, tagged to the cluster
    papers_input = tmp_path / "papers_input.json"
    assert papers_input.exists()
    data = json.loads(papers_input.read_text(encoding="utf-8"))
    papers = data["papers"] if isinstance(data, dict) else data
    assert any(p.get("title") == "Paper 1" for p in papers)
    restored = next(p for p in papers if p.get("title") == "Paper 1")
    assert restored.get("sub_category") == "agents"


def test_mcp_restore_quarantine_unknown_slug_is_not_a_vault_error(tmp_path, monkeypatch):
    # an unknown slug must surface as a clean ok:false + hint, not be
    # mislabeled "vault not initialized" (same FileNotFoundError trap as show).
    cfg, _qdir = _cfg_with_quarantine(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    result = mcp_server.restore_quarantine(slug="does-not-exist", cluster_slug="agents")

    assert result["ok"] is False
    assert "vault not initialized" not in result["error"]
    assert "available slugs" in result.get("hint", "")
