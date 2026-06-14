"""v1.1 P2-3 — PRISMA screening provenance log + relevance_unverified persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.screening import (
    STAGE_INCLUDED,
    STAGE_SCREENED_OUT,
    prisma_counts,
    read_screening_log,
    record_screening,
    render_prisma,
)


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    hub = root / "hub"
    rh = root / ".research_hub"
    for p in (raw, hub, rh):
        p.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        root=root, raw=raw, hub=hub, research_hub_dir=rh, clusters_file=rh / "clusters.yaml"
    )


# --------------------------------------------------------------------------- #
# log writer/reader
# --------------------------------------------------------------------------- #
def test_record_and_read_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    record_screening(cfg, stage=STAGE_INCLUDED, cluster="c1", doi="10.1/a", title="A", ts="T1")
    record_screening(cfg, stage=STAGE_SCREENED_OUT, cluster="c1", doi="10.1/b", reason="low_relevance", ts="T2")
    record_screening(cfg, stage=STAGE_INCLUDED, cluster="c2", doi="10.1/c", ts="T3")

    all_recs = read_screening_log(cfg)
    assert len(all_recs) == 3
    c1 = read_screening_log(cfg, cluster="c1")
    assert [r["doi"] for r in c1] == ["10.1/a", "10.1/b"]
    assert c1[0]["ts"] == "T1"

    # File is genuinely append-only JSONL.
    raw = (cfg.research_hub_dir / "screening_log.jsonl").read_text(encoding="utf-8")
    assert raw.count("\n") == 3


def test_read_skips_malformed_lines(tmp_path):
    cfg = _cfg(tmp_path)
    log = cfg.research_hub_dir / "screening_log.jsonl"
    log.write_text(
        json.dumps({"stage": "included", "cluster": "c1"}) + "\n"
        + "{ this is not json\n"
        + json.dumps({"stage": "screened_out", "cluster": "c1", "reason": "x"}) + "\n",
        encoding="utf-8",
    )
    recs = read_screening_log(cfg, cluster="c1")
    assert len(recs) == 2  # torn middle line skipped, not fatal


def test_record_is_best_effort_on_write_failure(tmp_path):
    # research_hub_dir points at a *file*, so mkdir/open raise — must not propagate.
    bad = tmp_path / "afile"
    bad.write_text("x", encoding="utf-8")
    cfg = SimpleNamespace(research_hub_dir=bad / "nested")
    rec = record_screening(cfg, stage=STAGE_INCLUDED, cluster="c1")  # no raise
    assert rec["stage"] == STAGE_INCLUDED


# --------------------------------------------------------------------------- #
# prisma counts + render
# --------------------------------------------------------------------------- #
def test_prisma_counts_funnel():
    records = [
        {"stage": "included", "unverified": False},
        {"stage": "included", "unverified": True},
        {"stage": "included", "unverified": True},
        {"stage": "screened_out", "reason": "low_relevance"},
        {"stage": "screened_out", "reason": "low_relevance"},
        {"stage": "screened_out", "reason": "duplicate"},
    ]
    counts = prisma_counts(records)
    assert counts["included"] == 3
    assert counts["unverified"] == 2
    assert counts["screened_out"] == 3
    assert counts["screened"] == 6  # included + screened_out
    assert counts["excluded_by_reason"] == {"low_relevance": 2, "duplicate": 1}


def test_render_prisma_populated_and_empty(tmp_path):
    cfg = _cfg(tmp_path)
    empty = render_prisma(cfg, "c1")
    assert "no screening records yet" in empty

    record_screening(cfg, stage=STAGE_INCLUDED, cluster="c1", unverified=True, ts="T1")
    record_screening(cfg, stage=STAGE_SCREENED_OUT, cluster="c1", reason="low_relevance", ts="T2")
    out = render_prisma(cfg, "c1")
    assert "Included:" in out
    assert "unverified:" in out
    assert "low_relevance" in out


# --------------------------------------------------------------------------- #
# relevance_unverified frontmatter persistence
# --------------------------------------------------------------------------- #
def _item():
    return {
        "title": "Test paper",
        "authors": ["Doe, J."],
        "year": 2024,
        "journal": "J",
        "doi": "10.1/x",
        "abstract": "Long enough abstract content " * 8,
        "tags": [],
        "key": "K",
    }


def test_make_raw_md_persists_relevance_unverified():
    from research_hub.zotero.fetch import make_raw_md

    rendered = make_raw_md(
        _item(), [], [], topic_cluster="c",
        provenance={"fit_score": 1.2, "relevance_unverified": True},
    )
    assert "relevance_unverified: true" in rendered


def test_make_raw_md_omits_flag_when_screened():
    from research_hub.zotero.fetch import make_raw_md

    rendered = make_raw_md(
        _item(), [], [], topic_cluster="c",
        provenance={"fit_score": 7.0},  # screened/verified → no flag
    )
    assert "relevance_unverified" not in rendered


# --------------------------------------------------------------------------- #
# auto.py gate emission (integration)
# --------------------------------------------------------------------------- #
def test_fit_check_step_emits_included_and_flags_unverified(tmp_path, capsys):
    from research_hub.auto import AutoReport, _run_fit_check_step

    cfg = _cfg(tmp_path)
    # 2 papers < _MIN_BATCH_FOR_GATE(5) → cold-start: all kept, all unverified.
    papers = [
        {"title": "P1", "abstract": "about agents", "doi": "10.1/p1"},
        {"title": "P2", "abstract": "about water", "doi": "10.1/p2"},
    ]
    report = AutoReport(cluster_slug="c1", cluster_created=False)
    kept = _run_fit_check_step(
        cfg, papers, topic="agents and water", slug="c1",
        llm_cli=None, threshold=3, report=report, started=0.0,
        print_progress=True, no_llm_fit_check=True,
    )

    assert len(kept) == 2
    assert all(p["provenance"]["relevance_unverified"] is True for p in kept)
    # The cold-start nudge fired.
    assert "ingested unverified" in capsys.readouterr().out

    counts = prisma_counts(read_screening_log(cfg, cluster="c1"))
    assert counts["included"] == 2
    assert counts["unverified"] == 2
    assert counts["screened_out"] == 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_clusters_prisma(tmp_path, monkeypatch, capsys):
    from research_hub import cli
    from research_hub.clusters import Cluster, ClusterRegistry

    cfg = _cfg(tmp_path)
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters["c1"] = Cluster(slug="c1", name="C1")
    registry.save()
    record_screening(cfg, stage=STAGE_INCLUDED, cluster="c1", unverified=True, ts="T1")
    record_screening(cfg, stage=STAGE_SCREENED_OUT, cluster="c1", reason="low_relevance", ts="T2")

    rc = cli.main(["clusters", "prisma", "c1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PRISMA screening" in out
    assert "low_relevance" in out

    rc2 = cli.main(["clusters", "prisma", "c1", "--json"])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    payload = json.loads(out2)
    assert payload["report"]["counts"]["included"] == 1
    assert payload["report"]["counts"]["unverified"] == 1


def test_mcp_cluster_prisma_tool(tmp_path, monkeypatch):
    import research_hub.mcp_server as m

    cfg = _cfg(tmp_path)
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)
    record_screening(cfg, stage=STAGE_INCLUDED, cluster="c1", unverified=True, ts="T1")
    record_screening(cfg, stage=STAGE_SCREENED_OUT, cluster="c1", reason="low_relevance", ts="T2")

    fn = getattr(m.cluster_prisma, "fn", m.cluster_prisma)  # unwrap FastMCP tool
    result = fn("c1")
    assert result["cluster"] == "c1"
    assert result["counts"]["included"] == 1
    assert result["counts"]["unverified"] == 1
    assert result["counts"]["excluded_by_reason"] == {"low_relevance": 1}
