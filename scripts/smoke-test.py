#!/usr/bin/env python3
"""Exercise packaged installation and normalized lifecycle against a live server."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from client.build import build_bytes
from client.transport import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    config = {"url": args.url.rstrip("/"), "token": os.getenv("AGENTWATCH_TEST_TOKEN", "")}
    assert request(config, "/api/v1/health")["status"] == "ok"
    created = []
    run_id = "agentwatch-smoke-" + str(uuid4())
    try:
        with tempfile.TemporaryDirectory(prefix="agentwatch-smoke-") as temp:
            home = Path(temp)
            tools = home / "tools"
            tools.mkdir()
            for name, version in (("codex", "codex-cli 0.154.0"), ("claude", "2.1.259 (Claude Code)")):
                binary = tools / name
                binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
                binary.chmod(0o755)
            source = home / "agentwatch.pyz"
            source.write_bytes(build_bytes())
            env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
                   "CLAUDE_CONFIG_DIR": str(home / ".claude"), "XDG_CONFIG_HOME": str(home / ".config"),
                   "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
                   "AGENTWATCH_TEST_TOKEN": config["token"]}
            def cli(*argv):
                result = subprocess.run([sys.executable, str(source), *argv], env=env, cwd=home,
                                        capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stderr
                return result.stdout
            print(cli("install", "--yes", "--url", config["url"], "--token-env", "AGENTWATCH_TEST_TOKEN"))
            print(cli("status"))
            installed = json.loads((home / ".config/agentwatch/config.json").read_text())
            for provider in ("codex", "claude"):
                identity = run_id + "-" + provider
                cmd = installed["integrations"][provider]["command"]
                for hook, state, extra in (("SessionStart", "IDLE", {}),
                                           ("UserPromptSubmit", "WORKING", {"prompt": "Smoke test"}),
                                           ("PermissionRequest", "WORKING", {"tool_name": "Bash"}),
                                           ("PostToolUse", "WORKING", {"tool_name": "Bash"}),
                                           ("Stop", "ATTENTION", {"last_assistant_message": "Smoke test completed"}),
                                           ("UserPromptSubmit", "WORKING", {}),
                                           ("SessionEnd", "CLOSED", {})):
                    payload = {"session_id": identity, "cwd": str(home / provider), "hook_event_name": hook, **extra}
                    result = subprocess.run(cmd, shell=True, env=env, cwd=home, input=json.dumps(payload),
                                            capture_output=True, text=True, timeout=3)
                    assert result.returncode == 0 and result.stdout == result.stderr == ""
                    rows = request(config, "/api/v1/sessions")
                    session = next(s for s in rows if s["provider_session_id"] == identity)
                    if session["id"] not in created:
                        created.append(session["id"])
                    assert session["state"] == state, (hook, session)
                path = "/api/v1/sessions/" + session["id"]
                assert request(config, path + "/archive", method="POST")["archived_at"]
                assert request(config, path + "/restore", method="POST")["archived_at"] is None
                print(provider + ": installed hook lifecycle, archive, restore OK")
            headers = {"Authorization": "Bearer " + config["token"]} if config["token"] else {}
            req = urllib.request.Request(config["url"] + "/api/v1/stream", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as response:
                assert response.readline() == b"event: change\n"
                assert response.readline().startswith(b"data: ")
                assert response.readline() == b"\n"
                request(config, "/api/v1/sessions/" + created[0], {"state": "ATTENTION"}, method="PATCH")
                assert response.readline() == b"event: change\n"
            print("SSE initial state and live change: OK")
            print(cli("uninstall"))
            assert not Path(installed["binary"]).exists()
            print("Fake HOME uninstall: OK")
    finally:
        for session_id in created:
            request(config, "/api/v1/sessions/" + session_id, method="DELETE")
    print("All smoke checks passed. Test sessions removed.")


if __name__ == "__main__":
    main()
