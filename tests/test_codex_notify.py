import json
from pathlib import Path
import subprocess
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from client.adapters import ADAPTERS
from client.codex_notify import replace_notify
from client.config import config_path, read_json
from client.installer import hooks_installed, install_files, provider_path, uninstall_files


def install():
    return install_files('http://127.0.0.1:1', '', ['codex', 'claude'])


def test_platform_completion_events(api):
    codex = ADAPTERS['codex']
    start = codex.normalize({'session_id': 'session', 'hook_event_name': 'UserPromptSubmit'}, hostname='dev')
    session = api.post('/api/v1/events', json=start).json()
    assert session['state'] == 'WORKING'
    assert codex.normalize({'session_id': 'session', 'hook_event_name': 'Stop'}) is None
    assert api.get('/api/v1/sessions/' + session['id']).json()['state'] == 'WORKING'
    event = codex.normalize_notification({'type': 'agent-turn-complete', 'thread-id': 'session',
        'turn-id': 'turn', 'cwd': '/work', 'last-assistant-message': 'Finished',
        'input-messages': ['private prompt']}, hostname='dev')
    done = api.post('/api/v1/events', json=event).json()
    assert done['id'] == session['id'] and done['state'] == 'ATTENTION'
    assert done['attention_reason'] == 'turn_finished' and done['last_message'] == 'Finished'
    assert event['metadata'] == {'turn_id': 'turn', 'provider_hook': 'agent-turn-complete'}
    assert 'private prompt' not in json.dumps(event)
    assert ADAPTERS['claude'].normalize({'session_id': 's', 'hook_event_name': 'Stop'})['event'] == 'turn_finished'
    for payload in ([], {}, {'type': 'approval-requested'}, {'type': 'agent-turn-complete'},
                    {'type': 'agent-turn-complete', 'thread-id': 12}):
        assert codex.normalize_notification(payload) is None


@pytest.mark.parametrize('raw', [
    b'', b'# unchanged\n[features]\nhooks = true\n',
    b'# heading\nnotify = [\n "old", # comment\n "arg"\n] # tail\n[features]\nhooks=true\n',
    b'"notify" = ["old"]', b"'notify' = ['old']\r\nmodel = 'example'\r\n",
    b'description = """\nnotify = ["fake"]\n"""\nnotify = ["real"]\n',
    b'[profiles.example]\nnotify = ["profile"]\n',
])
def test_toml_edit_round_trip(raw):
    argv = [sys.executable, '/path with spaces/client', 'notify']
    assignment = ('notify = ' + json.dumps(argv) + '\n').encode()
    changed, previous = replace_notify(raw, assignment)
    assert tomllib.loads(changed.decode())['notify'] == argv
    restored, _ = replace_notify(changed, previous)
    assert restored == raw


def test_migrate_old_stop_and_reinstall_then_uninstall(fake_home):
    config = install()
    path = provider_path('codex')
    hooks = read_json(path)
    hooks['hooks']['Stop'] = [{'hooks': [
        {'type': 'command', 'command': config['integrations']['codex']['command'], 'timeout': 2},
        {'type': 'command', 'command': 'other-stop'}]}]
    path.write_text(json.dumps(hooks))
    # Emulate a 1.1.0 installation record and its untouched Codex config.
    config['integrations']['codex'].pop('notify')
    config_path().write_text(json.dumps(config))
    path.with_name('config.toml').unlink()
    upgraded = install()
    assert read_json(path)['hooks']['Stop'] == [{'hooks': [{'type': 'command', 'command': 'other-stop'}]}]
    assert 'Stop' in read_json(provider_path('claude'))['hooks']
    assert hooks_installed('codex', upgraded)
    original = path.with_name('config.toml').read_bytes()
    assert install()['integrations']['codex']['notify'] == upgraded['integrations']['codex']['notify']
    assert path.with_name('config.toml').read_bytes() == original
    uninstall_files()
    assert not path.with_name('config.toml').exists()


def test_notifier_move_and_user_edits_survive(fake_home, monkeypatch):
    config = install()
    old = Path(config['integrations']['codex']['notify']['path'])
    old.write_text('# user edit\nnotify = ["new-notifier"]\n')
    assert not hooks_installed('codex', config)
    config = install()
    assert config['integrations']['codex']['notify']['previous_command'] == ['new-notifier']
    monkeypatch.setenv('CODEX_HOME', str(fake_home / 'moved'))
    moved = install()
    assert old.read_text() == '# user edit\nnotify = ["new-notifier"]\n'
    new = Path(moved['integrations']['codex']['notify']['path'])
    new.write_text('notify = ["changed-after-install"]\n')
    uninstall_files()
    assert new.read_text() == 'notify = ["changed-after-install"]\n'


@pytest.mark.parametrize('raw', ['notify = "wrong"', 'notify = [12]', 'notify = [', 'notify=[]\nnotify=[]'])
def test_invalid_toml_preflight(fake_home, raw):
    path = provider_path('codex').with_name('config.toml')
    path.parent.mkdir(parents=True)
    path.write_text(raw)
    with pytest.raises(ValueError):
        install()
    assert path.read_text() == raw
    assert not config_path().exists() and not provider_path('codex').exists()
    assert not (fake_home / '.local/bin/slopwatchdeluxe').exists()


def test_installed_notify_argv_sends_completion_and_chains_existing(fake_home):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = b'{"id":"session"}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = fake_home / 'previous.py'
    result_file = fake_home / 'forwarded.json'
    previous.write_text('import pathlib, sys\npathlib.Path(sys.argv[1]).write_text(sys.argv[2])\n')
    path = provider_path('codex').with_name('config.toml')
    path.parent.mkdir(parents=True)
    original = 'notify = ' + json.dumps([sys.executable, str(previous), str(result_file)]) + '\n'
    path.write_text(original)
    payload = json.dumps({'type': 'agent-turn-complete', 'thread-id': 'actual-session', 'turn-id': 'actual-turn',
                          'last-assistant-message': 'Done $(touch BAD)', 'cwd': str(fake_home)})
    try:
        config = install_files(f'http://127.0.0.1:{server.server_port}', '', ['codex'],
                               binary=fake_home / 'client with spaces')
        argv = tomllib.loads(path.read_text())['notify']
        # An open stdin must not block this argv-based callback.
        process = subprocess.Popen([*argv, payload], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.wait(timeout=3) == 0
        assert process.stdout.read() == process.stderr.read() == b''
        process.stdin.close()
        assert len(received) == 1
        assert received[0]['event'] == 'turn_finished'
        assert received[0]['provider_session_id'] == 'actual-session'
        assert received[0]['metadata']['turn_id'] == 'actual-turn'
        for _ in range(40):
            if result_file.exists():
                break
            threading.Event().wait(.05)
        assert result_file.read_text() == payload
        assert not (fake_home / 'BAD').exists()
        assert 'Stop' not in read_json(provider_path('codex'))['hooks']
        # A missing prior notifier must not suppress our event.
        config['integrations']['codex']['notify']['previous_command'] = ['/nonexistent/notifier']
        config_path().write_text(json.dumps(config))
        result = subprocess.run([*argv, payload], capture_output=True, text=True, timeout=3)
        assert result.returncode == 0 and result.stdout == result.stderr == ''
        assert len(received) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
