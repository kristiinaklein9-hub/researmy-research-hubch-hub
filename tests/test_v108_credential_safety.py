"""Credential-safety regression tests for v1.0.8 (P0-3, P0-4, P0-6).

P0-3: EZproxy session cookies are scoped to the proxy host and NEVER forwarded
      across an off-proxy redirect.
P0-4: every credentialed / redirect-followed fetch is http(s)-only (no file://
      / ftp:// / data: SSRF / local-file-read primitive).
P0-6: the secret-box encryption key is hardened through the real chmod_sensitive
      Windows-ACL path, not a no-op os.chmod.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from research_hub.security import host_in_suffix, is_safe_fetch_url


# --------------------------------------------------------------------------- #
# P0-4: URL safety guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x.pdf",
        "http://example.com/x.pdf",
        "https://www-nature-com.ezproxy.lib.test/article",
    ],
)
def test_is_safe_fetch_url_accepts_http_https(url):
    assert is_safe_fetch_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://host/x",
        "data:text/plain,hi",
        "//scheme-relative/x",
        "/local/path.pdf",
        "",
        None,
        "https://",  # no host
    ],
)
def test_is_safe_fetch_url_rejects_unsafe(url):
    assert is_safe_fetch_url(url) is False


@pytest.mark.parametrize(
    "host,suffix,expected",
    [
        ("ezproxy.lib.test", "ezproxy.lib.test", True),
        ("www-nature-com.ezproxy.lib.test", "ezproxy.lib.test", True),
        ("cdn.offproxy.test", "ezproxy.lib.test", False),
        ("ezproxy.lib.test.evil.com", "ezproxy.lib.test", False),  # suffix is not a subdomain
        ("", "ezproxy.lib.test", False),
        ("host", "", False),
    ],
)
def test_host_in_suffix(host, suffix, expected):
    assert host_in_suffix(host, suffix) is expected


# --------------------------------------------------------------------------- #
# P0-3 + P0-4 (httpx path): cookie scoping + scheme guard
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, *, status=200, location=None, content=b"%PDF-1.4\nx\n"):
        self.status_code = status
        self.is_redirect = location is not None
        self.is_success = 200 <= status < 300
        self.headers = (
            {"location": location} if location else {"Content-Type": "application/pdf"}
        )
        self.content = content


def test_ezproxy_cookies_not_forwarded_across_offproxy_redirect(monkeypatch):
    from research_hub.zotero import pdf_attach

    seen: list[tuple[str, dict]] = []

    def fake_get(url, *, cookies=None, follow_redirects=False, timeout=60, headers=None):
        host = urlparse(url).hostname or ""
        seen.append((host, dict(cookies or {})))
        if "ezproxy.lib.test" in host and "redirected" not in url:
            return _Resp(status=302, location="https://cdn.offproxy.test/redirected.pdf")
        return _Resp(status=200)

    monkeypatch.setattr(pdf_attach.httpx, "get", fake_get)

    result = pdf_attach._download_via_httpx_result(
        "https://www-nature-com.ezproxy.lib.test/article.pdf",
        cookies={"ezsession": "secret"},
        cookie_host="ezproxy.lib.test",
    )

    assert result.content == b"%PDF-1.4\nx\n"
    # hop 1 (proxy host) → cookie sent; hop 2 (off-proxy redirect) → NO cookie
    assert seen[0] == ("www-nature-com.ezproxy.lib.test", {"ezsession": "secret"})
    assert seen[1] == ("cdn.offproxy.test", {})


def test_download_via_httpx_rejects_file_url():
    from research_hub.zotero import pdf_attach

    result = pdf_attach._download_via_httpx_result("file:///etc/passwd", cookies={"x": "y"})
    assert result.content is None
    assert result.reason == "unsafe_url"


def test_download_via_httpx_caps_redirects(monkeypatch):
    from research_hub.zotero import pdf_attach

    def always_redirect(url, *, cookies=None, follow_redirects=False, timeout=60, headers=None):
        return _Resp(status=302, location="https://loop.test/next")

    monkeypatch.setattr(pdf_attach.httpx, "get", always_redirect)
    result = pdf_attach._download_via_httpx_result("https://loop.test/start")
    assert result.content is None
    assert result.reason == "too_many_redirects"


# --------------------------------------------------------------------------- #
# P0-4 (urllib path): pdf_fetcher scheme guard + safe redirect handler
# --------------------------------------------------------------------------- #
def test_pdf_fetcher_download_rejects_file_url(tmp_path):
    from research_hub.notebooklm import pdf_fetcher

    result = pdf_fetcher._download("file:///etc/passwd", tmp_path / "x.pdf", 5.0, source="arxiv")
    assert result.source == "not-found"
    assert "unsafe" in result.error.lower()
    assert not (tmp_path / "x.pdf").exists()


def test_pdf_fetcher_safe_redirect_handler_blocks_non_http():
    from research_hub.notebooklm import pdf_fetcher

    handler = pdf_fetcher._SafeRedirectHandler()
    # A redirect to file:// must NOT produce a follow-up Request (returns None).
    assert handler.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd") is None


# --------------------------------------------------------------------------- #
# P0-6: secret-box key routed through chmod_sensitive
# --------------------------------------------------------------------------- #
def test_secret_box_key_hardened_via_chmod_sensitive(tmp_path, monkeypatch):
    import research_hub.security as security
    from research_hub.security import secret_box

    calls: list[tuple[str, int]] = []

    def fake_chmod(path, *, mode):
        calls.append((str(path), mode))

    monkeypatch.setattr(security, "chmod_sensitive", fake_chmod)

    secret_box._ensure_key(tmp_path)

    key_path = str(tmp_path / ".secret_box.key")
    assert (key_path, 0o600) in calls  # hardened via the real ACL path, not os.chmod
    assert (tmp_path / ".secret_box.key").exists()
