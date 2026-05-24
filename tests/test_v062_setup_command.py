from __future__ import annotations

from types import SimpleNamespace


def test_setup_runs_init_then_install_then_login(monkeypatch):
    from research_hub import setup_command

    calls: list[str] = []
    monkeypatch.setattr(
        "research_hub.init_wizard.run_init",
        lambda **kwargs: calls.append("init") or 0,
    )
    monkeypatch.setattr(
        "research_hub.cli._cmd_install",
        lambda args: calls.append(f"install:{args.platform}") or 0,
    )
    monkeypatch.setattr(
        setup_command,
        "run_notebooklm_login",
        lambda: calls.append("login") or 0,
    )

    args = SimpleNamespace(
        vault="C:/vault",
        persona="researcher",
        skip_install=False,
        skip_login=False,
        platform="codex",
    )
    assert setup_command.run_setup(args) == 0
    assert calls == ["init", "install:codex", "login"]


def test_interactive_setup_does_not_launch_second_login(monkeypatch):
    from research_hub import setup_command

    calls: list[str] = []
    monkeypatch.setattr(
        "research_hub.init_wizard.run_init",
        lambda **kwargs: calls.append("init") or 0,
    )
    monkeypatch.setattr(
        "research_hub.cli._cmd_install",
        lambda args: calls.append(f"install:{args.platform}") or 0,
    )
    monkeypatch.setattr(
        setup_command,
        "run_notebooklm_login",
        lambda: calls.append("login") or 0,
    )
    monkeypatch.setattr(setup_command, "detect_host", lambda: "claude-code")

    args = SimpleNamespace(
        vault=None,
        persona=None,
        skip_install=False,
        skip_login=False,
        skip_sample=True,
        platform=None,
        no_browser=False,
    )
    assert setup_command.run_setup(args) == 0
    assert calls == ["init", "install:claude-code"]


def test_setup_skip_install_and_skip_login(monkeypatch):
    from research_hub import setup_command

    calls: list[str] = []
    monkeypatch.setattr(
        "research_hub.init_wizard.run_init",
        lambda **kwargs: calls.append("init") or 0,
    )
    monkeypatch.setattr(
        "research_hub.cli._cmd_install",
        lambda args: calls.append("install") or 0,
    )
    monkeypatch.setattr(
        setup_command,
        "run_notebooklm_login",
        lambda: calls.append("login") or 0,
    )

    args = SimpleNamespace(
        vault="C:/vault",
        persona="researcher",
        skip_install=True,
        skip_login=True,
        platform=None,
    )
    assert setup_command.run_setup(args) == 0
    assert calls == ["init"]


def test_setup_notebooklm_login_uses_auto_detect(monkeypatch, tmp_path):
    from research_hub import setup_command

    calls: list[dict] = []
    cfg = SimpleNamespace(research_hub_dir=tmp_path / ".research_hub")
    monkeypatch.setattr("research_hub.config.get_config", lambda: cfg)

    def fake_login_nlm(user_data_dir, **kwargs):
        calls.append({"user_data_dir": user_data_dir, **kwargs})
        return 0

    monkeypatch.setattr("research_hub.notebooklm.auth.login_nlm", fake_login_nlm)

    assert setup_command.run_notebooklm_login() == 0
    assert calls[0]["auto_detect"] is True
    assert calls[0]["wait_timeout"] == 300
    assert calls[0]["state_file"] == cfg.research_hub_dir / "nlm_sessions" / "state.json"


def test_detect_host_from_env(monkeypatch):
    from research_hub.setup_command import detect_host

    monkeypatch.setenv("RH_HOST", "cursor")
    assert detect_host() == "cursor"


def test_autonomous_setup_reports_extended_llm_cli(monkeypatch, tmp_path):
    from research_hub import setup_command

    monkeypatch.setattr("research_hub.llm_cli.detect_llm_cli", lambda: "opencode")
    monkeypatch.setattr(setup_command, "_probe_required_env_vars", lambda _env: ({}, []))
    monkeypatch.setattr(setup_command, "_probe_zotero_reachability", lambda _env: (True, ""))

    report = setup_command.run_autonomous(vault=tmp_path, persona="agent")

    assert report.llm_cli_detected == "opencode"
    assert report.ready is True


def test_skill_platform_maps_cursor_cli_to_cursor_installer():
    from research_hub import setup_command

    assert setup_command._skill_platform(None, "cursor") == "cursor"
    assert setup_command._skill_platform(None, "opencode") is None
