#!/usr/bin/env python3
"""Shared MCP runtime registry for governed collection tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import importlib.util
import sys


@dataclass(frozen=True)
class ToolMeta:
    tool_name: str
    platform: str
    data_scope: str
    endpoint_family: str
    warning_group: str
    requires_shop_id: bool = True
    write: bool = False
    source_id: str = ""
    description: str = ""
    input_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOL_REGISTRY: dict[str, ToolMeta] = {}
_DEFAULT_TOOLS_LOADED = False


def register_tool(meta: ToolMeta) -> ToolMeta:
    if not meta.tool_name or not meta.platform or not meta.data_scope:
        raise ValueError("ToolMeta requires tool_name/platform/data_scope")
    if not meta.endpoint_family or not meta.warning_group:
        raise ValueError("ToolMeta requires endpoint_family/warning_group")
    TOOL_REGISTRY[meta.tool_name] = meta
    return meta


def manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "tools": [meta.to_dict() for meta in sorted(TOOL_REGISTRY.values(), key=lambda item: item.tool_name)],
    }


def find_tool_meta(
    *,
    tool_name: str | None = None,
    platform: str | None = None,
    data_scope: str | None = None,
    endpoint_family: str | None = None,
    warning_group: str | None = None,
) -> ToolMeta | None:
    if tool_name:
        meta = TOOL_REGISTRY.get(tool_name)
        if not meta:
            return None
        if platform and meta.platform != platform:
            return None
        if data_scope and meta.data_scope != data_scope:
            return None
        if endpoint_family and meta.endpoint_family != endpoint_family:
            return None
        if warning_group and meta.warning_group != warning_group:
            return None
        return meta
    for meta in TOOL_REGISTRY.values():
        if platform and meta.platform != platform:
            continue
        if data_scope and meta.data_scope != data_scope:
            continue
        if endpoint_family and meta.endpoint_family != endpoint_family:
            continue
        if warning_group and meta.warning_group != warning_group:
            continue
        return meta
    return None


def _import_script_module(module_name: str) -> None:
    if module_name in sys.modules:
        return
    scripts_dir = Path(__file__).resolve().parent
    path = scripts_dir / f"{module_name}.py"
    if not path.exists():
        return
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def ensure_default_tools_registered() -> None:
    global _DEFAULT_TOOLS_LOADED
    if _DEFAULT_TOOLS_LOADED:
        return
    _DEFAULT_TOOLS_LOADED = True
    _import_script_module("tmall_mcp_server")


if __name__ == "__main__":
    import json

    ensure_default_tools_registered()
    print(json.dumps(manifest(), ensure_ascii=False, indent=2))
