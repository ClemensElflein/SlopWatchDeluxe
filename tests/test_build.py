import subprocess

from fastapi.testclient import TestClient

from server import build
from server.main import Settings, create_app


def test_image_build_is_reported_without_auth_and_fixed_for_app_lifetime(tmp_path, monkeypatch):
    monkeypatch.setenv('SLOPWATCHDELUXE_BUILD', ' v1.2.3-4-gabcdef0 ')
    app = create_app(Settings(database=str(tmp_path / 'build.db'), api_token='secret'))
    monkeypatch.setenv('SLOPWATCHDELUXE_BUILD', 'a-different-build')
    with TestClient(app) as api:
        health = api.get('/api/v1/health')
        assert health.json()['build'] == 'v1.2.3-4-gabcdef0'
        assert health.json()['version'] == build.VERSION
        assert health.headers['cache-control'] == 'no-store'


def test_checkout_build_includes_tag_commit_and_dirty_state(tmp_path, monkeypatch):
    monkeypatch.delenv('SLOPWATCHDELUXE_BUILD', raising=False)
    monkeypatch.setattr(build, '__file__', str(tmp_path / 'server/build.py'))
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    git('init', '--quiet')
    source = tmp_path / 'source.txt'
    source.write_text('original')
    git('add', 'source.txt')
    git('-c', 'user.name=Build test', '-c', 'user.email=build@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Initial')
    git('tag', 'v1.2.3')
    described = git('describe', '--tags', '--always', '--long', '--dirty').stdout.strip()
    assert build.build_id() == described
    assert described.startswith('v1.2.3-0-g')
    source.write_text('modified')
    assert build.build_id() == described + '-dirty'


def test_build_without_git_identifies_unknown_revision(tmp_path, monkeypatch):
    monkeypatch.delenv('SLOPWATCHDELUXE_BUILD', raising=False)
    monkeypatch.setattr(build, '__file__', str(tmp_path / 'server/build.py'))
    assert build.build_id() == f'v{build.VERSION}+unknown'
