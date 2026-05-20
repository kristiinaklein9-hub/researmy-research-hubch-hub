"""PR-E regression: after a successful `auto` ingest, the vault-level
navigation cascade (`populate_all_overviews` -> `populate_all_mocs` ->
`populate_home`) must fire so `_HOME.md`, MOC bodies, and per-cluster
overviews stay current.

Pre-fix this cascade existed and was reachable via `vault
rebuild-overviews` only — `auto`/`ingest` never called it, leaving
silently stale navigation artifacts after every research session
(empirically reproduced post-PR-D 4-leg E2E: 0 `_HOME.md` on disk,
MOC bodies frozen at "(populated by sync)").
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from research_hub.auto import auto_pipeline


def _real_cfg(tmp_path: Path) -> SimpleNamespace:
    """Minimal real-filesystem cfg the hook can read against.
    raw_dir.glob("*.md") needs an actual Path, not a MagicMock."""
    root = tmp_path / "vault"
    raw = root / "raw"
    logs = root / "logs"
    rh = root / ".research_hub"
    hub = root / "hub"
    for d in (raw, logs, rh, hub):
        d.mkdir(parents=True)
    return SimpleNamespace(
        root=root, raw=raw, logs=logs, hub=hub,
        research_hub_dir=rh,
        clusters_file=rh / "clusters.yaml",
        zotero_default_collection="DEFAULT",
        zotero_collections={},
        zotero_library_id="123",
    )


def _common_patches(mock_cfg):
    """Patch the auto_pipeline external dependencies we don't exercise here."""
    return [
        patch("research_hub.auto.get_config", return_value=mock_cfg),
        patch("research_hub.auto.run_pipeline", return_value=0),
        patch("research_hub.auto.detect_llm_cli", return_value="claude"),
        patch(
            "research_hub.auto._run_fit_check_step",
            side_effect=lambda cfg, papers, *a, **k: papers,
        ),
        patch(
            "research_hub.auto._run_search",
            return_value=[{
                "title": "Fake Paper", "doi": "10.1000/fake",
                "authors": ["X"], "year": 2025,
                "source": "openalex", "citation_count": 1,
            }],
        ),
    ]


def _setup_registry(mock_cluster_registry, *, returns_cluster):
    """Mock ClusterRegistry: get() returns None first (so create() fires),
    then `returns_cluster` on subsequent get() calls."""
    instance = MagicMock()
    instance.get.side_effect = [None, returns_cluster]
    instance.create.return_value = returns_cluster
    mock_cluster_registry.return_value = instance
    return instance


def test_populate_all_overviews_fires_on_successful_ingest(tmp_path, monkeypatch):
    """Happy path: ingest wrote >0 papers -> hook fires exactly once
    with the live cfg object."""
    cfg = _real_cfg(tmp_path)
    cluster_slug = "test-slug"
    # Simulate run_pipeline writing one paper note into raw/<slug>/
    (cfg.raw / cluster_slug).mkdir(parents=True)
    (cfg.raw / cluster_slug / "fake-paper.md").write_text(
        "---\ntitle: Fake\n---\n", encoding="utf-8",
    )

    populate_calls: list = []

    def fake_populate(passed_cfg):
        populate_calls.append(passed_cfg)
        return []

    monkeypatch.setattr(
        "research_hub.vault.hub_overview.populate_all_overviews",
        fake_populate,
    )

    fake_cluster = MagicMock(slug=cluster_slug)
    with patch("research_hub.auto.ClusterRegistry") as mock_registry, \
         _common_patches(cfg)[0], _common_patches(cfg)[1], \
         _common_patches(cfg)[2], _common_patches(cfg)[3], _common_patches(cfg)[4]:
        _setup_registry(mock_registry, returns_cluster=fake_cluster)
        report = auto_pipeline(
            topic="test topic",
            cluster_slug=cluster_slug,
            do_nlm=False,
            do_crystals=False,
            do_cluster_overview=False,
            print_progress=False,
        )

    assert len(populate_calls) == 1, (
        f"populate_all_overviews should fire exactly once after a "
        f"successful ingest, got {len(populate_calls)} calls"
    )
    assert populate_calls[0] is cfg


def test_populate_all_overviews_skipped_when_zero_papers_written(tmp_path, monkeypatch):
    """When ingest wrote 0 papers (all quarantined / no candidates),
    there's nothing new to surface — skip the cascade to avoid spurious
    churn on the navigation artifacts."""
    cfg = _real_cfg(tmp_path)
    cluster_slug = "empty-slug"
    # NOTE: do NOT create any .md under raw/<slug>/ -> raw_dir.glob("*.md") -> 0

    populate_calls: list = []
    monkeypatch.setattr(
        "research_hub.vault.hub_overview.populate_all_overviews",
        lambda c: populate_calls.append(c) or [],
    )

    fake_cluster = MagicMock(slug=cluster_slug)
    with patch("research_hub.auto.ClusterRegistry") as mock_registry, \
         _common_patches(cfg)[0], _common_patches(cfg)[1], \
         _common_patches(cfg)[2], _common_patches(cfg)[3], _common_patches(cfg)[4]:
        _setup_registry(mock_registry, returns_cluster=fake_cluster)
        report = auto_pipeline(
            topic="test topic",
            cluster_slug=cluster_slug,
            do_nlm=False,
            do_crystals=False,
            do_cluster_overview=False,
            print_progress=False,
        )

    assert populate_calls == [], (
        "populate_all_overviews must NOT fire when ingest wrote 0 papers; "
        f"got {len(populate_calls)} calls"
    )


def test_populate_all_overviews_failure_is_non_fatal(tmp_path, monkeypatch, capsys):
    """Per the hook docstring: a per-cluster overview / MOC / home-render
    failure should NOT sink the whole auto pipeline (the ingest itself
    succeeded). The exception must be logged to stderr and swallowed."""
    cfg = _real_cfg(tmp_path)
    cluster_slug = "test-slug"
    (cfg.raw / cluster_slug).mkdir(parents=True)
    (cfg.raw / cluster_slug / "fake.md").write_text("---\ntitle:X\n---\n", encoding="utf-8")

    def boom(_cfg):
        raise RuntimeError("synthetic populate_all_overviews failure")

    monkeypatch.setattr(
        "research_hub.vault.hub_overview.populate_all_overviews", boom,
    )

    fake_cluster = MagicMock(slug=cluster_slug)
    with patch("research_hub.auto.ClusterRegistry") as mock_registry, \
         _common_patches(cfg)[0], _common_patches(cfg)[1], \
         _common_patches(cfg)[2], _common_patches(cfg)[3], _common_patches(cfg)[4]:
        _setup_registry(mock_registry, returns_cluster=fake_cluster)
        # Must not raise:
        report = auto_pipeline(
            topic="test topic",
            cluster_slug=cluster_slug,
            do_nlm=False,
            do_crystals=False,
            do_cluster_overview=False,
            print_progress=False,
        )

    captured = capsys.readouterr()
    assert "populate_all_overviews failed" in captured.err
    assert "synthetic populate_all_overviews failure" in captured.err
    # Report itself should still reflect the ingest success.
    assert report.papers_ingested == 1
