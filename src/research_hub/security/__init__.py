"""Security helpers for research-hub."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:/\-]{1,256}$")
_FORBIDDEN_SEGMENTS = {"", ".", ".."}


class ValidationError(ValueError):
    """Raised when untrusted input fails validation."""


def validate_slug(value: object, *, field: str = "slug") -> str:
    """Validate that a string is safe for use as a slug/path segment."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValidationError(f"{field} has leading/trailing whitespace")
    slug = value.lower()
    if slug != value:
        raise ValidationError(f"{field}={value!r} invalid: must be lowercase")
    if not SLUG_RE.fullmatch(slug):
        raise ValidationError(
            f"{field}={value!r} invalid: must match {SLUG_RE.pattern} (lowercase a-z, 0-9, _, -)"
        )
    return slug


def validate_identifier(value: object, *, field: str = "identifier") -> str:
    """Validate a DOI/arXiv-style identifier."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string, got {type(value).__name__}")
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValidationError(
            f"{field}={value!r} invalid: contains characters outside [A-Za-z0-9._:/-]"
        )
    return value


def safe_join(root: Path, *segments: str) -> Path:
    """Join untrusted path segments to root without allowing traversal."""
    root_resolved = Path(root).resolve()
    for seg in segments:
        if not isinstance(seg, str):
            raise ValidationError(f"path segment must be string, got {type(seg).__name__}")
        if seg in _FORBIDDEN_SEGMENTS:
            raise ValidationError(f"path segment {seg!r} not allowed")
        if "/" in seg or "\\" in seg or "\x00" in seg:
            raise ValidationError(f"path segment {seg!r} contains separators")
        if seg.startswith(".") and seg in {".", ".."}:
            raise ValidationError(f"path segment {seg!r} not allowed")
    candidate = root_resolved.joinpath(*segments).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValidationError(f"path {candidate} escapes root {root_resolved}") from exc
    return candidate


_WINDOWS_ACL_WARNED = False


def _restrict_windows_acl(path: Path) -> None:
    """Restrict a path to the current user via icacls (G3 P2 #14).

    Pre-v0.91.0 `chmod_sensitive` was a silent no-op on Windows, so
    `config.json` (encrypted Zotero key), `.secret_box.key` (the
    Fernet key sitting NEXT TO the ciphertext), and NLM `state.json`
    (Google session cookies) inherited the parent's ACL — readable by
    any other account on a shared box. We now strip inheritance and
    grant Full control to the current user only. Best-effort: if
    icacls is missing or fails we warn ONCE per process rather than
    silently leaving the file world-readable.
    """
    global _WINDOWS_ACL_WARNED
    import getpass
    import subprocess

    # Skip real ACL mutation under pytest. icacls /inheritance:r on a
    # directory removes the inherited ACEs the test harness needs to
    # rmtree + recreate `.pytest-work/.../.research_hub` across runs,
    # causing FileExistsError on the next run's mkdir. Mirrors the
    # existing `"pytest" in sys.modules` guard in research_hub/__init__.py
    # for the Windows multiprocessing shim. Production behaviour
    # (user-only ACL on real config/secret/cookie files) is unchanged.
    if "pytest" in sys.modules:
        return

    try:
        user = getpass.getuser()
        # /inheritance:r removes inherited ACEs; /grant:r replaces any
        # existing explicit ACE for the user with Full control.
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and not _WINDOWS_ACL_WARNED:
            _WINDOWS_ACL_WARNED = True
            print(
                f"  [security] WARN icacls could not restrict {path} "
                f"(rc={result.returncode}); secrets are NOT OS-protected "
                f"on this Windows host. Detail: {result.stderr.strip()[:160]}",
                file=sys.stderr,
            )
    except Exception as exc:
        if not _WINDOWS_ACL_WARNED:
            _WINDOWS_ACL_WARNED = True
            print(
                f"  [security] WARN could not restrict {path} via icacls "
                f"({type(exc).__name__}: {exc}); secrets are NOT "
                f"OS-protected on this Windows host.",
                file=sys.stderr,
            )


def chmod_sensitive(path: Path, *, mode: int) -> None:
    """Best-effort permission tightening for sensitive files/dirs.

    POSIX: os.chmod to the requested mode. Windows (G3 P2 #14):
    icacls inheritance-strip + user-only Full control, since chmod
    is a no-op there. `mode` is POSIX-only; the Windows path always
    restricts to the current user regardless of the numeric value.
    """
    if sys.platform.startswith("win"):
        _restrict_windows_acl(path)
        return
    try:
        os.chmod(str(path), mode)
    except OSError:
        pass


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text atomically via tmp file + replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "SLUG_RE",
    "IDENTIFIER_RE",
    "ValidationError",
    "validate_slug",
    "validate_identifier",
    "safe_join",
    "chmod_sensitive",
    "atomic_write_text",
]
