"""[H2] bridge install_skill / remove_skill 的 symlink 防御测试。

攻击模型:
  攻击者预先在 target_dir 放置 `skill_id` 作为 symlink, 指向 /etc/cron.d/ 等系统目录,
  然后让 SkillForge 触发 install_skill 把 cron 任务通过 symlink 写到外部, 实现 RCE。

防御要求:
  - install_skill 拒绝 root 是 symlink, 拒绝任何 fp 是 symlink, 用 O_NOFOLLOW
  - remove_skill 拒绝 root 是 symlink, 阻断 rmtree 跟随 symlink 删外部
  - skill_id 校验 (不含 / 不含 .. 不以 . 开头)
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import pytest

from bridge.skillforgebridge import handle_bridge_op


def _b64(content: bytes | str) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return base64.b64encode(content).decode("ascii")


@pytest.fixture
def temp_skills_dir(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    return skills


# ═══════════════════════════════════════════════════════
# install_skill — 正常路径
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_install_skill_writes_files(temp_skills_dir):
    payload = {
        "skill_id": "EC-test-01",
        "target_dir": str(temp_skills_dir),
        "files": [
            {"path": "SKILL.md", "content_b64": _b64("# 测试 Skill")},
            {"path": "scripts/run.py", "content_b64": _b64("print('hi')")},
        ],
    }
    ok, result, err = await handle_bridge_op("install_skill", payload)
    assert ok, err
    assert result["count"] == 2

    root = temp_skills_dir / "EC-test-01"
    assert (root / "SKILL.md").read_text(encoding="utf-8") == "# 测试 Skill"
    assert (root / "scripts" / "run.py").read_text(encoding="utf-8") == "print('hi')"


@pytest.mark.asyncio
async def test_install_skill_writes_shared_runtime_outside_skill_root(temp_skills_dir):
    payload = {
        "skill_id": "EC-shared-01",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "SKILL.md", "content_b64": _b64("# Skill")}],
        "shared_files": [
            {"path": "scripts/collection_client.py", "content_b64": _b64("# runtime")},
        ],
    }

    ok, result, err = await handle_bridge_op("install_skill", payload)

    assert ok, err
    assert result["files"] == ["SKILL.md"]
    assert result["shared_files"] == ["scripts/collection_client.py"]
    assert not (temp_skills_dir / "EC-shared-01" / "scripts" / "collection_client.py").exists()
    assert (temp_skills_dir / "_shared" / "scripts" / "collection_client.py").read_text(encoding="utf-8") == "# runtime"


# ═══════════════════════════════════════════════════════
# install_skill — symlink 防御
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_install_skill_rejects_root_symlink(temp_skills_dir, tmp_path):
    """攻击者预先把 target_dir/skill_id 做成 symlink 指向 /etc → 拒绝。"""
    # 模拟系统目录 (这里用 tmp_path 下的 sysdir 代替, 不真碰 /etc)
    sysdir = tmp_path / "sysdir"
    sysdir.mkdir()

    malicious_link = temp_skills_dir / "EC-evil-01"
    os.symlink(str(sysdir), str(malicious_link))
    assert malicious_link.is_symlink()

    payload = {
        "skill_id": "EC-evil-01",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "passwd", "content_b64": _b64("root::0:0")}],
    }
    ok, result, err = await handle_bridge_op("install_skill", payload)
    assert not ok
    assert "symlink" in err["message"]
    # sysdir 必须没被写入
    assert not (sysdir / "passwd").exists()


@pytest.mark.asyncio
async def test_install_skill_rejects_file_symlink_inside(temp_skills_dir, tmp_path):
    """子文件预先存在为 symlink → 跳过该文件不跟随写。"""
    # 先正常 install 一次创建 root
    sysfile = tmp_path / "sysfile.txt"
    sysfile.write_text("original")

    root = temp_skills_dir / "EC-test-02"
    root.mkdir()
    # 在 root 下放一个 symlink 文件指向外部 sysfile
    inner_link = root / "SKILL.md"
    os.symlink(str(sysfile), str(inner_link))
    assert inner_link.is_symlink()

    payload = {
        "skill_id": "EC-test-02",
        "target_dir": str(temp_skills_dir),
        "files": [
            {"path": "SKILL.md", "content_b64": _b64("OVERWRITE")},
            {"path": "ok.txt", "content_b64": _b64("ok content")},
        ],
    }
    ok, result, err = await handle_bridge_op("install_skill", payload)
    assert ok, err
    # 外部 sysfile 必须没被覆盖
    assert sysfile.read_text(encoding="utf-8") == "original"
    # 但 ok.txt 是新文件, 应正常写入
    assert (root / "ok.txt").read_text(encoding="utf-8") == "ok content"


@pytest.mark.asyncio
async def test_install_skill_rejects_shared_root_symlink(temp_skills_dir, tmp_path):
    external = tmp_path / "external_shared"
    external.mkdir()
    os.symlink(str(external), str(temp_skills_dir / "_shared"))

    payload = {
        "skill_id": "EC-shared-evil",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "SKILL.md", "content_b64": _b64("# Skill")}],
        "shared_files": [{"path": "scripts/runtime.py", "content_b64": _b64("# bad")}],
    }

    ok, result, err = await handle_bridge_op("install_skill", payload)

    assert not ok
    assert "shared runtime" in err["message"]
    assert not (external / "scripts" / "runtime.py").exists()


@pytest.mark.asyncio
async def test_install_skill_rejects_invalid_skill_id(temp_skills_dir):
    """skill_id 含 / .. . 等危险字符 → 拒绝。"""
    bad_ids = ["../../etc", "/abs/path", ".hidden", "a/b", ""]
    for bad in bad_ids:
        payload = {
            "skill_id": bad,
            "target_dir": str(temp_skills_dir),
            "files": [{"path": "x", "content_b64": _b64("y")}],
        }
        ok, result, err = await handle_bridge_op("install_skill", payload)
        assert not ok, f"{bad!r} 应被拒绝"
        assert "invalid skill_id" in err["message"]


# ═══════════════════════════════════════════════════════
# remove_skill — symlink 防御
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_remove_skill_normal(temp_skills_dir):
    """正常 remove — 先 install 再 remove。"""
    root = temp_skills_dir / "EC-test-03"
    root.mkdir()
    (root / "SKILL.md").write_text("data")

    payload = {"skill_id": "EC-test-03", "target_dir": str(temp_skills_dir)}
    ok, result, err = await handle_bridge_op("remove_skill", payload)
    assert ok, err
    assert not root.exists()


@pytest.mark.asyncio
async def test_remove_skill_rejects_root_symlink(temp_skills_dir, tmp_path):
    """root 是 symlink 指向外部目录 → 拒绝, 不删 symlink target。"""
    external = tmp_path / "important_data"
    external.mkdir()
    (external / "secret.txt").write_text("CRITICAL")

    malicious_link = temp_skills_dir / "EC-evil-02"
    os.symlink(str(external), str(malicious_link))

    payload = {"skill_id": "EC-evil-02", "target_dir": str(temp_skills_dir)}
    ok, result, err = await handle_bridge_op("remove_skill", payload)
    assert not ok
    assert "symlink" in err["message"]
    # 外部目录必须仍存在
    assert (external / "secret.txt").read_text(encoding="utf-8") == "CRITICAL"


@pytest.mark.asyncio
async def test_remove_skill_invalid_id(temp_skills_dir):
    bad_ids = ["../../etc", "/", ".env"]
    for bad in bad_ids:
        payload = {"skill_id": bad, "target_dir": str(temp_skills_dir)}
        ok, result, err = await handle_bridge_op("remove_skill", payload)
        assert not ok
        assert "invalid skill_id" in err["message"]


@pytest.mark.asyncio
async def test_remove_skill_nonexistent(temp_skills_dir):
    """不存在的 skill_id — 应静默成功 (幂等)。"""
    payload = {"skill_id": "EC-not-here", "target_dir": str(temp_skills_dir)}
    ok, result, err = await handle_bridge_op("remove_skill", payload)
    assert ok
    assert result.get("note") == "not exists"


@pytest.mark.asyncio
async def test_run_skill_script_executes_main_json(temp_skills_dir):
    """run_skill_script 应直接执行 scripts/main.py，返回 stdout JSON。"""
    script = (
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'reports': [{'title': payload.get('title')}], 'todos': []}))\n"
    )
    install_payload = {
        "skill_id": "EC-run-01",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "scripts/main.py", "content_b64": _b64(script)}],
    }
    ok, result, err = await handle_bridge_op("install_skill", install_payload)
    assert ok, err

    ok, result, err = await handle_bridge_op(
        "run_skill_script",
        {
            "skill_id": "EC-run-01",
            "target_dir": str(temp_skills_dir),
            "payload": {"title": "actual"},
            "timeout": 5,
        },
    )

    assert ok, err
    assert result["success"] is True
    assert result["run_backend"] == "bridge_script"
    assert result["output"]["reports"][0]["title"] == "actual"


@pytest.mark.asyncio
async def test_run_skill_script_preserves_explicit_run_mode(temp_skills_dir):
    """审核验证等非定时执行必须把控制面 run_mode 透传给脚本。"""
    script = (
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'run_mode': os.environ.get('SKILLFORGE_RUN_MODE')}))\n"
    )
    install_payload = {
        "skill_id": "EC-run-mode",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "scripts/main.py", "content_b64": _b64(script)}],
    }
    ok, result, err = await handle_bridge_op("install_skill", install_payload)
    assert ok, err

    ok, result, err = await handle_bridge_op(
        "run_skill_script",
        {
            "skill_id": "EC-run-mode",
            "target_dir": str(temp_skills_dir),
            "run_id": "review-run-1",
            "run_token": "token-1",
            "run_mode": "sandbox_test",
            "payload": {},
            "timeout": 5,
        },
    )

    assert ok, err
    assert result["output"]["run_mode"] == "sandbox_test"


@pytest.mark.asyncio
async def test_run_skill_script_imports_tracked_sdk_without_shared_dir(temp_skills_dir):
    script = (
        "import json, sys\n"
        "from skillforge_sdk import SkillForge\n"
        "json.load(sys.stdin)\n"
        "sf = SkillForge('EC-run-sdk')\n"
        "print(json.dumps({'reports': [{'title': sf.skill_id}], 'todos': []}))\n"
    )
    install_payload = {
        "skill_id": "EC-run-sdk",
        "target_dir": str(temp_skills_dir),
        "files": [{"path": "scripts/main.py", "content_b64": _b64(script)}],
    }
    ok, result, err = await handle_bridge_op("install_skill", install_payload)
    assert ok, err

    ok, result, err = await handle_bridge_op(
        "run_skill_script",
        {
            "skill_id": "EC-run-sdk",
            "target_dir": str(temp_skills_dir),
            "payload": {},
            "timeout": 5,
        },
    )

    assert ok, err
    assert result["success"] is True
    assert result["output"]["reports"][0]["title"] == "EC-run-sdk"


@pytest.mark.asyncio
async def test_run_skill_script_rejects_path_escape(temp_skills_dir):
    ok, result, err = await handle_bridge_op(
        "run_skill_script",
        {
            "skill_id": "EC-run-01",
            "target_dir": str(temp_skills_dir),
            "script_path": "../outside.py",
        },
    )
    assert not ok
    assert "invalid script_path" in err["message"]
