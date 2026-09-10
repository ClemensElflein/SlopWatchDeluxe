"""Provider-neutral transitions. Adapters supply facts, never UI states."""

ATTENTION = {
    "turn_finished": "turn_finished",
    "permission_required": "permission_required",
    "input_required": "input_required",
    "failed": "failed",
    "interrupted": "interrupted",
}


def transition(session: dict, event: str, metadata: dict) -> None:
    waits = session["metadata"].setdefault("_waits", {})
    tool = str(metadata.get("tool_name") or metadata.get("mcp_server_name") or "unknown")[:255]
    # A subagent must not reset the parent's state, but its human requests matter.
    actor = str(metadata.get("agent_id", "main"))[:255]
    key = f"{actor}:{tool}"

    def set_state(state, reason=None):
        session["state"] = state
        session["attention_reason"] = reason

    if event == "work_started":
        waits.clear()
        set_state("WORKING")
    elif event == "session_started":
        waits.clear()
        set_state("IDLE")
    elif event == "session_ended":
        waits.clear()
        set_state("CLOSED")
    elif event in ATTENTION:
        if event in ("permission_required", "input_required"):
            waits[key] = ATTENTION[event]
            # Bound pending requests even for malicious event producers.
            if len(waits) > 64:
                del waits[next(iter(waits))]
        else:
            waits.clear()
        set_state("ATTENTION", ATTENTION[event])
    elif event in ("tool_finished", "tool_failed", "input_resolved"):
        resolved = waits.pop(key, None)
        # Delayed network/idle notifications have no tool identifier.
        if not metadata.get("agent_id") and not waits.get(key):
            resolved = waits.pop("main:unknown", None) or resolved
        if waits:
            set_state("ATTENTION", next(iter(waits.values())))
        elif resolved or session["state"] == "WORKING":
            set_state("WORKING")
    elif event == "tool_started" and not metadata.get("agent_id"):
        if not waits and session["state"] != "CLOSED":
            set_state("WORKING")
    # Generic activity (e.g. compaction) preserves the current state.
