"""v1.0.11 token-economy regression tests (P1-5 a/b/c).

Pins the MCP token caps the roadmap flagged as the most under-budgeted surfaces:
briefing default 100K→12K + section projection, detail-gated crystal projection,
and paginated / opt-in-markdown topic digest. The MCP tool functions are
directly callable as module attributes (see _tool_fn fallback in
test_v065_mcp_snapshots).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from research_hub import mcp_server


# --------------------------------------------------------------------------- #
# P1-5a: briefing cap + section projection
# --------------------------------------------------------------------------- #
def test_briefing_max_chars_lowered_to_12k():
    assert mcp_server._BRIEFING_MAX_CHARS == 12_000


def test_project_briefing_section_extracts_matching_section():
    text = "## Intro\nintro body\n## Methods\nmethods body\n## Results\nresults body\n"
    out = mcp_server._project_briefing_section(text, "methods")
    assert "methods body" in out
    assert "intro body" not in out and "results body" not in out


def test_project_briefing_section_no_match_returns_full():
    text = "## Intro\nbody\n"
    assert mcp_server._project_briefing_section(text, "nope") == text
    assert mcp_server._project_briefing_section(text, "") == text


# --------------------------------------------------------------------------- #
# P1-5b: detail-gated crystal projection
# --------------------------------------------------------------------------- #
def _fake_crystal_item():
    return SimpleNamespace(
        question_slug="q1", question="What?", tldr="t", gist="g", full="f",
        evidence=[SimpleNamespace(claim="c", papers=["p1"])],
        based_on_papers=["p1", "p2", "p3"], based_on_paper_count=3,
        last_generated="2026-06-13", confidence=0.9, see_also=["q2"],
    )


def test_read_crystal_gist_drops_heavy_fields(monkeypatch):
    monkeypatch.setattr("research_hub.config.get_config", lambda: SimpleNamespace())
    monkeypatch.setattr("research_hub.crystal.read_crystal", lambda cfg, c, s: _fake_crystal_item())
    r = mcp_server.read_crystal("agents", "q1", level="gist")
    assert r["based_on_paper_count"] == 3
    assert "based_on_papers" not in r
    assert "evidence" not in r
    assert "see_also" not in r


def test_read_crystal_full_includes_heavy_fields(monkeypatch):
    monkeypatch.setattr("research_hub.config.get_config", lambda: SimpleNamespace())
    monkeypatch.setattr("research_hub.crystal.read_crystal", lambda cfg, c, s: _fake_crystal_item())
    r = mcp_server.read_crystal("agents", "q1", level="full")
    assert r["based_on_papers"] == ["p1", "p2", "p3"]
    assert r["evidence"] and r["see_also"]


# --------------------------------------------------------------------------- #
# P1-5c: paginated + opt-in-markdown topic digest
# --------------------------------------------------------------------------- #
@dataclass
class _FakePaper:
    title: str
    doi: str = ""


def _fake_digest(n):
    return SimpleNamespace(
        cluster_slug="agents", cluster_title="Agents", paper_count=n,
        papers=[_FakePaper(title=f"P{i}") for i in range(n)],
        to_markdown=lambda: "# digest markdown",
    )


def test_get_topic_digest_paginates_default_10(monkeypatch):
    monkeypatch.setattr("research_hub.config.get_config", lambda: SimpleNamespace())
    monkeypatch.setattr("research_hub.topic.get_topic_digest", lambda cfg, s: _fake_digest(25))
    r = mcp_server.get_topic_digest("agents")
    assert r["returned"] == 10 and len(r["papers"]) == 10
    assert r["paper_count"] == 25  # total still reported
    assert "markdown" not in r  # opt-in only


def test_get_topic_digest_limit_zero_and_markdown_opt_in(monkeypatch):
    monkeypatch.setattr("research_hub.config.get_config", lambda: SimpleNamespace())
    monkeypatch.setattr("research_hub.topic.get_topic_digest", lambda cfg, s: _fake_digest(25))
    r = mcp_server.get_topic_digest("agents", limit=0, include_markdown=True)
    assert len(r["papers"]) == 25  # limit=0 → all
    assert r["markdown"] == "# digest markdown"


# --------------------------------------------------------------------------- #
# P1-5d: crystallization coverage (doctor check + ask_cluster inline prompt)
# --------------------------------------------------------------------------- #
def test_doctor_crystal_coverage_flags_uncrystalized(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = SimpleNamespace(clusters_file=tmp_path / "clusters.yaml", raw=tmp_path / "raw")
    monkeypatch.setattr(
        "research_hub.clusters.ClusterRegistry",
        lambda f: SimpleNamespace(list=lambda: [SimpleNamespace(slug="big")]),
    )
    monkeypatch.setattr("research_hub.vault.sync.list_cluster_notes", lambda slug, raw: ["p1", "p2", "p3"])
    monkeypatch.setattr("research_hub.crystal.list_crystals", lambda cfg, slug: [])  # 0 crystals
    r = doctor.check_cluster_crystal_coverage(cfg)
    assert r.status == "INFO"
    assert "big" in r.message and "crystal emit" in r.remedy


def test_doctor_crystal_coverage_ok_when_crystalized(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = SimpleNamespace(clusters_file=tmp_path / "clusters.yaml", raw=tmp_path / "raw")
    monkeypatch.setattr(
        "research_hub.clusters.ClusterRegistry",
        lambda f: SimpleNamespace(list=lambda: [SimpleNamespace(slug="c")]),
    )
    monkeypatch.setattr("research_hub.vault.sync.list_cluster_notes", lambda slug, raw: ["p1"])
    monkeypatch.setattr("research_hub.crystal.list_crystals", lambda cfg, slug: [SimpleNamespace()])
    assert doctor.check_cluster_crystal_coverage(cfg).status == "OK"


def test_ask_cluster_digest_fallback_includes_emit_crystal_prompt(monkeypatch):
    from research_hub import workflows

    monkeypatch.setattr("research_hub.crystal.list_crystals", lambda cfg, slug: [])  # uncrystalized
    monkeypatch.setattr("research_hub.topic.get_topic_digest", lambda cfg, slug: SimpleNamespace(papers=[], paper_count=3))
    monkeypatch.setattr("research_hub.topic.read_overview", lambda cfg, slug: "")
    monkeypatch.setattr("research_hub.crystal.emit_crystal_prompt", lambda cfg, slug: "## CRYSTAL PROMPT")

    result = workflows.ask_cluster(SimpleNamespace(), "somecluster", question="q")
    assert result["source"] == "digest"
    assert result["emit_crystal_prompt"] == "## CRYSTAL PROMPT"
