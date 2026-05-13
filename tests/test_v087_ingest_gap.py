"""v0.87 O4 — fit-check → ingest gap reporter.

Tests `compute_ingest_gap` against synthetic vault layouts plus the
roundtrip into `.ingest_gap.json` via `write_gap_sidecar`.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_hub.ingest_diff import (
    ACCEPTED_FILENAME,
    GAP_FILENAME,
    compute_ingest_gap,
    write_gap_sidecar,
)


def _make_vault(tmp_path: Path, slug: str, accepted: list[dict] | None, raw_notes: dict[str, str]) -> Path:
    """Build a minimal vault with fit-check sidecar + raw/<slug>/*.md."""
    hub_dir = tmp_path / "hub" / slug
    raw_dir = tmp_path / "raw" / slug
    hub_dir.mkdir(parents=True)
    raw_dir.mkdir(parents=True)
    if accepted is not None:
        (hub_dir / ACCEPTED_FILENAME).write_text(
            json.dumps({"cluster_slug": slug, "threshold": 4, "accepted": accepted}),
            encoding="utf-8",
        )
    for slug_name, doi in raw_notes.items():
        body = f'---\ntitle: "x"\ndoi: "{doi}"\n---\n# {slug_name}\n'
        (raw_dir / f"{slug_name}.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_compute_gap_when_3_of_5_accepted_were_ingested(tmp_path: Path) -> None:
    accepted = [
        {"doi": "10.1/a", "title": "A"},
        {"doi": "10.1/b", "title": "B"},
        {"doi": "10.1/c", "title": "C"},
        {"doi": "10.1/d", "title": "D"},
        {"doi": "10.1/e", "title": "E"},
    ]
    raw = {"x2024-a": "10.1/a", "x2024-c": "10.1/c", "x2024-e": "10.1/e"}
    _make_vault(tmp_path, "demo", accepted, raw)

    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    assert report["cluster_slug"] == "demo"
    assert report["accepted_count"] == 5
    assert report["ingested_count"] == 3
    assert report["gap_count"] == 2
    gap_dois = {g["doi"] for g in report["gap"]}
    assert gap_dois == {"10.1/b", "10.1/d"}
    titles = {g["title"] for g in report["gap"]}
    assert titles == {"B", "D"}


def test_compute_gap_returns_zero_when_all_accepted_were_ingested(tmp_path: Path) -> None:
    accepted = [{"doi": "10.1/a", "title": "A"}, {"doi": "10.1/b", "title": "B"}]
    raw = {"x2024-a": "10.1/a", "x2024-b": "10.1/b"}
    _make_vault(tmp_path, "demo", accepted, raw)

    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    assert report["gap_count"] == 0
    assert report["gap"] == []


def test_compute_gap_normalizes_doi_case_and_whitespace(tmp_path: Path) -> None:
    accepted = [{"doi": "  10.1/A  ", "title": "A"}]
    raw = {"x2024-a": "10.1/a"}
    _make_vault(tmp_path, "demo", accepted, raw)

    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    assert report["gap_count"] == 0


def test_compute_gap_tolerates_missing_accepted_sidecar(tmp_path: Path) -> None:
    _make_vault(tmp_path, "demo", accepted=None, raw_notes={"x2024-a": "10.1/a"})

    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    assert report["accepted_count"] == 0
    assert report["gap_count"] == 0


def test_compute_gap_tolerates_missing_raw_dir(tmp_path: Path) -> None:
    accepted = [{"doi": "10.1/a", "title": "A"}]
    # only hub dir
    hub_dir = tmp_path / "hub" / "demo"
    hub_dir.mkdir(parents=True)
    (hub_dir / ACCEPTED_FILENAME).write_text(
        json.dumps({"cluster_slug": "demo", "threshold": 4, "accepted": accepted}),
        encoding="utf-8",
    )

    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    assert report["accepted_count"] == 1
    assert report["ingested_count"] == 0
    assert report["gap_count"] == 1


def test_write_gap_sidecar_roundtrips(tmp_path: Path) -> None:
    accepted = [
        {"doi": "10.1/a", "title": "A"},
        {"doi": "10.1/b", "title": "B"},
    ]
    _make_vault(tmp_path, "demo", accepted, {"x2024-a": "10.1/a"})
    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)

    path = write_gap_sidecar(cluster_slug="demo", vault_root=tmp_path, gap_report=report)

    assert path.name == GAP_FILENAME
    assert path.parent.name == "demo"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["gap_count"] == 1
    assert written["gap"][0]["doi"] == "10.1/b"


def test_write_gap_sidecar_writes_clean_run_with_zero_gap(tmp_path: Path) -> None:
    """A zero-gap report should still be written so re-runs see green state."""
    _make_vault(tmp_path, "demo", accepted=[], raw_notes={})
    report = compute_ingest_gap(cluster_slug="demo", vault_root=tmp_path)
    path = write_gap_sidecar(cluster_slug="demo", vault_root=tmp_path, gap_report=report)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["gap_count"] == 0
