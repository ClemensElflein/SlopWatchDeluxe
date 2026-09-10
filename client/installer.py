"""Conservative JSON hook edits with backups, atomic writes, and ownership records."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from . import VERSION
from .adapters import ADAPTERS
from .build import build_bytes
from .config import config_path, endpoint, load_config, read_json
from .transport import request

MIN_VERSIONS = {"codex": (0, 154, 0), "claude": (2, 1, 259)}
MARKER = "--slopwatchdeluxe-managed"


def detect(provider):
    path = shutil.which(provider)
    version, supported = "unknown", False
    if path:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
            version = result.stdout.strip()[:200] or "unknown"
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
            supported = bool(match and tuple(map(int, match.groups())) >= MIN_VERSIONS[provider])
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"path": path, "version": version, "supported": supported}


def provider_path(provider):
    if provider == "codex":
        return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().absolute() / "hooks.json"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser().absolute() / "settings.json"


def command(binary, config_file, provider):
    return shlex.join([sys.executable, str(binary), "hook", "--provider", provider,
                       "--config", str(config_file), MARKER, "v1"])


def validate_hooks(data, path):
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Invalid hooks object in {path}; no files changed")
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError(f"Invalid hook groups for {event} in {path}")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"Invalid hook group for {event} in {path}")
            if "matcher" in group and not isinstance(group["matcher"], str):
                raise ValueError(f"Invalid matcher in {path}")
            if any(not isinstance(handler, dict) for handler in group["hooks"]):
                raise ValueError(f"Invalid hook handler in {path}")


def merge_hooks(data, owned_commands, new_command=None, provider=None):
    result = deepcopy(data)
    hooks = result.get("hooks", {})
    for event, groups in list(hooks.items()):
        remaining = []
        for group in groups:
            old = group["hooks"]
            kept = [h for h in old if not (h.get("type") == "command" and
                                          isinstance(h.get("command"), str) and
                                          h["command"] in owned_commands)]
            if kept or kept == old:
                remaining.append({**group, "hooks": kept})
        if remaining:
            hooks[event] = remaining
        elif groups:
            del hooks[event]
    if new_command:
        for event in ADAPTERS[provider].events:
            hooks.setdefault(event, []).append({"hooks": [
                {"type": "command", "command": new_command, "timeout": 2}
            ]})
        result["hooks"] = hooks
    elif "hooks" in result and not hooks:
        result.pop("hooks")
    return result


def encoded(data):
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def snapshot(path):
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {path}. Use a regular file for SlopWatchDeluxe-managed edits.")
    return path.read_bytes() if path.exists() else None


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".slopwatchdeluxe-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apply_changes(changes):
    """Preflight every file; roll back our writes if a later write fails."""
    prepared = []
    for path, old, new, mode in changes:
        if snapshot(path) != old:
            raise ValueError(f"Configuration changed during installation: {path}. Please run again.")
        if old != new:
            previous_mode = stat.S_IMODE(path.stat().st_mode) if old is not None else mode
            prepared.append((path, old, new, mode, previous_mode))
    completed = []
    try:
        for path, old, new, mode, previous_mode in prepared:
            if snapshot(path) != old:
                raise ValueError(f"Configuration changed concurrently: {path}")
            if old is not None:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = path.with_name(path.name + ".slopwatchdeluxe-backup-" + stamp)
                with backup.open("xb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(old)
                    handle.flush()
                    os.fsync(handle.fileno())
            if new is None:
                path.unlink()
            else:
                atomic_write(path, new, mode)
            completed.append((path, old, new, previous_mode))
    except Exception:
        for path, old, new, previous_mode in reversed(completed):
            if snapshot(path) == new:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, old, previous_mode)
        raise


@contextmanager
def install_lock(config_file):
    config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = config_file.parent / "install.lock"
    if lock.is_symlink():
        raise ValueError(f"Refusing symlink lock: {lock}")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another SlopWatchDeluxe installer is running") from None
        yield


def is_slopwatchdeluxe(path):
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("slopwatchdeluxe-build.json")
            return info.file_size < 1024 and json.loads(archive.read(info)).get("application") == "slopwatchdeluxe"
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False


def artifact_bytes():
    source = Path(sys.argv[0])
    return source.read_bytes() if is_slopwatchdeluxe(source) else build_bytes()


def install_files(url, token, providers, config_file=None, binary=None):
    config_file = Path(config_file or config_path()).absolute()
    binary = Path(binary or Path.home() / ".local/bin/slopwatchdeluxe").absolute()
    url = endpoint(url)
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in token):
        raise ValueError("API token must contain only printable ASCII and be at most 4096 characters")
    if not providers or any(p not in ADAPTERS for p in providers):
        raise ValueError("Choose at least one detected provider")
    with install_lock(config_file):
        old_config = snapshot(config_file)
        config = read_json(config_file, missing=True)
        old_binary = snapshot(binary)
        if old_binary is not None and not is_slopwatchdeluxe(binary):
            raise ValueError(f"An unrelated file already exists at {binary}; leaving it untouched")
        records = config.get("integrations", {})
        if not isinstance(records, dict):
            raise ValueError("Invalid SlopWatchDeluxe installation record")
        # Gather old and new targets first, including integrations moved to a different directory.
        targets = {}
        for provider, record in records.items():
            if provider not in ADAPTERS or not isinstance(record, dict):
                raise ValueError("Invalid SlopWatchDeluxe provider installation record")
            path = Path(record["path"])
            targets.setdefault(path, {"owned": set()})["owned"].add(record["command"])
        new_records = {}
        for provider in dict.fromkeys(providers):
            path = provider_path(provider)
            cmd = command(binary, config_file, provider)
            target = targets.setdefault(path, {"owned": set()})
            if "provider" in target:
                raise ValueError("Providers cannot share a hook configuration file")
            target.update(provider=provider, command=cmd)
            target["owned"].add(cmd)
            new_records[provider] = {"path": str(path), "command": cmd}
        changes = []
        for path, target in targets.items():
            original = snapshot(path)
            data = read_json(path, missing=True)
            validate_hooks(data, path)
            updated = merge_hooks(data, target["owned"], target.get("command"), target.get("provider"))
            if updated != data:
                mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
                changes.append((path, original, encoded(updated), mode))
        updated_config = {**config, "version": VERSION, "url": url, "token": token,
                          "binary": str(binary), "integrations": new_records}
        changes = [(binary, old_binary, artifact_bytes(), 0o755),
                   (config_file, old_config, encoded(updated_config), 0o600), *changes]
        apply_changes(changes)
        # Repair permissions even when a byte-identical installation is repeated.
        binary.chmod(0o755)
        config_file.chmod(0o600)
    return updated_config


def uninstall_files(config_file=None):
    config_file = Path(config_file or config_path()).absolute()
    if not config_file.exists():
        return False
    with install_lock(config_file):
        config = read_json(config_file)
        changes = []
        for record in config.get("integrations", {}).values():
            path = Path(record["path"])
            original = snapshot(path)
            data = read_json(path, missing=True)
            validate_hooks(data, path)
            updated = merge_hooks(data, {record["command"]})
            if updated != data:
                changes.append((path, original, encoded(updated), stat.S_IMODE(path.stat().st_mode)))
        binary = Path(config["binary"])
        if binary.exists() and is_slopwatchdeluxe(binary):
            changes.append((binary, snapshot(binary), None, 0o755))
        changes.append((config_file, snapshot(config_file), None, 0o600))
        apply_changes(changes)
    return True


def hooks_installed(provider, config):
    record = config.get("integrations", {}).get(provider)
    if not record:
        return False
    path = Path(record["path"])
    data = read_json(path, missing=True)
    validate_hooks(data, path)
    return all(any(h.get("command") == record["command"] and h.get("type") == "command" and h.get("timeout") == 2
                   for group in data.get("hooks", {}).get(event, []) for h in group["hooks"])
               for event in ADAPTERS[provider].events)


def connection(config):
    try:
        health = request(config, "/api/v1/health", timeout=2)
        request(config, "/api/v1/sessions?limit=1", timeout=2)
        return health.get("status") == "ok", "OK"
    except Exception as exc:
        return False, str(exc)


def install(args):
    print(f"SlopWatchDeluxe {VERSION} installer\n\nDetected:")
    detected = {p: detect(p) for p in ADAPTERS}
    for p, info in detected.items():
        print(f"  [{'x' if info['path'] else ' '}] {p}: {info['path'] or 'not found'}\n      {info['version']}")
        if info["path"] and not info["supported"]:
            print("      Upgrade required; supported baseline: " + ".".join(map(str, MIN_VERSIONS[p])))
    providers = args.provider or []
    if not providers:
        for p, info in detected.items():
            if info["path"] and info["supported"] and (args.yes or input(f"Install {p} integration? [Y/n] ").strip().lower() not in ("n", "no")):
                providers.append(p)
    if not providers or any(not detected[p]["supported"] for p in providers):
        raise ValueError("No supported tools selected. Install/upgrade Codex or Claude Code, then run again.")
    previous = read_json(config_path(), missing=True)
    url = args.url
    if not url:
        if args.yes:
            url = previous.get("url")
            if not url:
                raise ValueError("--url is required for the first noninteractive install")
        else:
            default = previous.get("url", "")
            url = input(f"Dashboard endpoint [{default}]: ").strip() or default
    token = previous.get("token", "")
    if args.token_env:
        if args.token_env not in os.environ:
            raise ValueError(f"Missing environment variable {args.token_env}")
        token = os.environ[args.token_env]
    elif not args.yes:
        entered = getpass.getpass("API token [optional; Enter keeps existing; '-' clears]: ")
        token = "" if entered == "-" else entered or token
    config = install_files(url, token, providers)
    print(f"\nInstalled: {config['binary']}\nConfig: {config_path()}")
    for p in providers:
        print(f"{p} hooks: configured ({config['integrations'][p]['path']})")
    if "codex" in providers:
        print("\nACTION REQUIRED: Start codex, open /hooks, and review/trust the SlopWatchDeluxe commands.\n"
              "Codex skips new hooks until you trust them. No trust checks have been disabled.")
    print("Start fresh CLI sessions to activate hooks.")
    if str(Path(config["binary"]).parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print('Add to your shell profile: export PATH="$HOME/.local/bin:$PATH"\nHooks already use absolute paths.')
    ok, detail = connection(config)
    print(f"Testing dashboard connection... {'OK' if ok else 'unreachable: ' + detail}")
    return 0


def status():
    print(f"SlopWatchDeluxe {VERSION}")
    config = None
    try:
        config = load_config()
        ok, detail = connection(config)
        print(f"\nServer: {config['url']}\n  reachable: {'yes' if ok else 'no (' + detail + ')'}")
        binary = Path(config.get("binary", ""))
        print(f"  installed executable: {'yes' if is_slopwatchdeluxe(binary) else 'missing or invalid'}")
    except (OSError, ValueError) as exc:
        print(f"\nConfiguration: {exc}")
    for p in ADAPTERS:
        info = detect(p)
        print(f"\n{p}:\n  detected: {info['path'] or 'no'}\n  version: {info['version']}")
        try:
            present = bool(config and hooks_installed(p, config))
            print(f"  hooks configured: {'yes' if present else 'no'}")
            if present and p == "codex":
                print("  activation: check /hooks in Codex for trust and enabled status")
            if present and p == "claude":
                settings = read_json(Path(config["integrations"][p]["path"]))
                if settings.get("disableAllHooks"):
                    print("  WARNING: disableAllHooks is enabled in user settings")
        except (OSError, ValueError) as exc:
            print(f"  hooks: invalid ({exc})")
    return 0
