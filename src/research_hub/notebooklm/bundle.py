"""Bundle a cluster's papers into a drag-drop-ready folder for NotebookLM."""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from research_hub.utils.doi import normalize_doi as _normalize_doi

logger = logging.getLogger(__name__)

@dataclass
class BundleEntry:
    doi: str
    title: str
    obsidian_path: str
    action: str
    pdf_path: str = ""
    pdf_source: str = ""
    url: str = ""
    skip_reason: str = ""
    url_quality: str = ""
    url_quality_reason: str = ""
    url_quality_signal: str = ""


@dataclass
class BundleReport:
    cluster_slug: str
    bundle_dir: Path
    entries: list[BundleEntry] = field(default_factory=list)
    created_at: str = ""

    @property
    def pdf_count(self) -> int:
        return sum(1 for entry in self.entries if entry.action == "pdf")

    @property
    def url_count(self) -> int:
        return sum(1 for entry in self.entries if entry.action == "url")

    @property
    def skip_count(self) -> int:
        return sum(1 for entry in self.entries if entry.action == "skip")


def _read_frontmatter(md_path: Path) -> str:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    return text[3:end]


def _parse_note_metadata(md_path: Path) -> dict[str, str]:
    """Extract title, doi, url, authors, year, summarize_status from note YAML frontmatter."""
    meta = {"title": "", "doi": "", "url": "", "authors": "", "year": "", "summarize_status": ""}
    frontmatter = _read_frontmatter(md_path)
    if not frontmatter:
        return meta
    for key in ("title", "doi", "url", "authors", "year", "summarize_status"):
        pattern = rf'^{key}:\s*[\'"]?([^\'"\n]*)[\'"]?'
        match = re.search(pattern, frontmatter, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            meta[key] = _normalize_doi(value) if key == "doi" else value
    return meta


def _find_pdf_for_doi(
    pdfs_dir: Path,
    doi: str,
    *,
    pdf_index: list[Path] | None = None,
) -> Path | None:
    """Look for a PDF file matching the DOI.

    v0.88.11: callers can pass a precomputed ``pdf_index`` (sorted
    ``rglob("*.pdf")``) so they don't pay the O(P) directory walk for
    every paper in a cluster. ``bundle_cluster`` now builds the index
    once at the top of the loop, dropping `_find_pdf_for_doi`'s
    cluster-wide cost from O(P²) to O(P) — a 50× speedup at 49 papers
    and a 500× speedup at 500.
    """
    normalized = _normalize_doi(doi)
    if not pdfs_dir.exists() or not normalized:
        return None

    exact = pdfs_dir / f"{normalized.replace('/', '_').replace(':', '_')}.pdf"
    if exact.exists():
        return exact

    candidates = pdf_index if pdf_index is not None else sorted(pdfs_dir.rglob("*.pdf"))

    tail = normalized.rsplit("/", 1)[-1]
    if tail:
        for candidate in candidates:
            if tail.lower() in candidate.name.lower():
                return candidate

    doi_without_prefix = normalized.replace("/", "_")
    for candidate in candidates:
        if doi_without_prefix.lower() in candidate.name.lower():
            return candidate
    return None


def _extract_first_author_surname(authors_str: str) -> str:
    """Extract the first author's last name from a semicolon-separated string.

    Handles "Last, First; Last2, First2; ..." format (Zotero standard)
    and "First Last; First2 Last2; ..." format.
    """
    if not authors_str:
        return ""
    first_author = authors_str.split(";")[0].strip()
    if "," in first_author:
        return first_author.split(",")[0].strip()
    parts = first_author.split()
    return parts[-1] if parts else ""


def _find_pdf_by_author_year(
    pdfs_dir: Path,
    authors: str,
    year: str,
    *,
    pdf_index: list[Path] | None = None,
) -> Path | None:
    """Match a PDF by Author_Year naming convention (e.g., Ben-Zion_2025.pdf).

    The surname must appear at the START of the filename (case-insensitive)
    followed by a non-alphabetic separator (`_`, `-`, space, digit).
    This prevents false positives like "Li" matching "Liu" or "Ma"
    matching "Mao".

    v0.88.11: same memoize pattern as `_find_pdf_for_doi`.
    """
    if not pdfs_dir.exists():
        return None
    surname = _extract_first_author_surname(authors)
    if not surname or len(surname) < 2:
        return None
    escaped = re.escape(surname.lower())
    pattern = re.compile(rf"^{escaped}(?:[_\-\s\d]|$)", re.IGNORECASE)
    year_str = str(year).strip() if year else ""
    candidates = pdf_index if pdf_index is not None else sorted(pdfs_dir.rglob("*.pdf"))
    for candidate in candidates:
        if pattern.match(candidate.stem):
            if not year_str or year_str in candidate.stem:
                return candidate
    return None


def _pick_url(meta: dict[str, str]) -> str:
    """Prefer DOI URL, then existing `url` YAML field."""
    doi = _normalize_doi(meta.get("doi", ""))
    url = meta.get("url", "").strip()

    if doi:
        arxiv_match = re.search(r"arxiv[.:/]?([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", doi, re.IGNORECASE)
        if arxiv_match:
            return f"https://arxiv.org/abs/{arxiv_match.group(1)}"
        return f"https://doi.org/{doi}"
    if url.startswith(("http://", "https://")):
        return url
    return ""


def bundle_cluster(
    cluster,
    cfg,
    out_root: Path | None = None,
    download_pdfs: bool = False,
) -> BundleReport:
    """Walk a cluster's papers and emit a drag-drop bundle."""
    from research_hub.vault.sync import list_cluster_notes

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundles_root = out_root or (cfg.research_hub_dir / "bundles")
    bundle_dir = bundles_root / f"{cluster.slug}-{timestamp}"
    bundle_pdfs = bundle_dir / "pdfs"
    bundle_pdfs.mkdir(parents=True, exist_ok=True)

    report = BundleReport(
        cluster_slug=cluster.slug,
        bundle_dir=bundle_dir,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    pdfs_dir = cfg.root / "pdfs"
    notes = list_cluster_notes(cluster.slug, cfg.raw)
    # v0.88.11: build the PDF index ONCE per bundle instead of having
    # `_find_pdf_for_doi` rglob the whole directory for every paper
    # (was O(P²) — 49 papers × 49 file-system walks). At 49 papers this
    # already saves ~50× time; at 500 papers ~500×.
    pdf_index = sorted(pdfs_dir.rglob("*.pdf")) if pdfs_dir.exists() else []
    for note_path in notes:
        meta = _parse_note_metadata(note_path)
        entry = BundleEntry(
            doi=meta.get("doi", ""),
            title=meta.get("title") or note_path.stem,
            obsidian_path=str(note_path),
            action="skip",
        )

        pdf = _find_pdf_for_doi(pdfs_dir, entry.doi, pdf_index=pdf_index)
        if pdf is None:
            pdf = _find_pdf_by_author_year(
                pdfs_dir, meta.get("authors", ""), meta.get("year", ""),
                pdf_index=pdf_index,
            )
            if pdf is not None:
                entry.pdf_source = "local-slug"
        elif pdf is not None:
            entry.pdf_source = "local-doi"

        fetch_result = None
        if pdf is None and download_pdfs:
            from research_hub.notebooklm.pdf_fetcher import fetch_paper_pdf

            fetch_result = fetch_paper_pdf(entry.doi, note_path.stem, pdfs_dir)
            if fetch_result.ok:
                pdf = fetch_result.path
                entry.pdf_source = fetch_result.source
            else:
                logger.info("pdf_fetch failed for %s: %s", entry.doi or note_path.stem, fetch_result.error)

        # Classify the URL quality so the field is always written into the
        # manifest entry (even when the entry ends up taking the pdf path).
        # When a local PDF is already present (pdf is not None) the URL will
        # never be uploaded, so we skip the network probe to avoid wasted
        # traffic — classify with probe=False in that case.
        url = _pick_url(meta)
        if url:
            from research_hub.notebooklm.url_quality import classify_url_source

            summarize_status = meta.get("summarize_status", "")
            # Only probe when there is no local PDF (pdf is None); a PDF-backed
            # entry will take action="pdf" and the URL is never uploaded.
            quality_result = classify_url_source(
                url, summarize_status, probe=(pdf is None)
            )
            entry.url_quality = quality_result.quality
            entry.url_quality_reason = quality_result.reason
            entry.url_quality_signal = quality_result.signal

            # Auto-prefer local PDF when the URL is known to be a likely error
            # page but no local PDF was found in the initial scan.  If a PDF
            # is already present (pdf is not None) it is handled below.
            if quality_result.quality == "likely_error_page" and pdf is None:
                pdf_retry = _find_pdf_for_doi(pdfs_dir, entry.doi, pdf_index=pdf_index)
                if pdf_retry is None and meta.get("authors"):
                    pdf_retry = _find_pdf_by_author_year(
                        pdfs_dir,
                        meta.get("authors", ""),
                        meta.get("year", ""),
                        pdf_index=pdf_index,
                    )
                if pdf_retry is not None:
                    pdf = pdf_retry
                    entry.pdf_source = entry.pdf_source or "local-doi"

        if pdf is not None:
            destination = bundle_pdfs / pdf.name
            shutil.copy2(pdf, destination)
            entry.action = "pdf"
            entry.pdf_path = str(destination)
            report.entries.append(entry)
            continue

        if url:
            entry.action = "url"
            entry.url = url
            if download_pdfs and fetch_result is not None:
                entry.skip_reason = "no OA; url fallback used"
            report.entries.append(entry)
            continue

        entry.skip_reason = "no DOI, no URL, no local PDF"
        report.entries.append(entry)

    sources_file = bundle_dir / "sources.txt"
    with sources_file.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in report.entries:
            if entry.action == "url" and entry.url:
                handle.write(f"{entry.url}\n")

    manifest_file = bundle_dir / "manifest.json"
    manifest_file.write_text(
        json.dumps(
            {
                "cluster_slug": cluster.slug,
                "cluster_name": cluster.name,
                "created_at": report.created_at,
                "pdf_count": report.pdf_count,
                "url_count": report.url_count,
                "skip_count": report.skip_count,
                "entries": [asdict(entry) for entry in report.entries],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    readme = bundle_dir / "README.md"
    readme.write_text(_render_readme(cluster, report), encoding="utf-8")
    pdf_sources = Counter(
        entry.pdf_source for entry in report.entries if entry.action == "pdf" and entry.pdf_source
    )
    source_summary = ", ".join(
        f"{source}: {count}" for source, count in sorted(pdf_sources.items())
    )
    print(f"bundle summary - {cluster.slug}:")
    if source_summary:
        print(f"  pdf: {report.pdf_count} ({source_summary})")
    else:
        print(f"  pdf: {report.pdf_count}")
    print(f"  url: {report.url_count}")
    print(f"  skip: {report.skip_count}")
    return report


def _render_readme(cluster, report: BundleReport) -> str:
    lines = [
        f"# Bundle: {cluster.name}",
        "",
        f"- Cluster slug: `{cluster.slug}`",
        f"- Created at: {report.created_at}",
        (
            f"- Papers: {len(report.entries)} total "
            f"({report.pdf_count} PDFs, {report.url_count} URLs, {report.skip_count} skipped)"
        ),
        "",
        "## Upload to NotebookLM (manual fallback)",
        "",
        (
            "1. Open <https://notebooklm.google.com/> and create or open the notebook "
            f"named `{cluster.name}`."
        ),
        "2. Drag each file from `pdfs/` into the notebook Sources panel.",
        (
            "3. For each URL in `sources.txt`, use NotebookLM's Website source flow and "
            "paste one URL at a time."
        ),
        "4. After upload, run the NotebookLM workflows you need.",
        "",
        "If you have v0.4.1+ installed, the same bundle can be uploaded automatically:",
        "",
        "```bash",
        f"research-hub notebooklm upload --cluster {cluster.slug}",
        "```",
        "",
        "## Skipped papers",
        "",
    ]
    skipped = [entry for entry in report.entries if entry.action == "skip"]
    if not skipped:
        lines.append("_None; every paper has either a PDF or a URL._")
    else:
        for entry in skipped:
            lines.append(
                f"- `{entry.doi or '(no DOI)'}` {entry.title[:80]} - {entry.skip_reason}"
            )
    return "\n".join(lines) + "\n"
