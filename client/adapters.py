"""Provider payloads -> normalized facts. No session state transitions here."""
from datetime import datetime, timezone
from pathlib import Path
import socket
from uuid import uuid4


def text(value, limit=8000):
    return value[:limit] if isinstance(value, str) else None


COMMON = {
    "SessionStart": "session_started", "UserPromptSubmit": "work_started",
    "PreToolUse": "tool_started", "PostToolUse": "tool_finished",
    "PermissionRequest": "permission_required", "Stop": "turn_finished",
    "SessionEnd": "session_ended",
}


def git_metadata(cwd):
    """Read only cheap git identity, once per start; support git worktrees."""
    if not cwd or not Path(cwd).is_absolute():
        return {}
    root = Path(cwd)
    for directory in (root, *root.parents):
        dotgit = directory / ".git"
        try:
            if dotgit.is_file():
                with dotgit.open() as handle:
                    line = handle.read(4096).strip()
                if not line.startswith("gitdir: "):
                    return {}
                dotgit = directory / line[8:]
            if dotgit.is_dir():
                with (dotgit / "HEAD").open() as handle:
                    head = handle.read(1024).strip()
                return {"git_repository": str(directory),
                        "git_branch": head.removeprefix("ref: refs/heads/")}
        except (OSError, ValueError):
            return {}
    return {}


class BaseAdapter:
    events = COMMON
    input_tools = set()

    def normalize(self, payload, hostname=None, collect_git=True):
        if not isinstance(payload, dict):
            return None
        sid = text(payload.get("session_id"), 255)
        hook = payload.get("hook_event_name")
        if not sid or not isinstance(hook, str):
            return None
        event = self.events.get(hook)
        tool = payload.get("tool_name")
        if hook == "PreToolUse" and isinstance(tool, str) and tool in self.input_tools:
            event = "input_required"
        if hook == "SessionStart" and payload.get("source") == "compact":
            event = "activity"
        event = self.special_event(payload, event)
        if event is None:
            return None
        metadata = {}
        for key in ("model", "agent_type", "agent_id", "turn_id", "tool_name", "tool_use_id",
                    "source", "reason", "notification_type", "mcp_server_name", "elicitation_id"):
            value = text(payload.get(key), 255)
            if value:
                metadata[key] = value
        metadata["provider_hook"] = hook
        if isinstance(payload.get("stop_hook_active"), bool):
            metadata["stop_hook_active"] = payload["stop_hook_active"]
        if isinstance(payload.get("background_tasks"), list):
            metadata["background_task_count"] = len(payload["background_tasks"])
        message = None
        if event == "work_started":
            message = text(payload.get("prompt"))
        elif event == "turn_finished":
            message = text(payload.get("last_assistant_message"))
        elif event in ("failed", "tool_failed", "interrupted"):
            message = (text(payload.get("error_details")) or text(payload.get("error")) or
                       text(payload.get("last_assistant_message")))
            metadata["last_error"] = text(message, 2000)
        elif event in ("permission_required", "input_required"):
            inputs = payload.get("tool_input")
            inputs = inputs if isinstance(inputs, dict) else {}
            message = text(payload.get("message")) or text(inputs.get("description")) or text(inputs.get("command"))
            questions = inputs.get("questions")
            if not message and isinstance(questions, list):
                message = "\n".join(text(q.get("question"), 2000) or "" for q in questions[:4] if isinstance(q, dict))
            message = message or text(tool) or "Waiting for your input"
        cwd = text(payload.get("cwd"), 4096) or ""
        if collect_git and event == "session_started":
            metadata.update(git_metadata(cwd))
        return {"provider": self.provider, "provider_session_id": sid,
                "hostname": (hostname or socket.gethostname())[:255], "cwd": cwd,
                "event": event, "event_id": str(uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": message, "metadata": metadata}

    def special_event(self, payload, event):
        return event


class CodexAdapter(BaseAdapter):
    provider = "codex"
    events = {**COMMON, "Interrupt": "interrupted"}
    input_tools = {"request_user_input", "request_user_input_async"}


class ClaudeAdapter(BaseAdapter):
    provider = "claude"
    events = {**COMMON, "StopFailure": "failed", "PostToolUseFailure": "tool_failed",
              "Notification": None, "Elicitation": "input_required", "ElicitationResult": "input_resolved"}
    input_tools = {"AskUserQuestion", "ExitPlanMode"}

    def special_event(self, payload, event):
        if payload.get("hook_event_name") == "Notification":
            event = {
                "permission_prompt": "permission_required", "idle_prompt": "input_required",
                "elicitation_dialog": "input_required", "elicitation_url_dialog": "input_required",
                "agent_needs_input": "input_required", "elicitation_complete": "input_resolved",
                "elicitation_response": "input_resolved",
            }.get(payload.get("notification_type"))
        if event == "tool_failed" and payload.get("is_interrupt") is True:
            event = "interrupted"
        if payload.get("agent_id") and event not in (
                "permission_required", "input_required", "input_resolved", "tool_finished", "tool_failed"):
            return None
        return event


ADAPTERS = {"codex": CodexAdapter(), "claude": ClaudeAdapter()}
