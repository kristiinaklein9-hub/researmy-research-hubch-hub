"""NotebookLM download artifact mirror helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_hub.notebooklm.client import BriefingArtifact
from research_hub.vault.hub_overview import derive_moc_links, populate_overview


def mirror_brief_and_populate_overview(
    *,
    cluster: Any,
    vault_root: Path,
    artifact: BriefingArtifact,
    archive_path: Path,
    generated_at: datetime,
    source_doi_list: list[str] | None = None,
) -> Path:
    """Write the in-vault markdown mirror and refresh the cluster overview."""

    cluster_slug = str(getattr(cluster, "slug", cluster))
    brief_md_path = write_brief_markdown_mirror(
        cluster_slug=cluster_slug,
        vault_root=vault_root,
        artifact=artifact,
        archive_path=archive_path,
        generated_at=generated_at,
        source_doi_list=source_doi_list,
    )
    cluster_queries = [str(getattr(cluster, "first_query", "") or "")]
    moc_links = derive_moc_links(
        cluster_slug,
        cluster_queries=cluster_queries,
        moc_links=list(getattr(cluster, "moc_links", []) or []),
    )
    populate_overview(
        cluster_slug=cluster_slug,
        vault_root=vault_root,
        brief_md_path=brief_md_path,
        moc_links=moc_links,
    )
    return brief_md_path


def write_brief_markdown_mirror(
    *,
    cluster_slug: str,
    vault_root: Path,
    artifact: BriefingArtifact,
    archive_path: Path,
    generated_at: datetime,
    source_doi_list: list[str] | None = None,
) -> Path:
    """Write ``hub/<cluster>/notebooklm-brief-<ts>.md``."""

    root = Path(vault_root)
    ts = archive_path.stem.removeprefix("brief-")
    brief_md_path = root / "hub" / cluster_slug / f"notebooklm-brief-{ts}.md"
    brief_md_path.parent.mkdir(parents=True, exist_ok=True)
    relative_archive = os.path.relpath(archive_path, start=brief_md_path.parent).replace("\\", "/")
    doi_list = source_doi_list if source_doi_list is not None else source_dois_for_cluster(root, cluster_slug)
    generated_iso = _iso8601_utc(generated_at)
    body = artifact.text
    frontmatter = "\n".join(
        [
            "---",
            "type: notebooklm-brief",
            f"cluster: {cluster_slug}",
            f"generated_at: {generated_iso}",
            f"source_count: {int(artifact.source_count or 0)}",
            f"source_doi_list: {json.dumps(doi_list, ensure_ascii=False)}",
            f"nlm_notebook_url: {_yaml_scalar(artifact.notebook_url)}",
            f"brief_archive_path: {relative_archive}",
            f'tags: {json.dumps([f"topic:{cluster_slug}", "type:notebooklm-brief"])}',
            "---",
            "",
        ]
    )
    brief_md_path.write_text(
        frontmatter + body + ("" if body.endswith("\n") else "\n"),
        encoding="utf-8",
    )
    return brief_md_path


def source_dois_for_cluster(vault_root: Path, cluster_slug: str) -> list[str]:
    """Read DOI values from ``raw/<cluster_slug>/*.md`` frontmatter."""

    raw_dir = Path(vault_root) / "raw" / cluster_slug
    if not raw_dir.exists():
        return []
    dois: list[str] = []
    for note_path in sorted(raw_dir.glob("*.md")):
        doi = _doi_from_note(note_path)
        if doi and doi not in dois:
            dois.append(doi)
    return dois


def _doi_from_note(note_path: Path) -> str:
    try:
        text = note_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    for raw_line in text[4:end].splitlines():
        line = raw_line.strip()
        if line.startswith("doi:"):
            return line.partition(":")[2].strip().strip('"').strip("'")
    return ""


def _iso8601_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml_scalar(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean else '""'
