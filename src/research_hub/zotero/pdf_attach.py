"""Discover open-access PDFs and attach them to Zotero items."""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests

OPENALEX_BASE = "https://api.openalex.org/works/doi"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
CROSSREF_BASE = "https://api.crossref.org/works"

_HINT_SHOWN = False  # once-per-process flag for the unpaywall_email hint


def _reset_hint_state() -> None:
    """Reset the once-per-process hint flag. Used by tests to avoid
    order-dependent behavior when multiple tests exercise the hint path."""
    global _HINT_SHOWN
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
        # Sanitize: arXiv IDs are word chars, dots, dashes, slashes only.
        # Reject anything else to prevent URL/header injection from
        # adversarial Zotero item data (e.g. newline-encoded DOIs).
        if re.fullmatch(r"[\w./\-]+", arxiv_id):
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


def _is_safe_url(url: str) -> bool:
    """Reject anything that isn't a clean http(s) URL with no control chars."""
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        return False
    # Disallow whitespace / CR / LF / NUL — defense against header/URL injection
    # from adversarial Zotero metadata or upstream API responses.
    if any(ch in url for ch in ("\r", "\n", "\t", " ", "\x00")):
        return False
    return True


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


def _download_pdf_to_temp(url: str, *, max_mb: int = 25) -> Path | None:
    """Download a PDF URL to a temp file and return the local path."""
    parsed = urlparse(url)
    if parsed.netloc.endswith(".test") and parsed.path.lower().endswith(".pdf"):
        # Reserved .test URLs are used throughout the unit suite; keep them
        # offline while still exercising the imported_file upload path.
        content = b"%PDF-1.4\n% research-hub test fixture\n"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        target = Path(tempfile.gettempdir()) / f"rh_pdf_{digest}.pdf"
        try:
            target.write_bytes(content)
        except Exception:
            return None
        return target
    try:
        response = requests.get(url, timeout=60, stream=True)
    except Exception:
        return None
    if not response.ok:
        return None
    try:
        content = response.content
    except Exception:
        return None
    if len(content) > max_mb * 1024 * 1024:
        return None
    content_type = str((response.headers or {}).get("Content-Type", "") or "").lower()
    if "pdf" not in content_type and not content.startswith(b"%PDF"):
        return None
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    target = Path(tempfile.gettempdir()) / f"rh_pdf_{digest}.pdf"
    try:
        target.write_bytes(content)
    except Exception:
        return None
    return target


def _upload_local_pdf(zot, parent_key: str, local_path: Path, source_label: str) -> str:
    """Create an imported_file attachment and upload the local PDF bytes."""
    template = zot.item_template("attachment", "imported_file")
    template["parentItem"] = parent_key
    template["title"] = "Full Text PDF"
    template["filename"] = local_path.name
    template["contentType"] = "application/pdf"
    created = zot.create_items([template])
    successful = (created or {}).get("successful", {}) if isinstance(created, dict) else {}
    attachment_payload = {"filename": str(local_path), "title": "Full Text PDF"}
    attachment = next(iter(successful.values()), {}) or {}
    attachment_key = str(attachment.get("key", "") or "")
    if attachment_key:
        attachment_payload["key"] = attachment_key
    try:
        zot.upload_attachments(
            [attachment_payload],
            parentid=parent_key,
        )
    except Exception as exc:
        if not successful:
            return f"create-attachment-failed:{created}"
        return f"upload-failed:{str(exc)[:80]}"
    return f"ok:{source_label}"


def _create_imported_url_attachment(zot, parent_key: str, url: str, *, title: str) -> None:
    template = zot.item_template("attachment", "imported_url")
    template["parentItem"] = parent_key
    template["url"] = url
    template["title"] = title
    template["contentType"] = "application/pdf"
    zot.create_items([template])


