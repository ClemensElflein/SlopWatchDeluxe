"""Deterministic zipapp builder shared by the installer and build script."""
from io import BytesIO
import json
from pathlib import Path
import zipfile

from . import VERSION


def build_bytes():
    result = BytesIO()
    result.write(b"#!/usr/bin/env python3\n")
    files = {"__main__.py": b"from client.__main__ import main\nraise SystemExit(main())\n",
             "agentwatch-build.json": json.dumps({"application": "agentwatch", "version": VERSION}).encode()}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        files["client/" + path.name] = path.read_bytes()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compresslevel=9)
    return result.getvalue()
