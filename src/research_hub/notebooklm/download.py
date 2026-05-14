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
        force_rebuild=True,
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
    # v0.88 #6: prepend TL;DR + cluster backlink before the synthesis body so
    # iPhone users don't have to scroll past 10 KB of generated headings
    # before they know what they're reading, and the brief can be navigated
    # back up to its cluster.
    tldr_block = _build_tldr_and_cluster_block(artifact, cluster_slug)
    # v0.88.11: also strip the legacy archive-header block from the body
    # (cluster-title H1 + Source:/Downloaded:/Sources:/Saved briefings:
    # lines). v0.88.3 added the TL;DR but left the redundant header in
    # place so every brief had the metadata duplicated above and below
    # the cluster pointer — wasting iPhone screen real estate.
    body = _strip_archive_header(body)
    brief_md_path.write_text(
        frontmatter + tldr_block + body + ("" if body.endswith("\n") else "\n"),
        encoding="utf-8",
    )
    return brief_md_path


def _strip_archive_header(body: str) -> str:
    """v0.88.11: drop the NotebookLM archive metadata block from the
    front of a brief body.

    Archive form (deterministic, set by notebooklm-py's downloader):

        # <Notebook Title>
        Source: https://notebooklm.google.com/notebook/<id>
        Downloaded: <ts>
        Sources: <n>
        Saved briefings: <list>

        # <Actual Synthesis Title>
        ...

    We want the second H1 onward — the first H1 is just the notebook
    name (already in `## TL;DR` and `**Cluster:**`), and the metadata
    is fully captured in YAML frontmatter.

    Conservative: if the header doesn't match the expected shape,
    return the body unchanged.
    """
    import re as _re

    text = body or ""
    if not text.lstrip().startswith("# "):
        return body  # not the archive shape
    lines = text.splitlines()
    # Look for the first H1, then a contiguous block of metadata lines
    # (Source:/Downloaded:/Sources:/Saved briefings:/Notebook:/Generated:),
    # then a blank line, then the next H1. Strip everything from start
    # up to (but not including) that next H1.
    metadata_re = _re.compile(
        r"^(Source|Downloaded|Sources|Saved briefings?|Notebook|Generated)\s*:",
        _re.IGNORECASE,
    )
    saw_first_h1 = False
    saw_metadata = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not saw_first_h1:
            if stripped.startswith("# "):
                saw_first_h1 = True
            continue
        # After first H1 — expect either metadata or a second H1
        if metadata_re.match(stripped):
            saw_metadata = True
            continue
        if not stripped:
            continue  # tolerate blank lines inside the metadata block
        if stripped.startswith("# ") and saw_metadata:
            # Found the start of real synthesis content
            return "\n".join(lines[idx:])
        # Anything else means we don't recognize this shape — bail out
        return body
    return body


def _build_tldr_and_cluster_block(artifact, cluster_slug: str) -> str:
    """Extract the first 3-5 sentences of the brief as TL;DR (capped 500 chars).

    Pulls from the NLM brief's `Executive Summary` block when present, else
    from the brief's opening paragraph. Always followed by an explicit
    `**Cluster:**` backlink wikilink.
    """
    cluster_line = f"**Cluster:** [[{cluster_slug}/00_overview|{cluster_slug}]]"

    text = (getattr(artifact, "text", "") or "").strip()
    # Try to find an Executive Summary / Overview section first.
    summary_text = _find_executive_summary(text) or _first_paragraph(text)
    if not summary_text:
        return f"\n## TL;DR\n\n_(brief body has no extractable summary yet)_\n\n{cluster_line}\n\n"

    truncated = summary_text.strip()
    if len(truncated) > 500:
        truncated = truncated[:497].rstrip() + "..."
    return f"\n## TL;DR\n\n{truncated}\n\n{cluster_line}\n\n"


def _find_executive_summary(text: str) -> str:
    """Pull the body of an `## Executive Summary` (or `## Overview`) section."""
    import re

    for heading in ("Executive Summary", "Overview", "Key Themes", "Key Findings"):
        pattern = re.compile(
            rf"^##[ \t]+{re.escape(heading)}[ \t]*\n(.*?)(?=^##[ \t]|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(text)
        if m:
            body = m.group(1).strip()
            if body:
                return body
    return ""


def _first_paragraph(text: str) -> str:
    """Fall back to the first non-heading, non-metadata paragraph of the brief.

    v0.88.3: skip the archive header block (lines like
    ``Source: <url>`` / ``Downloaded: <ts>`` / ``Sources: <n>`` /
    ``Saved briefings: <list>``) so the TL;DR shows actual synthesis
    prose, not the download receipt. Also skip table separator rows,
    bullet/list lines, and bold-only paragraphs that don't read like a
    sentence on mobile.
    """
    import re

    metadata_re = re.compile(
        r"^(Source|Downloaded|Sources|Saved briefings?|Notebook|Generated|Cluster)\s*:",
        re.IGNORECASE,
    )

    def _is_metadata_block(block: str) -> bool:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            return True
        # If >=80% of lines look like "Key: value" archive headers, skip.
        meta_hits = sum(1 for ln in lines if metadata_re.match(ln))
        return meta_hits >= max(1, int(len(lines) * 0.8))

    def _looks_like_prose(block: str) -> bool:
        text = block.strip()
        if not text:
            return False
        # Tables / dividers / bullet-only / bold-label-only — reject as TL;DR.
        if text.startswith(("|", "*", "-", "> ")):
            return False
        # Require at least one full sentence (>=20 chars and ends with .?!).
        if len(text) < 20:
            return False
        return any(text.rstrip().endswith(p) for p in (".", "?", "!", "。", "？", "！"))

    for raw in (text or "").split("\n\n"):
        block = raw.strip()
        if not block:
            continue
        # Strip leading heading lines (e.g. `### 1. Section title\n`) so a
        # block like "### Section\nProse..." still surfaces its prose body.
        prose_lines = [ln for ln in block.splitlines() if not ln.lstrip().startswith("#")]
        prose = "\n".join(prose_lines).strip()
        if not prose:
            continue
        if _is_metadata_block(prose):
            continue
        if _looks_like_prose(prose):
            return prose
    return ""


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
