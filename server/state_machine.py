"""Provider-neutral transitions. Adapters supply facts, never UI states."""
from datetime import timedelta

from .models import iso, utcnow

PERMISSION_GRACE_SECONDS = 30

ATTENTION = {
    "turn_finished": "turn_finished",
    "permission_required": "permission_required",
    "input_required": "input_required",
    "failed": "failed",
    "interrupted": "interrupted",
}


def promote_permissions(session: dict, now=None) -> bool:
    """Make unresolved permission requests visible after their grace period."""
    pending = session["metadata"].get("_pending_permissions", {})
    waits = session["metadata"].setdefault("_waits", {})
    stamp = iso(now or utcnow())
    changed = False
    for key, request in list(pending.items()):
        if request["due_at"] <= stamp:
            del pending[key]
            waits[key] = "permission_required"
            if len(waits) > 64:
                del waits[next(iter(waits))]
            if request.get("message") and not changed and not session["attention_reason"]:
                session["last_message"] = request["message"]
            changed = True
    if changed:
        session["state"] = "ATTENTION"
        session["attention_reason"] = next(iter(waits.values()))
    if not pending:
        session["metadata"].pop("_pending_permissions", None)
    return changed


def transition(session: dict, event: str, metadata: dict, now=None, message=None) -> None:
    waits = session["metadata"].setdefault("_waits", {})
    pending = session["metadata"].setdefault("_pending_permissions", {})
    tool = str(metadata.get("tool_name") or metadata.get("mcp_server_name") or "unknown")[:255]
    # A subagent must not reset the parent's state, but its human requests matter.
    actor = str(metadata.get("agent_id", "main"))[:255]
    key = f"{actor}:{tool}"

    def set_state(state, reason=None):
        session["state"] = state
        session["attention_reason"] = reason

    if event == "work_started":
        waits.clear()
        pending.clear()
        set_state("WORKING")
    elif event == "session_started":
        waits.clear()
        pending.clear()
        set_state("IDLE")
    elif event == "session_ended":
        waits.clear()
        pending.clear()
        set_state("CLOSED")
    elif event == "permission_required":
        if key not in waits:
            pending.setdefault(key, {"due_at": iso((now or utcnow()) + timedelta(seconds=PERMISSION_GRACE_SECONDS)),
                                     "message": message[:256] if message else None})
            if len(pending) > 32 or len(pending) + len(waits) > 64:
                del pending[next(iter(pending))]
        if not waits:
            set_state("WORKING")
    elif event in ATTENTION:
        if event == "input_required":
            pending.pop(key, None)
            waits[key] = ATTENTION[event]
            if pending and len(pending) + len(waits) > 64:
                del pending[next(iter(pending))]
            # Bound pending requests even for malicious event producers.
            if len(waits) > 64:
                del waits[next(iter(waits))]
        else:
            waits.clear()
            pending.clear()
        set_state("ATTENTION", ATTENTION[event])
    elif event in ("tool_finished", "tool_failed", "input_resolved"):
        resolved = waits.pop(key, None)
        resolved = pending.pop(key, None) or resolved
        # Delayed network/idle notifications have no tool identifier.
        if not metadata.get("agent_id") and not waits.get(key):
            resolved = waits.pop("main:unknown", None) or resolved
            resolved = pending.pop("main:unknown", None) or resolved
        if waits:
            set_state("ATTENTION", next(iter(waits.values())))
        elif resolved or session["state"] == "WORKING":
            set_state("WORKING")
    elif event == "tool_started" and not metadata.get("agent_id"):
        if not waits and session["state"] != "CLOSED":
            set_state("WORKING")
    # Generic activity (e.g. compaction) preserves the current state.
    promote_permissions(session, now)
