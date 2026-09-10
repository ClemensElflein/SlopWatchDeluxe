# MVP verification

Verified locally on 2026-09-10:

- 49 pytest tests passed: server lifecycle, identity/concurrency, permissions,
  persistence, archive/restore/delete, authentication, input limits, adapter
  mapping, git metadata, installer preservation/idempotence/backups/rollback,
  malicious command text, and offline/stdin deadlines. Two dependency
  deprecation warnings come from Starlette's current HTTPX test client.
- Docker image built and Compose started successfully with a healthy non-root
  container on port 8765. A real restart preserved all four test session records
  and their states exactly. The live background timer archived a session with
  activity older than 24 hours, without a restart.
- The packaged client installed, reported status, submitted its public test
  event, forwarded both providers' lifecycle payloads, and uninstalled in fake
  HOME directories. REST/SSE smoke checks passed against both an open server
  and an isolated bearer-token server on port 18765.
- Codex CLI 0.154.0's actual `hooks/list` API accepted all eight generated
  handlers as user hooks with no warnings/errors and the expected two-second
  timeout. It reported them as untrusted, confirming the documented review step.
- Claude Code 2.1.259's actual `doctor` command accepted the generated global
  settings. Warnings concerned the deliberately isolated HOME's missing native
  installation metadata and authentication, not hook syntax.
- Browser checks covered SSE updates/reconnection, attention counts/title,
  provider/state filters, acknowledgment, archive/restore, deletion confirmation,
  token entry, and narrow layout. HTML-looking agent text remained literal text;
  no injected image was created.
- The downloadable zipapp matched the local build byte-for-byte. Python
  compilation and `pip check` passed. Temporary dashboard records and the
  isolated authentication stack were removed; the main Compose service remains.

No real user Codex/Claude configuration was modified. No paid model sessions
were launched. Provider configuration loading was checked with real binaries;
full lifecycle transitions were exercised using representative documented
payloads through the installed hook commands. Actual interactive model runs
remain subject to the hook trust/policy and coverage limits in
[provider-hooks.md](provider-hooks.md).


## Follow-up verification (2026-09-10)

- 61 pytest tests passed after adding the 30-second permission grace period.
  Coverage includes exact expiration, automatic resolution without attention,
  repeated requests, unrelated waits, lifecycle cancellation, acknowledgment,
  and persistence across server restart.
- Browser checks on an isolated server confirmed Closed cards are hidden in
  Active and visible through Closed; the attention summary is neutral at zero,
  yellow after an unresolved permission timer expires, and neutral after the
  matching completion. The timed transition arrived via SSE without another
  provider event. Card entrance animations no longer replay on each update.
- Rebuilt and restarted the local Compose service. Authenticated packaged-client
  lifecycle and SSE smoke checks passed; existing session records were retained
  and temporary diagnostic records were removed.
- Actual idle Codex CLI 0.154.0 launches did not run SessionStart before a prompt,
  despite active/trusted hooks. SessionEnd ran on quit. No model prompts were
  submitted by these diagnostics. Immediate launch discovery remains separate
  from the hook-only integration.

## Release 1.1.0 verification (2026-09-10)

- 80 Python tests and 8 JavaScript sound tests passed. Sound checks cover initial
  load, new attention transitions, unchanged counts, repeated refreshes,
  archived sessions, muting, saved preferences, and unavailable audio/storage.
- Real Chrome gestures enabled the audio context. Live SSE updates triggered
  the two-note chime; repeated refreshes and filter changes stayed quiet.
  Muting and reload persistence worked, with no script exceptions.
- Header layout stayed within the viewport at widths of 390, 768, 1024, and
  1440 pixels. README screenshots were captured from the updated dashboard.
- The Docker image built successfully with version 1.1.0 and the GPL license.
