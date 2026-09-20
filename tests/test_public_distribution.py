"""Public-export safety and portable initialization regression tests."""
import json
from datetime import datetime

import pytest

from scripts.check_public_distribution import inspect_file
from scripts.init_public_env import initialize
from scripts.init_db import initial_admin_password


def test_runtime_files_are_rejected():
    assert inspect_file('data/users.json', b'{}')
    assert inspect_file('.env', b'KEY=value')
    assert inspect_file('private.pem', b'contents')
    assert not inspect_file('.env.example', b'AI_API_KEY=\n')


def test_public_defaults_require_explicit_training_authorization(monkeypatch):
    from app.config import Settings
    flags = ('TRAINING_AUTOMATION_ENABLED', 'LEARNING_AUTO_TRAINING_ENABLED', 'LEARNING_AUTO_TRAINING_DISPATCH_ENABLED', 'LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED', 'CODING_AGENT_ENABLED')
    for flag in flags:
        monkeypatch.delenv(flag, raising=False)
    settings = Settings(_env_file=None)
    assert all(getattr(settings, flag) is False for flag in flags)


def test_credentials_are_redacted_and_not_printed():
    token = 'sk-' + 'aB123456' * 5
    findings = inspect_file('config.py', token.encode())
    assert findings and token not in repr(findings)


def test_private_hosts_rejected_but_security_fixtures_allowed():
    host = '.'.join(('10', '23', '45', '67'))
    assert inspect_file('app/example.py', host.encode())
    assert not inspect_file('tests/test_ssrf.py', host.encode())
    assert not inspect_file('config.py', b'http://127.0.0.1:8000')


def test_environment_is_unique_private_and_never_overwritten(tmp_path):
    (tmp_path / '.env.example').write_text('SECRET_KEY=\nPOSTGRES_PASSWORD=\n')
    target = initialize(tmp_path)
    before = target.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert b'SECRET_KEY=\n' not in before
    with pytest.raises(FileExistsError):
        initialize(tmp_path)
    assert target.read_bytes() == before


def test_no_shared_admin_password(monkeypatch):
    monkeypatch.delenv('SKILLFORGE_ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    with pytest.raises(ValueError):
        initial_admin_password()
    monkeypatch.setenv('SKILLFORGE_ADMIN_PASSWORD', 'short')
    with pytest.raises(ValueError):
        initial_admin_password()
    monkeypatch.setenv('SKILLFORGE_ADMIN_PASSWORD', 'a-local-only-test-passphrase')
    assert initial_admin_password() == 'a-local-only-test-passphrase'


def test_node_names_never_infer_deployment_addresses(monkeypatch):
    from app.training import service
    monkeypatch.setattr(service, 'TRAINING_MODEL_TRANSFER_HOST_OVERRIDES', {})
    assert service._training_model_transfer_source_host('training-237') == ''
    assert service._training_model_transfer_source_host('training-primary', 'training.example.com') == 'training.example.com'


def test_continuous_media_creation_requires_explicit_recipe_and_deadline(monkeypatch, tmp_path):
    from app.config import settings
    from app.media import continuous_fill
    monkeypatch.setattr(settings, 'MEDIA_RECIPE_FILE', '')
    assert continuous_fill.create_window_status()['phase'] == 'disabled'
    path = tmp_path / 'recipes.json'
    path.write_text(json.dumps({'project_id': 'example-project'}))
    monkeypatch.setattr(settings, 'MEDIA_RECIPE_FILE', str(path))
    with pytest.raises(ValueError, match='stop_create_at'):
        continuous_fill.create_window_status()
    path.write_text(json.dumps({'project_id': 'example-project', 'stop_create_at': '2030-01-01T00:00:00Z'}))
    assert continuous_fill.create_window_status(datetime(2030, 1, 1, 7, 59))['open']
    assert not continuous_fill.create_window_status(datetime(2030, 1, 1, 8, 0))['open']


def test_reviewed_jpeg_screenshots_have_a_scoped_binary_exception():
    jpeg = b'\xff\xd8\xff\xe0\x00synthetic-jpeg-header'
    assert not inspect_file('docs/screenshots/example.jpg', jpeg)
    assert inspect_file('private/example.jpg', jpeg)
    assert inspect_file('docs/screenshots/example.jpg', b'not-an-image\x00')
