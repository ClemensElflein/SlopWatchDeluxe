from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from client.build import build_bytes
from client.config import config_path, read_json
from client.installer import (hooks_installed, install_files, provider_path, uninstall_files,
                             merge_hooks, is_slopwatchdeluxe, apply_changes, detect)


def setup(providers=("codex", "claude")):
    return install_files("http://127.0.0.1:8765", "test-token", providers)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_fresh_install_and_zipapp_runs(fake_home):
    config = setup()
    assert read_json(config_path())["token"] == "test-token"
    assert config_path().stat().st_mode & 0o777 == 0o600
    assert is_slopwatchdeluxe(Path(config["binary"]))
    result = subprocess.run([sys.executable, config["binary"], "--version"], capture_output=True, text=True)
    assert result.returncode == 0 and "SlopWatchDeluxe 1.0.0" in result.stdout
    for provider in ("codex", "claude"):
        assert hooks_installed(provider, config)
        assert "test-token" not in provider_path(provider).read_text()
    assert not (fake_home / ".codex/config.toml").exists()


def test_existing_hooks_and_idempotence(fake_home):
    before = {"model": "existing-model", "hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "echo existing"}, {"type": "prompt", "prompt": "Check quality"}]}]}}
    for provider in ("codex", "claude"):
        write(provider_path(provider), before)
    config = setup()
    first = {p: provider_path(p).read_bytes() for p in ("codex", "claude")}
    backups = list(fake_home.rglob("*.slopwatchdeluxe-backup-*"))
    assert len(backups) == 2
    for p in ("codex", "claude"):
        data = read_json(provider_path(p))
        assert data["model"] == before["model"]
        assert data["hooks"]["Stop"][0] == before["hooks"]["Stop"][0]
    setup()
    assert len(list(fake_home.rglob("*.slopwatchdeluxe-backup-*"))) == len(backups)
    for p in first:
        assert provider_path(p).read_bytes() == first[p]
    assert uninstall_files()
    for p in first:
        assert read_json(provider_path(p)) == before
    assert not Path(config["binary"]).exists()
    assert not config_path().exists()
    assert not uninstall_files()


def test_uninstall_preserves_hooks_added_after_install(fake_home):
    config = setup()
    path = provider_path("claude")
    data = read_json(path)
    unrelated = {"type": "command", "command": "echo someone-else-slopwatchdeluxe"}
    data["hooks"]["Stop"][0]["hooks"].append(unrelated)
    data["customSetting"] = "keep me"
    write(path, data)
    uninstall_files()
    after = read_json(path)
    assert after == {"customSetting": "keep me", "hooks": {"Stop": [{"hooks": [unrelated]}]}}


@pytest.mark.parametrize("malformed", ['{', '[]', '{"hooks": []}', '{"hooks":{"Stop":{}}}',
                                       '{"hooks":{"Stop":[{"hooks":[null]}]}}', '{"hooks":{},"hooks":{}}'])
def test_malformed_config_fails_before_writes(fake_home, malformed):
    path = provider_path("claude")
    path.parent.mkdir(parents=True)
    path.write_text(malformed)
    with pytest.raises(ValueError):
        setup()
    assert path.read_text() == malformed
    assert not provider_path("codex").exists()
    assert not config_path().exists()
    assert not (fake_home / ".local/bin/slopwatchdeluxe").exists()


def test_backup_original_bytes_and_toml_untouched(fake_home):
    path = provider_path("codex")
    path.parent.mkdir(parents=True)
    original = b'{ "description": "mine", "hooks": {} }\n'
    path.write_bytes(original)
    toml = path.parent / "config.toml"
    toml.write_text('# existing config\nnotify = ["my-notifier"]\n[features]\nhooks = true\n')
    before = toml.read_bytes()
    setup()
    backup = next(path.parent.glob("hooks.json.slopwatchdeluxe-backup-*"))
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    assert toml.read_bytes() == before


def test_reconfigure_provider_and_token(fake_home):
    setup()
    config = install_files("http://localhost:9999", "new-token", ["claude"])
    assert "codex" not in config["integrations"]
    assert read_json(provider_path("codex")) == {}
    assert hooks_installed("claude", config)
    assert config["url"] == "http://localhost:9999"


def test_custom_provider_directory_move(fake_home, monkeypatch):
    config = setup()
    old_path = provider_path("codex")
    monkeypatch.setenv("CODEX_HOME", str(fake_home / "another-codex"))
    config = setup()
    assert read_json(old_path) == {}
    assert hooks_installed("codex", config)
    uninstall_files()
    assert read_json(provider_path("codex")) == {}


def test_unrelated_binary_and_symlink_refused(fake_home):
    binary = fake_home / ".local/bin/slopwatchdeluxe"
    binary.parent.mkdir(parents=True)
    binary.write_text("not SlopWatchDeluxe")
    with pytest.raises(ValueError, match="unrelated"):
        setup()
    assert binary.read_text() == "not SlopWatchDeluxe"
    binary.unlink()
    target = fake_home / "actual.json"
    target.write_text("{}")
    path = provider_path("codex")
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        setup()
    assert path.is_symlink() and target.read_text() == "{}"


def test_uninstall_malformed_config_keeps_binary(fake_home):
    config = setup()
    provider_path("claude").write_text("broken")
    before = provider_path("codex").read_bytes()
    with pytest.raises(ValueError):
        uninstall_files()
    assert Path(config["binary"]).exists()
    assert provider_path("codex").read_bytes() == before


def test_concurrent_edit_and_rollback(fake_home, monkeypatch):
    import client.installer as installer
    a, b = fake_home / "a", fake_home / "b"
    a.write_bytes(b"old")
    b.write_bytes(b"old")
    with pytest.raises(ValueError, match="changed"):
        apply_changes([(a, b"wrong snapshot", b"new", 0o600)])
    real_write = installer.atomic_write
    def fail(path, data, mode):
        if path == b:
            raise OSError("simulated disk error")
        real_write(path, data, mode)
    monkeypatch.setattr(installer, "atomic_write", fail)
    with pytest.raises(OSError):
        apply_changes([(a, b"old", b"new", 0o600), (b, b"old", b"new", 0o600)])
    assert a.read_bytes() == b.read_bytes() == b"old"


def test_shell_quoted_install_path_and_untrusted_payload(fake_home):
    binary = fake_home / "bin weird ' $(touch BAD)" / "slopwatchdeluxe"
    config = install_files("http://127.0.0.1:1", "", ["codex"], binary=binary)
    command = config["integrations"]["codex"]["command"]
    assert shlex.split(command)[1] == str(binary)
    result = subprocess.run(command, shell=True, input=json.dumps({"session_id": "test", "hook_event_name": "Stop",
                            "last_assistant_message": "$(touch BAD)", "cwd": str(fake_home)}),
                            capture_output=True, text=True, cwd=fake_home, timeout=3)
    assert result.returncode == 0 and result.stdout == result.stderr == ""
    assert not (fake_home / "BAD").exists()


def test_build_reproducible_and_provider_detection(fake_home, monkeypatch):
    assert build_bytes() == build_bytes()
    bin_dir = fake_home / "tools"
    bin_dir.mkdir()
    tool = bin_dir / "codex"
    tool.write_text('#!/bin/sh\nprintf "codex-cli 0.154.0\\n"\n')
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert detect("codex")["supported"]
    assert detect("claude")["path"] is None
