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
