"""轻量 prompt 文件加载器（带进程级缓存）。

agent_core 节点统一通过 load_prompt(name) 读取 prompts/*.txt，
避免每次都打开文件，也方便后续切换到 system_config 或 git 版本管理。
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_cache: dict[str, str] = {}
_lock = Lock()


def load_prompt(name: str) -> str:
    """读取 prompts/{name}.txt 内容（含缓存）。

    name 示例：'intent_extract' / 'skill_generate' / 'adapter_bind' / 'progress_translator'
    """
    if name in _cache:
        return _cache[name]
    with _lock:
        if name in _cache:
            return _cache[name]
        path = _PROMPTS_DIR / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"prompt 文件不存在: {path}")
        text = path.read_text(encoding="utf-8")
        _cache[name] = text
        return text


def reload_prompt(name: str | None = None) -> None:
    """清除缓存，强制下次重新读盘（用于热更新或测试）。"""
    with _lock:
        if name is None:
            _cache.clear()
        else:
            _cache.pop(name, None)
