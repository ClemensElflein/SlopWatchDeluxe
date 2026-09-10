# Provider hook integration notes

Researched 2026-09-10, before implementation. Local binaries: Codex CLI
0.154.0 and Claude Code 2.1.259. These are the MVP's supported baselines;
older releases are not assumed to implement this contract.

Primary references:

- [Codex lifecycle hooks](https://learn.chatgpt.com/docs/hooks)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex configuration layers and CODEX_HOME](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Claude hook reference](https://code.claude.com/docs/en/hooks)
- [Claude configuration directory](https://code.claude.com/docs/en/claude-directory)
- [Claude tool reference](https://code.claude.com/docs/en/tools-reference)

## Configuration and activation

| | Codex | Claude Code |
| --- | --- | --- |
| Global file used by AgentWatch | `$CODEX_HOME/hooks.json`, default `~/.codex/hooks.json` | `$CLAUDE_CONFIG_DIR/settings.json`, default `~/.claude/settings.json` |
| Other representations | Inline TOML hooks and plugin hooks | Project/local/managed settings, plugins, skills |
| Shape | `hooks[event]` is an array of matcher groups, each with a `hooks` handler array | Same nesting |
| Handler used | `type: command`, shell-quoted absolute command, `timeout: 2` | Same |
| Direct HTTP | Not documented as a supported handler; command and MCP tool are supported | Supported for many events, but not SessionStart |
| Coexistence | All matching sources accumulate; inline TOML plus JSON produces a warning | Hook arrays merge across settings layers |
| Activation | Review/trust new or changed definitions through `/hooks`; hooks are enabled by default | Start a fresh CLI session after installation; inspect `/hooks` |

AgentWatch only edits JSON. It leaves Codex `config.toml` byte-for-byte intact,
including existing `notify` and inline hooks. It validates the whole JSON
structure, adds its own handler groups, preserves all unrelated values, backs
up original bytes, and atomically replaces the file. It uses an exact owned
command marker and executable/config paths to identify its handlers. Removing
the integration removes those handlers, not an entire event or another user's
matcher group. Changed files get timestamped backups, including on uninstall.
Duplicate JSON keys are rejected instead of silently losing values.

**Codex requires human trust review.** The installer does not synthesize trust
hashes or turn off trust checks. Configured does not mean activated: run Codex,
open `/hooks`, and trust the displayed AgentWatch commands once. A changed
command may need review again. Organization policy or disabled hooks can stop
either integration; AgentWatch does not override these settings.

### Observed Codex startup timing

A live check with Codex CLI 0.154.0 on 2026-09-10 confirmed that an idle, newly
opened CLI window does not run SessionStart until its first prompt, even when
`/hooks` reports the handler active and trusted. Quitting that empty window does
run SessionEnd. The hook-only client therefore cannot announce the window at
launch; immediate discovery requires a separate client-side integration.

## Wire contract

Both command-hook APIs send a JSON object on stdin containing `session_id`,
`cwd`, and `hook_event_name`. Neither supplies the machine hostname, so the
client collects it. Optional `model`, `agent_type`, `agent_id`, `turn_id`,
`tool_name`, and `tool_use_id` become bounded metadata. `prompt` supplies the
user message and `last_assistant_message` the completed response. Codex reports
model on common inputs; Claude principally reports it at SessionStart.
Tool inputs differ and are never executed by AgentWatch. On Linux, Codex Bash
permission hooks also pass the command to a detached local execution observer;
the command is used only for comparison with process arguments.

The client emits a UUID event ID, UTC capture time, compound session identity,
normalized event, bounded message, and allowlisted metadata. It never reads
`transcript_path`, arbitrary agent-supplied files, or elicitation answers.
Git repository/branch are collected by reading `.git/HEAD` only at session
start (including worktree indirection); no git subprocess runs in hooks.

## Adapter mapping

| User-visible meaning | Codex input | Claude input | Normalized event |
| --- | --- | --- | --- |
| Session opened/resumed | SessionStart | SessionStart | session_started |
| New work | UserPromptSubmit | UserPromptSubmit | work_started |
| Tool starts | PreToolUse | PreToolUse | tool_started |
| Tool ends | PostToolUse (also nonzero shell exits) | PostToolUse | tool_finished |
| Permission waiting | PermissionRequest | PermissionRequest; Notification permission_prompt | permission_required |
| User question | PreToolUse request_user_input / request_user_input_async (tool-name heuristic) | PreToolUse AskUserQuestion / ExitPlanMode; Elicitation; selected Notification types | input_required |
| Input returned | Corresponding PostToolUse | ElicitationResult / corresponding PostToolUse | input_resolved / tool_finished |
| Turn complete | Stop | Stop | turn_finished |
| Terminal failure | No documented general failure hook | StopFailure (`error`, `error_details`) | failed |
| Recoverable tool failure | PostToolUse is still tool activity | PostToolUseFailure (`error`, `is_interrupt`) | tool_failed, or interrupted |
| User interrupted | Interrupt | No general interrupt hook; interrupted tool failure is partial coverage | interrupted |
| Session ended | SessionEnd (`reason: other`) | SessionEnd (`reason`) | session_ended |
| Compaction continuing | SessionStart source=compact | SessionStart source=compact | activity |

Other supported lifecycle hooks are deliberately unused: Codex SubagentStart,
SubagentStop, PreCompact, PostCompact; Claude's setup, subagent, task, teammate,
configuration, file/worktree, model, and display hooks. SubagentStop must not
announce completion of the parent. Claude subagent tool events carry agent_id;
AgentWatch ignores ordinary subagent activity but forwards requests requiring
human attention and their resolutions into the parent card.

## State inference and limits

The **server** owns state transitions. Start is IDLE; prompt is WORKING;
completion, input waits, terminal failures, and interruption need ATTENTION.
Permission requests stay WORKING for a 30-second server-side grace period; only
unresolved requests then become ATTENTION. End is CLOSED, hidden in the default
Active view and available through the Closed filter. Tool failures are usually recoverable, so they record
diagnostic metadata and keep the agent working. Acknowledgment sets IDLE.

Pending permission deadlines survive server restarts and are canceled by matching
tool completion, explicit resolution, a new turn, session end, or acknowledgment.
The timer publishes a dashboard update even if no more hooks arrive. Repeated
permission events do not extend the deadline. Permission/input waits stay visible
across unrelated parallel tool activity.
A matching tool completion or explicit input resolution clears the wait. When
available, tool_use_id/elicitation_id distinguishes parallel calls of the same
tool. Older events without IDs still use tool-name matching as a fallback.

On Linux, a Codex Bash permission hook starts a detached observer. It compares
the requested command with new processes beneath the originating Codex process,
checking the parent's start time against PID reuse and excluding pre-existing
matches. An exact shell body or literal argument list confirms execution. The
observer immediately reports input_resolved with the permission request's UUID,
so an approved command remains Working even when it runs for hours. The server
ignores resolutions for canceled/replaced requests. Other pending approvals and
input questions remain visible. Polling stops after observation, cancellation,
agent exit, network failure, or 24 hours. No command is rewritten or approved.

There is no dedicated approval-result hook. Platforms other than Linux, non-Bash
tools, and commands whose shell has replaced itself with an unidentifiable
process retain completion-based resolution. Shell expansion and partial command
matches are deliberately not treated as proof of execution. Prompt start
clears previous waits. Timestamp ordering rejects older events; synchronized
machine clocks are recommended. Activity after an observed completion cannot
silently reopen it, except a new tool start or prompt indicating resumed work.

Stop can run before another hook decides to continue the agent. A later prompt
or tool start corrects the temporary completion indication. Background tasks
do not necessarily mean the main turn remains active. Plain prose questions
are reported as turn completion. Codex MCP elicitation occurring inside a tool
has no documented dedicated hook, so it cannot always be observed. Codex also
has no general terminal API-error signal. Crashes, SIGKILL, provider-disabled
hooks, untrusted hooks, network loss, and some Claude interrupts can leave
stale state. No wrapper or transcript tailer is installed. The Linux execution
observer is launched only for Codex Bash permission requests.
Last-activity age stays visible; inactivity archives the card, never invents a
successful completion.

Hooks run synchronously for useful ordering and use a 0.8-second HTTP socket
timeout plus a 1.2-second total Unix deadline (including DNS/stdin). Provider
timeout is 2 seconds. They return success with no decisions or output on all
handled failures. Offline events are dropped; no retries or local event spool.
Only the bounded hook request is sent to the configured endpoint. No token is
embedded in provider configuration or hook commands.
