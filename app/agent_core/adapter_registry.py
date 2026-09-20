"""Adapter 注册表 + jsonschema 校验（v7 G2）。

集中管理：
- 可用 adapter 列表（dingtalk_card / email / slack / json_webhook / csv_file）
- 每个 adapter 的 schema.json 加载与缓存
- TaskContract.output 字段的 jsonschema 校验
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger


_ADAPTERS_DIR = Path(__file__).resolve().parent.parent / "adapters"
_SUPPORTED_ADAPTERS = ["dingtalk_card", "email", "slack", "json_webhook", "csv_file"]

_schema_cache: dict[str, dict] = {}
_lock = Lock()


def list_adapters() -> list[str]:
    """返回当前支持的 adapter 名称列表。"""
    return list(_SUPPORTED_ADAPTERS)


def load_adapter_schema(name: str) -> dict | None:
    """加载某个 adapter 的 schema.json（带进程级缓存 + Redis adapter:schema 命名空间）。"""
    if name in _schema_cache:
        return _schema_cache[name]
    with _lock:
        if name in _schema_cache:
            return _schema_cache[name]
        schema_path = _ADAPTERS_DIR / name / "schema.json"
        if not schema_path.exists():
            return None
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            _schema_cache[name] = schema
            return schema
        except json.JSONDecodeError as exc:
            logger.warning("[adapter_registry] {} schema.json 解析失败: {}", name, exc)
            return None


async def load_adapter_schema_async(name: str) -> dict | None:
    """async 版本：先读 Redis adapter:schema 命名空间，未命中走文件系统。"""
    from app.common.cache import cached_namespace_get, cached_namespace_set

    cached = await cached_namespace_get("adapter:schema", name)
    if cached is not None:
        return cached
    schema = load_adapter_schema(name)
    if schema is not None:
        await cached_namespace_set("adapter:schema", name, schema)
    return schema


def reset_cache() -> None:
    with _lock:
        _schema_cache.clear()


def validate_output(adapter: str, output_schema: dict[str, Any]) -> dict:
    """用 jsonschema 校验 output_schema 是否符合 adapter 标准。

    返回：
        {"valid": bool, "errors": [str], "missing": [str]}
    """
    result = {"valid": True, "errors": [], "missing": []}

    if adapter not in _SUPPORTED_ADAPTERS:
        result["valid"] = False
        result["errors"].append(f"未知 adapter: {adapter}（可选: {', '.join(_SUPPORTED_ADAPTERS)}）")
        return result

    schema = load_adapter_schema(adapter)
    if schema is None:
        result["valid"] = False
        result["errors"].append(f"adapter {adapter} 的 schema.json 不存在")
        return result

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        result["errors"].append("jsonschema 库未安装，跳过校验")
        return result

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(output_schema or {}), key=lambda e: list(e.absolute_path))
    if errors:
        result["valid"] = False
        for err in errors:
            field = ".".join(str(p) for p in err.absolute_path) or "(root)"
            result["errors"].append(f"{field}: {err.message}")
            if err.validator == "required" and err.validator_value:
                missing_field = err.message.split("'")[1] if "'" in err.message else ""
                if missing_field:
                    result["missing"].append(missing_field)

    return result
