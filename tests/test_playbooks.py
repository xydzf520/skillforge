"""Playbook管理模块测试（CRUD + 校验，不包括执行引擎）"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


# ===== 列表 =====


@pytest.mark.asyncio
async def test_list_playbooks_empty():
    """测试列出Playbook——目录不存在时返回空列表"""
    with patch("app.playbooks.service.PLAYBOOKS_DIR", Path("/nonexistent/path")):
        from app.playbooks.service import list_playbooks
        result = await list_playbooks()

    assert result == []


@pytest.mark.asyncio
async def test_list_playbooks_with_files(tmp_path):
    """测试列出Playbook——有文件时返回元信息"""
    import yaml

    # 创建测试Playbook文件
    playbook_data = {
        "name": "test-playbook",
        "description": "测试用",
        "department": "电商",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a"},
            {"id": "step_2", "skill_id": "skill-b", "depends_on": ["step_1"]},
        ],
    }
    (tmp_path / "test-playbook.yaml").write_text(
        yaml.dump(playbook_data, allow_unicode=True), encoding="utf-8"
    )

    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path):
        from app.playbooks.service import list_playbooks
        result = await list_playbooks()

    assert len(result) == 1
    assert result[0]["name"] == "test-playbook"
    assert result[0]["steps_count"] == 2
    assert result[0]["department"] == "电商"


# ===== 保存 =====


@pytest.mark.asyncio
async def test_save_playbook(tmp_path):
    """测试保存Playbook——写入YAML文件"""
    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path):
        from app.playbooks.service import save_playbook
        result = await save_playbook("new-playbook", {
            "name": "new-playbook",
            "steps": [
                {"id": "step_1", "skill_id": "skill-a"},
            ],
        })

    assert result["file_name"] == "new-playbook"
    assert result["message"] == "保存成功"
    assert (tmp_path / "new-playbook.yaml").exists()


@pytest.mark.asyncio
async def test_save_playbook_no_steps():
    """测试保存Playbook——缺少steps应报错"""
    from app.common.exceptions import AppError

    with pytest.raises(AppError) as exc_info:
        from app.playbooks.service import save_playbook
        await save_playbook("bad-playbook", {"name": "bad"})

    assert exc_info.value.code == "PLAYBOOK_INVALID"


@pytest.mark.asyncio
async def test_save_playbook_overwrites_yml(tmp_path):
    """测试保存Playbook——如果存在同名.yml应被删除"""
    # 创建旧的.yml文件
    (tmp_path / "dup.yml").write_text("old: data", encoding="utf-8")

    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path):
        from app.playbooks.service import save_playbook
        await save_playbook("dup", {
            "name": "dup",
            "steps": [{"id": "s1", "skill_id": "sa"}],
        })

    assert (tmp_path / "dup.yaml").exists()
    assert not (tmp_path / "dup.yml").exists()


# ===== 获取详情 =====


@pytest.mark.asyncio
async def test_get_playbook(tmp_path):
    """测试获取Playbook详情"""
    import yaml

    data = {
        "name": "detail-test",
        "steps": [{"id": "s1", "skill_id": "sa"}],
        "sla_minutes": 30,
    }
    (tmp_path / "detail-test.yaml").write_text(
        yaml.dump(data, allow_unicode=True), encoding="utf-8"
    )

    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path):
        from app.playbooks.service import get_playbook
        result = await get_playbook("detail-test")

    assert result["file_name"] == "detail-test"
    assert result["name"] == "detail-test"
    assert result["sla_minutes"] == 30
    assert "_meta" in result


@pytest.mark.asyncio
async def test_get_playbook_not_found():
    """测试获取不存在的Playbook——应返回404"""
    from app.common.exceptions import AppError

    with patch("app.playbooks.service.PLAYBOOKS_DIR", Path("/nonexistent")):
        with pytest.raises(AppError) as exc_info:
            from app.playbooks.service import get_playbook
            await get_playbook("not-exist")

    assert exc_info.value.code == "PLAYBOOK_NOT_FOUND"


# ===== 校验——正常 =====


@pytest.mark.asyncio
async def test_validate_playbook_good():
    """测试校验Playbook——正常结构应通过"""
    from app.playbooks.service import validate_playbook

    data = {
        "name": "valid-pb",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a"},
            {"id": "step_2", "skill_id": "skill-b", "depends_on": ["step_1"]},
        ],
    }
    result = await validate_playbook(data)

    assert result["valid"] is True
    assert len(result["errors"]) == 0


# ===== 校验——循环依赖 =====


@pytest.mark.asyncio
async def test_validate_playbook_circular():
    """测试校验Playbook——循环依赖应报错"""
    from app.playbooks.service import validate_playbook

    data = {
        "name": "cycle-pb",
        "steps": [
            {"id": "step_a", "skill_id": "sa", "depends_on": ["step_b"]},
            {"id": "step_b", "skill_id": "sb", "depends_on": ["step_a"]},
        ],
    }
    result = await validate_playbook(data)

    assert result["valid"] is False
    assert any("循环依赖" in e for e in result["errors"])


# ===== 校验——缺少必填字段 =====


@pytest.mark.asyncio
async def test_validate_playbook_missing_fields():
    """测试校验Playbook——step缺少id或skill_id"""
    from app.playbooks.service import validate_playbook

    data = {
        "name": "bad-pb",
        "steps": [
            {"skill_id": "sa"},           # 缺少id
            {"id": "s2"},                  # 缺少skill_id
        ],
    }
    result = await validate_playbook(data)

    assert result["valid"] is False
    assert len(result["errors"]) >= 2


# ===== 校验——引用不存在的步骤 =====


@pytest.mark.asyncio
async def test_validate_playbook_bad_ref():
    """测试校验Playbook——depends_on引用不存在的步骤"""
    from app.playbooks.service import validate_playbook

    data = {
        "name": "bad-ref-pb",
        "steps": [
            {"id": "step_1", "skill_id": "sa"},
            {"id": "step_2", "skill_id": "sb", "depends_on": ["step_nonexistent"]},
        ],
    }
    result = await validate_playbook(data)

    assert result["valid"] is False
    assert any("不存在" in e for e in result["errors"])


# ===== 校验——重复id =====


@pytest.mark.asyncio
async def test_validate_playbook_duplicate_id():
    """测试校验Playbook——步骤id重复"""
    from app.playbooks.service import validate_playbook

    data = {
        "name": "dup-id-pb",
        "steps": [
            {"id": "step_1", "skill_id": "sa"},
            {"id": "step_1", "skill_id": "sb"},  # id重复
        ],
    }
    result = await validate_playbook(data)

    assert result["valid"] is False
    assert any("重复" in e for e in result["errors"])


# ===== 校验——空steps =====


@pytest.mark.asyncio
async def test_validate_playbook_empty_steps():
    """测试校验Playbook——空steps"""
    from app.playbooks.service import validate_playbook

    data = {"name": "empty-pb", "steps": []}
    result = await validate_playbook(data)

    assert result["valid"] is False


@pytest.mark.asyncio
async def test_validate_playbook_no_name():
    """测试校验Playbook——缺少name"""
    from app.playbooks.service import validate_playbook

    data = {"steps": [{"id": "s1", "skill_id": "sa"}]}
    result = await validate_playbook(data)

    assert result["valid"] is False
    assert any("name" in e for e in result["errors"])


# ===== 删除 =====


@pytest.mark.asyncio
async def test_delete_playbook(tmp_path):
    """测试删除Playbook"""
    # 创建文件
    (tmp_path / "to-delete.yaml").write_text("name: to-delete\nsteps: []", encoding="utf-8")

    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path):
        from app.playbooks.service import delete_playbook
        result = await delete_playbook("to-delete")

    assert result["file_name"] == "to-delete"
    assert result["message"] == "删除成功"
    assert not (tmp_path / "to-delete.yaml").exists()


@pytest.mark.asyncio
async def test_delete_playbook_not_found():
    """测试删除不存在的Playbook——应返回404"""
    from app.common.exceptions import AppError

    with patch("app.playbooks.service.PLAYBOOKS_DIR", Path("/nonexistent")):
        with pytest.raises(AppError) as exc_info:
            from app.playbooks.service import delete_playbook
            await delete_playbook("not-exist")

    assert exc_info.value.code == "PLAYBOOK_NOT_FOUND"


# ===== 名称安全校验 =====


def test_sanitize_name_path_traversal():
    """测试Playbook名称——路径穿越应被拒绝"""
    from app.common.exceptions import AppError
    from app.playbooks.service import _sanitize_name

    with pytest.raises(AppError):
        _sanitize_name("../etc/passwd")

    with pytest.raises(AppError):
        _sanitize_name("test/../../secret")


def test_sanitize_name_empty():
    """测试Playbook名称——空名称应被拒绝"""
    from app.common.exceptions import AppError
    from app.playbooks.service import _sanitize_name

    with pytest.raises(AppError):
        _sanitize_name("")

    with pytest.raises(AppError):
        _sanitize_name("   ")


def test_sanitize_name_valid():
    """测试Playbook名称——合法名称应通过"""
    from app.playbooks.service import _sanitize_name

    assert _sanitize_name("晨间检查-playbook") == "晨间检查-playbook"
    assert _sanitize_name("test_01") == "test_01"


# ===== 循环依赖检测（内部函数） =====


def test_detect_cycle_no_cycle():
    """测试循环检测——无循环"""
    from app.playbooks.service import _detect_cycle

    steps = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    assert _detect_cycle(steps) is None


def test_detect_cycle_with_cycle():
    """测试循环检测——有循环"""
    from app.playbooks.service import _detect_cycle

    steps = [
        {"id": "a", "depends_on": ["c"]},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    result = _detect_cycle(steps)
    assert result is not None
    assert "循环依赖" in result


# ===== HTTP端点测试（通过client fixture） =====


@pytest.mark.asyncio
async def test_list_playbooks_endpoint(client):
    """测试GET /api/playbooks/——列表端点"""
    resp = await client.get("/api/playbooks/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_validate_playbook_endpoint(client):
    """测试POST /api/playbooks/validate——校验端点"""
    resp = await client.post("/api/playbooks/validate", json={
        "name": "test-validate",
        "steps": [
            {"id": "s1", "skill_id": "skill-a"},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "valid" in data
    assert data["valid"] is True


@pytest.mark.asyncio
async def test_publish_playbook_persists_review(client, tmp_path):
    """发布Playbook后应真正提交并持久化审核单。"""
    import yaml
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.reviews.models import Review

    playbook = {
        "name": "replenishment-check",
        "description": "补货巡检",
        "department": "供应链中心",
        "steps": [
            {"id": "collect", "skill_id": "skill-forecast"},
            {"id": "recommend", "skill_id": "skill-summary", "depends_on": ["collect"]},
        ],
    }
    (tmp_path / "replenishment-check.yaml").write_text(
        yaml.dump(playbook, allow_unicode=True),
        encoding="utf-8",
    )

    with patch("app.playbooks.service.PLAYBOOKS_DIR", tmp_path), \
         patch("app.reviews.service._git_logs_for_path", return_value=[]), \
         patch("app.reviews.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()
        resp = await client.post("/api/playbooks/replenishment-check/publish")

    assert resp.status_code == 200
    review_id = resp.json()["review_id"]

    async with async_session_factory() as session:
        review = (
            await session.execute(select(Review).where(Review.id == review_id))
        ).scalar_one_or_none()

    assert review is not None
    assert review.skill_id == "playbook:replenishment-check"
    assert review.change_type == "new_skill"
    assert review.status == "pending"
