"""Populate cluster hub overview notes and MOC entry points."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from research_hub.security import safe_join


OVERVIEW_FILENAME = "00_overview.md"
PAPERS_HEADING = "Papers in this cluster"
BRIEF_HEADING = "NotebookLM brief"
MOC_HEADING = "Related MOCs"

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_SCAFFOLD_MARKERS = (
    "銝?啣?亥店",
    "?其??亥店撖思?",
    "TODO",
    "[TODO]",
)
_MOJIBAKE_MARKERS = (
    "銝",
    "蝭",
    "敹",
    "隢",
    "撱",
    "璁",
    "閰",
    "憿",
    "蝺",
    "箔",
    "摮",
    "瘨",
    "閬",
    "謕",
)


def populate_overview(
    *,
    cluster_slug: str,
    vault_root: Path,
    brief_md_path: Path | None = None,
    moc_links: list[str] | None = None,
) -> Path:
    """Idempotently populate ``hub/<cluster_slug>/00_overview.md``."""

    root = Path(vault_root)
    overview_path = _overview_path(root, cluster_slug)
    overview_path.parent.mkdir(parents=True, exist_ok=True)

    if overview_path.exists():
        existing = overview_path.read_text(encoding="utf-8", errors="ignore")
        preamble, section_text = _split_preamble(existing)
    else:
        preamble = _default_preamble(cluster_slug)
        section_text = ""

    existing_sections = _parse_overview_sections(section_text)
    scaffold_keys = {
        heading
        for heading, body in existing_sections.items()
        if _is_scaffold_body(body)
    }

    tldr_body = existing_sections.get("TL;DR", "")
    if "TL;DR" not in existing_sections or "TL;DR" in scaffold_keys:
        tldr_body = _render_tldr(_overview_tldr(root, cluster_slug, brief_md_path))

    paper_body = _render_papers_section(root, cluster_slug)
    brief_body = _generated_brief_body(brief_md_path)
    if brief_body is None:
        brief_body = _keep_existing_body(existing_sections, scaffold_keys, BRIEF_HEADING)
    moc_body = _generated_moc_body(moc_links)
    if moc_body is None:
        moc_body = _keep_existing_body(existing_sections, scaffold_keys, MOC_HEADING)

    preserved: dict[str, str] = {}
    for heading, body in existing_sections.items():
        if heading in scaffold_keys:
            continue
        if heading in {"TL;DR", PAPERS_HEADING, BRIEF_HEADING, MOC_HEADING}:
            continue
        preserved[heading] = body

    generated: dict[str, str] = {
        "TL;DR": tldr_body,
        PAPERS_HEADING: paper_body,
    }
    if brief_body:
        generated[BRIEF_HEADING] = brief_body
    if moc_body:
        generated[MOC_HEADING] = moc_body

    sections = _merge_sections(existing_sections, preserved, generated)
    rendered_sections = _render_overview(sections, scaffold_keys=set())
    new_text = _join_preamble_and_sections(preamble, rendered_sections)
    if not overview_path.exists() or overview_path.read_text(encoding="utf-8", errors="ignore") != new_text:
        overview_path.write_text(new_text, encoding="utf-8")
    return overview_path


def ensure_moc(vault_root: Path, name: str, *, description: str = "") -> Path:
    """Create ``hub/_moc/<name>.md`` if missing and return its path."""

    root = Path(vault_root)
    moc_dir = safe_join(root, "hub", "_moc")
    moc_dir.mkdir(parents=True, exist_ok=True)
    path = safe_join(moc_dir, f"{name}.md")
    if path.exists():
        return path

    topic_slug = _topic_slug(name)
    frontmatter = "\n".join(
        [
            "---",
            "type: moc",
            f"name: {name}",
            f'tags: ["topic:{topic_slug}", "type:moc"]',
            "---",
            "",
        ]
    )
    body = (
        f"# {name}\n\n"
        f"{description.strip()}\n\n"
        "## Clusters tagged with this MOC\n\n"
        "(populated by sync)\n"
    )
    path.write_text(frontmatter + body, encoding="utf-8")
    return path


def derive_moc_links(
    cluster_slug: str,
    cluster_queries: list[str] | None = None,
    moc_links: list[str] | None = None,
) -> list[str]:
    """Return v0.87 default MOC names for a cluster."""

    links: list[str] = []
    for name in moc_links or []:
        _append_unique(links, str(name).strip())
    text_parts = [cluster_slug, *(cluster_queries or [])]
    haystack = " ".join(str(part) for part in text_parts if part).lower()
    if "llm" in haystack or "large language model" in haystack:
        _append_unique(links, "LLM-Agents")
    if "water" in haystack:
        _append_unique(links, "Water-Resources")
    return links


def latest_brief_md(vault_root: Path, cluster_slug: str) -> Path | None:
    """Return the most-recent NotebookLM brief markdown mirror for a cluster.

    Mirrors are written by `notebooklm download --type brief` to
    `hub/<slug>/notebooklm-brief-<UTC-timestamp>.md`. Sorted by
    filename so the lexicographic order matches chronological order.
    Returns None when no brief mirror exists yet.
    """
    hub_dir = vault_root / "hub" / cluster_slug
    if not hub_dir.exists():
        return None
    candidates = sorted(hub_dir.glob("notebooklm-brief-*.md"))
    return candidates[-1] if candidates else None


def populate_all_overviews(
    cfg,
    *,
    cluster_slug_filter: str | None = None,
) -> list[tuple[str, Path]]:
    """Re-run populate_overview + ensure_moc for every cluster in the registry.

    Use cases (v0.87.1 §5):
    - Backfill clusters that were ingested BEFORE v0.87's
      post-ingest hub-overview hook landed.
    - Bulk refresh after a template change to the overview scaffold.
    - One-shot CLI: `research-hub hub rebuild-overviews`.

    For each cluster: look up the latest brief mirror (if any), derive
    MOC links from slug + cluster.first_query, ensure_moc them, then
    populate_overview. Returns list of (slug, overview_path) tuples.

    Errors per cluster are caught and logged; one bad cluster does
    not stop the rest.
    """
    from research_hub.clusters import ClusterRegistry

    registry = ClusterRegistry(cfg.clusters_file)
    vault_root = Path(cfg.root)
    written: list[tuple[str, Path]] = []
    for cluster in registry.list():
        slug = (cluster.slug or "").strip()
        if not slug:
            continue
        if cluster_slug_filter and slug != cluster_slug_filter:
            continue
        try:
            cluster_queries = [str(getattr(cluster, "first_query", "") or "")]
            moc_links = derive_moc_links(
                slug,
                cluster_queries=cluster_queries,
                moc_links=list(getattr(cluster, "moc_links", []) or []),
            )
            for name in moc_links:
                ensure_moc(vault_root, name)
            brief_md = latest_brief_md(vault_root, slug)
            overview_path = populate_overview(
                cluster_slug=slug,
                vault_root=vault_root,
                brief_md_path=brief_md,
                moc_links=moc_links,
            )
            written.append((slug, overview_path))
        except Exception as exc:  # noqa: BLE001 — surface per-cluster failures, continue with rest
            written.append((slug, Path(f"<error: {exc}>")))
    return written


def _parse_overview_sections(md: str) -> dict[str, str]:
    """Parse level-2 overview sections into an ordered mapping."""

    sections: dict[str, str] = {}
    matches = list(_H2_RE.finditer(md))
    for index, match in enumerate(matches):
        heading = match.group(1).strip().rstrip("#").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(md)
        sections[heading] = md[start:end].strip("\n")
    return sections


def _render_overview(sections: dict[str, str], *, scaffold_keys: set[str]) -> str:
    """Render section mapping back to markdown, omitting scaffold keys."""

    rendered: list[str] = []
    for heading, body in sections.items():
        if heading in scaffold_keys:
            continue
        body_text = body.rstrip()
        if not body_text:
            continue
        rendered.append(f"## {heading}\n\n{body_text}")
    return "\n\n".join(rendered).rstrip() + ("\n" if rendered else "")


def _paper_bullet(note_path: Path) -> str:
    """Build the overview bullet wikilink for a paper note."""

    meta = _read_frontmatter(note_path)
    title = _single_line(meta.get("title") or note_path.stem)
    return f"- [[{note_path.stem}]]: *{title}*"


def _overview_path(vault_root: Path, cluster_slug: str) -> Path:
    return safe_join(vault_root, "hub", cluster_slug, OVERVIEW_FILENAME)


def _raw_cluster_dir(vault_root: Path, cluster_slug: str) -> Path:
    return safe_join(vault_root, "raw", cluster_slug)


def _split_preamble(md: str) -> tuple[str, str]:
    match = _H2_RE.search(md)
    if not match:
        return md.rstrip(), ""
    return md[: match.start()].rstrip(), md[match.start() :]


def _join_preamble_and_sections(preamble: str, rendered_sections: str) -> str:
    parts = [part.rstrip() for part in (preamble, rendered_sections) if part.strip()]
    return "\n\n".join(parts).rstrip() + "\n"


def _default_preamble(cluster_slug: str) -> str:
    title = cluster_slug.replace("-", " ").strip().title() or cluster_slug
    return "\n".join(
        [
            "---",
            "type: topic-overview",
            f"cluster: {cluster_slug}",
            f"title: {title}",
            "status: draft",
            "---",
            "",
            f"# {title}",
        ]
    )


def _merge_sections(
    existing_sections: dict[str, str],
    preserved: dict[str, str],
    generated: dict[str, str],
) -> dict[str, str]:
    final: dict[str, str] = {}
    inserted_generated = False

    def insert_generated() -> None:
        nonlocal inserted_generated
        if inserted_generated:
            return
        for heading, body in generated.items():
            final[heading] = body
        inserted_generated = True

    if "TL;DR" not in existing_sections:
        insert_generated()
    for heading in existing_sections:
        if heading == "TL;DR":
            insert_generated()
            continue
        if heading in preserved:
            final[heading] = preserved[heading]
    insert_generated()
    return final


def _keep_existing_body(
    existing_sections: dict[str, str],
    scaffold_keys: set[str],
    heading: str,
) -> str | None:
    if heading in existing_sections and heading not in scaffold_keys:
        body = existing_sections[heading].strip()
        return body or None
    return None


def _render_tldr(text: str) -> str:
    clean = text.strip() or "No cluster summary available yet."
    quote_lines = [f"> {line}" if line else ">" for line in clean.splitlines()]
    return "\n".join(["> [!abstract]", *quote_lines, "^tldr"])


def _overview_tldr(vault_root: Path, cluster_slug: str, brief_md_path: Path | None) -> str:
    if brief_md_path is not None and Path(brief_md_path).exists():
        summary = _brief_summary(Path(brief_md_path))
        if summary:
            return summary[:200]
    query = _cluster_query_fallback(vault_root, cluster_slug)
    return query or cluster_slug.replace("-", " ")


def _brief_summary(brief_md_path: Path) -> str:
    text = brief_md_path.read_text(encoding="utf-8", errors="ignore")
    body = _strip_frontmatter(text)
    match = re.search(
        r"^#{1,3}\s+(Executive Summary|Summary|TL;DR)\s*$",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if match:
        next_heading = re.search(r"^#{1,3}\s+", body[match.end() :], re.MULTILINE)
        end = match.end() + next_heading.start() if next_heading else len(body)
        candidate = body[match.end() : end]
        summary = _plain_text(candidate)
        if summary:
            return summary
    return _plain_text(body)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).lstrip()


def _plain_text(markdown: str) -> str:
    lines: list[str] = []
    in_fence = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"\*(.*?)\*", r"\1", line)
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _cluster_query_fallback(vault_root: Path, cluster_slug: str) -> str:
    json_path = vault_root / ".research_hub" / "clusters" / f"{cluster_slug}.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for key in ("cluster_queries", "queries"):
            values = data.get(key)
            if isinstance(values, list) and values:
                return str(values[0]).strip()
        for key in ("first_query", "query", "name", "description"):
            value = str(data.get(key, "") or "").strip()
            if value:
                return value

    yaml_path = vault_root / ".research_hub" / "clusters.yaml"
    if yaml_path.exists():
        yaml_value = _cluster_query_from_yaml(yaml_path, cluster_slug)
        if yaml_value:
            return yaml_value
    return ""


def _cluster_query_from_yaml(path: Path, cluster_slug: str) -> str:
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cluster = (payload.get("clusters") or {}).get(cluster_slug) or {}
        for key in ("cluster_queries", "queries"):
            values = cluster.get(key)
            if isinstance(values, list) and values:
                return str(values[0]).strip()
        return str(cluster.get("first_query") or cluster.get("name") or "").strip()
    except Exception:
        text = path.read_text(encoding="utf-8", errors="ignore")
        pattern = re.compile(
            rf"^\s{{2}}{re.escape(cluster_slug)}:\s*\n(?P<body>(?:^\s{{4}}.*\n?)*)",
            re.MULTILINE,
        )
        match = pattern.search(text)
        if not match:
            return ""
        first_query = re.search(r"^\s{4}first_query:\s*(.+?)\s*$", match.group("body"), re.MULTILINE)
        return first_query.group(1).strip().strip('"').strip("'") if first_query else ""


def _render_papers_section(vault_root: Path, cluster_slug: str) -> str:
    raw_dir = _raw_cluster_dir(vault_root, cluster_slug)
    if not raw_dir.exists():
        return "(no papers found)"
    notes = [
        path
        for path in raw_dir.glob("*.md")
        if path.name not in {OVERVIEW_FILENAME, "index.md"}
    ]
    if not notes:
        return "(no papers found)"
    notes.sort(key=_paper_sort_key)
    return "\n".join(_paper_bullet(path) for path in notes)


def _paper_sort_key(note_path: Path) -> tuple[int, str, str]:
    meta = _read_frontmatter(note_path)
    year = _year_value(meta.get("year"))
    author = _first_author(meta.get("authors"))
    return (-year, author.lower(), note_path.stem)


def _generated_brief_body(brief_md_path: Path | None) -> str | None:
    if brief_md_path is None or not Path(brief_md_path).exists():
        return None
    return f"- [[{Path(brief_md_path).stem}]]"


def _generated_moc_body(moc_links: list[str] | None) -> str | None:
    links = [name.strip() for name in (moc_links or []) if name and name.strip()]
    if not links:
        return None
    unique = list(dict.fromkeys(links))
    return "\n".join(f"- [[{name}]]" for name in unique)


def _read_frontmatter(note_path: Path) -> dict[str, Any]:
    try:
        text = note_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    return _parse_simple_yaml(match.group(1))


def _parse_simple_yaml(frontmatter: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = _parse_scalar(value.strip())
    return data


def _parse_scalar(value: str) -> Any:
    if value in {"", "null", "Null", "NULL", "~"}:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    return value.strip().strip('"').strip("'")


def _year_value(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _first_author(value: Any) -> str:
    if isinstance(value, list):
        raw = str(value[0]) if value else ""
    else:
        raw = str(value or "")
    first = raw.split(";")[0].strip()
    return first.split(",")[0].strip() or first


def _is_scaffold_body(body: str) -> bool:
    text = body.strip()
    if not text:
        return True
    upper = text.upper()
    if any(marker.upper() in upper for marker in _SCAFFOLD_MARKERS):
        return True
    if any(marker in text for marker in _MOJIBAKE_MARKERS):
        return True
    meaningful = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("^"):
            continue
        normalized = re.sub(r"[\s|\-:*>#`_\[\]().!]+", "", stripped)
        if normalized:
            meaningful.append(normalized)
    return not meaningful


def _single_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _topic_slug(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return normalized or "moc"


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
