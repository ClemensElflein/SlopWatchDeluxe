# Operations and reference

Setup, configuration, lifecycle behavior, and troubleshooting for SlopWatchDeluxe.
For a quick start, see the [README](../README.md).

## Server configuration

Copy `.env.example` to `.env` to override Compose defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SLOPWATCHDELUXE_PORT` | `8765` | Published host port in Compose; listening port when running the server directly |
| `SLOPWATCHDELUXE_ARCHIVE_AFTER_HOURS` | `24` | Positive number of hours without activity before archiving; fractions accepted |
| `SLOPWATCHDELUXE_API_TOKEN` | empty | Optional shared API bearer token |
| `SLOPWATCHDELUXE_DATABASE` | `/data/slopwatchdeluxe.db` in Docker | SQLite file; Compose deliberately keeps this inside the volume |

Apply changes with `docker compose up -d`. For a different database filename,
edit the Compose service environment and keep the file under `/data`.
The server uses one worker. SSE invalidation is in-process; do not run multiple
workers or replicas against the same SQLite file.

### API token

Set `SLOPWATCHDELUXE_API_TOKEN` in `.env`, recreate the service, and enter the same
token in the client installer. The browser's **Connection settings** accepts
the token and stores it in `sessionStorage` for that tab. All session and
event APIs, including SSE, require `Authorization: Bearer TOKEN`. Health,
the empty UI shell/static assets, and the generic client download are public.
There are no user accounts, cookies, or tokens in URLs. The OpenAPI schema at `/openapi.json` describes requests; use your API client
to set the bearer header.

With no token the API is open to anyone who can reach it, intentionally for
trusted LAN use. Hook messages may include prompts, responses, cwd, machine
names, or command descriptions. Use a token and HTTPS through a reverse proxy
when traffic leaves your trusted network. Reverse proxies must pass the
Authorization header, permit streaming, disable SSE buffering, and allow
idle connections for more than 35 seconds. Serve the app at the domain root.

### Persistence and archiving

The Compose volume `slopwatchdeluxe-data` (named
`slopwatchdeluxe_slopwatchdeluxe-data` with the supplied `compose.yaml`) contains
SQLite and its WAL files. Restarting or recreating the container preserves
sessions and their states. `docker compose down` keeps the volume;
**`docker compose down -v` deletes it**.

Inactivity is checked on startup and once per minute, based on
`last_activity_at`. Archived records remain in SQLite and the Archived view.
Restoring counts as manual activity and grants another full archive interval;
a new hook event also restores its session automatically. Deleting a record
is permanent, but a future event for that provider session creates a new card.
Use archive to retain history. There is no permanent event log or transcript.

For a consistent backup, stop the service before copying the volume, or use
SQLite's backup API inside the container:

```bash
docker compose exec slopwatchdeluxe python -c 'import sqlite3; sqlite3.connect("/data/slopwatchdeluxe.db").backup(sqlite3.connect("/data/backup.db"))'
docker compose cp slopwatchdeluxe:/data/backup.db ./slopwatchdeluxe-backup.db
```

## Client and installer

```bash
slopwatchdeluxe status
slopwatchdeluxe test
slopwatchdeluxe install       # safely repeat to change endpoint/token or update hooks
slopwatchdeluxe uninstall
```

`test` adds a clearly labeled harmless ATTENTION card that you can delete.
`status` checks the server, token, provider paths/versions, and configured hook
handlers. Codex trust and managed policy must be checked in the provider UI;
configuration inspection cannot prove hooks have actually fired.

Installation copies the zipapp to `~/.local/bin/slopwatchdeluxe` and saves its
settings/ownership record in `$XDG_CONFIG_HOME/slopwatchdeluxe/config.json`, default
`~/.config/slopwatchdeluxe/config.json`. The config is mode `0600`, including the
token. Hook commands use the Python interpreter and quoted absolute paths
selected during installation, so they work even if `.local/bin` is absent
from PATH. To use the convenience commands, add:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Use a durable system Python interpreter to install; if that interpreter is
removed, reinstall. Native Windows installation is not supported; use WSL.

For scripted installation with detected supported tools:

```bash
python3 slopwatchdeluxe.pyz install --yes --url http://SERVER:8765 \
  --provider codex --provider claude --token-env SLOPWATCHDELUXE_TOKEN
