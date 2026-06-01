"""ARCH-1 — the outbound HTTP User-Agent must track ``__version__``.

Background: the UA literal (``research-hub/<version> (<comment>)``) was
copy-pasted into ~20 call sites. Three drifted to stale pins (``0.4.1``,
``0.9.0``, ``0.43``) while the package marched on, so our UA silently
stopped advertising the real version. ARCH-1 centralized the string in
:func:`research_hub._useragent.user_agent`.

Two invariants:

1. :func:`user_agent` rebuilds the UA from the live ``__version__`` (and
   honors the comment-suffix conventions still in use).
2. VERSION-SCAN GATE — no source file under ``src/research_hub`` may
   reintroduce a hardcoded ``research-hub/<major>.<minor>`` UA literal.
   ``_useragent.py`` is the single allowed home for the format string;
   everything else must call the helper. This is what stops the stale-pin
   bug from coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

import research_hub
from research_hub._useragent import DEFAULT_COMMENT, user_agent


REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = REPO_ROOT / "src" / "research_hub"

# A hardcoded UA literal looks like "research-hub/1.0" / "research-hub/0.43".
# The bare repo URL (github.com/WenyuChiou/research-hub) has NO version, so
# this pattern only ever matches version-bearing UA strings.
_HARDCODED_UA_RE = re.compile(r"research-hub/[0-9]+\.[0-9]+")

# The ONE file allowed to contain the format string (it builds the UA). Any
# genuine future coincidental (non-UA) match elsewhere can be added here with
# a justifying comment — but default-deny keeps the gate honest.
_ALLOWED_FILES = frozenset({"_useragent.py"})


def test_user_agent_tracks_version():
    """Default UA = research-hub/<__version__> (<repo url>)."""
    assert user_agent() == f"research-hub/{research_hub.__version__} ({DEFAULT_COMMENT})"


def test_user_agent_bare_form():
    """comment=None yields the bare research-hub/<version> form (the shape
    the formerly-stale ad-hoc importer/operations/verify sites used)."""
    assert user_agent(None) == f"research-hub/{research_hub.__version__}"
    assert user_agent("") == f"research-hub/{research_hub.__version__}"


def test_user_agent_mailto_form():
    """Polite-pool APIs (Crossref/PubMed/OpenAlex) keep their mailto comment."""
    assert (
        user_agent("mailto:a@b.invalid")
        == f"research-hub/{research_hub.__version__} (mailto:a@b.invalid)"
    )


def test_no_hardcoded_user_agent_literals():
    """VERSION-SCAN GATE: fail if any module reintroduces a hardcoded
    'research-hub/<digits>.<digits>' UA literal outside _useragent.py.

    Prevents the ARCH-1 regression where UA strings were copy-pasted and
    then drifted from __version__.
    """
    offenders: list[str] = []
    for path in sorted(PKG_ROOT.rglob("*.py")):
        if path.name in _ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _HARDCODED_UA_RE.search(line):
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Hardcoded 'research-hub/<version>' User-Agent literal(s) found — "
        "call research_hub._useragent.user_agent() instead:\n  "
        + "\n  ".join(offenders)
    )
