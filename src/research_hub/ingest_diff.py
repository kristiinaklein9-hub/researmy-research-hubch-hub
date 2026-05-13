"""Compute the gap between fit-check accepted DOIs and what actually
ended up in the vault's raw/<cluster>/ directory.

v0.86 silently dropped 3 of 15 fit-check-accepted papers during the
human-water-llm shakedown (Semantic Scholar 429 + Crossref Elsevier
DOIs that the resolver couldn't enrich). The ingest reported success
and nothing told the user that the cluster ended up smaller than
they curated. v0.87 §O4 surfaces the gap as a sidecar file plus a
log line, so re-runs can target the missing DOIs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ACCEPTED_FILENAME = ".fit_check_accepted.json"
GAP_FILENAME = ".ingest_gap.json"

_DOI_FRONTMATTER_RE = re.compile(r'^doi:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


@dataclass(frozen=True)
class GapEntry:
    doi: str
    title: str


def _normalize_doi(value: str) -> str:
    """Canonicalize a DOI for set comparison (lowercase, strip)."""
    return (value or "").strip().strip('"').strip().lower()


def _accepted_dois_with_titles(accepted_path: Path) -> list[tuple[str, str]]:
    """Read the fit-check sidecar and yield (doi, title) tuples.

    Tolerates missing file (returns empty list) and malformed JSON
    (returns empty list) — the caller decides whether that's fatal.
    """
    try:
        payload = json.loads(accepted_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    accepted = payload.get("accepted") if isinstance(payload, dict) else None
    if not isinstance(accepted, list):
        return []
    out: list[tuple[str, str]] = []
    for item in accepted:
        if not isinstance(item, dict):
            continue
        doi = _normalize_doi(str(item.get("doi", "")))
        if not doi:
            continue
        title = str(item.get("title", "")).strip()
        out.append((doi, title))
    return out


def _ingested_dois(raw_cluster_dir: Path) -> set[str]:
    """Extract DOIs from every .md frontmatter under raw/<cluster>/.

    Looks for the first `doi:` line inside the frontmatter; falls back
    to a permissive line-scan if the file lacks the standard wrapper.
    """
    found: set[str] = set()
    if not raw_cluster_dir.exists():
        return found
    for note in raw_cluster_dir.glob("*.md"):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        match = _DOI_FRONTMATTER_RE.search(text)
        if match:
            found.add(_normalize_doi(match.group(1)))
    return found


def compute_ingest_gap(
    *,
    cluster_slug: str,
    vault_root: Path,
) -> dict:
    """Compute the accepted-minus-ingested gap for a cluster.

    Returns a dict with:
      cluster_slug
      computed_at: ISO 8601 UTC
      accepted_count, ingested_count, gap_count
      gap: list of {doi, title} for accepted DOIs not present in raw/

    Does not write anything. Callers that want to persist the result
    should pass it to `write_gap_sidecar`.
    """
    accepted_path = vault_root / "hub" / cluster_slug / ACCEPTED_FILENAME
    raw_cluster_dir = vault_root / "raw" / cluster_slug

    accepted = _accepted_dois_with_titles(accepted_path)
    accepted_dois = {doi for doi, _ in accepted}
    ingested = _ingested_dois(raw_cluster_dir)

    title_lookup = {doi: title for doi, title in accepted}
    missing = sorted(accepted_dois - ingested)

    return {
        "cluster_slug": cluster_slug,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accepted_count": len(accepted_dois),
        "ingested_count": len(ingested),
        "gap_count": len(missing),
        "gap": [
            {"doi": doi, "title": title_lookup.get(doi, "")}
            for doi in missing
        ],
    }


def write_gap_sidecar(
    *,
    cluster_slug: str,
    vault_root: Path,
    gap_report: dict,
) -> Path:
    """Atomically write the gap report to hub/<cluster>/.ingest_gap.json.

    Writes even when gap_count is zero so re-runs can confirm a clean
    state. Returns the path written.
    """
    target_dir = vault_root / "hub" / cluster_slug
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / GAP_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(gap_report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path
