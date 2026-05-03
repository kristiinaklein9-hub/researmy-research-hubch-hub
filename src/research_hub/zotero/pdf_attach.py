"""Discover open-access PDFs and attach them to Zotero items."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import requests

OPENALEX_BASE = "https://api.openalex.org/works/doi"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
CROSSREF_BASE = "https://api.crossref.org/works"

_HINT_SHOWN = False


@dataclass
class PdfAttachPlan:
    item_key: str
    title: str
    doi: str
    arxiv_id: str
    pdf_url: str = ""
    source: str = ""
    publisher_url: str = ""
    error: str = ""


def _openalex_oa_url(doi: str, prefetched: Optional[dict] = None) -> str:
    try:
        if prefetched is None:
            response = requests.get(
                f"{OPENALEX_BASE}/{doi}",
                params={"select": "open_access,best_oa_location"},
                timeout=15,
            )
            if not response.ok:
                return ""
            data = response.json()
        else:
            data = prefetched
        best = data.get("best_oa_location") or {}
        pdf_url = str(best.get("pdf_url") or "").strip()
        if pdf_url:
            return pdf_url
        open_access = data.get("open_access") or {}
        if open_access.get("is_oa"):
            oa_url = str(open_access.get("oa_url") or "").strip()
            if oa_url and oa_url.lower().endswith(".pdf"):
                return oa_url
    except Exception:
        pass
    return ""


def _unpaywall_lookup(doi: str, email: str) -> str:
    try:
        response = requests.get(
            f"{UNPAYWALL_BASE}/{doi}",
            params={"email": email},
            timeout=15,
        )
        if not response.ok:
            return ""
        data = response.json()
        best = data.get("best_oa_location") or {}
        return str(best.get("url_for_pdf") or "").strip()
    except Exception:
        return ""


def _crossref_link_pdf(doi: str) -> str:
    try:
        response = requests.get(f"{CROSSREF_BASE}/{doi}", timeout=15)
        if not response.ok:
            return ""
        message = (response.json() or {}).get("message", {})
        for link in message.get("link", []) or []:
            content_type = str(link.get("content-type") or "").lower()
            url = str(link.get("URL") or "").strip()
            if "pdf" in content_type and url:
                return url
    except Exception:
        pass
    return ""


def _print_unpaywall_hint() -> None:
    global _HINT_SHOWN
    if _HINT_SHOWN:
        return
    _HINT_SHOWN = True
    print(
        "\nHINT: For 50%+ more PDF hits, register a free Unpaywall email:\n"
        "  python -m research_hub config set unpaywall_email <your-email>\n"
        "Then re-run. (Skipping Unpaywall for now.)\n",
        file=sys.stderr,
    )


def find_pdf_url(
    doi: str = "",
    arxiv_id: str = "",
    *,
    unpaywall_email: str = "",
    openalex_record: Optional[dict] = None,
) -> tuple[str, str]:
    """Return (pdf_url, source) or ("", "") if none found."""
    if arxiv_id:
        return (f"https://arxiv.org/pdf/{arxiv_id}.pdf", "arxiv")
    if doi:
        pdf_url = _openalex_oa_url(doi, prefetched=openalex_record)
        if pdf_url:
            return (pdf_url, "openalex-oa")
    if doi and unpaywall_email:
        pdf_url = _unpaywall_lookup(doi, unpaywall_email)
        if pdf_url:
            return (pdf_url, "unpaywall")
    elif doi and not unpaywall_email:
        _print_unpaywall_hint()
    if doi:
        pdf_url = _crossref_link_pdf(doi)
        if pdf_url:
            return (pdf_url, "crossref-link")
    return ("", "")


def _extract_arxiv_id(data: dict) -> str:
    doi = str(data.get("DOI", "") or "").strip().lower()
    match = re.match(r"10\.48550/arxiv\.(.+)", doi)
    if match:
        return match.group(1)
    url = str(data.get("url", "") or "").strip()
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s/?]+?)(?:\.pdf)?$", url)
    if match:
        return match.group(1)
    return ""


def plan_attach_for_items(
    items: list[dict],
    *,
    unpaywall_email: str = "",
    include_publisher_link: bool = False,
) -> list[PdfAttachPlan]:
    """Build PDF attach plans for the provided Zotero items."""
    plans: list[PdfAttachPlan] = []
    for item in items:
        data = item.get("data", {})
        doi = str(data.get("DOI", "") or "").strip()
        arxiv_id = _extract_arxiv_id(data)
        pdf_url, source = find_pdf_url(doi=doi, arxiv_id=arxiv_id, unpaywall_email=unpaywall_email)
        publisher_url = ""
        if not pdf_url and include_publisher_link:
            publisher_url = (
                str(data.get("url", "") or "").strip()
                or (f"https://doi.org/{doi}" if doi else "")
            )
            if publisher_url:
                source = "publisher-page"
        plans.append(
            PdfAttachPlan(
                item_key=item.get("key", ""),
                title=str(data.get("title", "") or "")[:60],
                doi=doi,
                arxiv_id=arxiv_id,
                pdf_url=pdf_url,
                source=source,
                publisher_url=publisher_url,
            )
        )
    return plans


def _has_existing_pdf(zot, item_key: str) -> bool:
    try:
        children = zot.children(item_key) or []
    except Exception:
        return False
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") == "attachment" and "pdf" in str(data.get("contentType") or "").lower():
            return True
    return False


def attach_pdfs(
    zot,
    plans: Iterable[PdfAttachPlan],
    *,
    rate_limit_rps: float = 2.0,
) -> dict[str, str]:
    """Create linked-PDF attachment items for each plan with a discovered PDF URL."""
    sleep_s = 1.0 / max(rate_limit_rps, 0.1)
    results: dict[str, str] = {}
    for plan in plans:
        if plan.pdf_url:
            if _has_existing_pdf(zot, plan.item_key):
                results[plan.item_key] = "skip:already-has-pdf"
                continue
            try:
                template = zot.item_template("attachment", "imported_url")
                template["parentItem"] = plan.item_key
                template["url"] = plan.pdf_url
                template["title"] = "Full Text PDF"
                template["contentType"] = "application/pdf"
                zot.create_items([template])
                results[plan.item_key] = "ok"
                time.sleep(sleep_s)
            except Exception as exc:
                results[plan.item_key] = str(exc)[:80]
        elif plan.publisher_url:
            try:
                template = zot.item_template("attachment", "linked_url")
                template["parentItem"] = plan.item_key
                template["url"] = plan.publisher_url
                template["title"] = "Publisher Page"
                template["contentType"] = "text/html"
                zot.create_items([template])
                results[plan.item_key] = "ok"
                time.sleep(sleep_s)
            except Exception as exc:
                results[plan.item_key] = str(exc)[:80]
        else:
            results[plan.item_key] = "skip:no-source"
    return results
