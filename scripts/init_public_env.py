#!/usr/bin/env python3
"""Generate local-only credentials. Never overwrite an existing .env."""
from pathlib import Path
import os
import secrets


def initialize(root: Path) -> Path:
    from cryptography.fernet import Fernet
    password = secrets.token_urlsafe(32)
    values = {
        'SECRET_KEY': secrets.token_urlsafe(48),
        'ENROLLMENT_PEPPER': secrets.token_urlsafe(48),
        'COOKIE_ENCRYPT_KEY': Fernet.generate_key().decode(),
        'POSTGRES_PASSWORD': password,
        'GRAFANA_ADMIN_PASSWORD': secrets.token_urlsafe(32),
        'DATABASE_URL': f'postgresql+asyncpg://skillforge:{password}@localhost:5432/skillforge',
        'DATABASE_URL_SYNC': f'postgresql+psycopg2://skillforge:{password}@localhost:5432/skillforge',
        'SKILL_REPO_PATH': str((root / 'skills-repo').resolve()),
        'COOKIE_SECURE': 'false',  # localhost HTTP only; enable for HTTPS deployment.
    }
    text = (root / '.env.example').read_text(encoding='utf-8')
    lines = []
    for line in text.splitlines():
        key = line.partition('=')[0]
        lines.append(f'{key}={values.pop(key)}' if key in values else line)
    lines += [f'{key}={value}' for key, value in values.items()]
    target = root / '.env'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write('\n'.join(lines) + '\n')
    return target


if __name__ == '__main__':
    initialize(Path(__file__).resolve().parents[1])
    print('Created local .env with unique credentials and mode 0600. Values were not printed.')
