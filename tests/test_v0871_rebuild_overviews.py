"""v0.87.1 #5 — vault rebuild-overviews backfills clusters that were
ingested BEFORE the v0.87 populate_overview hook landed.

`populate_all_overviews(cfg, cluster_slug_filter=None)` walks every
cluster in the registry, ensures their MOCs, and re-runs
populate_overview against the latest available brief mirror.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.vault.hub_overview import (
    OVERVIEW_FILENAME,
    PAPERS_HEADING,
    MOC_HEADING,
    BRIEF_HEADING,
    latest_brief_md,
    populate_all_overviews,
)


def _seed_cluster(
    vault_root: Path,
    slug: str,
    *,
    paper_stems: list[str],
    brief_mirror_name: str | None = None,
) -> None:
    raw = vault_root / "raw" / slug
    hub = vault_root / "hub" / slug
    raw.mkdir(parents=True)
    hub.mkdir(parents=True)
    for stem in paper_stems:
        (raw / f"{stem}.md").write_text(
            f'---\ntitle: "{stem}"\nyear: 2024\n---\n# {stem}\n',
            encoding="utf-8",
        )
    if brief_mirror_name:
        (hub / brief_mirror_name).write_text(
            "---\ntype: notebooklm-brief\n---\n# Brief\n\n## TL;DR\n\nfoo bar baz.\n",
            encoding="utf-8",
        )


def _seed_clusters_registry(vault_root: Path, slugs_and_queries: list[tuple[str, str]]) -> Path:
    """Write a minimal clusters.yaml registry matching ClusterRegistry shape.

    ClusterRegistry expects `clusters` as a {slug: {...fields...}} dict,
    not a list of paper records. The registry tries YAML first, falls
    back to JSON; since JSON is a subset of YAML 1.2 we can stick to
    json.dumps for the test fixture and still get parsed correctly.
    """
    clusters_path = vault_root / ".research_hub" / "clusters.yaml"
    clusters_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "clusters": {
            slug: {"name": slug, "first_query": query, "created_at": "2026-05-13"}
            for slug, query in slugs_and_queries
        }
    }
    clusters_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return clusters_path


def _cfg_for(vault_root: Path) -> SimpleNamespace:
    """Build a minimal cfg object with the attributes populate_all_overviews uses."""
    return SimpleNamespace(
        root=vault_root,
        clusters_file=vault_root / ".research_hub" / "clusters.yaml",
    )


def test_latest_brief_md_returns_most_recent(tmp_path: Path) -> None:
    hub = tmp_path / "hub" / "demo"
    hub.mkdir(parents=True)
    (hub / "notebooklm-brief-20260101T000000Z.md").write_text("a", encoding="utf-8")
    (hub / "notebooklm-brief-20260513T120000Z.md").write_text("b", encoding="utf-8")
    (hub / "notebooklm-brief-20260301T120000Z.md").write_text("c", encoding="utf-8")
    latest = latest_brief_md(tmp_path, "demo")
    assert latest is not None
    assert latest.name == "notebooklm-brief-20260513T120000Z.md"


def test_latest_brief_md_returns_none_when_no_brief(tmp_path: Path) -> None:
    (tmp_path / "hub" / "demo").mkdir(parents=True)
    assert latest_brief_md(tmp_path, "demo") is None


def test_latest_brief_md_returns_none_when_hub_missing(tmp_path: Path) -> None:
    assert latest_brief_md(tmp_path, "never-ingested") is None


def test_populate_all_overviews_walks_every_cluster(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "cluster-a", paper_stems=["paper2024-a"])
    _seed_cluster(
        tmp_path,
        "cluster-b-llm",
        paper_stems=["paper2025-b", "paper2024-c"],
        brief_mirror_name="notebooklm-brief-20260513T041410Z.md",
    )
    _seed_clusters_registry(
        tmp_path,
        [("cluster-a", "x"), ("cluster-b-llm", "llm agents social")],
    )

    results = populate_all_overviews(_cfg_for(tmp_path))

    assert len(results) == 2
    slugs = {slug for slug, _ in results}
    assert slugs == {"cluster-a", "cluster-b-llm"}

    overview_a = (tmp_path / "hub" / "cluster-a" / OVERVIEW_FILENAME).read_text(encoding="utf-8")
    overview_b = (tmp_path / "hub" / "cluster-b-llm" / OVERVIEW_FILENAME).read_text(encoding="utf-8")
    assert PAPERS_HEADING in overview_a
    assert PAPERS_HEADING in overview_b
    assert BRIEF_HEADING in overview_b  # brief mirror was seeded
    assert BRIEF_HEADING not in overview_a  # no brief for cluster-a
    # cluster-b-llm name triggers LLM-Agents MOC via derive_moc_links
    assert "[[LLM-Agents]]" in overview_b
    assert MOC_HEADING in overview_b


def test_populate_all_overviews_respects_filter(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "skip-me", paper_stems=["paper2024-a"])
    _seed_cluster(tmp_path, "do-me", paper_stems=["paper2024-b"])
    _seed_clusters_registry(tmp_path, [("skip-me", ""), ("do-me", "")])

    results = populate_all_overviews(_cfg_for(tmp_path), cluster_slug_filter="do-me")

    assert len(results) == 1
    assert results[0][0] == "do-me"
    assert (tmp_path / "hub" / "do-me" / OVERVIEW_FILENAME).exists()
    assert not (tmp_path / "hub" / "skip-me" / OVERVIEW_FILENAME).exists()


def test_populate_all_overviews_continues_after_per_cluster_error(tmp_path: Path) -> None:
    # cluster-a is well-formed; cluster-bad lacks a raw/ dir entirely
    _seed_cluster(tmp_path, "cluster-a", paper_stems=["paper2024-a"])
    # Don't seed cluster-bad's raw/ — let populate_overview hit an error path
    _seed_clusters_registry(tmp_path, [("cluster-a", ""), ("cluster-bad-no-raw", "")])

    results = populate_all_overviews(_cfg_for(tmp_path))

    slugs = {slug for slug, _ in results}
    assert "cluster-a" in slugs
    assert "cluster-bad-no-raw" in slugs
    # cluster-a should succeed even though cluster-bad's run may degrade
    overview = tmp_path / "hub" / "cluster-a" / OVERVIEW_FILENAME
    assert overview.exists()


def test_populate_all_overviews_is_idempotent(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "cluster-a", paper_stems=["paper2024-a", "paper2025-b"])
    _seed_clusters_registry(tmp_path, [("cluster-a", "water")])

    populate_all_overviews(_cfg_for(tmp_path))
    overview = tmp_path / "hub" / "cluster-a" / OVERVIEW_FILENAME
    first = overview.read_text(encoding="utf-8")
    populate_all_overviews(_cfg_for(tmp_path))
    second = overview.read_text(encoding="utf-8")
    assert first == second
