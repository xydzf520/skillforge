"""4 阶段方法论 prompt 模板单元测试。"""

from __future__ import annotations

import pytest


def test_eleven_lenses_count_is_eleven():
    from app.skills.intelligence.methodology import ELEVEN_LENSES
    assert len(ELEVEN_LENSES) == 11
    for name, question in ELEVEN_LENSES:
        assert name and question
        assert len(name) > 2


def test_phase_1_prompt_includes_all_lenses():
    from app.skills.intelligence.methodology import ELEVEN_LENSES, render_phase_1_prompt
    prompt = render_phase_1_prompt()
    for name, _ in ELEVEN_LENSES:
        assert name in prompt, f"lens {name} 未出现在 prompt"


def test_enhanced_system_prompt_has_all_phases_and_constraints():
    from app.skills.intelligence.methodology import ENHANCED_SYSTEM_PROMPT

    assert "Phase 0 Triage" in ENHANCED_SYSTEM_PROMPT
    assert "USE_EXISTING" in ENHANCED_SYSTEM_PROMPT
    assert "IMPROVE" in ENHANCED_SYSTEM_PROMPT
    assert "CREATE_NEW" in ENHANCED_SYSTEM_PROMPT
    assert "COMPOSE" in ENHANCED_SYSTEM_PROMPT
    assert "11 Lenses" in ENHANCED_SYSTEM_PROMPT
    assert "triage_decision" in ENHANCED_SYSTEM_PROMPT
    assert "description" in ENHANCED_SYSTEM_PROMPT
    assert "尖括号" in ENHANCED_SYSTEM_PROMPT  # 约束 description 不能含 < >
    assert "1024" in ENHANCED_SYSTEM_PROMPT  # description 长度上限


@pytest.mark.asyncio
async def test_generate_skill_uses_methodology_prompt(monkeypatch):
    """验证 _generate_skill_with_llm 真的会注入方法论 prompt 到 LLM 调用。"""
    captured_prompts: list[dict] = []

    async def fake_call_llm(*, system, user, **kwargs):
        captured_prompts.append({"system": system, "user": user})
        return {
            "triage_decision": "CREATE_NEW",
            "triage_reason": "全新业务",
            "description": "测试技能。Use when: 仅用于测试; NOT for: 生产",
            "compatibility": "Requires nothing",
            "body": "# 测试\n\n## 目的\n\n测试。\n\n## 执行步骤\n\n1. 不做任何事\n",
        }

    import app.common.ai as ai_mod
    monkeypatch.setattr(ai_mod, "call_llm", fake_call_llm)

    # 同时 patch service 模块里的 import (因为它在函数内部 import)
    from app.skills import service as service_mod

    # call_llm 是函数内 from app.common.ai import call_llm, monkeypatch ai_mod 就够了
    md = await service_mod._generate_skill_with_llm(
        skill_id="test-methodology-skill",
        name="方法论测试",
        department="EC",
        role="ai_engineer",
        trigger_type="manual",
        trigger_expression="",
        risk_level="R1",
    )

    assert captured_prompts, "call_llm 没被调用"
    sys_prompt = captured_prompts[0]["system"]
    assert "Phase 0 Triage" in sys_prompt
    assert "First Principles" in sys_prompt  # 11 lenses 中的第一个
    assert "Inversion" in sys_prompt
    assert "triage_decision" in sys_prompt

    # 校验生成的 SKILL.md 是合法的
    from app.skills.validators import quick_validate
    qv = quick_validate(md)
    assert qv.ok, f"方法论生成的 md 校验失败: {qv.errors}"


def test_report_analysis_prompt_has_methodology():
    """周报路径 (_REPORT_ANALYSIS_PROMPT) 也注入了 4 阶段方法论。"""
    from app.skills.intelligence.generation_service import _REPORT_ANALYSIS_PROMPT
    from app.skills.intelligence.methodology import ELEVEN_LENSES

    assert "Phase 0 Triage" in _REPORT_ANALYSIS_PROMPT
    assert "USE_EXISTING" in _REPORT_ANALYSIS_PROMPT
    assert "CREATE_NEW" in _REPORT_ANALYSIS_PROMPT
    assert "triage_decision" in _REPORT_ANALYSIS_PROMPT
    assert "constraints" in _REPORT_ANALYSIS_PROMPT
    assert "upstream_downstream" in _REPORT_ANALYSIS_PROMPT
    # 11 视角全部出现
    for lens, _ in ELEVEN_LENSES:
        assert lens in _REPORT_ANALYSIS_PROMPT, f"周报 prompt 缺 lens {lens}"


