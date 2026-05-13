"""v0.87.1 #6 — backfill `topic:<slug>` tags into existing paper-note frontmatter.

V4 audit (Agent 4 post-v0.87) found that all 35 existing paper notes
have `tags: []` empty in frontmatter, even though `topic_cluster:`
is set. Obsidian's tag pane and graph color-group both read the
`tags:` array — `topic_cluster:` lives in Properties as a generic
field and does NOT activate tag-based UI.

This module backfills `topic:<topic_cluster>` into every paper
note's `tags` array, preserving any user-added tags and skipping
notes that already have the tag.

Decision locked in V088_PLAN.md Q1: `topic:` prefix (NOT `cluster/`).
The legacy `cluster/<slug>` tag emitted by older ingest paths is
deferred to a separate normalization pass — this migration only ADDS
the new tag, it does not remove anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_FRONTMATTER_RE = re.compile(r"\A(---\n)(.*?)(\n---\n?)", re.DOTALL)
_TOPIC_CLUSTER_RE = re.compile(r'^topic_cluster:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)
_TAGS_LINE_RE = re.compile(r'^(tags:\s*)(\[.*?\])\s*$', re.MULTILINE)


@dataclass(frozen=True)
class TagMigrationResult:
    path: Path
    action: str  # "added" | "already_present" | "skipped_no_topic_cluster" | "skipped_no_frontmatter" | "skipped_no_tags_line"
    topic_tag: str = ""


def _parse_tags_line(line: str) -> list[str]:
    """Parse the inline-list YAML form `tags: ["a", "b"]` into a list of strings."""
    inner = line.strip()
    if not inner.startswith("[") or not inner.endswith("]"):
        return []
    body = inner[1:-1].strip()
    if not body:
        return []
    parts: list[str] = []
    for raw in body.split(","):
        token = raw.strip()
        if token.startswith('"') and token.endswith('"'):
            token = token[1:-1]
        elif token.startswith("'") and token.endswith("'"):
            token = token[1:-1]
        if token:
            parts.append(token)
    return parts


def _render_tags_line(tags: list[str]) -> str:
    """Render a list back into the inline-list YAML form."""
    if not tags:
        return "[]"
    return "[" + ", ".join(f'"{t}"' for t in tags) + "]"


def migrate_one_note(path: Path) -> TagMigrationResult:
    """Ensure `topic:<topic_cluster>` is present in `tags:` for one note.

    Returns a TagMigrationResult describing what happened. Idempotent:
    re-running on an already-migrated note returns action="already_present".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return TagMigrationResult(path=path, action="skipped_no_frontmatter")

    frontmatter_match = _FRONTMATTER_RE.search(text)
    if not frontmatter_match:
        return TagMigrationResult(path=path, action="skipped_no_frontmatter")

    fm_body = frontmatter_match.group(2)
    topic_match = _TOPIC_CLUSTER_RE.search(fm_body)
    if not topic_match:
        return TagMigrationResult(path=path, action="skipped_no_topic_cluster")
    topic_cluster = topic_match.group(1).strip()
    if not topic_cluster:
        return TagMigrationResult(path=path, action="skipped_no_topic_cluster")
    topic_tag = f"topic:{topic_cluster}"

    tags_match = _TAGS_LINE_RE.search(fm_body)
    if not tags_match:
        return TagMigrationResult(path=path, action="skipped_no_tags_line", topic_tag=topic_tag)

    current_tags = _parse_tags_line(tags_match.group(2))
    if topic_tag in current_tags:
        return TagMigrationResult(path=path, action="already_present", topic_tag=topic_tag)

    new_tags = current_tags + [topic_tag]
    new_line = tags_match.group(1) + _render_tags_line(new_tags)
    new_fm_body = fm_body[: tags_match.start()] + new_line + fm_body[tags_match.end():]
    new_text = (
        frontmatter_match.group(1) + new_fm_body + frontmatter_match.group(3) + text[frontmatter_match.end():]
    )
    path.write_text(new_text, encoding="utf-8")
    return TagMigrationResult(path=path, action="added", topic_tag=topic_tag)


def migrate_all(
    vault_root: Path,
    *,
    cluster_slug_filter: str | None = None,
    dry_run: bool = False,
) -> list[TagMigrationResult]:
    """Walk raw/*/*.md and ensure `topic:<topic_cluster>` is present in each."""
    raw_root = vault_root / "raw"
    if not raw_root.exists():
        return []
    results: list[TagMigrationResult] = []
    pattern = f"{cluster_slug_filter}/*.md" if cluster_slug_filter else "*/*.md"
    for note in sorted(raw_root.glob(pattern)):
        if dry_run:
            # simulate the same code path without writing — read state, decide action
            try:
                text = note.read_text(encoding="utf-8")
            except OSError:
                results.append(TagMigrationResult(path=note, action="skipped_no_frontmatter"))
                continue
            fm = _FRONTMATTER_RE.search(text)
            if not fm:
                results.append(TagMigrationResult(path=note, action="skipped_no_frontmatter"))
                continue
            tc = _TOPIC_CLUSTER_RE.search(fm.group(2))
            if not tc or not tc.group(1).strip():
                results.append(TagMigrationResult(path=note, action="skipped_no_topic_cluster"))
                continue
            topic_tag = f"topic:{tc.group(1).strip()}"
            tl = _TAGS_LINE_RE.search(fm.group(2))
            if not tl:
                results.append(TagMigrationResult(path=note, action="skipped_no_tags_line", topic_tag=topic_tag))
                continue
            current = _parse_tags_line(tl.group(2))
            action = "already_present" if topic_tag in current else "added"
            results.append(TagMigrationResult(path=note, action=action, topic_tag=topic_tag))
        else:
            results.append(migrate_one_note(note))
    return results
