from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_hub.clusters import Cluster, ClusterRegistry, NotebookShard
from research_hub.notebooklm import upload as upload_mod  # for module-ref monkeypatching
from research_hub.notebooklm.client import NotebookHandle, UploadResult
from research_hub.notebooklm.upload import NotebookLMCapacityError, upload_cluster


@pytest.fixture(autouse=True)
def _refresh_stale_module_refs():
    """Rebind this file's import-time module references to the LIVE modules.

    An earlier suite (test_v033_workflows) pops research_hub.notebooklm.upload /
    research_hub.clusters from sys.modules via its autouse re-import fixture
    (with no restore), so this file's collection-time `upload_mod` /
    `upload_cluster` / `ClusterRegistry` can go stale vs the module the
    production code re-imports — a module-identity mismatch that intermittently
    corrupts the shard round-trip (the documented full-suite flake). The
    "module-reference monkeypatch pattern" alone is insufficient because
    `upload_mod` ITSELF is a stale object after a reload; refreshing here keeps
    the test refs and the production code on the same module instance.
    """
    import importlib

    global upload_mod, upload_cluster, NotebookLMCapacityError
    global Cluster, ClusterRegistry, NotebookShard, NotebookHandle, UploadResult
    upload_mod = importlib.import_module("research_hub.notebooklm.upload")
    upload_cluster = upload_mod.upload_cluster
    NotebookLMCapacityError = upload_mod.NotebookLMCapacityError
    _clusters = importlib.import_module("research_hub.clusters")
    Cluster = _clusters.Cluster
    ClusterRegistry = _clusters.ClusterRegistry
    NotebookShard = _clusters.NotebookShard
    _client = importlib.import_module("research_hub.notebooklm.client")
    NotebookHandle = _client.NotebookHandle
    UploadResult = _client.UploadResult
    yield


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
    )


def _cluster(cfg, slug: str = "alpha") -> Cluster:
    cluster = Cluster(slug=slug, name="Alpha Cluster")
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters[slug] = cluster
    registry.save()
    return cluster


def _entry(index: int, **extra) -> dict:
    payload = {
        "action": "url",
        "url": f"https://example.com/paper-{index:03d}",
        "doi": f"10.1000/{index:03d}",
        "title": f"Paper {index:03d}",
        "ingested_at": f"2026-01-{(index // 24) + 1:02d}T{index % 24:02d}:00:00Z",
        "citation_count": index,
    }
    payload.update(extra)
    return payload


def _write_bundle(cfg, cluster_slug: str, entries: list[dict]) -> Path:
    bundle_dir = cfg.research_hub_dir / "bundles" / f"{cluster_slug}-20260513T000000Z"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return bundle_dir


def _dois(entries: list[dict]) -> list[str]:
    return [entry["doi"] for entry in entries]


def test_cluster_with_50_sources_has_no_overflow(tmp_path):
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(50)])

    report = upload_cluster(cluster, cfg, dry_run=True)

    assert report.success_count == 50
    assert report.over_cap_skipped == []
    assert report.over_cap_strategy == "fail"


def test_cluster_with_51_sources_default_raises_capacity_error(tmp_path):
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(51)])

    with pytest.raises(NotebookLMCapacityError) as excinfo:
        upload_cluster(cluster, cfg, dry_run=True)

    message = str(excinfo.value)
    assert "Alpha Cluster" in message
    assert "1 source over" in message
    assert "--over-cap-strategy shard" in message


def test_top_n_recent_uploads_50_newest_and_records_10_skipped(tmp_path):
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(60)])

    report = upload_cluster(cluster, cfg, dry_run=True, over_cap_strategy="top-n-recent")

    assert report.success_count == 50
    assert len(report.over_cap_skipped) == 10
    assert report.uploaded[0].path_or_url == "https://example.com/paper-059"
    assert set(_dois(report.over_cap_skipped)) == {f"10.1000/{i:03d}" for i in range(10)}


def test_top_n_cited_uploads_50_highest_cited_and_records_10_skipped(tmp_path):
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    entries = [_entry(i, citation_count=1000 - i) for i in range(60)]
    _write_bundle(cfg, cluster.slug, entries)

    report = upload_cluster(cluster, cfg, dry_run=True, over_cap_strategy="top-n-cited")

    assert report.success_count == 50
    assert len(report.over_cap_skipped) == 10
    assert report.uploaded[0].path_or_url == "https://example.com/paper-000"
    assert set(_dois(report.over_cap_skipped)) == {f"10.1000/{i:03d}" for i in range(50, 60)}


