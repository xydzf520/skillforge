"""A Bridge upgrade refreshes its tray icon without touching an unchanged cache."""
import ast
import base64
from pathlib import Path

import pytest


def _icon_writer(state_dir: Path):
    # Extract this pure filesystem helper; do not import the daemon or start services.
    source = Path(__file__).resolve().parents[1] / "bridge/skillforgebridge.py"
    tree = ast.parse(source.read_text())
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_write_icon_to_cache")
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ICON_PNG_B64" for t in n.targets))
    namespace = {"Path": Path, "base64": base64, "STATE_DIR": state_dir, "ICON_CACHE_PATH": state_dir / "icon.png"}
    exec(compile(ast.Module(body=[assignment, writer], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_write_icon_to_cache"], base64.b64decode(namespace["ICON_PNG_B64"])


@pytest.mark.parametrize("old_icon", [None, b"previous-version-icon"])
def test_brand_icon_created_or_refreshed(tmp_path, old_icon):
    state = tmp_path / "node-state"
    if old_icon is not None:
        state.mkdir()
        (state / "icon.png").write_bytes(old_icon)
    write_icon, expected = _icon_writer(state)
    output = write_icon()
    assert output.read_bytes() == expected
    assert expected.startswith(b"\x89PNG\r\n\x1a\n")


def test_current_icon_is_not_rewritten(tmp_path):
    write_icon, expected = _icon_writer(tmp_path)
    output = write_icon()
    stamp = output.stat().st_mtime_ns
    assert write_icon().read_bytes() == expected
    assert output.stat().st_mtime_ns == stamp
