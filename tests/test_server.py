from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from server.main import Settings, create_app
from server.models import utcnow
from server.state_machine import PERMISSION_GRACE_SECONDS


def event(api, name, **overrides):
    payload = dict(provider="codex", provider_session_id="abc", hostname="devbox",
                   cwd="/work/my-project", event=name, event_id=str(uuid4()))
    response = api.post("/api/v1/events", json={**payload, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_session_and_compound_identity(api):
    payload = {"provider": "codex", "provider_session_id": "abc", "hostname": "devbox", "cwd": "/work/demo"}
    response = api.post("/api/v1/sessions", json=payload)
    assert response.status_code == 201
    session = response.json()
    assert session["project_name"] == "demo" and session["state"] == "IDLE"
    assert api.get(f"/api/v1/sessions/{session['id']}").json() == session
    assert api.post("/api/v1/sessions", json=payload).status_code == 409
    assert api.post("/api/v1/sessions", json={**payload, "hostname": "another"}).status_code == 201
    assert api.post("/api/v1/sessions", json={**payload, "provider": "claude"}).status_code == 201


def test_event_creates_and_transitions(api):
    assert event(api, "session_started")["state"] == "IDLE"
    assert event(api, "work_started", message="Fix it")["state"] == "WORKING"
    assert event(api, "tool_started")["state"] == "WORKING"
    assert event(api, "tool_finished")["state"] == "WORKING"
    done = event(api, "turn_finished", message="Fixed it")
    assert done["state"] == "ATTENTION" and done["attention_reason"] == "turn_finished"
    assert done["metadata"]["last_user_prompt"] == "Fix it"
    assert done["metadata"]["last_agent_message"] == "Fixed it"
    assert event(api, "tool_finished")["state"] == "ATTENTION"
    assert event(api, "work_started")["state"] == "WORKING"
    assert event(api, "session_ended")["state"] == "CLOSED"
    assert event(api, "session_started")["state"] == "IDLE"


@pytest.mark.parametrize("name", ["input_required", "failed", "interrupted"])
def test_attention_reasons(api, name):
    event(api, "work_started")
    result = event(api, name)
    assert result["state"] == "ATTENTION" and result["attention_reason"] == name
    assert event(api, "work_started")["state"] == "WORKING"


def test_permission_wait_survives_unrelated_parallel_activity(api, app):
    event(api, "work_started")
    event(api, "permission_required", metadata={"tool_name": "Bash"})
    app.state.db.settle_permissions(utcnow() + timedelta(seconds=PERMISSION_GRACE_SECONDS))
    for name in ("tool_started", "tool_finished", "tool_failed"):
        assert event(api, name, metadata={"tool_name": "Read"})["state"] == "ATTENTION"
    assert event(api, "tool_finished", metadata={"tool_name": "Bash"})["state"] == "WORKING"


def test_two_pending_requests_and_subagent(api):
    event(api, "work_started")
    event(api, "permission_required", metadata={"tool_name": "Bash"})
    event(api, "input_required", metadata={"tool_name": "AskUserQuestion", "agent_id": "child"})
    assert event(api, "tool_finished", metadata={"tool_name": "Bash"})["state"] == "ATTENTION"
    assert event(api, "tool_finished", metadata={"tool_name": "AskUserQuestion", "agent_id": "child"})["state"] == "WORKING"
    event(api, "turn_finished")
    assert event(api, "tool_started", metadata={"agent_id": "child"})["state"] == "ATTENTION"


def test_tool_error_is_recoverable_and_compaction_does_not_reset(api):
    event(api, "work_started")
    assert event(api, "tool_failed", message="test failed")["state"] == "WORKING"
    assert event(api, "activity")["state"] == "WORKING"


def test_old_and_duplicate_events_do_not_regress(api):
    old = (utcnow() - timedelta(seconds=10)).isoformat()
    current = event(api, "turn_finished", event_id="unique-event")
    assert event(api, "work_started", timestamp=old) == current
    assert event(api, "work_started", event_id="unique-event") == current


def test_archive_restore_delete_and_acknowledge(api, app):
    session = event(api, "turn_finished", timestamp=(utcnow() - timedelta(hours=25)).isoformat())
    path = f"/api/v1/sessions/{session['id']}"
    assert app.state.db.archive_inactive(24) == 1
    assert api.get("/api/v1/sessions").json() == []
    assert len(api.get("/api/v1/sessions?archived=true").json()) == 1
    assert api.post(path + "/restore").json()["archived_at"] is None
    assert app.state.db.archive_inactive(24) == 0
    assert api.patch(path, json={"state": "IDLE"}).json()["attention_reason"] is None
    assert api.post(path + "/archive").json()["archived_at"] is not None
    assert event(api, "work_started")["archived_at"] is None
    assert api.delete(path).status_code == 204
    assert api.get(path).status_code == 404
    assert api.delete(path).status_code == 404


def test_archive_at_exact_threshold_and_recent_retained(api, app):
    now = utcnow()
    event(api, "work_started", timestamp=(now - timedelta(hours=24)).isoformat())
    event(api, "work_started", provider_session_id="recent")
    assert app.state.db.archive_inactive(24, now) == 1
    assert len(api.get("/api/v1/sessions").json()) == 1


def test_persistence_and_startup_archiving(tmp_path):
    settings = Settings(database=str(tmp_path / "persistent.db"))
    with TestClient(create_app(settings)) as api:
        recent = event(api, "permission_required")
        old = event(api, "work_started", provider_session_id="old", timestamp=(utcnow() - timedelta(hours=25)).isoformat())
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v1/sessions/" + recent["id"]).json() == recent
        assert api.get("/api/v1/sessions/" + old["id"]).json()["archived_at"] is not None
        assert event(api, "work_started")["id"] == recent["id"]


def test_filters_search_patch(api):
    session = event(api, "work_started")
    event(api, "turn_finished", provider="claude")
    assert len(api.get("/api/v1/sessions?state=ATTENTION&provider=claude").json()) == 1
    assert api.get("/api/v1/sessions?search=absent").json() == []
    assert len(api.get("/api/v1/sessions?search=DEVBOX").json()) == 2
    api.patch("/api/v1/sessions/" + session["id"], json={"project_name": "Custom title"})
    assert event(api, "turn_finished")["project_name"] == "Custom title"


def test_auth_and_body_limits(tmp_path):
    with TestClient(create_app(Settings(str(tmp_path / "auth.db"), api_token="secret"))) as api:
        assert api.get("/api/v1/health").status_code == 200
        assert api.get("/").status_code == 200
        for method, path in [("GET", "/sessions"), ("POST", "/events"), ("GET", "/stream"), ("DELETE", "/sessions/abc")]:
            assert api.request(method, "/api/v1" + path).status_code == 401
        assert api.get("/api/v1/sessions", headers={"Authorization": "Bearer wrong"}).status_code == 401
        headers = {"Authorization": "Bearer secret", "Content-Type": "application/json"}
        assert api.get("/api/v1/sessions", headers=headers).status_code == 200
        assert api.post("/api/v1/events", content=b"x" * 65537, headers=headers).status_code == 413
        assert api.post("/api/v1/events", content=b"{}", headers={"Authorization": "Bearer secret"}).status_code == 415


def test_validation_untrusted_content(api):
    payload = {"provider": "codex", "provider_session_id": "abc", "hostname": "dev", "event": "turn_finished"}
    for change in ({"provider": "unknown"}, {"event": "Stop"}, {"timestamp": "2026-01-01T00:00:00"},
                   {"message": "x" * 8001}, {"metadata": {"_waits": {}}},
                   {"metadata": {"large": "x" * 17000}}, {"timestamp": (utcnow() + timedelta(hours=1)).isoformat()}):
        assert api.post("/api/v1/events", json={**payload, **change}).status_code == 422
    response = api.post("/api/v1/events", json={**payload, "message": '<script>alert("x")</script>'})
    assert response.json()["last_message"] == '<script>alert("x")</script>'
    assert "script-src 'self'" in api.get("/").headers["content-security-policy"]
    assert api.get("/static/../../etc/passwd").status_code == 404


def test_concurrent_identity_upsert(api):
    with ThreadPoolExecutor(max_workers=8) as pool:
        sessions = list(pool.map(lambda _: event(api, "work_started"), range(20)))
    assert len({s["id"] for s in sessions}) == 1
    assert len(api.get("/api/v1/sessions").json()) == 1


@pytest.fixture
def server_clock(monkeypatch):
    import server.database as database
    clock = [utcnow()]
    monkeypatch.setattr(database, "utcnow", lambda: clock[0])
    return clock


def test_permission_grace_exact_deadline_and_public_state(api, app, server_clock):
    started = event(api, "work_started", message="Fix it")
    pending = event(api, "permission_required", message="Run build", metadata={"tool_name": "Bash"})
    assert pending["state"] == "WORKING" and pending["attention_reason"] is None
    assert pending["last_message"] == started["last_message"]
    assert all(not key.startswith("_") for key in pending["metadata"])
    revision = app.state.db.revision
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS, microseconds=-1)
    assert app.state.db.settle_permissions() == 0
    server_clock[0] += timedelta(microseconds=1)
    assert app.state.db.settle_permissions() == 1
    assert app.state.db.revision > revision  # Publishes a change for SSE without another hook.
    waiting = api.get("/api/v1/sessions/" + pending["id"]).json()
    assert waiting["state"] == "ATTENTION" and waiting["attention_reason"] == "permission_required"
    assert waiting["last_message"] == "Run build"
    assert waiting["last_activity_at"] == pending["last_activity_at"]
    assert app.state.db.settle_permissions() == 0
    assert event(api, "tool_finished", metadata={"tool_name": "Bash"})["state"] == "WORKING"


@pytest.mark.parametrize("resolution", ["tool_finished", "tool_failed", "input_resolved"])
def test_automatically_resolved_permission_never_needs_attention(api, app, server_clock, resolution):
    event(api, "work_started")
    pending = event(api, "permission_required", metadata={"tool_name": "Bash"})
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS - 1)
    resolved = event(api, resolution, metadata={"tool_name": "Bash"})
    assert resolved["state"] == "WORKING"
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS + 1)
    assert app.state.db.settle_permissions() == 0
    assert api.get("/api/v1/sessions/" + pending["id"]).json()["state"] == "WORKING"


