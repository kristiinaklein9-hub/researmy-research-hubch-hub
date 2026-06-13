"""v1.0.4: hostname-rewriting EZproxy mode.

Lehigh (and most modern institutions) run EZproxy in hostname-rewriting mode
(``www.nature.com`` -> ``www-nature-com.ezproxy.lib.lehigh.edu``) rather than the
legacy ``/login?qurl=`` starting-point form. The latter returns an
``EZproxyCheckBack`` JavaScript interstitial that a non-browser HTTP client
(research-hub's httpx PDF fetch) cannot follow, so paywalled PDFs were never
retrieved. These tests pin the host transform + the dual-mode ``wrap_url``.
"""
from __future__ import annotations

from types import SimpleNamespace

from research_hub import ezproxy

SUFFIX = "ezproxy.lib.lehigh.edu"


def test_hostname_rewrite_basic() -> None:
    assert (
        ezproxy.wrap_url_hostname("https://www.nature.com/articles/nature14539", SUFFIX)
        == "https://www-nature-com.ezproxy.lib.lehigh.edu/articles/nature14539"
    )


def test_hostname_rewrite_preserves_path_query_fragment_and_forces_https() -> None:
    out = ezproxy.wrap_url_hostname(
        "http://www.nature.com/articles/x.pdf?download=true#sec", SUFFIX
    )
    assert out == "https://www-nature-com.ezproxy.lib.lehigh.edu/articles/x.pdf?download=true#sec"


def test_hostname_rewrite_doubles_existing_hyphens() -> None:
    # EZproxy doubles '-' then maps '.' -> '-' so the encoding is reversible.
    assert (
        ezproxy.wrap_url_hostname("https://my-host.example.com/p", SUFFIX)
        == "https://my--host-example-com.ezproxy.lib.lehigh.edu/p"
    )


def test_hostname_rewrite_is_idempotent_when_already_proxied() -> None:
    already = "https://www-nature-com.ezproxy.lib.lehigh.edu/articles/x"
    assert ezproxy.wrap_url_hostname(already, SUFFIX) == already


def test_hostname_rewrite_empty_suffix_returns_original() -> None:
    u = "https://www.nature.com/x"
    assert ezproxy.wrap_url_hostname(u, "") == u


def test_hostname_rewrite_bad_input_returns_original() -> None:
    assert ezproxy.wrap_url_hostname("not a url", SUFFIX) == "not a url"
    assert ezproxy.wrap_url_hostname("", SUFFIX) == ""


def test_hostname_rewrite_skips_ipv6_literal() -> None:
    # urlsplit drops the [] brackets from IPv6 hosts; rewriting would yield an
    # invalid netloc, so such URLs must pass through untouched.
    u = "https://[::1]/path"
    assert ezproxy.wrap_url_hostname(u, SUFFIX) == u


def test_wrap_url_prefers_host_suffix_over_template() -> None:
    template = "https://login.ezproxy.lib.lehigh.edu/login?qurl={encoded_url}"
    # host-rewrite must win (no /login?qurl= interstitial)
    assert (
        ezproxy.wrap_url("https://www.nature.com/x", template, SUFFIX)
        == "https://www-nature-com.ezproxy.lib.lehigh.edu/x"
    )


def test_wrap_url_falls_back_to_template_without_suffix() -> None:
    template = "https://login.ezproxy.lib.lehigh.edu/login?qurl={encoded_url}"
    out = ezproxy.wrap_url("https://www.nature.com/x", template, "")
    assert out.startswith("https://login.ezproxy.lib.lehigh.edu/login?qurl=")
    assert "%3A%2F%2F" in out  # the target was percent-encoded


def test_wrap_url_returns_original_when_neither_configured() -> None:
    u = "https://www.nature.com/x"
    assert ezproxy.wrap_url(u, "", "") == u


def test_resolve_config_reads_host_suffix_and_is_enabled(tmp_path) -> None:
    cookies = tmp_path / "ezproxy_cookies.json"
    cookies.write_text('{"cookies": []}', encoding="utf-8")
    cfg = SimpleNamespace(
        ezproxy_url_template="",
        ezproxy_cookies_path=str(cookies),
        ezproxy_host_suffix="ezproxy.lib.lehigh.edu",
        research_hub_dir=str(tmp_path),
    )
    ezc = ezproxy.resolve_config(cfg)
    assert ezc.host_suffix == "ezproxy.lib.lehigh.edu"
    # enabled with host_suffix alone (no template) + an existing cookie file
    assert ezc.enabled is True


def test_resolve_config_not_enabled_without_cookie_file(tmp_path) -> None:
    cfg = SimpleNamespace(
        ezproxy_url_template="",
        ezproxy_cookies_path=str(tmp_path / "missing.json"),
        ezproxy_host_suffix="ezproxy.lib.lehigh.edu",
        research_hub_dir=str(tmp_path),
    )
    assert ezproxy.resolve_config(cfg).enabled is False


def test_pdf_attach_uses_hostname_rewrite_when_host_suffix_set(tmp_path, monkeypatch) -> None:
    """Shipping path: with ``ezproxy_host_suffix`` set, the PDF fetch must hit
    the rewritten host (``www-nature-com.<suffix>``), NOT the ``/login?qurl=``
    template (which returns an ``EZproxyCheckBack`` JS interstitial httpx cannot
    follow). Mirrors the template-mode test in test_ezproxy.py.
    """
    from research_hub.zotero import pdf_attach

    cookies_path = tmp_path / "ezproxy_cookies.json"
    cookies_path.write_text('{"cookies": [{"name": "ez", "value": "cookie"}]}', encoding="utf-8")
    cfg = SimpleNamespace(
        ezproxy_url_template="https://login.ezproxy.lib.lehigh.edu/login?qurl={encoded_url}",
        ezproxy_host_suffix="ezproxy.lib.lehigh.edu",
        ezproxy_cookies_path=str(cookies_path),
    )
    calls: list[str] = []

    def fake_download_result(url, *, cookies=None, cookie_host="", timeout=60, max_size_mb=25):
        calls.append(url)
        from research_hub.zotero.pdf_attach import _PdfBytesResult

        if "ezproxy.lib.lehigh.edu" in url:
            return _PdfBytesResult(b"%PDF-1.4\nfixture\n", status=200)
        return _PdfBytesResult(None, status=404, reason="not_found_404")

    monkeypatch.setattr(pdf_attach, "_download_via_httpx_result", fake_download_result)

    result = pdf_attach._download_pdf_bytes_with_ezproxy_result(
        "https://www.nature.com/articles/nature14539.pdf",
        cfg=cfg,
        timeout=60,
        max_size_mb=25,
    )

    assert result.content == b"%PDF-1.4\nfixture\n"
    # the FIRST fetch must target the hostname-rewritten URL, not the qurl template
    assert calls[0] == "https://www-nature-com.ezproxy.lib.lehigh.edu/articles/nature14539.pdf"
