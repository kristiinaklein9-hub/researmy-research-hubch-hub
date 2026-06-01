"""Google Scholar search backend (optional dependency: `scholarly`).

Gracefully skips if ``scholarly`` is not installed — callers receive an
empty list and a DEBUG log rather than an exception. Install with::

    pip install scholarly

Rate-limited to 3 seconds per request by default to avoid Google's
anti-bot protection. Do not lower this below 2 seconds.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from research_hub.search.base import SearchResult
from research_hub._useragent import user_agent

logger = logging.getLogger(__name__)

_USER_AGENT = user_agent()
_DEFAULT_DELAY = 3.0  # seconds between requests — do not lower below 2


try:
    from scholarly import scholarly as _scholarly_mod  # type: ignore[import-not-found]
    _SCHOLARLY_AVAILABLE = True
except ImportError:
    _scholarly_mod = None  # type: ignore[assignment]
    _SCHOLARLY_AVAILABLE = False


class GoogleScholarBackend:
    """Google Scholar search via the ``scholarly`` library.

    Results include citation counts and venue information not available
    from other backends. Falls back to an empty list if ``scholarly``
    is not installed (pip install scholarly).
    """

    name = "google-scholar"

    def __init__(self, delay_seconds: float = _DEFAULT_DELAY) -> None:
        self.delay = delay_seconds
        self._last_request: Optional[float] = None

    def _throttle(self) -> None:
        now = time.time()
        if self._last_request is not None:
            elapsed = now - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_request = time.time()

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> list[SearchResult]:
        """Search Google Scholar. Returns [] if scholarly is not installed."""
        if not _SCHOLARLY_AVAILABLE:
            logger.debug(
                "google-scholar backend skipped: 'scholarly' not installed "
                "(pip install scholarly)"
            )
            return []

        results: list[SearchResult] = []
        try:
            self._throttle()
            search_gen = _scholarly_mod.search_pubs(query)
            for pub in search_gen:
                if len(results) >= limit:
                    break
                result = self._convert(pub)
                if result is None:
                    continue
                if year_from and result.year and result.year < year_from:
                    continue
                if year_to and result.year and result.year > year_to:
                    continue
                results.append(result)
                if len(results) < limit:
                    # Only throttle when we plan to fetch more results.
                    self._throttle()
        except (StopIteration, RuntimeError):
            # PEP 479 (mandatory Python 3.7+): StopIteration raised inside a
            # generator is converted to RuntimeError before the caller sees it.
            # Both exceptions signal natural generator exhaustion — return
            # whatever was collected rather than discarding partial results.
            pass
        except Exception as exc:
            logger.debug("Google Scholar search failed: %s", exc)
        return results

    def get_paper(self, identifier: str) -> Optional[SearchResult]:
        results = self.search(identifier, limit=1)
        return results[0] if results else None

    def _convert(self, pub: dict) -> Optional[SearchResult]:
        bib = pub.get("bib") or {}
        title = (bib.get("title") or "").strip()
        if not title:
            return None

        year_raw = bib.get("pub_year") or bib.get("year")
        year: Optional[int] = None
        if year_raw:
            try:
                year = int(str(year_raw))
            except (ValueError, TypeError):
                pass

        authors_raw = bib.get("author") or ""
        if isinstance(authors_raw, list):
            authors = [str(a).strip() for a in authors_raw if str(a).strip()]
        elif isinstance(authors_raw, str):
            authors = [a.strip() for a in authors_raw.split(" and ") if a.strip()]
        else:
            authors = []

        url = pub.get("pub_url") or ""
        eprint_url = pub.get("eprint_url") or ""
        citation_count = int(pub.get("num_citations") or 0)
        venue = (bib.get("venue") or bib.get("journal") or "").strip()
        abstract = (bib.get("abstract") or "").strip()

        return SearchResult(
            title=title,
            doi="",
            arxiv_id="",
            abstract=abstract,
            year=year,
            authors=authors,
            venue=venue,
            url=url or eprint_url,
            citation_count=citation_count,
            pdf_url=eprint_url,
            source=self.name,
            confidence=0.5,
        )
