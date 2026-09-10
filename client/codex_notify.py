"""Codex's argv-based completion callback and lossless top-level TOML edits."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib

from .config import load_config


def notify_value(raw):
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Codex configuration exceeds 4 MiB")
    value = tomllib.loads(raw.decode()).get("notify")
    if value is not None and (not isinstance(value, list) or
                              any(not isinstance(arg, str) for arg in value)):
        raise ValueError("Codex notify must be an array of strings")
    return value


def replace_notify(raw, assignment):
    """Replace only the root notify assignment, retaining all other original bytes.

    Parse complete prefixes to distinguish real keys from text inside multiline
    strings, comments, arrays and tables. tomllib validates the complete document.
    """
    value = notify_value(raw)
    if value is None:
        return (assignment or b"") + raw, None
    end = previous_end = 0
    for line in raw.splitlines(keepends=True):
        end += len(line)
        try:
            prefix = tomllib.loads(raw[:end].decode())
        except tomllib.TOMLDecodeError:
            continue
        if "notify" in prefix:
            return raw[:previous_end] + (assignment or b"") + raw[end:], raw[previous_end:end]
        previous_end = end
    raise ValueError("Cannot locate Codex notify assignment")


def prepare_changes(records, new_records, config_file, binary, snapshot):
    """Plan install, migration, directory moves and uninstall before any writes."""
    changes = []
    old_record = records.get("codex", {}).get("notify")
    new_record = new_records.get("codex")
    new_path = Path(new_record["path"]).with_name("config.toml") if new_record else None
    paths = dict.fromkeys(([Path(old_record["path"])] if old_record else []) +
                          ([new_path] if new_path else []))
    for path in paths:
        original = snapshot(path)
        raw = original or b""
        was_missing = original is None
        if old_record and path == Path(old_record["path"]) and notify_value(raw) == old_record["command"]:
            previous = old_record["previous"]
            raw, _ = replace_notify(raw, previous.encode() if previous is not None else None)
            was_missing = old_record["was_missing"]
        if path == new_path:
            previous_command = notify_value(raw)
            argv = [sys.executable, str(binary), "notify", "--config", str(config_file),
                    "--slopwatchdeluxe-managed", "v1"]
            assignment = ("notify = " + json.dumps(argv, ensure_ascii=False) + "\n").encode()
            raw, previous = replace_notify(raw, assignment)
            # Verify both TOML syntax and the exact argv (JSON escapes overlap TOML).
            if notify_value(raw) != argv:
                raise ValueError("Cannot encode Codex notify command")
            new_record["notify"] = {"path": str(path), "command": argv,
                                    "previous": previous.decode() if previous is not None else None,
                                    "previous_command": previous_command, "was_missing": was_missing}
        updated = None if was_missing and not raw else raw
        if original != updated:
            mode = path.stat().st_mode & 0o777 if original is not None else 0o600
            changes.append((path, original, updated, mode))
    return changes


def handle_notification(payload, config_file=None):
    # Preserve an existing user's notifier with literal argv, never a shell.
    # Launch independently so a slow/broken notifier cannot delay dashboard events.
    try:
        if len(payload) > 1048576:
            return 0
        config = load_config(config_file)
        record = config.get("integrations", {}).get("codex", {}).get("notify", {})
        previous = record.get("previous_command")
        if previous and previous != record.get("command"):
            subprocess.Popen([*previous, payload], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
    except (Exception, KeyboardInterrupt):
        pass
    from .hooks import handle
    return handle("codex", config_file, notification=payload)
