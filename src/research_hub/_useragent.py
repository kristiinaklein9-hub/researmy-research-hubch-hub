"""Single source of truth for the outbound HTTP ``User-Agent`` header.

Historically the UA literal (``research-hub/<version> (<comment>)``) was
copy-pasted into ~20 call sites. That drifted: three sites were left pinned
at stale versions (``0.4.1``, ``0.9.0``, ``0.43``) while the package moved on,
so our UA stopped tracking :data:`research_hub.__version__`. This module
rebuilds the string from ``__version__`` on every call, so the UA can never
go stale again.

Two comment conventions exist in the wild and are both preserved:

* the house default points at the repo URL --
  ``research-hub/<ver> (https://github.com/WenyuChiou/research-hub)`` -- used
  by every search backend and the NotebookLM PDF fetcher;
* polite-pool APIs (Crossref, PubMed, OpenAlex) want a contact mailto --
  ``research-hub/<ver> (mailto:...)``.

Pass ``comment=None`` for the bare ``research-hub/<ver>`` form (the shape the
three formerly-stale ad-hoc sites used).

NOTE: deliberately browser-masquerading UAs (``Mozilla/5.0 ... Chrome/...`` in
``notebooklm/pdf_fetcher.py``, ``notebooklm/url_quality.py``,
``zotero/pdf_attach.py``) and the version-less DOI-resolver UA in
``authenticity.py`` are NOT built here -- they intentionally do not advertise
``research-hub/<version>`` and must stay as-is to get past publisher bot
filters.
"""

from __future__ import annotations

from research_hub import __version__

#: House default comment: the public repo URL.
DEFAULT_COMMENT = "https://github.com/WenyuChiou/research-hub"


def user_agent(comment: str | None = DEFAULT_COMMENT) -> str:
    """Return the outbound ``User-Agent`` string for *this* package version.

    Args:
        comment: Parenthetical contact/info appended after the version. Defaults
            to the repo URL (the modal house pattern). Pass a
            ``"mailto:you@example.com"`` string for polite-pool APIs, or
            ``None`` for the bare ``research-hub/<version>`` form.

    Returns:
        ``"research-hub/<version> (<comment>)"`` -- or ``"research-hub/<version>"``
        when *comment* is ``None`` / empty.
    """
    base = f"research-hub/{__version__}"
    if comment:
        return f"{base} ({comment})"
    return base
