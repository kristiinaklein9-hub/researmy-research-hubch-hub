"""v0.88.1 §B — paper retype: change Zotero itemType via create + trash."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from research_hub.paper import retype_paper


def _seed_note(vault: Path, slug: str, zotero_key: str, cluster: str = "demo") -> Path:
    raw = vault / "raw" / cluster
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / f"{slug}.md"
    path.write_text(
        f'---\ntitle: "Test paper"\ntopic_cluster: "{cluster}"\nzotero-key: {zotero_key}\n---\n# {slug}\n',
        encoding="utf-8",
    )
    return path


def _cfg(vault: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=vault,
        raw=vault / "raw",
        hub=vault / "hub",
        research_hub_dir=vault / ".research_hub",
        clusters_file=vault / ".research_hub" / "clusters.yaml",
    )


class _FakeZotero:
    """Minimal pyzotero stub for retype tests. Mirrors the actual API
    shapes that retype_paper consumes (`item`, `item_template`,
    `create_items`, `delete_item`)."""

    def __init__(self, *, item_data: dict, target_template: dict):
        self._item_data = item_data
        self._target_template = target_template
        self.created: list[dict] = []
        self.trashed: list[str] = []
        self._next_key = "NEWKEY01"

    def item(self, key: str) -> dict:
        return {"data": self._item_data, "key": key}

    def item_template(self, item_type: str) -> dict:
        return dict(self._target_template)

    def create_items(self, batch: list[dict]) -> dict:
        self.created.append(batch[0])
        return {"success": {"0": self._next_key}}

    def trash_item(self, item: dict) -> bool:
        self.trashed.append(item.get("key", item.get("data", {}).get("key", "")))
        return True

    # research-hub's _trash_zotero_item also tries `delete_item` as a
    # fallback. Mirror that here for completeness.
    def delete_item(self, item: dict) -> bool:
        self.trashed.append(item.get("key", item.get("data", {}).get("key", "")))
        return True


@pytest.fixture
def fake_zot(monkeypatch):
    """Inject a controllable _FakeZotero into research_hub.paper helpers."""
    holder = {}

    def _stash_and_return(zot):
        holder["client"] = zot
        return zot

    # We monkeypatch the GETTER so retype_paper picks up our fake.
    def _set_fake(item_data: dict, target_template: dict):
        zot = _FakeZotero(item_data=item_data, target_template=target_template)
        monkeypatch.setattr(
            "research_hub.paper._get_zotero_web_client",
            lambda: zot,
        )
        monkeypatch.setattr(
            "research_hub.paper._trash_zotero_item",
            lambda z, key: (z.trashed.append(key) or True),
        )
        monkeypatch.setattr(
            "research_hub.paper._rebuild_dedup_index",
            lambda cfg: None,
        )
        return zot

    return _set_fake


def test_retype_dry_run_does_not_create_or_trash(tmp_path: Path, fake_zot) -> None:
    _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    zot = fake_zot(
        item_data={
            "itemType": "journalArticle",
            "title": "T",
            "publicationTitle": "arXiv",
            "DOI": "10.1/x",
            "tags": [],
            "creators": [],
            "collections": [],
        },
        target_template={
            "itemType": "conferencePaper",
            "title": "",
            "proceedingsTitle": "",
            "DOI": "",
        },
    )
    report = retype_paper(cfg, "p2024-a", target_type="conferencePaper", dry_run=True)
    assert report["from_type"] == "journalArticle"
    assert report["to_type"] == "conferencePaper"
    assert report["old_zotero_key"] == "OLDKEY01"
    assert report["new_zotero_key"] == ""
    # Venue cross-translation captured in fields_copied
    assert "publicationTitle->proceedingsTitle" in report["fields_copied"]
    assert zot.created == []
    assert zot.trashed == []


def test_retype_apply_creates_new_trashes_old_rewrites_key(tmp_path: Path, fake_zot) -> None:
    note = _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    zot = fake_zot(
        item_data={
            "itemType": "journalArticle",
            "title": "T",
            "publicationTitle": "ASCE",
            "DOI": "10.1061/x",
            "tags": [{"tag": "topic:demo"}],
            "creators": [{"creatorType": "author", "lastName": "Smith"}],
            "collections": ["COLL01"],
        },
        target_template={
            "itemType": "conferencePaper",
            "title": "",
            "proceedingsTitle": "",
            "DOI": "",
        },
    )
    report = retype_paper(cfg, "p2024-a", target_type="conferencePaper", dry_run=False)
    assert not report["errors"], report
    assert report["new_zotero_key"] == "NEWKEY01"
    assert zot.trashed == ["OLDKEY01"]
    assert len(zot.created) == 1
    new_data = zot.created[0]
    assert new_data["itemType"] == "conferencePaper"
    assert new_data["proceedingsTitle"] == "ASCE"
    assert new_data["creators"] == [{"creatorType": "author", "lastName": "Smith"}]
    # Note frontmatter rewritten with new key (quoted form is acceptable)
    text = note.read_text(encoding="utf-8")
    assert "NEWKEY01" in text
    assert "OLDKEY01" not in text


def test_retype_to_dataset_blanks_venue_and_drops_unsupported(tmp_path: Path, fake_zot) -> None:
    _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    fake_zot(
        item_data={
            "itemType": "journalArticle",
            "title": "Dataset deposit",
            "publicationTitle": "Open MIND",
            "DOI": "10.5281/zenodo.123",
            "volume": "1",
            "issue": "2",
            "pages": "10-20",
            "tags": [],
            "creators": [],
            "collections": [],
        },
        # dataset template has no proceedingsTitle / publicationTitle /
        # volume / issue / pages; those should land in fields_dropped.
        target_template={
            "itemType": "dataset",
            "title": "",
            "DOI": "",
            "abstractNote": "",
        },
    )
    report = retype_paper(cfg, "p2024-a", target_type="dataset", dry_run=False)
    assert report["to_type"] == "dataset"
    # dataset has no venue field in venue_field_map → publicationTitle DROPPED
    assert "publicationTitle" in report["fields_dropped"]
    # volume/issue/pages also dropped (not in target template)
    assert "volume" in report["fields_dropped"]
    assert "issue" in report["fields_dropped"]
    assert "pages" in report["fields_dropped"]


def test_retype_target_type_unchanged_returns_error(tmp_path: Path, fake_zot) -> None:
    _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    fake_zot(
        item_data={"itemType": "journalArticle", "title": "T", "creators": [], "tags": [], "collections": []},
        target_template={"itemType": "journalArticle"},
    )
    report = retype_paper(cfg, "p2024-a", target_type="journalArticle", dry_run=True)
    assert any("already the target itemType" in e for e in report["errors"])


def test_retype_missing_slug_returns_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw.mkdir(parents=True, exist_ok=True)
    report = retype_paper(cfg, "missing-slug", target_type="conferencePaper", dry_run=True)
    assert any("note not found" in e for e in report["errors"])


def test_retype_missing_zotero_key_returns_error(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "demo"
    raw.mkdir(parents=True)
    (raw / "p2024-a.md").write_text(
        '---\ntitle: "T"\ntopic_cluster: "demo"\nzotero-key: \n---\n# x\n',
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path)
    report = retype_paper(cfg, "p2024-a", target_type="conferencePaper", dry_run=True)
    assert any("zotero-key" in e for e in report["errors"])


def test_retype_unknown_target_type_returns_error(tmp_path: Path, fake_zot, monkeypatch) -> None:
    _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    zot = fake_zot(
        item_data={"itemType": "journalArticle", "title": "T", "creators": [], "tags": [], "collections": []},
        target_template={},
    )

    def bad_template(*_a, **_k):
        raise ValueError("Invalid itemType 'banana'")

    monkeypatch.setattr(zot, "item_template", bad_template)
    report = retype_paper(cfg, "p2024-a", target_type="banana", dry_run=True)
    assert any("unknown itemType" in e for e in report["errors"])


def test_retype_preserves_collections_and_creators(tmp_path: Path, fake_zot) -> None:
    """Critical: the new item must inherit the cluster collection
    + creator list verbatim so the paper doesn't lose its placement."""
    _seed_note(tmp_path, "p2024-a", zotero_key="OLDKEY01", cluster="demo")
    cfg = _cfg(tmp_path)
    zot = fake_zot(
        item_data={
            "itemType": "journalArticle",
            "title": "T",
            "creators": [
                {"creatorType": "author", "lastName": "A"},
                {"creatorType": "author", "lastName": "B"},
            ],
            "tags": [{"tag": "topic:demo"}, {"tag": "research-hub"}],
            "collections": ["6ZANW2CZ", "HJPFXWMF"],
        },
        target_template={"itemType": "conferencePaper", "title": ""},
    )
    retype_paper(cfg, "p2024-a", target_type="conferencePaper", dry_run=False)
    new = zot.created[0]
    assert new["creators"] == [
        {"creatorType": "author", "lastName": "A"},
        {"creatorType": "author", "lastName": "B"},
    ]
    assert new["tags"] == [{"tag": "topic:demo"}, {"tag": "research-hub"}]
    assert new["collections"] == ["6ZANW2CZ", "HJPFXWMF"]
