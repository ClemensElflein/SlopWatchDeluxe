import argparse
from pathlib import Path
import sys
from uuid import uuid4

from . import VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description="Attention tracking for Codex and Claude Code")
    parser.add_argument("--version", action="version", version=f"SlopWatchDeluxe {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="Safely install global provider hooks")
    install_parser.add_argument("--yes", action="store_true", help="Use defaults without prompting")
    install_parser.add_argument("--provider", choices=["codex", "claude"], action="append")
    install_parser.add_argument("--url")
    install_parser.add_argument("--token-env", help="Read API token from this environment variable")
    commands.add_parser("uninstall", help="Remove only SlopWatchDeluxe files/hooks; retain backups")
    commands.add_parser("status", help="Show configuration, integration, and server status")
    commands.add_parser("watch-execution", help=argparse.SUPPRESS)
    commands.add_parser("test", help="Send a harmless attention event to the dashboard")
    hook = commands.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("--provider", required=True, choices=["codex", "claude"])
    hook.add_argument("--config")
    hook.add_argument("--slopwatchdeluxe-managed", choices=["v1"])
    notify = commands.add_parser("notify", help=argparse.SUPPRESS)
    notify.add_argument("--config")
    notify.add_argument("--slopwatchdeluxe-managed", choices=["v1"])
    notify.add_argument("payload")
    args = parser.parse_args(argv)
    if args.command == "notify":
        from .codex_notify import handle_notification
        return handle_notification(args.payload, args.config)
    if args.command == "watch-execution":
        from .execution import handle_watch
        return handle_watch()
    if args.command == "hook":
        from .hooks import handle
        return handle(args.provider, args.config)
    if sys.platform == "win32":
        parser.error("The installer supports Linux, macOS, and WSL. Use WSL on Windows.")
    try:
        from .installer import install, status, uninstall_files
        if args.command == "install":
            return install(args)
        if args.command == "status":
            return status()
        if args.command == "uninstall":
            print("SlopWatchDeluxe uninstalled. Unrelated hooks and backups retained." if uninstall_files() else "SlopWatchDeluxe is not installed.")
            return 0
        from .adapters import ADAPTERS
        from .config import load_config
        from .transport import request
        config = load_config()
        provider = next(iter(config.get("integrations", {})), "codex")
        event = ADAPTERS[provider].normalize({"session_id": "slopwatchdeluxe-test-" + str(uuid4()),
                                            "cwd": str(Path.cwd()), "hook_event_name": "Stop",
                                            "last_assistant_message": "SlopWatchDeluxe connection test succeeded. You can delete this card."})
        if provider == "codex":
            event = ADAPTERS[provider].normalize_notification({"type": "agent-turn-complete",
                "thread-id": "slopwatchdeluxe-test-" + str(uuid4()), "cwd": str(Path.cwd()),
                "last-assistant-message": "SlopWatchDeluxe connection test succeeded. You can delete this card."})
        event["metadata"]["test"] = True
        result = request(config, "/api/v1/events", event, timeout=2)
        if result.get("provider_session_id") != event["provider_session_id"] or result.get("state") != "ATTENTION":
            raise ValueError("Server returned an unexpected test result")
        print(f"Test accepted: {result['id']} (ATTENTION)\nOpen {config['url']}")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"SlopWatchDeluxe: {exc or 'cancelled'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