@pytest.mark.asyncio
async def test_generate_from_report_uses_methodology(monkeypatch):
    """周报生成: LLM 返回 triage_decision 应被记录, 不是 CREATE_NEW 应在 purpose 头加前缀。"""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    # 模拟 LLM 返回带 triage_decision 的 JSON
    llm_json = {
        "triage_decision": "IMPROVE",
        "triage_reason": "已有 sku-monitor Skill 接近, 应该改进它而不是新建",
        "name": "SKU 异常检测",
        "purpose": "检测异常 SKU 并通知",
        "steps": [{"name": "检查", "branches": [{"condition": "差评率>10%", "conclusion": "异常"}]}],
        "params": [{"name": "rate", "default_value": "10", "description": "差评率阈值"}],
        "antipatterns": [
            {"scenario": "促销期误判", "correct_action": "排除促销商品"},
            {"scenario": "数据延迟", "correct_action": "等数据稳定后判断"},
        ],
        "constraints": ["数据库访问权限", "钉钉 API 限频"],
        "upstream_downstream": {"upstream": ["SKU 数据库"], "downstream": ["运营钉钉通知"]},
    }
    mock_resp_payload = {
        "choices": [{"message": {"content": json.dumps(llm_json, ensure_ascii=False)}}]
    }

    with patch("app.skills.generation_service.httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_resp_payload
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_instance

        from app.skills.intelligence.generation_service import generate_from_report
        result = await generate_from_report("本周差评率超 10% 的 SKU 增加", "EC", "运营")

    # 1) triage 字段透传到返回值
    assert result["triage_decision"] == "IMPROVE"
    assert "sku-monitor" in result["triage_reason"]
    # 2) constraints 和 upstream_downstream 透传
    assert result["constraints"] == ["数据库访问权限", "钉钉 API 限频"]
    assert result["upstream_downstream"]["upstream"] == ["SKU 数据库"]
    # 3) skill_md_draft 里 purpose 应被强制加 [Phase 0 建议: IMPROVE] 前缀
    assert "[Phase 0 建议: IMPROVE]" in result["skill_md_draft"]


@pytest.mark.asyncio
async def test_generate_from_report_create_new_no_prefix(monkeypatch):
    """triage=CREATE_NEW 时 purpose 不加任何前缀。"""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    llm_json = {
        "triage_decision": "CREATE_NEW",
        "triage_reason": "全新业务能力",
        "name": "新决策",
        "purpose": "做点新事",
        "steps": [{"name": "step1", "branches": [{"condition": "x", "conclusion": "y"}]}],
        "params": [],
        "antipatterns": [
            {"scenario": "a", "correct_action": "b"},
            {"scenario": "c", "correct_action": "d"},
        ],
        "constraints": ["c1", "c2"],
        "upstream_downstream": {"upstream": [], "downstream": []},
    }
    mock_resp_payload = {
        "choices": [{"message": {"content": json.dumps(llm_json, ensure_ascii=False)}}]
    }

    with patch("app.skills.generation_service.httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_resp_payload
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_instance

        from app.skills.intelligence.generation_service import generate_from_report
        result = await generate_from_report("全新场景", "EC", "运营")

    assert result["triage_decision"] == "CREATE_NEW"
    assert "[Phase 0 建议:" not in result["skill_md_draft"]


@pytest.mark.asyncio
async def test_generate_skill_triage_use_existing_prefixes_description(monkeypatch):
    """LLM 返回 triage=USE_EXISTING 时, description 应被注入提醒前缀。"""
    async def fake_call_llm(*, system, user, **kwargs):
        return {
            "triage_decision": "USE_EXISTING",
            "triage_reason": "已经有相同功能的 sys-monitor",
            "description": "看系统状态。Use when: 查询资源占用",
            "compatibility": "Requires nothing",
            "body": (
                "# 重复功能\n\n"
                "## 目的\n\n这个 Skill 跟已有的 sys-monitor 功能高度重叠, 不建议新建。\n\n"
                "## 执行步骤\n\n1. 调用现有的 sys-monitor\n2. 解析输出\n3. 返回结果\n\n"
                "## 反模式\n\n- 不要绕过 sys-monitor 直接执行 free 命令\n"
            ),
        }

    import app.common.ai as ai_mod
    monkeypatch.setattr(ai_mod, "call_llm", fake_call_llm)

    from app.skills import service as service_mod
    md = await service_mod._generate_skill_with_llm(
        skill_id="test-triage-prefix",
        name="重复功能",
        department="EC",
        role="ai_engineer",
        trigger_type="manual",
        trigger_expression="",
        risk_level="R1",
    )

    # description 行应包含 [Phase 0 建议: USE_EXISTING]
    assert "[Phase 0 建议: USE_EXISTING]" in md
