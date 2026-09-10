import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if not root.is_absolute():
        root = Path.home() / ".config"
    return root / "agentwatch" / "config.json"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path, missing=False):
    path = Path(path)
    if not path.exists():
        if missing:
            return {}
        raise ValueError(f"Missing configuration: {path}. Run agentwatch install.")
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f"Not a regular configuration file, or exceeds 4 MiB: {path}")
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def endpoint(value):
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or any(ord(c) < 33 for c in value)):
        raise ValueError("Dashboard URL must be http(s)://host[:port], optionally with a path; no credentials/query/fragment")
    _ = parsed.port  # Validates the port as well.
    return value


def load_config(path=None):
    data = read_json(path or config_path())
    data["url"] = endpoint(data.get("url", ""))
    token = data.get("token", "")
    if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in token):
        raise ValueError("API token must contain only printable ASCII and be at most 4096 characters")
    return data