def test_repeated_permission_does_not_extend_grace_or_clear_other_wait(api, app, server_clock):
    event(api, "work_started")
    event(api, "permission_required", metadata={"tool_name": "Bash"})
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS - 2)
    event(api, "permission_required", metadata={"tool_name": "Bash"})
    event(api, "input_required", metadata={"tool_name": "AskUserQuestion"})
    server_clock[0] += timedelta(seconds=2)
    assert app.state.db.settle_permissions() == 1
    waiting = event(api, "tool_finished", metadata={"tool_name": "Bash"})
    assert waiting["state"] == "ATTENTION" and waiting["attention_reason"] == "input_required"
    assert event(api, "tool_finished", metadata={"tool_name": "AskUserQuestion"})["state"] == "WORKING"


@pytest.mark.parametrize("next_event, expected", [("work_started", "WORKING"), ("session_started", "IDLE"),
    ("turn_finished", "ATTENTION"), ("failed", "ATTENTION"), ("interrupted", "ATTENTION"), ("session_ended", "CLOSED")])
def test_new_lifecycle_cancels_pending_permissions(api, app, server_clock, next_event, expected):
    event(api, "permission_required", metadata={"tool_name": "Bash"})
    result = event(api, next_event)
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS + 1)
    assert app.state.db.settle_permissions() == 0
    current = api.get("/api/v1/sessions/" + result["id"]).json()
    assert current["state"] == expected
    assert current["attention_reason"] != "permission_required"


def test_acknowledgment_cancels_pending_permission(api, app, server_clock):
    pending = event(api, "permission_required")
    path = "/api/v1/sessions/" + pending["id"]
    api.patch(path, json={"state": "IDLE"})
    server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS + 1)
    assert app.state.db.settle_permissions() == 0
    assert api.get(path).json()["state"] == "IDLE"


def test_pending_permissions_survive_server_restart(tmp_path, server_clock):
    settings = Settings(database=str(tmp_path / "pending.db"))
    with TestClient(create_app(settings)) as api:
        pending = event(api, "permission_required", metadata={"tool_name": "Bash"})
    app = create_app(settings)
    with TestClient(app) as api:
        assert api.get("/api/v1/sessions/" + pending["id"]).json()["state"] == "WORKING"
        server_clock[0] += timedelta(seconds=PERMISSION_GRACE_SECONDS)
        assert app.state.db.settle_permissions() == 1
        assert api.get("/api/v1/sessions/" + pending["id"]).json()["attention_reason"] == "permission_required"
