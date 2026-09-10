import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from client import execution
from client.adapters import ADAPTERS


@pytest.mark.parametrize('command,argv,expected', [
    ('sleep 7200', ['/usr/bin/sleep', '7200'], True),
    ('sleep 7200', ['/usr/bin/sleep', '30'], False),
    ('flash && sleep 7200; true', ['/bin/zsh', '-lc', 'flash && sleep 7200; true'], True),
    (shlex.join(['/bin/zsh', '-lc', 'flash && sleep 7200; true']),
     ['/bin/zsh', '-lc', 'flash && sleep 7200; true'], True),
    ('flash && sleep 7200', ['/usr/bin/sleep', '7200'], False),
    ('echo $(dangerous)', ['/bin/echo', 'something'], False),
    ('sleep 7200', ['/bin/zsh', '-lc', 'echo sleep 7200'], False),
])
def test_exact_command_matching(command, argv, expected):
    assert execution.command_matches(command, argv) is expected


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux execution observer')
def test_only_new_process_in_same_agent_resolves_permission(api, app, monkeypatch):
    root = (os.getpid(), execution.process_info(os.getpid())[1])
    payload = {'session_id': 'long-flash', 'hook_event_name': 'PermissionRequest',
               'tool_name': 'Bash', 'tool_use_id': 'long-call', 'turn_id': 'turn-1',
               'tool_input': {'command': 'sleep 7200'}}
    event = ADAPTERS['codex'].normalize(payload, collect_git=False)
    event['metadata']['permission_request_id'] = event['event_id']
    session = api.post('/api/v1/events', json=event).json()
    monkeypatch.setattr(execution, 'load_config', lambda _: {})
    def request(config, path, data=None):
        response = api.post(path, json=data) if data else api.get(path)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(execution, 'request', request)
    since = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
    process = subprocess.Popen(['sleep', '7200'])
    try:
        deadline = time.monotonic() + 3
        while not execution.executing('sleep 7200', root, since) and time.monotonic() < deadline:
            time.sleep(.01)
        assert execution.executing('sleep 7200', root, since)
        assert not execution.executing('sleep 7200', root, since,
                                       [(process.pid, execution.process_info(process.pid)[1])])
        assert not execution.executing('sleep 7201', root, since)
        assert not execution.executing('sleep 7200', (root[0], root[1] + 1), since)
        assert not execution.executing('sleep 7200', root, since + 10000)
        execution.watch({'root': root, 'since': since, 'command': 'sleep 7200',
                         'config': 'unused', 'event': event, 'session_id': session['id']}, lifetime=2)
        assert process.poll() is None  # Resolved while the hours-long command is alive.
        from datetime import timedelta
        from server.models import utcnow
        assert app.state.db.settle_permissions(utcnow() + timedelta(hours=3)) == 0
        current = api.get('/api/v1/sessions/' + session['id']).json()
        assert current['state'] == 'WORKING' and current['attention_reason'] is None
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_watcher_exits_when_request_is_canceled(monkeypatch):
    monkeypatch.setattr(execution, 'load_config', lambda _: {})
    monkeypatch.setattr(execution, 'process_info', lambda _: (1, 42))
    monkeypatch.setattr(execution, 'executing', lambda *args: False)
    monkeypatch.setattr(execution, 'request', lambda *args: {'state': 'IDLE', 'metadata': {}})
    monkeypatch.setattr(execution.time, 'sleep', lambda _: pytest.fail('Canceled watcher kept running'))
    execution.watch({'root': (123, 42), 'since': 0, 'command': 'sleep 7200',
                     'config': 'unused', 'event': {'metadata': {}}, 'session_id': 's'})


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux execution observer')
def test_installed_hook_reports_execution_without_waiting_for_completion(fake_home):
    """Exercise the zipapp, detached watcher, stdin handoff, procfs and HTTP."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import threading
    from client.installer import install_files

    received = []
    resolved = threading.Event()
    done = fake_home / 'observed'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            event = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(event)
            if event['event'] == 'input_resolved':
                done.touch()
                resolved.set()
            self.reply()

        def do_GET(self):
            self.reply()

        def reply(self):
            body = json.dumps({'id': 'session', 'state': 'WORKING', 'metadata': {
                'turn_id': 'turn', 'pending_permission_request_ids': [received[0]['event_id']],
                'last_event': 'permission_required'}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = install_files(f'http://127.0.0.1:{server.server_port}', '', ['codex'],
                           binary=fake_home / 'client with spaces')
    harness = fake_home / 'agent.py'
    harness.write_text('''import ctypes, json, subprocess, sys, time
from pathlib import Path
ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0)
payload = {"session_id": "test", "turn_id": "turn", "hook_event_name": "PermissionRequest",
           "tool_name": "Bash", "tool_use_id": "long", "tool_input": {"command": "sleep 7200"}}
result = subprocess.run([sys.executable, sys.argv[1], "hook", "--provider", "codex"],
                        input=json.dumps(payload), text=True, capture_output=True, timeout=3)
assert result.returncode == 0 and result.stdout == result.stderr == ""
process = subprocess.Popen(["sleep", "7200"])
try:
    end = time.monotonic() + 6
    while not Path(sys.argv[2]).exists() and time.monotonic() < end:
        time.sleep(.05)
    assert Path(sys.argv[2]).exists(), "Execution was not reported"
    assert process.poll() is None, "Watcher waited for command completion"
finally:
    process.terminate()
    process.wait(timeout=2)
''')
    try:
        result = subprocess.run([sys.executable, str(harness), config['binary'], str(done)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert resolved.is_set()
        assert [event['event'] for event in received] == ['permission_required', 'input_resolved']
        assert received[1]['metadata']['permission_request_id'] == received[0]['event_id']
        assert received[1]['metadata']['execution_observed'] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
