from datetime import datetime, timezone, timedelta
from enum import StrEnum
import json
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow():
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class State(StrEnum):
    WORKING = "WORKING"
    ATTENTION = "ATTENTION"
    IDLE = "IDLE"
    CLOSED = "CLOSED"


EventName = Literal[
    "session_started", "work_started", "tool_started", "tool_finished", "tool_failed",
    "activity", "permission_required", "input_required", "input_resolved",
    "turn_finished", "failed", "interrupted", "session_ended",
]
Provider = Literal["codex", "claude"]
Short = Annotated[str, Field(min_length=1, max_length=255)]
Message = Annotated[str, Field(max_length=8000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Model):
    provider: Provider
    provider_session_id: Short
    hostname: Short
    cwd: Annotated[str, Field(max_length=4096)] = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("metadata exceeds 16 KiB")
        if any(key.startswith("_") for key in value):
            raise ValueError("metadata keys starting with _ are reserved")
        return value


class Event(Identity):
    event_id: Short = Field(default_factory=lambda: str(uuid4()))
    event: EventName
    timestamp: datetime = Field(default_factory=utcnow)
    message: Message | None = None

    @field_validator("timestamp")
    @classmethod
    def valid_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        if value > utcnow() + timedelta(minutes=5):
            raise ValueError("timestamp is over five minutes in the future; check your clock")
        return value.astimezone(timezone.utc)


class SessionCreate(Identity):
    project_name: Short | None = None
    state: State = State.IDLE
    attention_reason: Annotated[str, Field(max_length=255)] | None = None
    last_message: Message | None = None


class SessionPatch(Model):
    project_name: Short | None = None
    state: State | None = None
    last_message: Message | None = None

    @field_validator("project_name", "state")
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError("omit this field instead of setting it to null")
        return value
