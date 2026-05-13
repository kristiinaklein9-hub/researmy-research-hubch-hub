from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import Cluster, ClusterRegistry
from research_hub.dedup import DedupHit, DedupIndex


class FakeZotero:
    def __init__(self, items: dict[str, dict] | None = None, stale: set[str] | None = None) -> None:
        self.items = items or {}
        self.stale = stale or set()
        self.updated: list[dict] = []

    def item(self, key: str) -> dict:
        if key in self.stale:
            raise RuntimeError("404 Not Found")
        return self.items[key]

    def update_item(self, data: dict) -> dict:
        key = data.get("key")
        if key and key in self.items:
            self.items[key]["data"] = data
        self.updated.append(dict(data))
        return {"success": True}


def _cfg(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "vault"
    raw = root / "raw"
    hub = root / "hub"
    research_hub_dir = root / ".research_hub"
    for path in (raw, hub, research_hub_dir):
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        root=root,
        raw=raw,
        hub=hub,
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
        zotero_library_id="123",
    )


def _registry(cfg, *clusters: Cluster) -> ClusterRegistry:
    registry = ClusterRegistry(cfg.clusters_file)
    for cluster in clusters:
        registry.clusters[cluster.slug] = cluster
    registry.save()
    return registry


def _write_note(
    cfg,
    cluster: str,
    slug: str,
    *,
    labels: list[str] | None = None,
    tags: list[str] | None = None,
    zotero_key: str = "",
    collections: list[str] | None = None,
) -> Path:
    labels = labels if labels is not None else ["deprecated"]
    tags = tags if tags is not None else [f"topic:{cluster}"]
    note_dir = cfg.raw / cluster
    note_dir.mkdir(parents=True, exist_ok=True)
    path = note_dir / f"{slug}.md"
    path.write_text(
        (
            "---\n"
            f'title: "{slug}"\n'
            f'doi: "10.1000/{slug}"\n'
            f'topic_cluster: "{cluster}"\n'
            f"labels: [{', '.join(labels)}]\n"
            f"tags: [{', '.join(tags)}]\n"
            f'zotero-key: "{zotero_key}"\n'
            f"collections: [{', '.join(collections or [])}]\n"
            "---\n"
            "Body\n"
        ),
        encoding="utf-8",
    )
    return path


def _fake_item(key: str, *, collections: list[str] | None = None, tags: list[str] | None = None) -> dict:
    return {
        "key": key,
        "data": {
            "key": key,
            "title": key,
            "collections": collections or [],
            "tags": [{"tag": tag} for tag in (tags or [])],
        },
    }


def test_bulk_relabel_dry_run_reports_changes(tmp_path):
    from research_hub.paper import bulk_relabel

    cfg = _cfg(tmp_path)
    path = _write_note(cfg, "alpha", "paper-a", labels=["deprecated", "core"])

    result = bulk_relabel(cfg, "deprecated", "archive-candidate", dry_run=True)

    assert [change["slug"] for change in result["changed"]] == ["paper-a"]
    assert "deprecated" in path.read_text(encoding="utf-8")


def test_bulk_relabel_apply_writes(tmp_path, monkeypatch):
    from research_hub.paper import bulk_relabel

    cfg = _cfg(tmp_path)
    path = _write_note(cfg, "alpha", "paper-a", labels=["deprecated"], zotero_key="A1")
    fake = FakeZotero({"A1": _fake_item("A1", tags=["label/deprecated"])})
    monkeypatch.setattr("research_hub.paper._get_zotero_web_client", lambda: fake)

    result = bulk_relabel(cfg, "deprecated", "keep-review", dry_run=False)

    text = path.read_text(encoding="utf-8")
    assert "keep-review" in text
    assert "deprecated" not in text
    assert result["zotero_updated"] == ["A1"]
    assert fake.items["A1"]["data"]["tags"] == [{"tag": "label/keep-review"}]


