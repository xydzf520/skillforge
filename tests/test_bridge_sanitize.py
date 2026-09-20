"""M4 & M3 bridge_router 纯单元测试：路径白名单、nonce 上限。"""

from datetime import datetime, timedelta

import pytest


# ===== M4: _sanitize_skills_dir 拒绝敏感子目录 =====


def test_sanitize_allows_normal_path():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("/home/alice/skillforge/skills") == "/home/alice/skillforge/skills"


def test_sanitize_rejects_dotdot():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("/home/alice/../etc/passwd") is None


def test_sanitize_rejects_system_prefixes():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    for bad in ("/etc", "/etc/", "/etc/passwd", "/var/log", "/root/.cache", "/sys/kernel"):
        assert _sanitize_skills_dir(bad) is None


def test_sanitize_rejects_user_ssh_keys():
    """M4 新增：~/.ssh 等敏感子目录应被拒（攻击者利用 install_skill 写 authorized_keys）"""
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("/home/alice/.ssh") is None
    assert _sanitize_skills_dir("/home/alice/.ssh/") is None
    assert _sanitize_skills_dir("/home/alice/.ssh/keys") is None
    assert _sanitize_skills_dir("/Users/alice/.ssh") is None
    assert _sanitize_skills_dir("/Users/alice/.ssh/authorized_keys") is None


def test_sanitize_rejects_aws_gnupg_keychains():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("/home/alice/.aws") is None
    assert _sanitize_skills_dir("/home/alice/.aws/credentials") is None
    assert _sanitize_skills_dir("/home/alice/.gnupg") is None
    assert _sanitize_skills_dir("/Users/alice/Library/Keychains") is None


def test_sanitize_rejects_windows_appdata():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("C:\\Users\\alice\\AppData") is None
    assert _sanitize_skills_dir("C:\\Users\\alice\\AppData\\Local") is None
    assert _sanitize_skills_dir("C:\\Users\\alice\\.ssh") is None


def test_sanitize_requires_absolute_path():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    assert _sanitize_skills_dir("./skills") is None
    assert _sanitize_skills_dir("skills") is None
    assert _sanitize_skills_dir("") is None
    assert _sanitize_skills_dir(None) is None


def test_sanitize_rejects_overlong_path():
    from app.aiclaw.bridge_router import _sanitize_skills_dir
    long = "/home/alice/" + ("x" * 600)
    assert _sanitize_skills_dir(long) is None


# ===== M3: _USED_NONCES 超上限自动淘汰 =====


def test_used_nonces_self_prunes_past_cap():
    """当 nonce 数量超过 _USED_NONCES_MAX 时，_prune_used_nonces 应丢最老的一半。"""
    from app.aiclaw import bridge_router as br

    # 备份原状态
    backup = dict(br._USED_NONCES)
    try:
        br._USED_NONCES.clear()
        # 填充 cap + 100 条，全部未过期
        future = datetime.utcnow() + timedelta(hours=1)
        for i in range(br._USED_NONCES_MAX + 100):
            br._USED_NONCES[f"nonce-{i:06d}"] = future + timedelta(seconds=i)

        br._prune_used_nonces()

        # 应该至少缩到 (_USED_NONCES_MAX + 100) / 2 左右
        assert len(br._USED_NONCES) <= br._USED_NONCES_MAX + 50
        # 最老的那些 key 应已被丢弃（expires 最早）
        assert "nonce-000000" not in br._USED_NONCES
    finally:
        br._USED_NONCES.clear()
        br._USED_NONCES.update(backup)