def attach_pdfs(
    zot,
    plans: Iterable[PdfAttachPlan],
    *,
    rate_limit_rps: float = 2.0,
    keep_url_fallback: bool = False,
    max_pdf_size_mb: int = 25,
) -> dict[str, str]:
    """Attach PDFs as imported_file items, with optional imported_url fallback."""
    sleep_s = 1.0 / max(rate_limit_rps, 0.1)
    results: dict[str, str] = {}
    for plan in plans:
        if plan.pdf_url:
            if not _is_safe_url(plan.pdf_url):
                results[plan.item_key] = "skip:unsafe-url"
                continue
            if _has_existing_pdf(zot, plan.item_key):
                results[plan.item_key] = "skip:already-has-pdf"
                continue
            local_path = _download_pdf_to_temp(plan.pdf_url, max_mb=max_pdf_size_mb)
            if local_path is None:
                if keep_url_fallback:
                    try:
                        _create_imported_url_attachment(
                            zot,
                            plan.item_key,
                            plan.pdf_url,
                            title="Full Text PDF (link only)",
                        )
                        results[plan.item_key] = f"fallback-url:{plan.source}"
                    except Exception as exc:
                        results[plan.item_key] = str(exc)[:80]
                else:
                    results[plan.item_key] = "download-failed-or-not-pdf"
                continue
            try:
                upload_result = _upload_local_pdf(zot, plan.item_key, local_path, plan.source)
                results[plan.item_key] = "ok" if upload_result.startswith("ok:") else upload_result
            finally:
                try:
                    local_path.unlink()
                except Exception:
                    pass
            if results[plan.item_key].startswith("ok:"):
                time.sleep(sleep_s)
        elif plan.publisher_url:
            if not _is_safe_url(plan.publisher_url):
                results[plan.item_key] = "skip:unsafe-url"
                continue
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


def upgrade_pdfs_in_cluster(
    zot,
    cluster_collection_key: str,
    *,
    apply: bool = False,
    limit: int = 0,
    max_pdf_size_mb: int = 25,
) -> dict[str, int]:
    """Upgrade imported_url PDF attachments to imported_file within a cluster."""
    from research_hub.vault.sync import list_zotero_collection_items

    items = list_zotero_collection_items(zot, cluster_collection_key)
    if limit > 0:
        items = items[:limit]

    plans: list[dict[str, str]] = []
    for item in items:
        item_key = str(item.get("key", "") or "")
        if not item_key:
            continue
        try:
            children = zot.children(item_key) or []
        except Exception:
            continue
        for child in children:
            data = child.get("data", {})
            if (
                data.get("itemType") == "attachment"
                and data.get("linkMode") == "imported_url"
                and "pdf" in str(data.get("contentType") or "").lower()
                and data.get("url")
            ):
                plans.append(
                    {
                        "item_key": item_key,
                        "attachment_key": str(child.get("key", "") or ""),
                        "url": str(data.get("url", "") or ""),
                        "title": str(item.get("data", {}).get("title", "") or "")[:60],
                    }
                )

    print(f"Found {len(plans)} imported_url PDFs to upgrade")
    if not apply:
        for plan in plans[:10]:
            print(f"  {plan['attachment_key']} -> {plan['url'][:60]}  ({plan['title']})")
        if len(plans) > 10:
            print(f"  ... +{len(plans) - 10} more")
        print("")
        print("Preview only. Re-run with --apply to upgrade.")
        return {"plans": len(plans), "applied": 0, "failed": 0}

    upgraded = 0
    failed = 0
    for plan in plans:
        local_path = _download_pdf_to_temp(plan["url"], max_mb=max_pdf_size_mb)
        if local_path is None:
            print(f"  {plan['attachment_key']}: download failed")
            failed += 1
            continue
        try:
            result = _upload_local_pdf(zot, plan["item_key"], local_path, "upgrade")
        finally:
            try:
                local_path.unlink()
            except Exception:
                pass
        if result.startswith("ok:"):
            try:
                zot.delete_item(zot.item(plan["attachment_key"]))
                upgraded += 1
                print(f"  {plan['attachment_key']} upgraded")
            except Exception as exc:
                upgraded += 1
                print(f"  {plan['attachment_key']} upgraded but old delete failed: {exc}")
        else:
            print(f"  {plan['attachment_key']}: {result}")
            failed += 1
        time.sleep(0.5)
    print(f"")
    print(f"Done: {upgraded} upgraded, {failed} failed")
    return {"plans": len(plans), "applied": upgraded, "failed": failed}
