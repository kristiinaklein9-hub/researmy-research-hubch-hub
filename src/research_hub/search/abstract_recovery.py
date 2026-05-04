"""Best-effort abstract recovery from DOI metadata services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)

_USER_AGENT = "research-hub/0.80.0 (https://github.com/WenyuChiou/research-hub)"
_UNPAYWALL_EMAIL = "research-hub@anthropic.com"


@dataclass
class RecoveredAbstract:
    text: str
    source: str
    oa_url: str = ""


def _recover_from_crossref(doi: str, *, timeout: int = 10) -> RecoveredAbstract:
    try:
        from research_hub.search.crossref import _extract_crossref_abstract

        response = requests.get(
            f"https://api.crossref.org/works/{quote(doi.strip(), safe='')}",
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
        if response.status_code == 200:
            work = (response.json().get("message") or {})
            abstract = _extract_crossref_abstract(work)
            if abstract:
                logger.info("abstract recovery: doi=%s source=crossref", doi)
                return RecoveredAbstract(text=abstract, source="crossref")
    except Exception as exc:
        logger.debug("Crossref abstract recovery failed for %s: %s", doi, exc)
    return RecoveredAbstract(text="", source="")


def _recover_from_unpaywall(doi: str, *, timeout: int = 10) -> RecoveredAbstract:
    try:
        response = requests.get(
            f"https://api.unpaywall.org/v2/{quote(doi.strip(), safe='')}",
            params={"email": _UNPAYWALL_EMAIL},
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
        if response.status_code == 200:
            data = response.json() or {}
            best_oa = (data.get("best_oa_location") or {})
            oa_url = best_oa.get("url", "") or ""
            if oa_url:
                logger.info("abstract recovery: doi=%s source=unpaywall oa_url=%s", doi, oa_url)
                return RecoveredAbstract(text="", source="unpaywall", oa_url=oa_url)
    except Exception as exc:
        logger.debug("Unpaywall lookup failed for %s: %s", doi, exc)
    return RecoveredAbstract(text="", source="")


def _recover_from_semantic_scholar(doi: str, *, timeout: int = 10) -> RecoveredAbstract:
    try:
        response = requests.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi.strip(), safe='')}",
            params={"fields": "abstract,tldr"},
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
        if response.status_code != 200:
            return RecoveredAbstract(text="", source="")
        data = response.json() or {}
        abstract = str(data.get("abstract", "") or "").strip()
        if abstract:
            logger.info("abstract recovery: doi=%s source=s2", doi)
            return RecoveredAbstract(text=abstract, source="s2")
        tldr = data.get("tldr") or {}
        tldr_text = str((tldr.get("text", "") if isinstance(tldr, dict) else "") or "").strip()
        if tldr_text:
            logger.info("abstract recovery: doi=%s source=s2-tldr", doi)
            return RecoveredAbstract(text=tldr_text, source="s2-tldr")
    except Exception as exc:
        logger.debug("Semantic Scholar abstract recovery failed for %s: %s", doi, exc)
    return RecoveredAbstract(text="", source="")


def recover_abstract(doi: str, *, timeout: int = 10) -> RecoveredAbstract:
    """Try Crossref, then Unpaywall, then Semantic Scholar for a missing abstract."""
    if not doi:
        return RecoveredAbstract(text="", source="")

    crossref = _recover_from_crossref(doi, timeout=timeout)
    if crossref.text:
        return crossref

    unpaywall = _recover_from_unpaywall(doi, timeout=timeout)
    if unpaywall.text:
        return unpaywall

    semantic_scholar = _recover_from_semantic_scholar(doi, timeout=timeout)
    if semantic_scholar.text:
        return semantic_scholar

    if unpaywall.oa_url:
        return unpaywall
    return RecoveredAbstract(text="", source="")
