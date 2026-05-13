"""v0.88 #7 — vault root _HOME.md as canonical landing page."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.vault.hub_overview import (
    HOME_FILENAME,
    populate_home,
)


def _seed_cluster(vault: Path, slug: str, paper_stems: list[str], status: str = "unread") -> None:
    raw = vault / "raw" / slug
    raw.mkdir(parents=True)
    (vault / "hub" / slug).mkdir(parents=True)
    for stem in paper_stems:
        year = int(stem.split("year=")[1].split("_")[0]) if "year=" in stem else 2024
        (raw / f"{stem}.md").write_text(
            f'---\ntitle: "Title of {stem}"\nyear: {year}\nstatus: {status}\n---\n# {stem}\n',
            encoding="utf-8",
        )


def _seed_clusters_registry(vault: Path, slugs_and_names: list[tuple[str, str]]) -> None:
    clusters_path = vault / ".research_hub" / "clusters.yaml"
    clusters_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "clusters": {
            slug: {"name": name, "first_query": ""}
            for slug, name in slugs_and_names
        }
    }
    clusters_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cfg(vault: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=vault,
        clusters_file=vault / ".research_hub" / "clusters.yaml",
    )


def test_populate_home_creates_file_with_canonical_sections(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo-llm-water", ["paper2024_a", "paper2024_b"])
    _seed_clusters_registry(tmp_path, [("demo-llm-water", "Demo LLM Water")])

    populate_home(_cfg(tmp_path))

    home = tmp_path / HOME_FILENAME
    assert home.exists()
    text = home.read_text(encoding="utf-8")
    assert "type: home" in text
    assert 'aliases: ["Home", "🏠"]' in text
    assert "## Clusters" in text
    assert "## Reading queue" in text
    assert "## Recent NotebookLM briefs" in text
    assert "## Dashboard" in text


def test_populate_home_lists_clusters_with_paper_count(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "cluster-a", ["p1_year=2024_", "p2_year=2023_"])
    _seed_cluster(tmp_path, "cluster-b", ["p3_year=2024_"])
    _seed_clusters_registry(tmp_path, [
        ("cluster-a", "Cluster A"),
        ("cluster-b", "Cluster B"),
    ])

    populate_home(_cfg(tmp_path))

    text = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    assert "[[cluster-a/00_overview|Cluster A]] (2 papers)" in text
    assert "[[cluster-b/00_overview|Cluster B]] (1 papers)" in text


def test_populate_home_reading_queue_filters_unread(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo", ["a_year=2024_", "b_year=2023_"], status="unread")
    _seed_cluster(tmp_path, "done-cluster", ["c_year=2024_"], status="read")
    _seed_clusters_registry(tmp_path, [
        ("demo", "Demo"),
        ("done-cluster", "Done"),
    ])

    populate_home(_cfg(tmp_path))

    text = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    # Both unread "demo" papers should appear; the done-cluster paper should NOT
    assert "[[a_year=2024_" in text
    assert "[[b_year=2023_" in text
    assert "[[c_year=2024_" not in text


def test_populate_home_reading_queue_empty_state(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo", ["a_year=2024_"], status="read")
    _seed_clusters_registry(tmp_path, [("demo", "Demo")])

    populate_home(_cfg(tmp_path))

    text = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    assert "all clusters caught up" in text


def test_populate_home_recent_briefs_empty_state(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo", ["a_year=2024_"])
    _seed_clusters_registry(tmp_path, [("demo", "Demo")])

    populate_home(_cfg(tmp_path))

    text = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    assert "no NotebookLM briefs downloaded yet" in text


def test_populate_home_recent_briefs_lists_latest(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo", ["a_year=2024_"])
    _seed_clusters_registry(tmp_path, [("demo", "Demo")])
    brief = tmp_path / "hub" / "demo" / "notebooklm-brief-20260513T041410Z.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("...", encoding="utf-8")

    populate_home(_cfg(tmp_path))

    text = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    assert "notebooklm-brief-20260513T041410Z" in text
    assert "demo" in text


def test_populate_home_idempotent_on_regenerated_sections(tmp_path: Path) -> None:
    _seed_cluster(tmp_path, "demo", ["a_year=2024_"])
    _seed_clusters_registry(tmp_path, [("demo", "Demo")])

    populate_home(_cfg(tmp_path))
    first = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    populate_home(_cfg(tmp_path))
    second = (tmp_path / HOME_FILENAME).read_text(encoding="utf-8")
    assert first == second


def test_populate_home_preserves_user_added_section(tmp_path: Path) -> None:
    """User-written sections like `## My pinned notes` survive
    re-population — only the canonical 4 sections get regenerated."""
    _seed_cluster(tmp_path, "demo", ["a_year=2024_"])
    _seed_clusters_registry(tmp_path, [("demo", "Demo")])

    populate_home(_cfg(tmp_path))
    home = tmp_path / HOME_FILENAME
    # User appends a custom section
    home.write_text(
        home.read_text(encoding="utf-8") + "\n## My pinned notes\n\n- [[some-note]]\n",
        encoding="utf-8",
    )
    populate_home(_cfg(tmp_path))
    text = home.read_text(encoding="utf-8")
    assert "## My pinned notes" in text
    assert "[[some-note]]" in text