```

Set `SLOPWATCHDELUXE_TOKEN` in the calling environment; omit `--token-env` if no
token is needed. Interactive Enter keeps an existing token; `-` clears it.
Reinstalling with a different provider selection removes SlopWatchDeluxe hooks
for deselected providers. Uninstall uses the saved provider paths, even when
the CLI is no longer on PATH. If you intentionally change `CODEX_HOME` or
`CLAUDE_CONFIG_DIR`, rerun install under that environment.

Before changes, the installer validates all target JSON, rejects duplicate
keys and invalid hook nesting, and detects unrelated executable collisions.
Symlinked config files are refused to avoid changing dotfile manager targets
unexpectedly. Use regular files for automated edits. Updates are atomic per
file, with best-effort rollback on write failure and concurrent-edit detection.
Avoid editing provider settings concurrently with installation. Original bytes
are backed up beside each changed file as `.slopwatchdeluxe-backup-TIMESTAMP`, mode
`0600`. Byte-identical repeat installs create no additional backups.

Uninstall removes only recorded SlopWatchDeluxe handlers and its binary/config,
preserves unrelated settings and hooks added since installation, and retains
backups. Inspect backups before restoring one manually: replacing an entire
provider file would also roll back unrelated edits. Backups may contain tokens;
remove them manually when you no longer need them. The installer lock file is
harmless and remains in the configuration directory.

## Provider integrations and inference

The researched contracts, exact event mapping, sources, and limitations are
in [docs/provider-hooks.md](provider-hooks.md).

**Codex:** Adds handlers to `$CODEX_HOME/hooks.json`, default
`~/.codex/hooks.json`. Preserves existing JSON hooks, inline TOML hooks, and
legacy `notify` commands. Existing inline TOML plus JSON may produce Codex's
multiple-source warning; both still load. Open `/hooks` to trust new or changed
commands. Disabled hooks or managed-only policies must be resolved in Codex.

**Claude:** Merges handlers into `$CLAUDE_CONFIG_DIR/settings.json`, default
`~/.claude/settings.json`. Session start/prompt/tool/permission/stop/end events
cover the main lifecycle. AskUserQuestion, ExitPlanMode, MCP elicitation, and
selected notifications cover input waits. StopFailure covers terminal API
errors. Existing hooks are retained.

Adapters convert provider payloads into provider-neutral facts. The server
applies transitions; changing the state model does not require reinstalling
clients. Provider API changes can still require a client update. Git identity
is read cheaply at session start, never by invoking git on every tool call.
SlopWatchDeluxe does not read transcripts or elicitation answers.

Limitations to understand:

- Codex does not expose a general terminal API-error hook or dedicated MCP
  elicitation hook in the researched contract. Its input-tool detection is a
  heuristic. These cannot be made perfectly observable using only its hooks.
- Claude does not have a general interrupt hook; an interrupted tool failure
  is only partial coverage. SIGKILL/crashes cannot reliably report session end.
- Permission approval itself has no separate event. A matching tool completion
  clears the wait, so a long approved tool can keep displaying attention until
  it returns. Matching by tool name is ambiguous for concurrent identical tools.
- Another Stop hook can continue a turn. SlopWatchDeluxe may briefly show completion
  until the next prompt/tool event. Subagent completion does not finish the
  parent; human requests from Claude subagents appear on its parent card.
- Hooks use short synchronous calls: 0.8-second socket timeout, 1.2-second total
  Unix deadline, 2-second provider timeout. Offline events are dropped silently;
  there is no retry spool. Missing/malformed/oversized input never makes a
  handled hook deny an operation. A dead server can add about one second to
  each hook call until you restore connectivity or disable the integration.
- Events are ordered using capture timestamps. Keep machine clocks synchronized.
  Older events and duplicate most-recent event IDs are ignored. This is an
  attention snapshot, not a durable, exactly-once audit system.
- Hooks are not heartbeats. Long model responses or commands may have no activity
  events for a while. Age is shown explicitly; no elapsed-time guess turns a
  running session into a successful completion. The archive threshold applies
  to every state, including a task running silently for more than 24 hours.

Permission requests have a 30-second server-side grace period. Requests resolved
within that window stay Working; unresolved requests become Needs attention,
including without further hook events. On Linux, the client observes the exact
Codex Bash command starting and resolves its permission immediately, without
waiting for a long flash/playback command to finish. This requires the updated
client as well as the server; reinstall the client after deploying the update.
The observer uses local process arguments, never executes tool input, and exits
when resolved, canceled, or the agent exits (at most 24 hours). If the shell
replaces itself and exact matching is unavailable, completion remains the
fallback. Other platforms and tools continue using hook-based resolution.
Input questions and turn completion still appear immediately. The Needs attention summary is yellow only when its count is
nonzero. Closed sessions are hidden in the default Active view; select Closed to
see previous runs, including earlier sessions in the same directory.

## REST API

OpenAPI schema: `/openapi.json`. All routes below use JSON.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/sessions` | Create explicitly; duplicate compound identity returns 409 |
| GET | `/api/v1/sessions` | Active records by default |
| GET | `/api/v1/sessions/{id}` | Read one record |
| PATCH | `/api/v1/sessions/{id}` | Set project name, state (including acknowledgment), or last message |
| DELETE | `/api/v1/sessions/{id}` | Permanently delete; returns 204 |
| POST | `/api/v1/events` | Apply a normalized event; upsert missing session |
| POST | `/api/v1/sessions/{id}/archive` | Archive manually |
| POST | `/api/v1/sessions/{id}/restore` | Restore manually |
| GET | `/api/v1/health` | Check application/database health |
| GET | `/api/v1/stream` | SSE change notifications; refetch sessions on change |

