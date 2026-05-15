from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_run_init_sample_copies_vault_without_probes(tmp_path, monkeypatch, capsys):
    from research_hub import init_wizard

    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        init_wizard.platformdirs,
        "user_config_dir",
        lambda *args, **kwargs: str(config_dir),
    )
    monkeypatch.setattr(
        init_wizard,
        "_check_first_run_readiness",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("readiness probe should be skipped")),
    )
    monkeypatch.setattr(
        "requests.head",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("zotero probe should be skipped")),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (_ for _ in ()).throw(AssertionError("sample init should not prompt")),
    )

    vault = tmp_path / "demo"
    rc = init_wizard.run_init(vault_root=str(vault), non_interactive=True, sample=True)

    assert rc == 0
    assert (vault / "_HOME.md").exists()
    assert not (config_dir / "config.json").exists()
    out = capsys.readouterr().out
    assert "Sample vault ready at" in out
    assert "python -m research_hub describe" in out


def test_cli_routes_init_sample_flag(monkeypatch):
    from research_hub import cli

    calls: list[dict[str, object]] = []

    def fake_run_init(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("research_hub.init_wizard.run_init", fake_run_init)

    rc = cli.main(["init", "--sample", "--vault", "kb"])

    assert rc == 0
    assert calls == [{"vault_root": "kb", "zotero_key": None, "zotero_library_id": None, "non_interactive": False, "persona": None, "no_browser": False, "sample": True}]


def test_run_init_sample_refuses_non_empty_destination(tmp_path, monkeypatch, capsys):
    """P0 guard (v0.89.1): if --vault points at a non-empty directory,
    refuse to run rather than silently rmtree the user's existing work
    via copy_sample_vault()."""
    from research_hub import init_wizard

    vault = tmp_path / "real-vault"
    vault.mkdir()
    sentinel = vault / "important.md"
    sentinel.write_text("user's irreplaceable notes\n", encoding="utf-8")
    subdir = vault / "raw"
    subdir.mkdir()
    (subdir / "paper.md").write_text("ingested paper\n", encoding="utf-8")

    # If the guard fails, copy_sample_vault would be invoked and rmtree the dir.
    # Patch it to a hard fail to detect that case loudly.
    monkeypatch.setattr(
        "research_hub.sample_vault.copy_sample_vault",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("guard should have prevented copy_sample_vault call")
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (_ for _ in ()).throw(AssertionError("guard should not prompt")),
    )

    rc = init_wizard.run_init(vault_root=str(vault), non_interactive=True, sample=True)

    assert rc == 1
    # Sentinel + subdir must survive untouched
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "user's irreplaceable notes\n"
    assert (subdir / "paper.md").exists()
    out = capsys.readouterr().out
    assert "is not empty" in out
    assert "REPLACES" in out


def test_cli_init_sample_subprocess_closed_stdin(tmp_path):
    repo_root = Path.cwd()
    vault = tmp_path / "sample-vault"
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"

    result = subprocess.run(
        [sys.executable, "-m", "research_hub", "init", "--sample", "--vault", str(vault)],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (vault / "_HOME.md").exists()
    assert "Sample vault ready at" in result.stdout
