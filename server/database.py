"""One SQLite connection, short serialized transactions, no external services."""
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import PureWindowsPath, Path
import sqlite3
import threading
from uuid import uuid4

from .models import Event, SessionCreate, iso, utcnow
from .state_machine import promote_permissions, transition


def project_name(cwd):
    return PureWindowsPath(cwd).name or "Unknown project"


class Database:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.revision = 0
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_session_id TEXT NOT NULL,
                hostname TEXT NOT NULL,
                cwd TEXT NOT NULL,
                project_name TEXT NOT NULL,
                state TEXT NOT NULL,
                attention_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                archived_at TEXT,
                last_message TEXT,
                metadata TEXT NOT NULL,
                last_event_at TEXT,
                last_event_id TEXT,
                UNIQUE(provider, hostname, provider_session_id)
            );
            CREATE INDEX IF NOT EXISTS sessions_activity ON sessions(archived_at, last_activity_at);
            PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        with self.lock, self.conn:
            yield

    def decode(self, row):
        if row is None:
            raise KeyError("Session not found")
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def public(self, session):
        result = {k: ({mk: mv for mk, mv in v.items() if not mk.startswith("_")}
                      if k == "metadata" else v)
                  for k, v in session.items() if k not in ("last_event_at", "last_event_id")}
        metadata = session["metadata"]
        pending = metadata.get("_pending_permissions", {})
        waits = metadata.get("_waits", {})
        result["metadata"]["pending_permission_request_ids"] = [
            request_id for key, request_id in metadata.get("_permission_ids", {}).items()
            if key in pending or waits.get(key) == "permission_required"]
        return result

    def get(self, session_id):
        with self.lock:
            return self.public(self.decode(self.conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()))

    def list(self, archived=False, state=None, provider=None, search=None, limit=1000, offset=0):
        clauses, args = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"], []
        for column, value in (("state", state), ("provider", provider)):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        if search:
            clauses.append("instr(lower(project_name || ' ' || cwd || ' ' || hostname), lower(?)) > 0")
            args.append(search)
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM sessions WHERE " + " AND ".join(clauses) +
                " ORDER BY CASE state WHEN 'ATTENTION' THEN 0 WHEN 'WORKING' THEN 1"
                " WHEN 'IDLE' THEN 2 ELSE 3 END, updated_at DESC, id LIMIT ? OFFSET ?",
                [*args, limit, offset]).fetchall()
            return [self.public(self.decode(row)) for row in rows]

    def save(self, session):
        # Bound accumulated arbitrary metadata across the session's lifetime.
        metadata = session["metadata"]
        while len(json.dumps(metadata).encode()) > 49152:
            removable = next((key for key in metadata if not key.startswith("_")), None)
            if removable is None:
                break
            del metadata[removable]
        data = {**session, "metadata": json.dumps(session["metadata"], allow_nan=False)}
        # Column names are internal, never taken from a request.
        columns = list(data)
        self.conn.execute(
            f"INSERT INTO sessions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in columns),
            list(data.values()))
        self.revision += 1

    def new(self, data: SessionCreate, now):
        metadata = dict(data.metadata)
        if data.project_name:
            metadata["_custom_project"] = True
        return dict(id=str(uuid4()), **data.model_dump(exclude={"project_name", "metadata"}), metadata=metadata,
                    project_name=data.project_name or project_name(data.cwd),
                    created_at=now, updated_at=now, last_activity_at=now,
                    archived_at=None, last_event_at=None, last_event_id=None)

    def create(self, data):
        with self.transaction():
            session = self.new(data, iso(utcnow()))
            if session["state"] == "ATTENTION" and not session["attention_reason"]:
                session["attention_reason"] = "manual"
            elif session["state"] != "ATTENTION":
                session["attention_reason"] = None
            self.save(session)
            return self.public(session)

    def event(self, data: Event):
        with self.transaction():
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE provider=? AND hostname=? AND provider_session_id=?",
                (data.provider, data.hostname, data.provider_session_id)).fetchone()
            received_at = utcnow()
            now = iso(received_at)
            stamp = min(iso(data.timestamp), now)
            session = self.decode(row) if row else self.new(SessionCreate(
                **data.model_dump(include={"provider", "provider_session_id", "hostname", "cwd"})), now)
            if (session["last_event_id"] == data.event_id or
                    (session["last_event_at"] and stamp < session["last_event_at"])):
                return self.public(session)
            if transition(session, data.event, data.metadata, now=received_at, message=data.message) is False:
                return self.public(session)
            session["metadata"].update(data.metadata)
            session["metadata"]["last_event"] = data.event
            if data.cwd:
                if not session["metadata"].get("_custom_project"):
                    session["project_name"] = project_name(data.cwd)
                session["cwd"] = data.cwd
            if data.message and data.event not in ("tool_started", "tool_finished", "activity", "permission_required"):
                session["last_message"] = data.message
            if data.event == "work_started":
                session["metadata"]["last_user_prompt"] = data.message
            if data.event == "turn_finished" and data.message:
                session["metadata"]["last_agent_message"] = data.message
            session.update(updated_at=now, last_activity_at=stamp, archived_at=None,
                           last_event_at=stamp, last_event_id=data.event_id)
            self.save(session)
            return self.public(session)

    def settle_permissions(self, now=None):
        now = now or utcnow()
        changed = 0
        with self.transaction():
            rows = self.conn.execute(
                "SELECT * FROM sessions WHERE archived_at IS NULL "
                "AND json_type(metadata, '$._pending_permissions') = 'object'").fetchall()
            for row in rows:
                session = self.decode(row)
                if promote_permissions(session, now):
                    session["updated_at"] = iso(now)
                    self.save(session)
                    changed += 1
        return changed

    def patch(self, session_id, changes):
        with self.transaction():
            session = self.decode(self.conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
            session.update(changes)
            if "project_name" in changes:
                session["metadata"]["_custom_project"] = True
            if "state" in changes:
                session["attention_reason"] = "manual" if session["state"] == "ATTENTION" else None
                session["metadata"].pop("_waits", None)
                session["metadata"].pop("_pending_permissions", None)
                session["metadata"].pop("_permission_ids", None)
            session["updated_at"] = iso(utcnow())
            self.save(session)
            return self.public(session)

    def archive(self, session_id, restore=False):
        with self.transaction():
            session = self.decode(self.conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
            now = iso(utcnow())
            session.update(archived_at=None if restore else now, updated_at=now)
            if restore:
                # Manual restoration counts as activity and grants a full archive interval.
                session["last_activity_at"] = now
            self.save(session)
            return self.public(session)

    def archive_inactive(self, hours, now=None):
        now = now or utcnow()
        with self.transaction():
            result = self.conn.execute(
                "UPDATE sessions SET archived_at=?, updated_at=? "
                "WHERE archived_at IS NULL AND last_activity_at <= ?",
                (iso(now), iso(now), iso(now - timedelta(hours=hours))))
            if result.rowcount:
                self.revision += 1
            return result.rowcount

    def delete(self, session_id):
        with self.transaction():
            if not self.conn.execute("DELETE FROM sessions WHERE id=?", (session_id,)).rowcount:
                raise KeyError("Session not found")
            self.revision += 1

    def healthy(self):
        with self.lock:
            return self.conn.execute("SELECT 1").fetchone()[0] == 1

    def close(self):
        self.conn.close()
