"""Observe Linux command execution without controlling tools or approvals."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import quote
import zipfile

from .config import config_path, load_config
from .transport import request

PROC = Path('/proc')
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'fish'}


def process_info(pid):
    fields = (PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[1]), int(fields[19])  # parent PID, start ticks (PID reuse guard)


def codex_parent():
    pid = os.getpid()
    for _ in range(32):
        parent, started = process_info(pid)
        if (PROC / str(pid) / 'comm').read_text().strip() == 'codex':
            return pid, started
        if parent < 2:
            break
        pid = parent
    return None


def shell_body(argv):
    if not argv or Path(argv[0]).name not in SHELLS:
        return None
    for index, arg in enumerate(argv[1:], 1):
        if arg.startswith('-') and not arg.startswith('--') and 'c' in arg:
            return argv[index + 1] if index + 1 < len(argv) else None
    return None


def command_matches(command, argv):
    """Exact shell body or literal argv; never execute or expand hook input."""
    if shell_body(argv) == command:
        return True
    try:
        expected = shlex.split(command)
    except ValueError:
        return False
    # PermissionRequest can render the outer shell invocation instead of its body.
    command = shell_body(expected) or command
    if shell_body(argv) == command:
        return True
    # A shell may exec its final simple command. Only accept literal argv here.
    if any(char in command for char in '\n;|&<>$`*?(){}'):
        return False
    try:
        expected = shlex.split(command)
    except ValueError:
        return False
    return bool(expected and argv and Path(expected[0]).name == Path(argv[0]).name
                and expected[1:] == argv[1:])


def matching_processes(command, root, since):
    for path in PROC.iterdir():
        if not path.name.isdigit():
            continue
        try:
            parent, started = process_info(int(path.name))
            if started < since:
                continue
            with (path / 'cmdline').open('rb') as handle:
                raw = handle.read(65537)
            if len(raw) > 65536:
                continue
            argv = [os.fsdecode(arg) for arg in raw.rstrip(b'\0').split(b'\0')]
            if not command_matches(command, argv):
                continue
            for _ in range(32):
                if parent == root[0]:
                    if process_info(parent)[1] == root[1]:
                        yield int(path.name), started
                    break
                if parent < 2:
                    break
                parent, _ = process_info(parent)
        except (OSError, ValueError, IndexError):
            continue


def executing(command, root, since, existing=()):
    return any(process not in existing for process in matching_processes(command, root, since))


def start_watcher(payload, event, session_id, config_file=None):
    if (sys.platform != 'linux' or event['provider'] != 'codex'
            or event['event'] != 'permission_required' or payload.get('tool_name') != 'Bash'):
        return
    inputs = payload.get('tool_input')
    command = inputs.get('command') if isinstance(inputs, dict) else None
    if not isinstance(command, str) or not command or len(command.encode()) > 32768:
        return
    root = codex_parent()
    if not root:
        return
    # Capture before returning the permission hook, when execution is still blocked.
    since = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
    data = {'command': command, 'root': root, 'since': since, 'event': {**event, 'message': None},
            'config': str(config_file or config_path()), 'session_id': session_id,
            'existing': list(matching_processes(command, root, 0))}
    encoded = json.dumps(data, ensure_ascii=False).encode()
    if len(encoded) > 131072:
        return
    entry = Path(sys.argv[0]).absolute()
    argv = ([sys.executable, str(entry)] if entry.is_file() and zipfile.is_zipfile(entry) else [sys.executable, '-m', 'client'])
    child = subprocess.Popen([*argv, 'watch-execution'], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
    try:
        child.stdin.write(encoded)
    finally:
        child.stdin.close()


def watch(data, interval=1, lifetime=86400):
    root = data['root']
    event = data['event']
    config = load_config(data['config'])
    end = time.monotonic() + lifetime
    next_check = 0
    while time.monotonic() < end:
        if process_info(root[0])[1] != root[1]:
            return
        if executing(data['command'], root, data['since'], [tuple(p) for p in data.get('existing', [])]):
            resolved = {**event, 'event': 'input_resolved', 'event_id': str(uuid4()),
                        'timestamp': datetime.now(timezone.utc).isoformat(), 'message': None,
                        'metadata': {**event['metadata'], 'execution_observed': True}}
            request(config, '/api/v1/events', resolved)
            return
        if time.monotonic() >= next_check:
            # Stop promptly after completion, cancellation, or a new turn. Only
            # compare our request marker, never infer approval from other activity.
            current = request(config, '/api/v1/sessions/' + quote(data['session_id'], safe=''))
            if current is not None and 'pending_permission_request_ids' in current['metadata']:
                if event['metadata'].get('permission_request_id') not in current['metadata']['pending_permission_request_ids']:
                    return
            if current is None or current['state'] in ('IDLE', 'CLOSED'):
                return
            if current['metadata'].get('turn_id') != event['metadata'].get('turn_id'):
                return
            if current['metadata'].get('last_event') in ('turn_finished', 'failed', 'interrupted', 'session_ended'):
                return
            if (current['metadata'].get('last_event') in ('tool_finished', 'tool_failed', 'input_resolved')
                    and event['metadata'].get('tool_use_id')
                    and current['metadata'].get('tool_use_id') == event['metadata']['tool_use_id']):
                return
            next_check = time.monotonic() + 10
        time.sleep(interval)


def handle_watch():
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) <= 131072:
            watch(json.loads(raw))
    except (Exception, KeyboardInterrupt):
        pass
    return 0
