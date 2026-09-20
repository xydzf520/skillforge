#!/usr/bin/env python3
"""Check the files that would be published. Never print matched secret values.

Use --staged in a pre-commit hook: it checks index blobs, not working copies.
This supplements, and does not replace, a full secret scanner and manual review.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {"data", "artifacts", "vendor", "skills-repo", "node_modules", ".venv", ".private"}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".har", ".log", ".dump", ".deb", ".tgz"}
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b")
INTERNAL_HOST = re.compile(r"\b[a-zA-Z0-9][a-zA-Z0-9.-]*\.lan\b")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def inspect_file(name: str, raw: bytes) -> list[tuple[int, str]]:
    path = Path(name)
    findings: list[tuple[int, str]] = []
    if any(p in FORBIDDEN_DIRS for p in path.parts) or path.suffix in FORBIDDEN_SUFFIXES:
        findings.append((0, "private/runtime file"))
    if path.name.startswith('.env') and not path.name.endswith(('.example', '.template')):
        findings.append((0, "local environment file"))
    if b'\0' in raw:
        if path.suffix.lower() not in {'.png', '.ico'}:
            findings.append((0, "unreviewed binary"))
        return findings
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        return findings + [(0, "unreviewed binary encoding")]
    fixture = name.startswith(('tests/', 'web/src/__tests__/', 'web/e2e/')) or '/__tests__/' in name
    for number, line in enumerate(content.splitlines(), 1):
        synthetic = fixture and "public-scan: synthetic-fixture" in line
        if (PRIVATE_KEY.search(line) or TOKEN.search(line)) and not synthetic:
            findings.append((number, "credential pattern"))
        if INTERNAL_HOST.search(line) and not fixture:
            findings.append((number, "internal hostname"))
        # Private addresses in security unit tests are deliberate negative cases.
        if not fixture:
            for match in IPV4.finditer(line):
                try:
                    ip = ipaddress.ip_address(match.group())
                except ValueError:
                    continue
                if any(ip in network for network in PRIVATE_NETWORKS) and name != 'scripts/check_public_distribution.py':
                    findings.append((number, "private deployment address"))
                    break
    return findings


def collect(root: Path, staged: bool = False) -> list[tuple[str, int, str]]:
    args = ['git', 'ls-files', '-z', '--cached']
    if not staged:
        args += ['--others', '--exclude-standard']
    names = sorted(set(subprocess.check_output(args, cwd=root).decode().split('\0')) - {''})
    results = []
    for name in names:
        if staged:
            raw = subprocess.check_output(['git', 'show', f':{name}'], cwd=root)
        else:
            path = root / name
            if not path.is_file():
                continue
            raw = path.read_bytes()
        results.extend((name, number, reason) for number, reason in inspect_file(name, raw))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    findings = collect(ROOT, args.staged)
    for name, line, reason in findings:
        print(f'{name}:{line}: {reason} (value redacted)')
    print(f'Public distribution check: {len(findings)} finding(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
