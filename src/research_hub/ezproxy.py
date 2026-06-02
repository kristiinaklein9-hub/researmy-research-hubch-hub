"""EZproxy support for institutional PDF access.

Opt-in. Two modes (configure one, then run ``research-hub ezproxy login`` once
to capture the institutional SSO cookies):

* **Hostname rewriting** (recommended) -- set ``cfg.ezproxy_host_suffix`` to the
  institution's full EZproxy host (e.g. ``ezproxy.lib.lehigh.edu``). Publisher
  hosts are rewritten in place (``www.nature.com`` ->
  ``www-nature-com.ezproxy.lib.lehigh.edu``). Preferred because it avoids the
  ``/login?qurl=`` ``EZproxyCheckBack`` JavaScript interstitial that a
  non-browser HTTP client cannot follow.
* **Login template** (legacy fallback) -- set ``cfg.ezproxy_url_template`` to a
  format template like ``https://login.youruniversity.edu/login?qurl={encoded_url}``.

After login, ``paper attach-pdfs`` wraps publisher URLs through the proxy,
falling back to the original URL on any proxy failure.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


@dataclass
class EZproxyConfig:
    """Resolved EZproxy settings for a HubConfig-like object."""

    url_template: str
    cookies_path: Path
    host_suffix: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.url_template or self.host_suffix) and self.cookies_path.exists()


def resolve_config(cfg: Any) -> EZproxyConfig:
    """Read EZproxy settings from a HubConfig-like object."""

    try:
        template = (getattr(cfg, "ezproxy_url_template", "") or "").strip()
    except Exception:
        template = ""
    try:
        host_suffix = (getattr(cfg, "ezproxy_host_suffix", "") or "").strip()
    except Exception:
        host_suffix = ""
    try:
        raw_path = getattr(cfg, "ezproxy_cookies_path", "") or ""
    except Exception:
        raw_path = ""
    try:
        cookies_path = Path(raw_path).expanduser() if raw_path else None
    except Exception:
        cookies_path = None
    if cookies_path is None:
        try:
            base = Path(getattr(cfg, "research_hub_dir", ".")).expanduser()
        except Exception:
            base = Path(".")
        cookies_path = base / "ezproxy_cookies.json"
    return EZproxyConfig(url_template=template, cookies_path=cookies_path, host_suffix=host_suffix)


def wrap_url(original_url: str, template: str = "", host_suffix: str = "") -> str:
    """Wrap an absolute publisher URL for institutional EZproxy access.

    Two EZproxy modes are supported, in priority order:

    * **Hostname rewriting** (``host_suffix`` set) -- the modern default at most
      institutions, e.g. ``www.nature.com`` -> ``www-nature-com.<suffix>``.
      Preferred when available: the proxied host is reached directly, with no
      ``/login?qurl=`` JavaScript interstitial (``EZproxyCheckBack``) that a
      non-browser HTTP client cannot follow.
    * **Login template** (``template`` containing ``{encoded_url}``) -- the
      legacy starting-point-URL form, kept as a fallback.
    """

    if host_suffix:
        rewritten = wrap_url_hostname(original_url, host_suffix)
        if rewritten != original_url:
            return rewritten
    try:
        if not template or "{encoded_url}" not in template:
            return original_url
        return template.format(encoded_url=quote(original_url, safe=""))
    except Exception:
        return original_url


def wrap_url_hostname(original_url: str, host_suffix: str) -> str:
    """Rewrite a URL's host through an EZproxy hostname-rewriting proxy.

    ``https://www.nature.com/articles/x`` -> ``https://www-nature-com.<suffix>/articles/x``.
    EZproxy host encoding doubles existing hyphens (``-`` -> ``--``) then maps
    dots to single hyphens (``.`` -> ``-``) so the transform stays reversible.
    The scheme is forced to https (proxied hosts are served over TLS) and any
    port / userinfo is dropped (academic resources are on 443). Returns the URL
    unchanged on parse failure, when no suffix is configured, or when the URL is
    already proxied (idempotent).

    ``host_suffix`` must be the institution's FULL EZproxy host (e.g.
    ``ezproxy.lib.lehigh.edu``), never a parent domain -- otherwise unrelated
    campus hosts under that parent would be wrongly treated as already-proxied.
    """

    suffix = (host_suffix or "").strip().strip(".")
    if not suffix or not original_url:
        return original_url
    try:
        parts = urlsplit(original_url)
        host = parts.hostname
        # ':' in host => IPv6 literal (urlsplit drops the brackets); rewriting
        # it would yield an invalid netloc, so leave such URLs untouched.
        if not host or ":" in host or host == suffix or host.endswith("." + suffix):
            return original_url
        rewritten_host = host.replace("-", "--").replace(".", "-") + "." + suffix
        return urlunsplit(("https", rewritten_host, parts.path, parts.query, parts.fragment))
    except Exception:
        return original_url


def load_cookies(cookies_path: Path) -> dict[str, str]:
    """Load Playwright storage-state cookies as a ``{name: value}`` dict."""

    try:
        if not cookies_path.exists():
            return {}
        import json

        data = json.loads(cookies_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cookies = data.get("cookies", []) if isinstance(data, dict) else []
    out: dict[str, str] = {}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str) and isinstance(value, str):
            out[name] = value
    return out


def login(
    cookies_path: Path,
    *,
    url_template: str = "",
    host_suffix: str = "",
    sentinel_url: str = "https://ieeexplore.ieee.org/",
    profile_dir: Path | None = None,
) -> int:
    """Open a persistent browser context and save EZproxy cookies on close."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "  [ezproxy] Playwright is not installed; cannot open browser login.\n"
            "            Install it with: pip install 'research-hub-pipeline[playwright]'",
            file=sys.stderr,
        )
        return 1

    from research_hub.notebooklm.auth import _playwright_event_loop
    from research_hub.notebooklm.auth import _tighten_state_file_perms

    cookies_path = Path(cookies_path).expanduser()
    profile = Path(profile_dir).expanduser() if profile_dir is not None else cookies_path.parent / "ezproxy_profile"
    try:
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"  [ezproxy] cannot create browser profile dir: {exc}", file=sys.stderr)
        return 1

    homepage = (
        wrap_url(sentinel_url, url_template, host_suffix)
        if (url_template or host_suffix)
        else sentinel_url
    )
    launch_kwargs = {
        "user_data_dir": str(profile),
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--password-store=basic",
        ],
        "ignore_default_args": ["--enable-automation"],
    }

    with _playwright_event_loop():
        playwright = None
        context = None
        try:
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(homepage, timeout=30_000)
            except PlaywrightError:
                pass
            print(
                "  [ezproxy] Browser opened. Complete institutional SSO, verify access,\n"
                "            then close the browser window to save cookies.",
            )
            while True:
                try:
                    if not context.pages:
                        break
                except PlaywrightError:
                    break
                time.sleep(1.0)
            context.storage_state(path=str(cookies_path))
            _tighten_state_file_perms(cookies_path)
            print(f"  [ezproxy] Saved cookies to {cookies_path}")
            return 0
        except Exception as exc:  # noqa: BLE001 - login must fail closed
            print(f"  [ezproxy] login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if playwright is not None:
                    playwright.stop()
            except Exception:
                pass
