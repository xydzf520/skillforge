"""Regenerate the single-file Bridge copies of governed media templates."""

from __future__ import annotations

import base64
import json
import re
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "bridge" / "skillforgebridge.py"
TEMPLATE_DIR = ROOT / "bridge" / "media_templates"
ASSIGNMENT_RE = re.compile(r"^MEDIA_EMBEDDED_TEMPLATES = .*?$", re.MULTILINE)


def main() -> None:
    embedded = {
        path.stem: base64.b64encode(zlib.compress(path.read_bytes())).decode("ascii")
        for path in sorted(TEMPLATE_DIR.glob("h3_*_v1.json"))
    }
    assignment = "MEDIA_EMBEDDED_TEMPLATES = " + json.dumps(
        embedded,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    source = BRIDGE_PATH.read_text(encoding="utf-8")
    updated, count = ASSIGNMENT_RE.subn(assignment, source, count=1)
    if count != 1:
        raise RuntimeError("MEDIA_EMBEDDED_TEMPLATES assignment was not found exactly once")
    BRIDGE_PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
