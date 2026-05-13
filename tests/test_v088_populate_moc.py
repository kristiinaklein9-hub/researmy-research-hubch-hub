"""v0.88 #4 — MOC body populator writes the real Clusters list."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.vault.hub_overview import (
    ensure_moc,
    populate_all_mocs,
    populate_moc,
)


def _seed_clusters_registry(vault_root: Path, slugs_and_queries: list[tuple[str, str]]) -> Path:
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
    return SimpleNamespace(
        root=vault_root,
        clusters_file=vault_root / ".research_hub" / "clusters.yaml",
    )


def test_populate_moc_replaces_placeholder_with_cluster_list(tmp_path: Path) -> None:
    ensure_moc(tmp_path, "LLM-Agents")
    path = populate_moc(tmp_path, "LLM-Agents", ["cluster-a", "cluster-b"])
    text = path.read_text(encoding="utf-8")
    assert "[[cluster-a/00_overview|cluster-a]]" in text
    assert "[[cluster-b/00_overview|cluster-b]]" in text
    assert "(populated by sync)" not in text


def test_populate_moc_sorts_clusters_deterministically(tmp_path: Path) -> None:
    ensure_moc(tmp_path, "LLM-Agents")
    path = populate_moc(tmp_path, "LLM-Agents", ["zebra", "alpha", "mike"])
    text = path.read_text(encoding="utf-8")
    # alpha should appear before mike before zebra
    alpha_pos = text.find("[[alpha/")
    mike_pos = text.find("[[mike/")
    zebra_pos = text.find("[[zebra/")
    assert 0 < alpha_pos < mike_pos < zebra_pos


def test_populate_moc_empty_list_writes_placeholder(tmp_path: Path) -> None:
    ensure_moc(tmp_path, "Orphan-MOC")
    path = populate_moc(tmp_path, "Orphan-MOC", [])
    text = path.read_text(encoding="utf-8")
    assert "no clusters reference this MOC yet" in text


def test_populate_moc_is_idempotent(tmp_path: Path) -> None:
    ensure_moc(tmp_path, "LLM-Agents")
    path = populate_moc(tmp_path, "LLM-Agents", ["cluster-a"])
    first = path.read_text(encoding="utf-8")
    populate_moc(tmp_path, "LLM-Agents", ["cluster-a"])
    second = path.read_text(encoding="utf-8")
    assert first == second


def test_populate_moc_creates_missing_file(tmp_path: Path) -> None:
    """If ensure_moc was never called, populate_moc creates it first."""
    path = populate_moc(tmp_path, "Brand-New-MOC", ["cluster-a"])
    assert path.exists()
    assert "[[cluster-a/00_overview|cluster-a]]" in path.read_text(encoding="utf-8")


def test_populate_all_mocs_walks_registry(tmp_path: Path) -> None:
    """End-to-end: derive_moc_links from each cluster, group by MOC,
    write each MOC's body with the right cluster list."""
    _seed_clusters_registry(
        tmp_path,
        [
            ("human-water-llm", "LLMs for human-water systems"),
            ("llm-agents-social", "LLM agents in social interaction"),
            ("water-pipes", "water pipes"),
        ],
    )
    results = populate_all_mocs(_cfg_for(tmp_path))
    names = {n for n, _ in results}
    # human-water-llm + llm-agents-social both match LLM-Agents (via "llm")
    # human-water-llm + water-pipes match Water-Resources (via "water")
    assert "LLM-Agents" in names
    assert "Water-Resources" in names

    llm_text = (tmp_path / "hub" / "_moc" / "LLM-Agents.md").read_text(encoding="utf-8")
    assert "human-water-llm" in llm_text
    assert "llm-agents-social" in llm_text

    water_text = (tmp_path / "hub" / "_moc" / "Water-Resources.md").read_text(encoding="utf-8")
    assert "human-water-llm" in water_text
    assert "water-pipes" in water_text
    # llm-agents-social does NOT have "water" in its slug or query → not in Water-Resources MOC
    assert "llm-agents-social" not in water_text