def test_bulk_relabel_respects_cluster_filter(tmp_path):
    from research_hub.paper import bulk_relabel

    cfg = _cfg(tmp_path)
    alpha = _write_note(cfg, "alpha", "paper-a", labels=["deprecated"])
    beta = _write_note(cfg, "beta", "paper-b", labels=["deprecated"])

    result = bulk_relabel(cfg, "deprecated", "keep", cluster_slug="alpha", dry_run=False)

    assert [change["slug"] for change in result["changed"]] == ["paper-a"]
    assert "keep" in alpha.read_text(encoding="utf-8")
    assert "deprecated" in beta.read_text(encoding="utf-8")


def test_bulk_move_moves_note_and_updates_zotero_collection(tmp_path, monkeypatch):
    from research_hub.paper import bulk_move

    cfg = _cfg(tmp_path)
    _registry(
        cfg,
        Cluster(slug="alpha", name="Alpha", zotero_collection_key="COLA"),
        Cluster(slug="beta", name="Beta", zotero_collection_key="COLB"),
    )
    _write_note(
        cfg,
        "alpha",
        "paper-a",
        labels=["core"],
        tags=["topic:alpha"],
        zotero_key="A1",
        collections=["COLA"],
    )
    fake = FakeZotero({"A1": _fake_item("A1", collections=["COLA"], tags=["topic:alpha"])})
    monkeypatch.setattr("research_hub.paper._get_zotero_web_client", lambda: fake)

    result = bulk_move(cfg, ["paper-a"], "beta", dry_run=False)

    assert result["moved"] == ["paper-a"]
    assert not (cfg.raw / "alpha" / "paper-a.md").exists()
    moved = cfg.raw / "beta" / "paper-a.md"
    assert moved.exists()
    text = moved.read_text(encoding="utf-8")
    assert 'topic_cluster: "beta"' in text
    assert "topic:beta" in text
    assert fake.items["A1"]["data"]["collections"] == ["COLB"]


def test_bulk_move_handles_missing_slug(tmp_path):
    from research_hub.paper import bulk_move

    cfg = _cfg(tmp_path)
    _registry(cfg, Cluster(slug="beta", name="Beta"))

    result = bulk_move(cfg, ["missing"], "beta", dry_run=True)

    assert result["missing"] == ["missing"]
    assert result["would_move"] == []


def test_bulk_delete_dry_run_preview(tmp_path):
    from research_hub.paper import bulk_delete_by_tag

    cfg = _cfg(tmp_path)
    path = _write_note(cfg, "alpha", "paper-a", tags=["trash-me"])

    result = bulk_delete_by_tag(cfg, "trash-me", dry_run=True)

    assert [item["slug"] for item in result["would_delete"]] == ["paper-a"]
    assert path.exists()


def test_bulk_delete_apply_deletes_and_trashes_zotero(tmp_path, monkeypatch):
    from research_hub.paper import bulk_delete_by_tag

    cfg = _cfg(tmp_path)
    path = _write_note(cfg, "alpha", "paper-a", tags=["trash-me"], zotero_key="A1")
    fake = FakeZotero({"A1": _fake_item("A1")})
    monkeypatch.setattr("research_hub.paper._get_zotero_web_client", lambda: fake)

    result = bulk_delete_by_tag(cfg, "trash-me", dry_run=False)

    assert result["deleted"] == ["paper-a"]
    assert result["zotero_trashed"] == ["A1"]
    assert not path.exists()
    assert fake.items["A1"]["data"]["deleted"] == 1


def test_bulk_delete_respects_by_tag_filter(tmp_path):
    from research_hub.paper import bulk_delete_by_tag

    cfg = _cfg(tmp_path)
    keep = _write_note(cfg, "alpha", "keep", tags=["keep-me"])
    delete = _write_note(cfg, "alpha", "delete", tags=["trash-me"])

    result = bulk_delete_by_tag(cfg, "trash-me", dry_run=False)

    assert result["deleted"] == ["delete"]
    assert keep.exists()
    assert not delete.exists()


