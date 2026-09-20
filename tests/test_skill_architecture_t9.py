"""T9 分层收敛回归。"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(relative_path: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_methodology_compat_alias_points_to_core_module():
    core_mod = importlib.import_module("app.skills.core.methodology")
    legacy_mod = importlib.import_module("app.skills.intelligence.methodology")

    assert legacy_mod is core_mod
    assert legacy_mod.ENHANCED_SYSTEM_PROMPT == core_mod.ENHANCED_SYSTEM_PROMPT


def test_param_index_compat_aliases_share_core_module():
    core_mod = importlib.import_module("app.skills.core.param_index")
    legacy_mod = importlib.import_module("app.skills.intelligence.param_index")
    shim_mod = importlib.import_module("app.skills.param_index")

    assert legacy_mod is core_mod
    assert shim_mod is core_mod
    assert hasattr(core_mod, "find_param_usages")


def test_t9_import_boundaries_are_guarded():
    lifecycle_imports = _imported_modules("app/skills/lifecycle/service.py")
    quality_imports = _imported_modules("app/skills/tooling/service_quality.py")
    runtime_imports = _imported_modules("app/skills/router_runtime.py")
    workbench_imports = _imported_modules("app/workbench/router.py")
    chat_imports = _imported_modules("app/workbench/router_chat.py")

    assert "app.skills.core.methodology" in lifecycle_imports
    assert "app.skills.intelligence.methodology" not in lifecycle_imports

    assert "app.skills.core.param_index" in quality_imports
    assert "app.skills.intelligence.param_index" not in quality_imports

    assert "app.skills.lifecycle.dependency_graph" in runtime_imports
    assert "app.skills" not in runtime_imports

    assert "app.workbench.router_support" in workbench_imports
    assert "app.skills.core.service_shared" not in workbench_imports

    assert "app.workbench.router_support" in chat_imports
    assert "app.skills.core.service_shared" not in chat_imports
