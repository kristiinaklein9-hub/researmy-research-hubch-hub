"""v1.0.10 "Institutional access end-to-end" tests.

P1-2 (this file so far): EZproxy liveness probe + auth-state recency + the
RequiresAuthRefresh exception + the doctor ezproxy_session check. P1-1
(institutional PDF localization) tests are appended when that lands.

The probe is mocked at httpx.get throughout — zero real egress under the v1.0.9
network fence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from research_hub import ezproxy
from research_hub.ezproxy import EZproxyProbeResult, RequiresAuthRefresh, ezproxy_probe


def _ezproxy_cfg(tmp_path):
    cookies = tmp_path / "ezproxy_cookies.json"
    cookies.write_text(
        '{"cookies": [{"name": "ez", "value": "s", "domain": ".ezproxy.lib.test", "path": "/"}]}',
        encoding="utf-8",
    )
    return SimpleNamespace(
        ezproxy_url_template="",
        ezproxy_host_suffix="ezproxy.lib.test",
        ezproxy_cookies_path=str(cookies),
        research_hub_dir=str(tmp_path),
    )


class _Resp:
    def __init__(self, status, location=None):
        self.status_code = status
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}


# --------------------------------------------------------------------------- #
# P1-2: ezproxy_probe
# --------------------------------------------------------------------------- #
def test_ezproxy_probe_live_records_timestamp(tmp_path, monkeypatch):
    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200))
    result = ezproxy_probe(cfg)
    assert result.live is True
    assert ezproxy.ezproxy_last_verified(cfg) is not None  # recency persisted


def test_ezproxy_probe_expired_on_login_redirect(tmp_path, monkeypatch):
    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(
        httpx, "get",
        lambda *a, **k: _Resp(302, location="https://login.ezproxy.lib.test/login?url=x"),
    )
    result = ezproxy_probe(cfg)
    assert result.live is False
    assert "expired" in result.reason
    assert ezproxy.ezproxy_last_verified(cfg) is None  # NOT recorded on expiry


def test_ezproxy_probe_inproxy_redirect_is_live(tmp_path, monkeypatch):
    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(
        httpx, "get",
        lambda *a, **k: _Resp(302, location="https://www-nature-com.ezproxy.lib.test/articles/x"),
    )
    assert ezproxy_probe(cfg).live is True  # in-proxy redirect = served


def test_ezproxy_probe_unreachable_is_advisory_not_expired(tmp_path, monkeypatch):
    cfg = _ezproxy_cfg(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(httpx, "get", boom)
    result = ezproxy_probe(cfg)
    assert result.live is False
    assert "unreachable" in result.reason and "expired" not in result.reason


def test_ezproxy_probe_not_configured(tmp_path):
    cfg = SimpleNamespace(
        ezproxy_url_template="", ezproxy_host_suffix="",
        ezproxy_cookies_path=str(tmp_path / "none.json"), research_hub_dir=str(tmp_path),
    )
    result = ezproxy_probe(cfg)
    assert result.live is False and "not configured" in result.reason


def test_requires_auth_refresh_carries_command():
    exc = RequiresAuthRefresh("EZproxy", "research-hub ezproxy login")
    assert exc.service == "EZproxy"
    assert exc.command == "research-hub ezproxy login"
    assert "research-hub ezproxy login" in str(exc)


def test_auth_state_roundtrip(tmp_path):
    cfg = SimpleNamespace(research_hub_dir=str(tmp_path))
    assert ezproxy.ezproxy_last_verified(cfg) is None
    ezproxy.record_ezproxy_live_probe(cfg)
    assert ezproxy.ezproxy_last_verified(cfg) is not None
    assert json.loads((tmp_path / "auth_state.json").read_text(encoding="utf-8"))["ezproxy"]


# --------------------------------------------------------------------------- #
# P1-2: doctor ezproxy_session check
# --------------------------------------------------------------------------- #
def test_doctor_ezproxy_session_not_configured(tmp_path):
    from research_hub.doctor import check_ezproxy_session

    cfg = SimpleNamespace(
        ezproxy_url_template="", ezproxy_host_suffix="",
        ezproxy_cookies_path=str(tmp_path / "n.json"), research_hub_dir=str(tmp_path),
    )
    r = check_ezproxy_session(cfg)
    assert r.name == "ezproxy_session" and r.status == "INFO"


def test_doctor_ezproxy_session_expired_gives_remedy(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(
        "research_hub.ezproxy.ezproxy_probe",
        lambda c, **k: EZproxyProbeResult(False, "session expired (login redirect)", "2026-06-13T00:00:00+00:00"),
    )
    r = doctor.check_ezproxy_session(cfg)
    assert r.status == "WARN" and "ezproxy login" in r.remedy


def test_doctor_ezproxy_session_live(tmp_path, monkeypatch):
    from research_hub import doctor

    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(
        "research_hub.ezproxy.ezproxy_probe",
        lambda c, **k: EZproxyProbeResult(True, "live", "2026-06-13T00:00:00+00:00"),
    )
    r = doctor.check_ezproxy_session(cfg)
    assert r.status == "OK"


# --------------------------------------------------------------------------- #
# P1-1: institutional PDF resolution (find_institutional_pdf_url + wiring)
# --------------------------------------------------------------------------- #
_LANDING_HTML = (
    '<html><head><meta name="citation_pdf_url" '
    'content="https://www-nature-com.ezproxy.lib.test/articles/x.pdf"></head></html>'
)


class _HtmlResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self.is_success = 200 <= status < 300
        self.headers = {"Content-Type": "text/html"}


def test_extract_citation_pdf_url_both_orderings():
    from research_hub.zotero.pdf_attach import _extract_citation_pdf_url

    assert _extract_citation_pdf_url('<meta name="citation_pdf_url" content="https://x/p.pdf">') == "https://x/p.pdf"
    assert _extract_citation_pdf_url('<meta content="https://y/q.pdf" name="citation_pdf_url">') == "https://y/q.pdf"
    assert _extract_citation_pdf_url("<html>no meta here</html>") == ""


def test_find_institutional_pdf_url_disabled_returns_empty(tmp_path):
    from research_hub.zotero.pdf_attach import find_institutional_pdf_url

    cfg = SimpleNamespace(
        ezproxy_url_template="", ezproxy_host_suffix="",
        ezproxy_cookies_path=str(tmp_path / "none.json"), research_hub_dir=str(tmp_path),
    )
    assert find_institutional_pdf_url("10.1/x", cfg) == ("", "")
    assert find_institutional_pdf_url("", cfg) == ("", "")  # no DOI
    assert find_institutional_pdf_url("10.1/x", None) == ("", "")  # no cfg


def test_find_institutional_pdf_url_extracts_via_proxy(tmp_path, monkeypatch):
    from research_hub.zotero import pdf_attach

    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(pdf_attach, "_credentialed_get", lambda url, **k: (_HtmlResp(_LANDING_HTML), None))
    url, source = pdf_attach.find_institutional_pdf_url("10.1/x", cfg)
    assert source == "ezproxy-citation-pdf"
    assert url.endswith("/articles/x.pdf")


def test_find_institutional_pdf_url_no_meta_returns_empty(tmp_path, monkeypatch):
    from research_hub.zotero import pdf_attach

    cfg = _ezproxy_cfg(tmp_path)
    monkeypatch.setattr(
        pdf_attach, "_credentialed_get",
        lambda url, **k: (_HtmlResp("<html>paywall splash, no citation meta</html>"), None),
    )
    assert pdf_attach.find_institutional_pdf_url("10.1/x", cfg) == ("", "")


def test_plan_attach_institutional_fallback_when_no_oa(monkeypatch):
    from research_hub.zotero import pdf_attach

    monkeypatch.setattr(pdf_attach, "find_pdf_url", lambda **k: ("", ""))  # no OA
    monkeypatch.setattr(
        pdf_attach, "find_institutional_pdf_url",
        lambda doi, cfg, **k: ("https://www-nature-com.ezproxy.lib.test/x.pdf", "ezproxy-citation-pdf"),
    )
    items = [{"key": "K1", "data": {"DOI": "10.1/x", "title": "Paywalled"}}]
    plans = pdf_attach.plan_attach_for_items(items, cfg=SimpleNamespace())
    assert plans[0].pdf_url.endswith("/x.pdf")
    assert plans[0].source == "ezproxy-citation-pdf"
    assert plans[0].error == ""


def test_plan_attach_no_cfg_skips_institutional(monkeypatch):
    from research_hub.zotero import pdf_attach

    monkeypatch.setattr(pdf_attach, "find_pdf_url", lambda **k: ("", ""))
    items = [{"key": "K1", "data": {"DOI": "10.1/x", "title": "X"}}]
    plans = pdf_attach.plan_attach_for_items(items)  # cfg defaults None → backward compat
    assert plans[0].pdf_url == "" and plans[0].error == "no_oa_record"


def test_pdf_fetcher_institutional_fallback(tmp_path, monkeypatch):
    from research_hub.notebooklm import pdf_fetcher
    from research_hub.zotero import pdf_attach
    from research_hub.zotero.pdf_attach import _PdfBytesResult

    monkeypatch.setattr(pdf_fetcher, "_query_unpaywall", lambda doi, t: "")  # OA fails
    monkeypatch.setattr(
        pdf_attach, "find_institutional_pdf_url",
        lambda doi, cfg, **k: ("https://www-x.ezproxy.lib.test/p.pdf", "ezproxy-citation-pdf"),
    )
    monkeypatch.setattr(
        pdf_attach, "_download_pdf_bytes_with_ezproxy_result",
        lambda url, **k: _PdfBytesResult(b"%PDF-1.4\nok\n", status=200),
    )
    result = pdf_fetcher.fetch_paper_pdf("10.1/x", "paper", tmp_path / "pdfs", cfg=SimpleNamespace())
    assert result.source == "ezproxy"
    assert result.path.read_bytes().startswith(b"%PDF")


def test_pdf_fetcher_no_cfg_no_institutional(tmp_path, monkeypatch):
    from research_hub.notebooklm import pdf_fetcher

    monkeypatch.setattr(pdf_fetcher, "_query_unpaywall", lambda doi, t: "")
    result = pdf_fetcher.fetch_paper_pdf("10.1/x", "paper", tmp_path / "pdfs")  # no cfg
    assert result.source == "not-found"


# --------------------------------------------------------------------------- #
# P1-1: adversarial security regression (pin the properties the v1.0.10
# adversarial review verified — sound today, guarded against regression).
# --------------------------------------------------------------------------- #
def test_find_institutional_rejects_non_http_citation_pdf_url(tmp_path, monkeypatch):
    """A poisoned landing page whose citation_pdf_url is file:// must NOT be
    returned as a fetchable URL (defense-in-depth before the download guard)."""
    from research_hub.zotero import pdf_attach

    poisoned = '<meta name="citation_pdf_url" content="file:///etc/passwd">'
    monkeypatch.setattr(pdf_attach, "_credentialed_get", lambda url, **k: (_HtmlResp(poisoned), None))
    assert pdf_attach.find_institutional_pdf_url("10.1/x", _ezproxy_cfg(tmp_path)) == ("", "")


def test_find_institutional_rejects_file_landing_with_zero_fetches(tmp_path, monkeypatch):
    """A poisoned OpenAlex landing_page_url=file:// must short-circuit BEFORE any
    proxied fetch (is_safe_fetch_url(landing) guard precedes _credentialed_get)."""
    from research_hub.zotero import pdf_attach

    def _must_not_fetch(url, **k):
        raise AssertionError(f"unexpected fetch of {url!r} for a file:// landing")

    monkeypatch.setattr(pdf_attach, "_credentialed_get", _must_not_fetch)
    record = {"primary_location": {"landing_page_url": "file:///etc/passwd"}}
    assert pdf_attach.find_institutional_pdf_url("10.1/x", _ezproxy_cfg(tmp_path), openalex_record=record) == ("", "")


def test_find_institutional_landing_fetch_is_cookie_scoped(tmp_path, monkeypatch):
    """The landing-HTML fetch must be cookie-scoped to the proxy suffix (passes
    cookie_host=host_suffix to _credentialed_get) so the SSO session can't leak
    on an off-proxy redirect during landing resolution."""
    from research_hub.zotero import pdf_attach

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return (_HtmlResp(_LANDING_HTML), None)

    monkeypatch.setattr(pdf_attach, "_credentialed_get", fake_get)
    pdf_attach.find_institutional_pdf_url("10.1/x", _ezproxy_cfg(tmp_path))
    assert captured.get("cookie_host") == "ezproxy.lib.test"