def test_clusters_archive_sets_status(tmp_path):
    cfg = _cfg(tmp_path)
    _registry(cfg, Cluster(slug="alpha", name="Alpha"))

    ClusterRegistry(cfg.clusters_file).archive("alpha")
    loaded = ClusterRegistry(cfg.clusters_file).get("alpha")

    assert loaded.status == "archived"
    assert loaded.archived_at


def test_clusters_archive_auto_runs_skip_archived_cluster(tmp_path, monkeypatch):
    from research_hub.auto import auto_pipeline

    cfg = _cfg(tmp_path)
    _registry(cfg, Cluster(slug="alpha", name="Alpha", status="archived"))
    monkeypatch.setattr("research_hub.auto.get_config", lambda: cfg)
    monkeypatch.setattr(
        "research_hub.auto._run_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("search should not run")),
    )

    report = auto_pipeline("alpha", do_nlm=False, dry_run=False)

    assert report.ok is True
    assert any(step.name == "archive" and "skipped" in step.detail for step in report.steps)


def test_run_pipeline_default_skips_archived_cluster_before_zotero_guard(tmp_path, monkeypatch):
    from research_hub.pipeline import run_pipeline

    cfg = _cfg(tmp_path)
    _registry(cfg, Cluster(slug="alpha", name="Alpha", status="archived"))
    monkeypatch.setattr("research_hub.pipeline.get_config", lambda: cfg)

    assert run_pipeline(cluster_slug="alpha", dry_run=False) == 0


def test_clusters_unarchive_reverts_status(tmp_path):
    cfg = _cfg(tmp_path)
    _registry(cfg, Cluster(slug="alpha", name="Alpha", status="archived", archived_at="2026-05-13T00:00:00Z"))

    ClusterRegistry(cfg.clusters_file).unarchive("alpha")
    loaded = ClusterRegistry(cfg.clusters_file).get("alpha")

    assert loaded.status == "active"
    assert loaded.archived_at == ""


def test_dedup_compact_drops_stale_zotero_hits(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    index = DedupIndex.empty()
    index.add(DedupHit(source="zotero", doi="10.1/live", title="Live Paper", zotero_key="LIVE"))
    index.add(DedupHit(source="zotero", doi="10.1/stale", title="Stale Paper", zotero_key="STALE"))
    fake = FakeZotero({"LIVE": _fake_item("LIVE")}, stale={"STALE"})

    compacted, report = index.compact(raw, fake, dry_run=False)

    assert report.removed_zotero_keys == ["STALE"]
    assert "10.1/live" in compacted.doi_to_hits
    assert "10.1/stale" not in compacted.doi_to_hits


def test_dedup_compact_dry_run_does_not_write(tmp_path, monkeypatch):
    from research_hub.cli import main

    cfg = _cfg(tmp_path)
    index = DedupIndex.empty()
    index.add(DedupHit(source="zotero", doi="10.1/stale", title="Stale Paper", zotero_key="STALE"))
    path = cfg.research_hub_dir / "dedup_index.json"
    index.save(path)
    before = json.loads(path.read_text(encoding="utf-8"))
    fake = FakeZotero(stale={"STALE"})
    monkeypatch.setattr("research_hub.cli.get_config", lambda: cfg)
    monkeypatch.setattr("research_hub.zotero.client.get_client", lambda: fake)

    rc = main(["dedup", "compact", "--dry-run"])

    assert rc == 0
    assert json.loads(path.read_text(encoding="utf-8")) == before


def test_dedup_compact_idempotent_on_clean_index(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    index = DedupIndex.empty()
    index.add(DedupHit(source="zotero", doi="10.1/live", title="Live Paper", zotero_key="LIVE"))
    fake = FakeZotero({"LIVE": _fake_item("LIVE")})

    compacted, report = index.compact(raw, fake, dry_run=False)

    assert report.removed_zotero_keys == []
    assert compacted.doi_to_hits == index.doi_to_hits
