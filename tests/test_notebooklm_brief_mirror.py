from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from research_hub.clusters import Cluster
from research_hub.notebooklm.client import BriefingArtifact, NotebookHandle
from research_hub.notebooklm.upload import download_briefing_for_cluster


def _cfg(tmp_path: Path) -> SimpleNamespace:
    research_hub_dir = tmp_path / ".research_hub"
    research_hub_dir.mkdir(parents=True)
    (tmp_path / "raw" / "alpha").mkdir(parents=True)
    (tmp_path / "hub" / "alpha").mkdir(parents=True)
    return SimpleNamespace(
        root=tmp_path,
        raw=tmp_path / "raw",
        hub=tmp_path / "hub",
        research_hub_dir=research_hub_dir,
        clusters_file=research_hub_dir / "clusters.yaml",
    )


def _write_source_note(cfg: SimpleNamespace, stem: str, doi: str) -> None:
    (cfg.raw / "alpha" / f"{stem}.md").write_text(
        f"""---
title: "{stem}"
year: 2026
authors: "Adams, A."
doi: "{doi}"
---

# {stem}
""",
        encoding="utf-8",
    )


def _download(tmp_path: Path, monkeypatch) -> tuple[SimpleNamespace, object]:
    from research_hub.notebooklm import upload as upload_mod

    cfg = _cfg(tmp_path)
    _write_source_note(cfg, "paper-one", "10.123/one")
    _write_source_note(cfg, "paper-two", "10.123/two")
    cluster = Cluster(
        slug="alpha",
        name="Alpha",
        notebooklm_notebook="Alpha Notebook",
        notebooklm_notebook_url="https://notebooklm.google.com/notebook/abc",
    )

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def open_notebook_by_name(self, name):
            return NotebookHandle(name=name, url="https://notebooklm.google.com/notebook/abc", notebook_id="abc")

        def download_briefing(self, handle):
            return BriefingArtifact(
                notebook_name=handle.name,
                notebook_url=handle.url,
                notebook_id=handle.notebook_id,
                text="# Executive Summary\n\nThis is a cluster brief.\n",
                titles=["Briefing Doc"],
                source_count=2,
            )

        def close(self):
            return None

    monkeypatch.setattr(upload_mod, "NotebookLMClient", FakeClient)
    report = download_briefing_for_cluster(cluster, cfg, headless=True)
    return cfg, report


def test_brief_md_mirror_has_canonical_frontmatter(tmp_path, monkeypatch):
    _cfg_obj, report = _download(tmp_path, monkeypatch)

    assert report.brief_md_path is not None
    text = report.brief_md_path.read_text(encoding="utf-8")
    assert text.startswith("---\ntype: notebooklm-brief\ncluster: alpha\n")
    assert re.search(r"generated_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)
    assert 'source_doi_list: ["10.123/one", "10.123/two"]' in text
    assert "source_count: 2" in text
    assert "nlm_notebook_url: https://notebooklm.google.com/notebook/abc" in text
    assert 'tags: ["topic:alpha", "type:notebooklm-brief"]' in text
    assert "# Executive Summary\n\nThis is a cluster brief." in text


def test_brief_md_links_to_txt_archive_relative(tmp_path, monkeypatch):
    _cfg_obj, report = _download(tmp_path, monkeypatch)

    assert report.brief_md_path is not None
    text = report.brief_md_path.read_text(encoding="utf-8")
    expected = f"brief_archive_path: ../../.research_hub/artifacts/alpha/{report.artifact_path.name}"
    assert expected in text


def test_overview_gets_brief_link_after_mirror(tmp_path, monkeypatch):
    cfg, report = _download(tmp_path, monkeypatch)

    assert report.brief_md_path is not None
    overview = cfg.hub / "alpha" / "00_overview.md"
    text = overview.read_text(encoding="utf-8")
    assert f"[[{report.brief_md_path.stem}]]" in text
    assert "## NotebookLM brief" in text