List query parameters: `archived=true`, `state=ATTENTION`, `provider=claude`,
`search=project`, `limit=1000` (maximum), `offset=0`. The UI follows pagination.
`archived=true` selects archived records only. Compound identity is
`provider + hostname + provider_session_id`; give your machines distinct names.

```bash
curl -X POST http://SERVER:8765/api/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"provider":"codex","provider_session_id":"example","hostname":"devbox","cwd":"/work/demo","event":"turn_finished","message":"Finished the requested work."}'
```

Add `Authorization: Bearer TOKEN` if configured. `event_id` and `timestamp`
(timezone required) are optional for manual producers; the client supplies
both. Accepted events: `session_started`, `work_started`, `tool_started`,
`tool_finished`, `tool_failed`, `activity`, `permission_required`,
`input_required`, `input_resolved`, `turn_finished`, `failed`, `interrupted`,
`session_ended`. Provider-specific names such as `Stop` are not accepted.

Requests must be JSON and at most 64 KiB, including chunked requests. Messages
are capped at 8,000 characters and per-event metadata at 16 KiB; accumulated
metadata is bounded. Agent text is rendered as text, never HTML. The web API
does not expose local files or execute agent data. Health responses reveal no
session data or token. Tokenless deployments should remain on a trusted LAN.

## Build and develop

```bash
python3 scripts/build-zipapp.py
./dist/slopwatchdeluxe.pyz --version
python3 dist/slopwatchdeluxe.pyz install
```

The builder uses sorted entries, fixed timestamps/permissions, and the standard
library. Identical source and Python/zlib toolchain produce identical zipapp
bytes. Docker builds its own client artifact; rebuild the image after editing
client or server source with `docker compose up -d --build`.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
docker compose up -d --build --wait
python3 scripts/smoke-test.py http://localhost:8765
```

The smoke test installs into a temporary HOME with fake provider executables,
runs the actual installed hook commands against the live server, verifies
transitions/SSE/archive/restore/delete, and uninstalls. It removes only its own
test sessions. Set `SLOPWATCHDELUXE_TEST_TOKEN` when testing a protected server.
Automated tests never modify your real provider configuration.

For local server development: `.venv/bin/python -m server`. Data defaults to
`./data/slopwatchdeluxe.db` outside Docker. No frontend build or Node service exists.

## Troubleshooting

- **No cards:** Run `slopwatchdeluxe status`, then `slopwatchdeluxe test`. Check endpoint,
  token, firewall, and `docker compose logs --tail=100`. Use your server's LAN
  address on other machines; their `localhost` is not your server.
- **Codex is open but has no card yet:** Codex CLI 0.154.0 defers its
  `SessionStart` hook until the first prompt. An empty window does not register
  immediately with this hook-only client. Submit a prompt to register it.
- **Test card works but CLI does not:** Start a fresh session. In Codex use
  `/hooks` to review/trust definitions. In Claude inspect `/hooks` and
  `disableAllHooks`. Check custom config directories and organization policy.
- **Token rejected:** Update both installer config and browser Connection
  settings. The healthcheck intentionally works without authentication.
- **Disconnected browser:** Verify the server and any proxy's SSE settings.
  The browser retries automatically; the last loaded cards remain visible.
- **State looks stale:** Inspect its last-activity time and the provider
  limitations above. SlopWatchDeluxe cannot recover a dropped offline event.
  Acknowledge or archive manually, or submit another prompt to produce a fresh
  lifecycle event.
- **Malformed config or symlink:** Installer stops before rewriting target
  config. Repair the file or use a regular config file, then rerun install.
- **Port already used:** Set `SLOPWATCHDELUXE_PORT` in `.env` and recreate the service.
- **Permission denied for a bind-mounted database:** The container runs as
  UID/GID 10001. Prefer the supplied named volume, or make your bind mount
  writable by that UID. Do not run the service as root just to bypass it.

## Upgrading from AgentWatch

The Compose file is now `compose.yaml`. The Compose project, service, and
container are named `slopwatchdeluxe`; the image is
`ghcr.io/clemenselflein/slopwatchdeluxe:latest`. Remove or update old
`docker-compose.yml` files and stop the old service before starting the new one.

Before switching, run the old `agentwatch uninstall` command to remove its
managed hooks, then install the new `slopwatchdeluxe.pyz` client. Use the same
server URL and token. Rename server environment variables from `AGENTWATCH_`
to `SLOPWATCHDELUXE_`. To preserve existing sessions, attach the existing Docker
data volume and set `SLOPWATCHDELUXE_DATABASE` to its existing `/data/agentwatch.db`
file. The new default volume and database names otherwise start a fresh database.

## Attention sounds

The header sound toggle starts muted and remembers your preference in this
browser. Enabling it plays a short preview. A chime then plays when a new session
needs attention or an existing session enters Needs attention again. Several
arrivals in one refresh share a chime. Changing filters, refreshing unchanged
cards, and loading existing attention cards do not replay alerts. Muting also
stops a chime already playing.

Keep the dashboard tab open for sounds. After a reload, use **Resume sound** or
interact with the page to allow audio. Browser or system muting and suspended
background tabs can prevent or delay playback. No audio file is downloaded.
