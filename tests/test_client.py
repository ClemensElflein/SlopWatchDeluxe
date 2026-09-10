import json
import socket
import subprocess
import sys
import threading
import time

import pytest

from client.adapters import ADAPTERS, git_metadata
from client.config import endpoint
from client.installer import install_files


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_adapter_common_lifecycle(provider):
    adapter = ADAPTERS[provider]
    for hook, expected in (("SessionStart", "session_started"), ("UserPromptSubmit", "work_started"),
                           ("Stop", "turn_finished"), ("PermissionRequest", "permission_required"),
                           ("PreToolUse", "tool_started"), ("PostToolUse", "tool_finished"), ("SessionEnd", "session_ended")):
        payload = {"session_id": "abc", "cwd": "/work", "hook_event_name": hook,
                   "prompt": "Help", "last_assistant_message": "Done", "model": "model-id"}
        event = adapter.normalize(payload, hostname="dev", collect_git=False)
        assert event["event"] == expected
        assert event["provider_session_id"] == "abc" and event["hostname"] == "dev"
        assert event["metadata"]["model"] == "model-id"
        assert "state" not in event
    assert adapter.normalize({"hook_event_name": "Stop"}) is None
    assert adapter.normalize({"session_id": "abc", "hook_event_name": "SubagentStop"}) is None


def test_claude_failure_input_and_subagent():
    def normalize(**data):
        return ADAPTERS["claude"].normalize({"session_id": "s", **data}, collect_git=False)
    assert normalize(hook_event_name="StopFailure", error="rate_limit")["event"] == "failed"
    assert normalize(hook_event_name="PostToolUseFailure", error="nonzero")["event"] == "tool_failed"
    assert normalize(hook_event_name="PostToolUseFailure", is_interrupt=True)["event"] == "interrupted"
    assert normalize(hook_event_name="PreToolUse", tool_name="AskUserQuestion")["event"] == "input_required"
    assert normalize(hook_event_name="Notification", notification_type="permission_prompt")["event"] == "permission_required"
    assert normalize(hook_event_name="Notification", notification_type="auth_success") is None
    assert normalize(hook_event_name="PreToolUse", tool_name="Read", agent_id="child") is None
    assert normalize(hook_event_name="PermissionRequest", tool_name="Bash", agent_id="child")["event"] == "permission_required"
    assert normalize(hook_event_name="Elicitation")["event"] == "input_required"
    event = normalize(hook_event_name="ElicitationResult", content={"password": "secret"})
    assert event["event"] == "input_resolved" and "secret" not in json.dumps(event)


def test_codex_compaction_and_user_input():
    adapter = ADAPTERS["codex"]
    assert adapter.normalize({"session_id": "s", "hook_event_name": "SessionStart", "source": "compact"})["event"] == "activity"
    assert adapter.normalize({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "request_user_input"})["event"] == "input_required"
    assert adapter.normalize({"session_id": "s", "hook_event_name": "Interrupt"})["event"] == "interrupted"


def test_git_metadata_and_worktree(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/feature/demo\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    assert git_metadata(str(sub)) == {"git_repository": str(tmp_path), "git_branch": "feature/demo"}
    worktree = tmp_path / "tree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../.git\n")
    assert git_metadata(str(worktree))["git_branch"] == "feature/demo"


@pytest.mark.parametrize("value", ["file:///etc/passwd", "ftp://server", "http://a@b", "http://a?token=x", "http://a/#x", "http://a:99999", "http://a\n"])
def test_bad_endpoints(value):
    # Surrounding whitespace is intentionally accepted and stripped.
    if value == "http://a\n":
        assert endpoint(value) == "http://a"
    else:
        with pytest.raises(ValueError):
            endpoint(value)


def test_hook_silent_on_malformed_input_and_unreachable_server(fake_home):
    config = install_files("http://127.0.0.1:1", "", ["codex"])
    for raw in ("not json", "[]", '{"session_id":"abc","hook_event_name":"Stop"}', '"oversize' + 'x' * 1048576):
        start = time.monotonic()
        result = subprocess.run([sys.executable, config["binary"], "hook", "--provider", "codex"], input=raw,
                                capture_output=True, text=True, timeout=3)
        assert result.returncode == 0 and not result.stdout and not result.stderr
        assert time.monotonic() - start < 2


def test_hook_total_deadline_even_when_peer_stalls(fake_home):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    finished = threading.Event()
    def slow_server():
        try:
            conn, _ = listener.accept()
            with conn:
                finished.wait(3)
        finally:
            listener.close()
    worker = threading.Thread(target=slow_server, daemon=True)
    worker.start()
    config = install_files(f"http://127.0.0.1:{listener.getsockname()[1]}", "", ["claude"])
    start = time.monotonic()
    try:
        result = subprocess.run([sys.executable, config["binary"], "hook", "--provider", "claude"],
                                input='{"session_id":"abc","hook_event_name":"Stop"}', capture_output=True, text=True, timeout=3)
        assert time.monotonic() - start < 1.8
        assert result.returncode == 0 and result.stdout == result.stderr == ""
    finally:
        finished.set()
        worker.join(3)


def test_hook_stdin_deadline(fake_home):
    config = install_files("http://127.0.0.1:1", "", ["codex"])
    start = time.monotonic()
    process = subprocess.Popen([sys.executable, config["binary"], "hook", "--provider", "codex"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.wait(timeout=2) == 0
    assert time.monotonic() - start < 1.8
    assert process.stdout.read() == process.stderr.read() == b""
    process.stdin.close()
