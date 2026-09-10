#!/usr/bin/env python3
"""Build the stdlib-only client. No pip or build environment needed."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from client.build import build_bytes

destination = ROOT / "dist" / "slopwatchdeluxe.pyz"
destination.parent.mkdir(exist_ok=True)
destination.write_bytes(build_bytes())
destination.chmod(0o755)
print(destination)
