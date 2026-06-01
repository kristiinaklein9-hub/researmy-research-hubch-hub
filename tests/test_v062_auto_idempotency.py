from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    raw.mkdir(parents=True)
    return SimpleNamespace(raw=raw)


def test_auto_errors_on_nonempty_cluster_without_force_or_append(tmp_path, monkeypatch, capsys):
    from research_hub.cli import _auto

    cfg = _cfg(tmp_path)
    cluster = cfg.raw / "agents"
    cluster.mkdir()
    (cluster / "paper.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr("research_hub.auto.auto_pipeline", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))

    rc = _auto(topic="x", cluster_slug="agents", cluster_name=None, max_papers=1, field=None, do_nlm=False, do_crystals=False, llm_cli=None, dry_run=False)
    assert rc == 2
    assert "already has 1 paper(s)" in capsys.readouterr().out


def test_auto_proceeds_with_append_flag(tmp_path, monkeypatch):
    from research_hub.cli import _auto

    cfg = _cfg(tmp_path)
    cluster = cfg.raw / "agents"
    cluster.mkdir()
    (cluster / "paper.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr(
        "research_hub.auto.auto_pipeline",
        lambda *args, **kwargs: SimpleNamespace(ok=True, error=""),
    )
    assert _auto(topic="x", cluster_slug="agents", cluster_name=None, max_papers=1, field=None, do_nlm=False, do_crystals=False, llm_cli=None, dry_run=False, append=True) == 0


def test_auto_proceeds_with_force_flag(tmp_path, monkeypatch):
    from research_hub.cli import _auto

    cfg = _cfg(tmp_path)
    cluster = cfg.raw / "agents"
    cluster.mkdir()
    (cluster / "paper.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr(
        "research_hub.auto.auto_pipeline",
        lambda *args, **kwargs: SimpleNamespace(ok=True, error=""),
    )
    assert _auto(topic="x", cluster_slug="agents", cluster_name=None, max_papers=1, field=None, do_nlm=False, do_crystals=False, llm_cli=None, dry_run=False, force=True) == 0


def _auto_pipeline_cfg(tmp_path: Path) -> SimpleNamespace:
    """A real-filesystem cfg for driving auto_pipeline directly (non-dry-run)."""
    root = tmp_path / "vault"
    raw = root / "raw"
    raw.mkdir(parents=True)
    return SimpleNamespace(
        root=root,
        raw=raw,
        clusters_file=root / "clusters.yaml",
        llm_cli_adapters={},
    )


def _patch_auto_pipeline_internals(monkeypatch, cfg, *, run_pipeline_side_effect):
    """Stub the heavy auto_pipeline steps so the test isolates the force/append
    overwrite behaviour. run_pipeline is given a custom side_effect so the test
    can observe the raw-dir state at ingest time."""
    monkeypatch.setattr("research_hub.auto.get_config", lambda: cfg)
    monkeypatch.setattr("research_hub.auto.detect_llm_cli", lambda **k: "claude")
    monkeypatch.setattr(
        "research_hub.auto.ClusterRegistry",
        lambda *a, **k: SimpleNamespace(
            get=lambda slug: SimpleNamespace(
                slug=slug, status="active", zotero_collection_key="ZCOLL"
            ),
        ),
    )
    monkeypatch.setattr("research_hub.auto._run_search", lambda *a, **k: [{"title": "New Paper", "doi": "10.1/new"}])
    monkeypatch.setattr("research_hub.auto._run_fit_check_step", lambda cfg, papers, *a, **k: papers)
    monkeypatch.setattr("research_hub.auto.run_pipeline", run_pipeline_side_effect)
    monkeypatch.setattr("research_hub.vault.hub_overview.populate_all_overviews", lambda cfg: None)


def test_auto_pipeline_force_overwrites_existing_notes_before_ingest(tmp_path, monkeypatch):
    """FUNC-2 (force semantics): force=true must genuinely OVERWRITE -- i.e.
    clear the cluster's existing Obsidian notes before ingest -- not merely
    bypass the guard and merge old+new (which was indistinguishable from
    append)."""
    from research_hub.auto import auto_pipeline

    cfg = _auto_pipeline_cfg(tmp_path)
    cluster = cfg.raw / "agents"
    cluster.mkdir()
    (cluster / "stale-paper.md").write_text("# old", encoding="utf-8")
    (cluster / "stale-paper-2.md").write_text("# old2", encoding="utf-8")

    notes_at_ingest = {}

    def fake_run_pipeline(*args, **kwargs):
        # capture what notes survive into the ingest step
        notes_at_ingest["files"] = sorted(p.name for p in cluster.glob("*.md"))
        # simulate ingest writing one fresh note
        (cluster / "new-paper.md").write_text("# new", encoding="utf-8")
        return 0

    _patch_auto_pipeline_internals(monkeypatch, cfg, run_pipeline_side_effect=fake_run_pipeline)

    report = auto_pipeline(
        topic="agents",
        cluster_slug="agents",
        do_nlm=False,
        do_fit_check=False,
        force=True,
        print_progress=False,
    )

    assert report.ok is True
    # the stale notes were cleared BEFORE ingest ran (genuine overwrite)
    assert notes_at_ingest["files"] == []
    # an explicit overwrite step was logged
    assert any(s.name == "overwrite" and s.ok for s in report.steps)
    # only the freshly-ingested note remains
    assert sorted(p.name for p in cluster.glob("*.md")) == ["new-paper.md"]


def test_auto_pipeline_append_preserves_existing_notes(tmp_path, monkeypatch):
    """append=true must NOT clear existing notes (the additive path stays
    additive) -- this is the contrast that proves force!=append now."""
    from research_hub.auto import auto_pipeline

    cfg = _auto_pipeline_cfg(tmp_path)
    cluster = cfg.raw / "agents"
    cluster.mkdir()
    (cluster / "kept-paper.md").write_text("# keep", encoding="utf-8")

    notes_at_ingest = {}

    def fake_run_pipeline(*args, **kwargs):
        notes_at_ingest["files"] = sorted(p.name for p in cluster.glob("*.md"))
        return 0

    _patch_auto_pipeline_internals(monkeypatch, cfg, run_pipeline_side_effect=fake_run_pipeline)

    report = auto_pipeline(
        topic="agents",
        cluster_slug="agents",
        do_nlm=False,
        do_fit_check=False,
        append=True,
        print_progress=False,
    )

    assert report.ok is True
    # append left the existing note in place at ingest time
    assert notes_at_ingest["files"] == ["kept-paper.md"]
    # no overwrite step was logged on the append path
    assert not any(s.name == "overwrite" for s in report.steps)