def test_fit_score_uploads_50_highest_fit_scores_and_records_10_skipped(tmp_path):
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(60)])
    fit_dir = cfg.hub / cluster.slug
    fit_dir.mkdir(parents=True)
    (fit_dir / ".fit_check_accepted.json").write_text(
        json.dumps({"accepted": [{"doi": f"10.1000/{i:03d}", "score": i} for i in range(60)]}),
        encoding="utf-8",
    )

    report = upload_cluster(cluster, cfg, dry_run=True, over_cap_strategy="fit-score")

    assert report.success_count == 50
    assert len(report.over_cap_skipped) == 10
    assert report.uploaded[0].path_or_url == "https://example.com/paper-059"
    assert set(_dois(report.over_cap_skipped)) == {f"10.1000/{i:03d}" for i in range(10)}


class FakeNotebookLMClient:
    def __init__(self) -> None:
        self.active_notebook_id = ""
        self.handles: list[NotebookHandle] = []
        self.uploads: list[tuple[str, str]] = []

    def find_or_create_notebook(self, name: str) -> NotebookHandle:
        notebook_id = f"nb-{len(self.handles) + 1}"
        handle = NotebookHandle(
            name=name,
            url=f"https://notebooklm.google.com/notebook/{notebook_id}",
            notebook_id=notebook_id,
        )
        self.handles.append(handle)
        return handle

    def set_active_notebook(self, notebook_id: str) -> None:
        self.active_notebook_id = notebook_id

    def upload_url(self, url: str) -> UploadResult:
        self.uploads.append((self.active_notebook_id, url))
        return UploadResult(source_kind="url", path_or_url=url, success=True)

    def list_sources(self, _notebook_id: str) -> list:
        return []

    def close(self) -> None:
        return None


def test_shard_strategy_materializes_three_notebooks_for_110_sources(tmp_path, monkeypatch):
    """v0.88.1 fix: switched from `monkeypatch.setattr("research_hub...string", ...)`
    to `monkeypatch.setattr(upload_mod, ...)` with the real module reference.
    The string-path form caused pytest to resolve the module path via
    importlib at fixture time, and in some test orderings the resolved
    module object was a different instance than the one held by
    `_upload_cluster_shards`'s globals — so the patch landed on a stale
    sys.modules entry while the live function kept the original
    _make_client. Resolving the module ourselves removes that ambiguity."""
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(110)])
    fake_client = FakeNotebookLMClient()
    monkeypatch.setattr(upload_mod, "_make_client", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(upload_mod, "NotebookLMClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(upload_mod.time, "sleep", lambda _seconds: None)
    # Sanity assertion: confirm patches landed on the SAME module instance
    # _upload_cluster_shards reads from. If sys.modules has drifted, this
    # fires immediately rather than silently constructing a real client.
    assert upload_mod._make_client is not None
    assert upload_mod._make_client(None, headless=True) is fake_client

    report = upload_cluster(cluster, cfg, over_cap_strategy="shard", shard_size=50)

    loaded = ClusterRegistry(cfg.clusters_file).get(cluster.slug)
    assert report.success_count == 110
    assert [shard.source_count for shard in loaded.notebooklm_shards] == [50, 50, 10]
    assert [shard.notebook_name for shard in loaded.notebooklm_shards] == [
        "Alpha Cluster [1/3]",
        "Alpha Cluster [2/3]",
        "Alpha Cluster [3/3]",
    ]


def test_sharding_preserves_doi_uniqueness_across_shards(tmp_path, monkeypatch):
    """v0.88.1 fix: same module-reference monkeypatch pattern as the
    sibling shard test — bypasses the string-path import-resolution
    ambiguity that caused full-suite flakes."""
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(110)])
    fake_client = FakeNotebookLMClient()
    monkeypatch.setattr(upload_mod, "_make_client", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(upload_mod, "NotebookLMClient", lambda *_args, **_kwargs: fake_client)
    monkeypatch.setattr(upload_mod.time, "sleep", lambda _seconds: None)

    upload_cluster(cluster, cfg, over_cap_strategy="shard", shard_size=50)

    loaded = ClusterRegistry(cfg.clusters_file).get(cluster.slug)
    doi_list = [doi for shard in loaded.notebooklm_shards for doi in shard.source_doi_list]
    assert len(doi_list) == 110
    assert len(set(doi_list)) == 110


def test_notebook_shard_round_trips_through_clusters_yaml(tmp_path):
    cfg = _cfg(tmp_path)
    registry = ClusterRegistry(cfg.clusters_file)
    registry.clusters["alpha"] = Cluster(
        slug="alpha",
        name="Alpha",
        notebooklm_shards=[
            NotebookShard(
                notebook_id="nb-1",
                notebook_url="https://notebooklm.google.com/notebook/nb-1",
                notebook_name="Alpha [1/1]",
                source_count=2,
                source_doi_list=["10.1000/a", "10.1000/b"],
                created_at="2026-05-13T00:00:00Z",
            )
        ],
    )
    registry.save()

    loaded = ClusterRegistry(cfg.clusters_file).get("alpha")

    assert isinstance(loaded.notebooklm_shards[0], NotebookShard)
    assert loaded.notebooklm_shards[0].source_doi_list == ["10.1000/a", "10.1000/b"]


@pytest.mark.parametrize("strategy", ["fail", "top-n-recent", "shard"])
def test_cli_parses_upload_over_cap_strategies(strategy):
    from research_hub.cli import build_parser

    args = build_parser().parse_args(
        ["notebooklm", "upload", "--cluster", "alpha", "--over-cap-strategy", strategy]
    )

    assert args.notebooklm_command == "upload"
    assert args.over_cap_strategy == strategy


def test_cli_default_upload_over_cap_strategy_is_fail():
    from research_hub.cli import build_parser

    args = build_parser().parse_args(["notebooklm", "upload", "--cluster", "alpha"])

    assert args.over_cap_strategy == "fail"
    assert args.shard_size == 50


def test_cli_parses_notebooklm_shard_command():
    from research_hub.cli import build_parser

    args = build_parser().parse_args(
        ["notebooklm", "shard", "--cluster", "alpha", "--strategy", "recent", "--shard-size", "25"]
    )

    assert args.notebooklm_command == "shard"
    assert args.strategy == "recent"
    assert args.shard_size == 25


def test_save_nlm_cache_is_atomic_and_makes_parents(tmp_path):
    """WF-4: cache writes go through atomic_write_text (tmp + os.replace) so a
    crash mid-write cannot leave a half-written file that _load degrades to {}."""
    cache_path = tmp_path / "nested" / "nlm_cache.json"
    upload_mod._save_nlm_cache(cache_path, {"alpha": {"uploaded_sources": ["x"]}})

    assert json.loads(cache_path.read_text(encoding="utf-8"))["alpha"]["uploaded_sources"] == ["x"]
    # parent dir created, and NO leftover .tmp file from the atomic write
    assert [p.name for p in cache_path.parent.iterdir()] == ["nlm_cache.json"]


def test_shard_upload_checkpoints_cache_after_each_shard(tmp_path, monkeypatch):
    """STAB-3 (sharded path): the resume cache is flushed after EACH shard, so a
    crash before later shards complete keeps earlier shards' uploaded sources on
    disk instead of forcing a full re-upload against NotebookLM's hard cap."""
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(110)])

    class CrashAfterFirstShard(FakeNotebookLMClient):
        def find_or_create_notebook(self, name: str) -> NotebookHandle:
            # blow up opening the SECOND shard notebook -> simulates an OS-kill
            # after shard 1 fully uploaded but before shard 2 completes
            if len(self.handles) >= 1:
                raise RuntimeError("simulated crash opening shard 2")
            return super().find_or_create_notebook(name)

    fake = CrashAfterFirstShard()
    monkeypatch.setattr(upload_mod, "_make_client", lambda *_a, **_k: fake)
    monkeypatch.setattr(upload_mod, "NotebookLMClient", lambda *_a, **_k: fake)
    monkeypatch.setattr(upload_mod.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="shard 2"):
        upload_cluster(cluster, cfg, over_cap_strategy="shard", shard_size=50)

    cache = json.loads((cfg.research_hub_dir / "nlm_cache.json").read_text(encoding="utf-8"))
    shard_sources = cache[cluster.slug]["shard_uploaded_sources"]
    # exactly shard 1 persisted, with all 50 of its sources (would be {} pre-fix)
    assert len(shard_sources) == 1
    first_shard_key = next(iter(shard_sources))
    assert "[1/3]" in first_shard_key
    assert len(shard_sources[first_shard_key]) == 50


