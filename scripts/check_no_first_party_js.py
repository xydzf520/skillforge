#!/usr/bin/env python3
"""Fail if first-party product source reintroduces JavaScript files.

This guard intentionally scans product source roots, not operational smoke
scripts. Runtime/E2E helper scripts may stay in .mjs/.cjs while the frontend
product code remains TypeScript-only.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOTS = (
    Path("web/src"),
    Path("web/e2e"),
    Path("chrome-extension/src"),
    Path("playbook-editor/src"),
)

TARGET_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs"}


def main() -> int:
    matches: list[Path] = []

    for source_root in SOURCE_ROOTS:
        root = ROOT / source_root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TARGET_SUFFIXES:
                matches.append(path.relative_to(ROOT))

    if matches:
        print("Found forbidden first-party .js/.jsx files:")
        for match in sorted(matches):
            print(f"- {match}")
        return 1

    print("No first-party .js/.jsx files found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
