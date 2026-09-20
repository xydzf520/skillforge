import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import AppError
from app.workbench.models import SkillStudioDraft
from app.workbench.review_context_service import (
    finalize_skill_creation,
    get_creation_context,
    get_my_active_draft,
    preview_creation_name,
)
from app.workbench.task_contract import merge_review_state


def _build_contract() -> dict:
    return {
        "goal": "每天生成业务日报并形成闭环",
        "trigger": {"type": "manual", "expression": "", "description": "手动触发"},
        "input": [
            {"name": "业务数据", "type": "json", "source": "user", "required": True},
        ],
        "output": {
            "adapter": "dingtalk_card",
            "recipient": "业务群",
            "schema": {
                "title": "日报标题",
                "items": "核心明细列表",
            },
        },
        "output_schema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["title", "items", "reports"],
            "properties": {
                "title": {"type": "string"},
                "items": {"type": "array", "items": {}},
                "reports": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["channel", "title", "summary", "recipients"],
                        "properties": {
                            "channel": {"type": "string"},
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "content_markdown": {"type": "string"},
                            "recipients": {"type": "array", "items": {"type": "string"}},
                            "payload": {"type": "object"},
                        },
                    },
                },
            },
        },
        "permissions": [
            {"action": "send_message", "target": "钉钉:业务群", "reversible": False},
        ],
        "risks": {"level": "R2", "department": "AI小组"},
        "test_cases": [
            {"name": "case-1", "input": {"scenario": "one"}, "expected_keywords": ["日报"]},
            {"name": "case-2", "input": {"scenario": "two"}, "expected_keywords": ["日报"]},
            {"name": "case-3", "input": {"scenario": "three"}, "expected_keywords": ["日报"]},
        ],
    }


def _approved_review_state(contract: dict) -> dict:
    state = merge_review_state(contract, {})
    for item in state.values():
        if item.get("required"):
            item["approved"] = True
            item["decision"] = "approved"
    return state


def _skill_md_with_wrong_output_block() -> str:
    return """---
name: finalize-test
department: AI小组
trigger_type: manual
risk_level: R2
description: 发布前复验测试
---

## 目的
验证 finalize 会复跑 verify_schema。

## 输出定义
- 错字段: 旧说明

## 测试用例
1. **case-1**: 基本场景
"""


def _intent_md() -> str:
    return """## 原始需求
测试

## 任务理解
测试

## 输入依赖
测试

## 所需权限
测试

## 失败策略
测试

## 回归用例摘要
测试
"""


def _policy_yaml() -> str:
    return """version: 1
trigger:
  type: manual
output:
  adapter: dingtalk_card
"""


def _main_py(valid: bool = True) -> str:
    if valid:
        return (
            "import json, sys\n\n"
            "def main(payload: dict) -> dict:\n"
            "    return {\n"
            "        'title': '日报',\n"
            "        'items': [payload.get('date', '2026-04-15')],\n"
            "        'reports': [{'channel': 'dingtalk_card', 'title': '日报', 'summary': '日报摘要', 'recipients': ['业务群']}],\n"
            "    }\n\n"
            "if __name__ == '__main__':\n"
            "    payload = json.loads(sys.stdin.read())\n"
            "    print(json.dumps(main(payload), ensure_ascii=False))\n"
        )
    return (
        "import json, sys\n\n"
        "def main(payload: dict) -> dict:\n"
        "    return {\n"
        "        'title': '日报'\n"
        "    }\n\n"
        "if __name__ == '__main__':\n"
        "    payload = json.loads(sys.stdin.read())\n"
        "    print(json.dumps(main(payload), ensure_ascii=False))\n"
    )


def _test_main_py() -> str:
    return (
        "from main import main\n\n"
        "def test_main_basic():\n"
        "    out = main({'date': '2026-04-15'})\n"
        "    assert 'title' in out\n"
    )


