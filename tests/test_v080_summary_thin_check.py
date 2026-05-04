from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import ClusterRegistry
from research_hub.doctor import check_cluster_summary_thin


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    research_hub_dir = root / ".research_hub"
    raw.mkdir(parents=True)
    research_hub_dir.mkdir()
    return SimpleNamespace(
        root=root,
        raw=raw,
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
    )


def _bind_cluster(cfg, slug: str) -> None:
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create(query=slug, name=slug.title(), slug=slug)
    registry.bind(slug, zotero_collection_key=f"{slug.upper()}1", sync_zotero=False)


def _write_note(path: Path, *, summary: str, methodology: str = "Method.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
title: "{path.stem}"
doi: "10.1/{path.stem}"
authors: "Jane Doe"
year: "2026"
topic_cluster: "agents"
status: "unread"
ingested_at: "2026-05-04T00:00:00Z"
---

## Summary
{summary}

## Key Findings
- Finding.

## Methodology
{methodology}

## Relevance
Relevant.
""",
        encoding="utf-8",
    )


def test_summary_thin_info_when_threshold_exceeded(tmp_path):
    cfg = _cfg(tmp_path)
    _bind_cluster(cfg, "agents")
    cluster_dir = cfg.raw / "agents"
    _write_note(cluster_dir / "paper1.md", summary="[TODO] fill me")
    _write_note(cluster_dir / "paper2.md", summary="[TODO] fill me too")
    _write_note(cluster_dir / "paper3.md", summary="Ready summary.")
    _write_note(cluster_dir / "paper4.md", summary="Ready summary.")

    result = check_cluster_summary_thin(cfg)

    assert result.status == "INFO"
    assert "cluster/summary_thin" == result.name
    assert "agents: 2/4 (50%)" in result.details


def test_summary_thin_ok_when_threshold_not_exceeded(tmp_path):
    cfg = _cfg(tmp_path)
    _bind_cluster(cfg, "agents")
    cluster_dir = cfg.raw / "agents"
    _write_note(cluster_dir / "paper1.md", summary="[TODO] fill me")
    _write_note(cluster_dir / "paper2.md", summary="Ready summary.")
    _write_note(cluster_dir / "paper3.md", summary="Ready summary.")
    _write_note(cluster_dir / "paper4.md", summary="Ready summary.")

    result = check_cluster_summary_thin(cfg)

    assert result.status == "OK"
    assert "30%" in result.message


def test_summary_thin_ignores_todo_outside_summary_block(tmp_path):
    cfg = _cfg(tmp_path)
    _bind_cluster(cfg, "agents")
    cluster_dir = cfg.raw / "agents"
    _write_note(cluster_dir / "paper1.md", summary="Ready summary.", methodology="[TODO: later]")

    result = check_cluster_summary_thin(cfg)

    assert result.status == "OK"