def test_nonsharded_upload_checkpoints_each_successful_source(tmp_path, monkeypatch):
    """STAB-3 (non-sharded path): uploaded_sources is flushed after each success,
    so a crash mid-loop preserves prior progress instead of re-uploading all."""
    cfg = _cfg(tmp_path)
    cluster = _cluster(cfg)
    _write_bundle(cfg, cluster.slug, [_entry(i) for i in range(10)])
    fake = FakeNotebookLMClient()
    monkeypatch.setattr(upload_mod, "_make_client", lambda *_a, **_k: fake)
    monkeypatch.setattr(upload_mod, "NotebookLMClient", lambda *_a, **_k: fake)

    # time.sleep runs once per source AFTER its checkpoint; raise on the 3rd to
    # simulate an OS-kill after 3 sources have been uploaded + checkpointed.
    calls = {"n": 0}

    def crashing_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("simulated crash mid-upload-loop")

    monkeypatch.setattr(upload_mod.time, "sleep", crashing_sleep)

    with pytest.raises(RuntimeError, match="mid-upload-loop"):
        upload_cluster(cluster, cfg)  # 10 < cap -> non-sharded path

    cache = json.loads((cfg.research_hub_dir / "nlm_cache.json").read_text(encoding="utf-8"))
    uploaded = cache[cluster.slug]["uploaded_sources"]
    # 3 sources checkpointed before the crash (would be [] pre-fix)
    assert len(uploaded) == 3