async def _insert_ready_draft(
    draft_id: str,
    *,
    valid_main: bool,
    contract: dict | None = None,
    skill_md: str | None = None,
) -> None:
    from app.database import async_session_factory

    contract = contract or _build_contract()
    async with async_session_factory() as session:
        session.add(
            SkillStudioDraft(
                id=draft_id,
                skill_id=None,
                branch="main",
                user_id="admin",
                source_message="生成日报",
                intent_md=_intent_md(),
                skill_md=skill_md or _skill_md_with_wrong_output_block(),
                policy_yaml=_policy_yaml(),
                contract_json=contract,
                review_state_json=_approved_review_state(contract),
                generation_status="ready",
                extra_files={
                    "scripts/main.py": _main_py(valid=valid_main),
                    "tests/test_main.py": _test_main_py(),
                    "fixtures/sample_input.json": json.dumps({"date": "2026-04-15"}, ensure_ascii=False),
                },
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_finalize_skill_creation_rechecks_schema_and_blocks_drift(client, monkeypatch):
    await _insert_ready_draft("draft-finalize-drift", valid_main=False)

    create_mock = AsyncMock(return_value={"skill_id": "skill-x", "git_commit": "abc123", "quality_score": 88})
    monkeypatch.setattr("app.skills.service.create_skill_from_files", create_mock)
    from app.common.audit import audit
    monkeypatch.setattr(audit, "log", AsyncMock())

    with pytest.raises(AppError) as exc:
        await finalize_skill_creation(draft_id="draft-finalize-drift", user_id="admin")

    assert exc.value.code == "CONTRACT_DRIFT"
    assert any("items" in e for e in exc.value.detail.get("schema_errors", []))
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_finalize_skill_creation_normalizes_skill_md_from_output_schema(client, monkeypatch):
    await _insert_ready_draft("draft-finalize-pass", valid_main=True)

    create_mock = AsyncMock(return_value={"skill_id": "skill-y", "git_commit": "def456", "quality_score": 91})
    monkeypatch.setattr("app.skills.service.create_skill_from_files", create_mock)
    from app.common.audit import audit
    monkeypatch.setattr(audit, "log", AsyncMock())

    result = await finalize_skill_creation(draft_id="draft-finalize-pass", user_id="admin")

    assert result["skill_id"] == "skill-y"
    assert await get_my_active_draft(user_id="admin") is None
    assert create_mock.await_count == 1
    files = create_mock.await_args.kwargs["files"]
    assert "- title: 日报标题" in files["SKILL.md"]
    assert "- items: 核心明细列表" in files["SKILL.md"]
    assert "错字段" not in files["SKILL.md"]


@pytest.mark.asyncio
async def test_finalize_skill_creation_uses_tasktree_department_and_skill_md_name(client, monkeypatch):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    contract = _build_contract()
    contract["risks"]["department"] = "未指定"
    skill_md = """---
name: 天猫链接下滑分析
department: 未指定
trigger_type: manual
risk_level: R2
description: 发布部门兜底测试
---

## 目的
验证 finalize 从任务树归属补齐部门。

## 输出定义
- 错字段: 旧说明

## 测试用例
1. **case-1**: 基本场景
"""

    async with async_session_factory() as session:
        session.add(User(id="admin", username="admin", name="管理员", role="admin", department="兜底部门"))
        root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
        session.add(OrgUnit(id=root_id, name="总经办", type="department", path=f"/{root_id}"))
        session.add(OrgUnit(id="dept-ec", name="EC", type="department", parent_id=root_id, path=f"/{root_id}/dept-ec"))
        session.add(OrgUnit(id="dept-ec-ops", name="EC运营组", type="department", parent_id="dept-ec", path=f"/{root_id}/dept-ec/dept-ec-ops"))
        session.add(UserOrgMembership(user_id="admin", org_unit_id="dept-ec-ops", membership_type="primary"))
        await session.commit()

    await _insert_ready_draft("draft-finalize-tasktree-dept", valid_main=True, contract=contract, skill_md=skill_md)

    create_mock = AsyncMock(return_value={"skill_id": "skill-tasktree", "git_commit": "abc789", "quality_score": 92})
    monkeypatch.setattr("app.skills.service.create_skill_from_files", create_mock)
    from app.common.audit import audit
    monkeypatch.setattr(audit, "log", AsyncMock())

    result = await finalize_skill_creation(draft_id="draft-finalize-tasktree-dept", user_id="admin")

    assert result["skill_id"] == "skill-tasktree"
    kwargs = create_mock.await_args.kwargs
    assert kwargs["name"] == "天猫链接下滑分析"
    assert kwargs["department"] == "EC"


@pytest.mark.asyncio
async def test_creation_context_uses_backend_org_membership(client):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    async with async_session_factory() as session:
        session.add(User(id="owner", username="owner", name="Owner", role="aibp", department="兜底部门"))
        root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
        session.add(OrgUnit(id=root_id, name="总部", type="department", path=f"/{root_id}"))
        session.add(OrgUnit(id="dept-ec", name="EC", type="department", parent_id=root_id, path=f"/{root_id}/dept-ec"))
        session.add(OrgUnit(id="dept-ec-ops", name="EC运营组", type="department", parent_id="dept-ec", path=f"/{root_id}/dept-ec/dept-ec-ops"))
        session.add(UserOrgMembership(user_id="owner", org_unit_id="dept-ec-ops", membership_type="primary"))
        await session.commit()

    user = User(id="owner", username="owner", name="Owner", role="aibp", department="兜底部门")
    context = await get_creation_context(user=user)

    assert context["default_department"] == "EC"
    assert context["allowed_departments"][0] == "EC"
    assert context["department_source"] == "org_membership"
    assert context["naming_policy"]["mode"] == "gen_from_name"

    preview = preview_creation_name(name="天猫链接下滑分析", department="EC")
    assert preview["name"] == "天猫链接下滑分析"
    assert preview["department"] == "EC"
    assert preview["skill_id"].startswith("tian-mao-lian-jie-xia-hua-fen-xi-")


@pytest.mark.asyncio
async def test_finalize_skill_creation_accepts_confirm_overrides(client, monkeypatch):
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    async with async_session_factory() as session:
        session.add(User(id="admin", username="admin", name="管理员", role="admin", department="AI小组", can_view_all=True))
        root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
        session.add(OrgUnit(id=root_id, name="总部", type="department", path=f"/{root_id}"))
        session.add(OrgUnit(id="dept-ec", name="EC", type="department", parent_id=root_id, path=f"/{root_id}/dept-ec"))
        session.add(UserOrgMembership(user_id="admin", org_unit_id="dept-ec", membership_type="primary"))
        await session.commit()

    await _insert_ready_draft("draft-finalize-override", valid_main=True)

    create_mock = AsyncMock(return_value={"skill_id": "custom-skill-01", "git_commit": "ovr123", "quality_score": 93})
    monkeypatch.setattr("app.skills.service.create_skill_from_files", create_mock)
    from app.common.audit import audit
    monkeypatch.setattr(audit, "log", AsyncMock())

    result = await finalize_skill_creation(
        draft_id="draft-finalize-override",
        user_id="admin",
        override={
            "name": "天猫链接下滑分析",
            "department": "EC",
            "risk_level": "R3",
            "skill_id": "custom-skill-01",
        },
    )

    assert result["skill_id"] == "custom-skill-01"
    kwargs = create_mock.await_args.kwargs
    assert kwargs["skill_id"] == "custom-skill-01"
    assert kwargs["name"] == "天猫链接下滑分析"
    assert kwargs["department"] == "EC"
    assert kwargs["risk_level"] == "R3"
    assert "name: 天猫链接下滑分析" in kwargs["files"]["SKILL.md"]
    assert "department: EC" in kwargs["files"]["SKILL.md"]
    assert "risk_level: R3" in kwargs["files"]["SKILL.md"]


@pytest.mark.asyncio
async def test_get_my_active_draft_returns_preview_from_sample_input(client):
    await _insert_ready_draft("draft-preview-ready", valid_main=True)

    result = await get_my_active_draft(user_id="admin")

    assert result is not None
    assert result["draft_id"] == "draft-preview-ready"
    assert result["preview"]["fixture_used"] == {"date": "2026-04-15"}
    assert result["preview"]["success"] is True
    assert result["preview"]["sandbox_counts"] == {"todos": 0, "reports": 1}
    assert result["preview"]["sandbox_output"] == {
        "todos": [],
        "reports": [{"channel": "dingtalk_card", "title": "日报", "summary": "日报摘要", "recipients": ["业务群"]}],
    }
    assert "📢 将推送 **1 份报告**" in result["preview"]["sandbox_summary"]


@pytest.mark.asyncio
async def test_get_my_active_draft_ignores_stale_ready_draft(client):
    await _insert_ready_draft("draft-preview-stale", valid_main=True)

    from app.database import async_session_factory

    async with async_session_factory() as session:
        draft = await session.get(SkillStudioDraft, "draft-preview-stale")
        draft.updated_at = datetime.utcnow() - timedelta(minutes=10)
        await session.commit()

    result = await get_my_active_draft(user_id="admin")

    assert result is None

    async with async_session_factory() as session:
        draft = await session.get(SkillStudioDraft, "draft-preview-stale")
        assert draft.generation_status == "cancelled"
