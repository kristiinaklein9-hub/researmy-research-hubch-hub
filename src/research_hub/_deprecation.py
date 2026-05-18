"""Deprecation helpers for research-hub's public API (v0.91.0 W7, G2 #13).

research-hub had no deprecation policy: the entire module tree was
de-facto public (tests reach `research_hub.search._rank` etc.), and
CHANGELOG v0.89.0 said "deprecation TBD" for the exception import
paths. This module gives us one mechanism so we can rename / retire
surface without a hard break.

Policy
------
- Public surface = whatever `research_hub.__all__` re-exports +
  the documented CLI subcommands + the documented MCP tools.
  Everything else (underscore modules, deep imports) is internal
  and may change without a deprecation cycle.
- A deprecation always emits ``DeprecationWarning`` (visible under
  ``python -W default`` / pytest) and names the replacement + the
  release the shim is removed in.
- One minor-version grace period minimum. Removal only on a minor
  bump, never a patch.
"""

from __future__ import annotations

import functools
import warnings
from typing import Any, Callable


def warn_deprecated(
    what: str,
    *,
    replacement: str,
    removed_in: str,
    stacklevel: int = 2,
) -> None:
    """Emit a standardized DeprecationWarning.

    Parameters
    ----------
    what:
        Human-readable name of the deprecated thing
        (e.g. ``"research-hub websearch"`` or
        ``"research_hub.errors.ApiError import path"``).
    replacement:
        What the caller should use instead.
    removed_in:
        Version string the shim is scheduled to be removed in
        (e.g. ``"v2.0.0"``). Per semver, deprecated surface is
        retained for the remainder of the major and removed only at
        the next major.
    stacklevel:
        Passed through to ``warnings.warn``. Default 2 points at the
        caller of the deprecated thing, not this helper.
    """
    warnings.warn(
        f"{what} is deprecated and will be removed in {removed_in}. "
        f"Use {replacement} instead.",
        DeprecationWarning,
        stacklevel=stacklevel + 1,
    )


def deprecated_callable(
    func: Callable[..., Any],
    *,
    what: str,
    replacement: str,
    removed_in: str,
) -> Callable[..., Any]:
    """Wrap a callable so calling it emits a DeprecationWarning.

    Used for aliasing renamed functions / CLI handlers without
    breaking existing callers. The wrapped callable forwards all
    args/kwargs unchanged.
    """

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        warn_deprecated(
            what,
            replacement=replacement,
            removed_in=removed_in,
            stacklevel=3,
        )
        return func(*args, **kwargs)

    # functools.wraps copies __name__/__qualname__/__module__/__dict__/
    # __annotations__ and sets __wrapped__ for inspect.unwrap(). Override
    # only the docstring to prepend the deprecation banner.
    _wrapper.__doc__ = (
        f"DEPRECATED (removed in {removed_in}): use {replacement}. "
        f"{getattr(func, '__doc__', '') or ''}"
    )
    return _wrapper


__all__ = ["warn_deprecated", "deprecated_callable"]
