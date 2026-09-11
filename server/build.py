"""Identify the running source checkout or the Git build baked into an image."""
import os
from pathlib import Path
import subprocess

VERSION = "1.1.1"


def build_id():
    supplied = os.getenv("SLOPWATCHDELUXE_BUILD", "").strip()
    if supplied:
        return supplied
    root = Path(__file__).resolve().parent.parent
    if (root / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--always", "--long", "--dirty"],
                cwd=root, capture_output=True, text=True, check=True, timeout=2)
            if result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return f"v{VERSION}+unknown"
