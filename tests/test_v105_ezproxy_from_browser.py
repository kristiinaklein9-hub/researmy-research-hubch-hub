"""v1.0.5: better EZproxy ergonomics.

- #2 `capture_cookies_from_browser` — import EZproxy cookies from an
  already-logged-in real browser via rookiepy (no Playwright popup), with
  graceful degradation when rookiepy is absent (no py3.14 wheel yet).
- #3 `detect_host_suffix` + `resolve_config` fallback — auto-derive the EZproxy
  host suffix from captured cookie domains so a one-time login enables
  hostname-rewrite without a manual `config set`.

(The #1 save-while-open `login()` refactor is Playwright-integration and is
verified manually; these unit tests cover the testable surface.)
"""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

from research_hub import ezproxy


def _write_cookies(path, cookies) -> None:
    path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")


# --- #3 detect_host_suffix -------------------------------------------------
def test_detect_host_suffix_from_proxy_cookie(tmp_path) -> None:
    p = tmp_path / "c.json"
    _write_cookies(p, [{"name": "ez", "value": "x", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"}])
    assert ezproxy.detect_host_suffix(p) == "ezproxy.lib.lehigh.edu"


def test_detect_host_suffix_prefers_shortest_ezproxy_domain(tmp_path) -> None:
    p = tmp_path / "c.json"
    _write_cookies(
        p,
        [
            {"name": "a", "value": "1", "domain": "www-nature-com.ezproxy.lib.lehigh.edu", "path": "/"},
            {"name": "b", "value": "2", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},
        ],
    )
    assert ezproxy.detect_host_suffix(p) == "ezproxy.lib.lehigh.edu"


def test_detect_host_suffix_none_when_no_ezproxy(tmp_path) -> None:
    p = tmp_path / "c.json"
    _write_cookies(p, [{"name": "x", "value": "y", "domain": ".nature.com", "path": "/"}])
    assert ezproxy.detect_host_suffix(p) == ""


def test_detect_host_suffix_missing_file(tmp_path) -> None:
    assert ezproxy.detect_host_suffix(tmp_path / "nope.json") == ""


# --- #3 resolve_config fallback --------------------------------------------
def test_resolve_config_auto_detects_host_suffix(tmp_path) -> None:
    p = tmp_path / "ezproxy_cookies.json"
    _write_cookies(p, [{"name": "ez", "value": "x", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"}])
    cfg = SimpleNamespace(
        ezproxy_url_template="",
        ezproxy_cookies_path=str(p),
        ezproxy_host_suffix="",
        research_hub_dir=str(tmp_path),
    )
    ezc = ezproxy.resolve_config(cfg)
    assert ezc.host_suffix == "ezproxy.lib.lehigh.edu"
    assert ezc.enabled is True


def test_resolve_config_explicit_host_suffix_wins(tmp_path) -> None:
    p = tmp_path / "ezproxy_cookies.json"
    _write_cookies(p, [{"name": "ez", "value": "x", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"}])
    cfg = SimpleNamespace(
        ezproxy_url_template="",
        ezproxy_cookies_path=str(p),
        ezproxy_host_suffix="explicit.example.edu",
        research_hub_dir=str(tmp_path),
    )
    assert ezproxy.resolve_config(cfg).host_suffix == "explicit.example.edu"


# --- #2 capture_cookies_from_browser ---------------------------------------
def _install_fake_rookiepy(monkeypatch, cookies) -> None:
    mod = types.ModuleType("rookiepy")
    mod.chrome = lambda domains=None: cookies
    mod.load = lambda domains=None: cookies
    monkeypatch.setitem(sys.modules, "rookiepy", mod)


def test_capture_from_browser_writes_filtered_storage_state(tmp_path, monkeypatch) -> None:
    _install_fake_rookiepy(
        monkeypatch,
        [
            {"name": "ez", "value": "sess", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},
            {"name": "other", "value": "z", "domain": ".google.com", "path": "/"},  # filtered out
        ],
    )
    out = tmp_path / "ezproxy_cookies.json"
    rc = ezproxy.capture_cookies_from_browser(out, "chrome", domain="ezproxy.lib.lehigh.edu")
    assert rc == 0
    saved = json.loads(out.read_text(encoding="utf-8"))["cookies"]
    assert [c["name"] for c in saved] == ["ez"]  # only the ezproxy-domain cookie kept
    assert saved[0]["domain"] == ".ezproxy.lib.lehigh.edu"


def test_capture_from_browser_rejects_lookalike_domain(tmp_path, monkeypatch) -> None:
    # A suffix-anchored filter must reject x-ezproxy.lib.lehigh.edu.evil.com even
    # though it contains the wanted host as a substring (W1 regression).
    _install_fake_rookiepy(
        monkeypatch,
        [
            {"name": "evil", "value": "x", "domain": "x-ezproxy.lib.lehigh.edu.evil.com", "path": "/"},
            {"name": "ez", "value": "ok", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},
        ],
    )
    out = tmp_path / "c.json"
    assert ezproxy.capture_cookies_from_browser(out, "chrome", domain="ezproxy.lib.lehigh.edu") == 0
    saved = json.loads(out.read_text(encoding="utf-8"))["cookies"]
    assert [c["name"] for c in saved] == ["ez"]  # lookalike rejected, real one kept


def test_capture_from_browser_skips_cookies_missing_name(tmp_path, monkeypatch) -> None:
    # rookiepy entries lacking name/value must not produce null cookies (W2).
    _install_fake_rookiepy(
        monkeypatch,
        [
            {"value": "novalue", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},  # no name
            {"name": "ez", "value": "ok", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},
        ],
    )
    out = tmp_path / "c.json"
    assert ezproxy.capture_cookies_from_browser(out, "chrome", domain="ezproxy.lib.lehigh.edu") == 0
    saved = json.loads(out.read_text(encoding="utf-8"))["cookies"]
    assert [c["name"] for c in saved] == ["ez"]


def test_detect_host_suffix_rejects_lookalike_label(tmp_path) -> None:
    # "short-ezproxy.evil.com" has no full "ezproxy" label -> must not win the min().
    p = tmp_path / "c.json"
    _write_cookies(
        p,
        [
            {"name": "evil", "value": "x", "domain": "short-ezproxy.evil.com", "path": "/"},
            {"name": "ez", "value": "ok", "domain": ".ezproxy.lib.lehigh.edu", "path": "/"},
        ],
    )
    assert ezproxy.detect_host_suffix(p) == "ezproxy.lib.lehigh.edu"


def test_capture_from_browser_empty_domain_errors(tmp_path, monkeypatch) -> None:
    _install_fake_rookiepy(monkeypatch, [{"name": "ez", "value": "s", "domain": ".ezproxy.x.edu", "path": "/"}])
    assert ezproxy.capture_cookies_from_browser(tmp_path / "c.json", "chrome", domain="") == 1


def test_capture_from_browser_no_matching_cookies_errors(tmp_path, monkeypatch) -> None:
    _install_fake_rookiepy(monkeypatch, [{"name": "x", "value": "y", "domain": ".google.com", "path": "/"}])
    assert (
        ezproxy.capture_cookies_from_browser(tmp_path / "c.json", "chrome", domain="ezproxy.lib.lehigh.edu")
        == 1
    )


def test_capture_from_browser_missing_rookiepy_degrades(tmp_path, monkeypatch) -> None:
    # sys.modules["rookiepy"] = None makes `import rookiepy` raise ImportError.
    monkeypatch.setitem(sys.modules, "rookiepy", None)
    assert (
        ezproxy.capture_cookies_from_browser(tmp_path / "c.json", "chrome", domain="ezproxy.lib.lehigh.edu")
        == 1
    )
