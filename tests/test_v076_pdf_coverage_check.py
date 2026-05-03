from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import ClusterRegistry


def _make_cfg(tmp_path: Path, *, unpaywall_email: str = ""):
    root = tmp_path / "vault"
    raw = root / "raw"
    research_hub_dir = root / ".research_hub"
    raw.mkdir(parents=True)
    research_hub_dir.mkdir(parents=True)
    return SimpleNamespace(
        root=root,
        raw=raw,
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
        unpaywall_email=unpaywall_email,
        zotero={},
    )


def _bind_cluster(cfg, slug: str, key: str):
    registry = ClusterRegistry(cfg.clusters_file)
    registry.create(query=slug, name=slug.title(), slug=slug)
    registry.bind(slug, zotero_collection_key=key, sync_zotero=False)


def test_pdf_coverage_info_includes_config_remedy_when_email_missing(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = _make_cfg(tmp_path)
    _bind_cluster(cfg, "alpha", "COLL1")
    items = [{"key": "A1"}, {"key": "A2"}, {"key": "A3"}, {"key": "A4"}]
    pdf_keys = {"A1"}

    class _Zot:
        def children(self, item_key):
            if item_key in pdf_keys:
                return [{"data": {"itemType": "attachment", "contentType": "application/pdf"}}]
            return []

    monkeypatch.setattr("research_hub.zotero.client.get_client", lambda: _Zot())
    monkeypatch.setattr("research_hub.vault.sync.list_zotero_collection_items", lambda zot, key: items)

    result = doctor.check_cluster_pdf_coverage(cfg)

    assert result.status == "INFO"
    assert "alpha: 1/4 (25%)" in result.details
    assert "config set unpaywall_email" in result.remedy


def test_pdf_coverage_info_omits_config_remedy_when_email_present(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = _make_cfg(tmp_path, unpaywall_email="user@example.com")
    _bind_cluster(cfg, "alpha", "COLL1")
    items = [{"key": "A1"}, {"key": "A2"}]

    class _Zot:
        def children(self, item_key):
            del item_key
            return []

    monkeypatch.setattr("research_hub.zotero.client.get_client", lambda: _Zot())
    monkeypatch.setattr("research_hub.vault.sync.list_zotero_collection_items", lambda zot, key: items)

    result = doctor.check_cluster_pdf_coverage(cfg)

    assert result.status == "INFO"
    assert "config set unpaywall_email" not in result.remedy


def test_pdf_coverage_ok_when_threshold_met(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = _make_cfg(tmp_path)
    _bind_cluster(cfg, "alpha", "COLL1")
    items = [{"key": "A1"}, {"key": "A2"}, {"key": "A3"}, {"key": "A4"}]
    pdf_keys = {"A1", "A2"}

    class _Zot:
        def children(self, item_key):
            if item_key in pdf_keys:
                return [{"data": {"itemType": "attachment", "contentType": "application/pdf"}}]
            return []

    monkeypatch.setattr("research_hub.zotero.client.get_client", lambda: _Zot())
    monkeypatch.setattr("research_hub.vault.sync.list_zotero_collection_items", lambda zot, key: items)

    result = doctor.check_cluster_pdf_coverage(cfg)

    assert result.status == "OK"
    assert ">=50%" in result.message
