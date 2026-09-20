"""沙箱子进程入口（v7 G1）。

这个文件由 executor.py 通过 subprocess 拉起，运行在被资源限制 / 环境剥离的隔离上下文中。

协议：
- stdin: JSON {"script_path": "...", "fixture": {...}, "contract": {...}, "dry_run": true}
- stdout: JSON {"success": bool, "output": dict, "error": str | null, "duration_ms": int}

用法（被 executor.py 调用，不手动跑）：
    python -m app.sandbox.runner < payload.json
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import traceback
from importlib import util as import_util
from pathlib import Path


# 默认资源限制
DEFAULT_CPU_SECONDS = 10
DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MB
DEFAULT_FILE_DESCRIPTORS = 64


def _apply_resource_limits() -> None:
    """对当前子进程设置 rlimit。"""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_CPU_SECONDS, DEFAULT_CPU_SECONDS + 2))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (DEFAULT_MEMORY_BYTES, DEFAULT_MEMORY_BYTES))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (DEFAULT_FILE_DESCRIPTORS, DEFAULT_FILE_DESCRIPTORS))
    except (ValueError, OSError):
        pass


def _block_network() -> None:
    """阻止子进程发起网络连接（非 root 方案：monkey-patch socket）。

    原理：替换 socket.socket 构造函数，使任何网络 connect/bind 抛 PermissionError。
    比 unshare(CLONE_NEWNET) 不需要 CAP_SYS_ADMIN，适用于普通用户进程。
    """
    import socket as _socket

    _orig_socket = _socket.socket

    class _BlockedSocket(_orig_socket):
        def connect(self, *args, **kwargs):
            raise PermissionError("沙箱环境禁止网络访问")

        def connect_ex(self, *args, **kwargs):
            raise PermissionError("沙箱环境禁止网络访问")

        def bind(self, *args, **kwargs):
            raise PermissionError("沙箱环境禁止网络绑定")

    _socket.socket = _BlockedSocket


def _import_user_script(script_path: str):
    """从绝对路径动态加载 main.py 模块。"""
    path = Path(script_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"sandbox script not found: {script_path}")

    spec = import_util.spec_from_file_location("user_skill_script", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"failed to load script: {script_path}")
    module = import_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_user_entrypoint(module, request: dict):
    """统一兼容 Skill 脚本的两个入口：
    - main(payload: dict) -> dict
    - execute(input_data: dict) -> dict
    """
    if hasattr(module, "main"):
        payload = request.get("payload")
        if payload is None:
            payload = {
                "fixture": request.get("fixture") or {},
                "contract": request.get("contract") or {},
                "dry_run": bool(request.get("dry_run", True)),
            }
        return module.main(payload)

    if hasattr(module, "execute"):
        payload = request.get("payload")
        if payload is None:
            payload = request.get("fixture") or {}
        return module.execute(payload)

    raise AttributeError("script 必须定义 main(payload) 或 execute(input_data)")


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        sys.stdout.write(json.dumps({"success": False, "error": f"invalid stdin json: {exc}"}))
        return 2

    _apply_resource_limits()
    _block_network()

    start = time.monotonic()
    try:
        script_path = request.get("script_path") or ""
        if not script_path:
            raise ValueError("script_path 必填")

        module = _import_user_script(script_path)
        result = _call_user_entrypoint(module, request)
        if not isinstance(result, dict):
            raise TypeError("script 入口函数必须返回 dict")

        duration_ms = int((time.monotonic() - start) * 1000)
        sys.stdout.write(json.dumps({
            "success": True,
            "output": result,
            "error": None,
            "duration_ms": duration_ms,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - start) * 1000)
        sys.stdout.write(json.dumps({
            "success": False,
            "output": None,
            "error": str(exc),
            "traceback": traceback.format_exc()[-2000:],
            "duration_ms": duration_ms,
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
