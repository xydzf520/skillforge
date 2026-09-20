"""C7 收口验证：aiclawcode 子进程 env 必须走白名单。

背景：
    旧实现 `env = os.environ.copy()` 会把 DATABASE_URL /
    DINGTALK_APP_SECRET / SKILLFORGE_SESSION_SECRET / ANTHROPIC_API_KEY
    等敏感环境变量全部透传给 aiclawcode Node 子进程。一旦子进程被
    prompt injection 诱导调 `Bash echo $X` 就直接外泄。

新实现：
    `_build_subprocess_env(env_override)` 仅放行 _SAFE_ENV_KEYS 白名单,
    敏感凭据由调用方通过 env_override 显式传入(并被保留)。

本测试只覆盖纯函数级行为,避免依赖 asyncio.create_subprocess_exec
的真实 Node 进程。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.coding_agent.subprocess_session import (
    _SAFE_ENV_KEYS,
    _build_subprocess_env,
)


# ─────────────────────────────────────────────────────
# _SAFE_ENV_KEYS 白名单本身的边界
# ─────────────────────────────────────────────────────


def test_safe_env_keys_excludes_sensitive_keys():
    """敏感 env 一定不在白名单中。"""
    assert "DATABASE_URL" not in _SAFE_ENV_KEYS
    assert "DINGTALK_APP_SECRET" not in _SAFE_ENV_KEYS
    assert "DINGTALK_APP_KEY" not in _SAFE_ENV_KEYS
    assert "SKILLFORGE_SESSION_SECRET" not in _SAFE_ENV_KEYS
    assert "ANTHROPIC_API_KEY" not in _SAFE_ENV_KEYS  # 必须由 env_override 主动传
    assert "LITELLM_API_KEY" not in _SAFE_ENV_KEYS
    assert "OPENCLAW_TOKEN" not in _SAFE_ENV_KEYS


def test_safe_env_keys_includes_node_basics():
    """运行 Node 必需的基础 env 必须在白名单内。"""
    assert "PATH" in _SAFE_ENV_KEYS
    assert "HOME" in _SAFE_ENV_KEYS
    assert "NODE_OPTIONS" in _SAFE_ENV_KEYS
    assert "LANG" in _SAFE_ENV_KEYS


# ─────────────────────────────────────────────────────
# _build_subprocess_env 行为
# ─────────────────────────────────────────────────────


def _fake_environ() -> dict[str, str]:
    """构造一个含敏感 + 安全混合 env 的伪 os.environ。"""
    return {
        # 安全 — 应保留
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/test",
        "USER": "test",
        "LANG": "en_US.UTF-8",
        "TZ": "Asia/Shanghai",
        # 敏感 — 必须剥离
        "DATABASE_URL": "postgres://user:pass@db/skillforge",
        "DINGTALK_APP_SECRET": "supersecret-dingtalk",
        "DINGTALK_APP_KEY": "ding-app-key",
        "SKILLFORGE_SESSION_SECRET": "session-supersecret",
        "ANTHROPIC_API_KEY": "sk-leak-from-env",  # 必须从 env_override 传,不能从全局 env 漏
        "LITELLM_API_KEY": "litellm-secret",
        # 不在白名单的随机变量 — 也应剥离
        "MY_RANDOM_VAR": "random",
    }


def test_build_env_strips_sensitive_keys():
    """敏感环境变量必须被剥离。"""
    with patch.dict("os.environ", _fake_environ(), clear=True):
        env = _build_subprocess_env({})

    # 敏感键完全不应出现
    assert "DATABASE_URL" not in env, "DATABASE_URL 泄露给子进程!"
    assert "DINGTALK_APP_SECRET" not in env, "DINGTALK_APP_SECRET 泄露给子进程!"
    assert "DINGTALK_APP_KEY" not in env, "DINGTALK_APP_KEY 泄露给子进程!"
    assert "SKILLFORGE_SESSION_SECRET" not in env, "SKILLFORGE_SESSION_SECRET 泄露给子进程!"
    assert "LITELLM_API_KEY" not in env, "LITELLM_API_KEY 泄露给子进程!"
    # 因为 env_override 没传 ANTHROPIC_API_KEY,且白名单也没放它,所以也必须剥离
    assert "ANTHROPIC_API_KEY" not in env, "ANTHROPIC_API_KEY 不应从全局 env 漏出!"
    # 任意非白名单键
    assert "MY_RANDOM_VAR" not in env


def test_build_env_keeps_safe_keys():
    """安全的基础环境变量必须保留。"""
    with patch.dict("os.environ", _fake_environ(), clear=True):
        env = _build_subprocess_env({})

    assert env.get("PATH") == "/usr/bin:/bin"
    assert env.get("HOME") == "/home/test"
    assert env.get("USER") == "test"
    assert env.get("LANG") == "en_US.UTF-8"
    assert env.get("TZ") == "Asia/Shanghai"


def test_build_env_keeps_env_override_credentials():
    """调用方通过 env_override 注入的 ANTHROPIC_API_KEY / BASE_URL 必须保留。"""
    overrides = {
        "ANTHROPIC_API_KEY": "test-key",
        "ANTHROPIC_BASE_URL": "https://litellm.example/v1",
    }
    with patch.dict("os.environ", _fake_environ(), clear=True):
        env = _build_subprocess_env(overrides)

    assert env.get("ANTHROPIC_API_KEY") == "test-key", "env_override 中的 API key 被丢弃了!"
    assert env.get("ANTHROPIC_BASE_URL") == "https://litellm.example/v1"


def test_build_env_override_wins_over_global_env():
    """env_override 同名键必须覆盖 os.environ(白名单内的)。"""
    base = _fake_environ()
    base["NODE_OPTIONS"] = "--inspect"  # 全局 env 给的(白名单内)
    overrides = {"NODE_OPTIONS": "--max-old-space-size=512"}  # caller 想覆盖

    with patch.dict("os.environ", base, clear=True):
        env = _build_subprocess_env(overrides)

    # 注意:_build_subprocess_env 还会追加 --max-old-space-size=1024 如果原来没有
    # caller 的 --max-old-space-size=512 已经包含 "--max-old-space-size",
    # 所以不会再追加;但 NODE_OPTIONS 一定包含 caller 给的值
    assert "--max-old-space-size=512" in env["NODE_OPTIONS"]
    assert "--inspect" not in env["NODE_OPTIONS"], (
        "env_override 应该完全覆盖 os.environ 中同名键"
    )


def test_build_env_injects_node_max_old_space_default():
    """没有 NODE_OPTIONS 时,必须注入默认的 --max-old-space-size。"""
    with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
        env = _build_subprocess_env({})

    assert "NODE_OPTIONS" in env
    assert "--max-old-space-size=1024" in env["NODE_OPTIONS"]


def test_build_env_does_not_mutate_inputs():
    """构造 env 不能修改 os.environ 或 env_override 入参。"""
    overrides = {"ANTHROPIC_API_KEY": "k1"}
    overrides_snapshot = dict(overrides)
    fake_env = _fake_environ()
    fake_env_snapshot = dict(fake_env)

    with patch.dict("os.environ", fake_env, clear=True):
        _ = _build_subprocess_env(overrides)

    assert overrides == overrides_snapshot, "_build_subprocess_env 修改了入参 env_override!"
    # patch.dict 退出会还原 os.environ,这里只验证 overrides
    # fake_env 我们也比一下,确保我们没在 fake_env 上做手脚
    # (不能直接比 os.environ,因为 patch.dict 已经退出)
    assert fake_env == fake_env_snapshot, "_build_subprocess_env 修改了 os.environ 引用!"


def test_build_env_handles_none_override():
    """env_override 为 None 时不应崩(防御性)。"""
    with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
        env = _build_subprocess_env(None)  # type: ignore[arg-type]

    assert "PATH" in env
    assert "DATABASE_URL" not in env
