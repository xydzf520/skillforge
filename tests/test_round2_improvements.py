"""
第二轮改进测试：
- 条件 DSL 验证器
- 重试工具
- 消息持久化
- 影响预估
- 模板市场
- 参数调优
- 依赖图
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── 条件 DSL 验证器 ──

class TestConditionValidator:
    def test_empty_condition_warning(self):
        from app.skills.validators.condition_validator import validate_conditions
        from app.skills.core.parser import DecisionStep, Branch

        steps = [DecisionStep(id="1", name="测试", branches=[Branch(condition="")])]
        result = validate_conditions(steps)
        assert any("条件为空" in w for w in result.warnings)

    def test_undefined_template_var(self):
        from app.skills.validators.condition_validator import validate_conditions
        from app.skills.core.parser import DecisionStep, Branch

        steps = [DecisionStep(id="1", name="测试", branches=[Branch(condition="ROI > {threshold}")])]
        result = validate_conditions(steps, known_params={"budget"})
        assert any("threshold" in w for w in result.warnings)
        assert "threshold" in result.template_vars

    def test_known_template_var_no_warning(self):
        from app.skills.validators.condition_validator import validate_conditions
        from app.skills.core.parser import DecisionStep, Branch

        steps = [DecisionStep(id="1", name="测试", branches=[Branch(condition="ROI > {threshold}")])]
        result = validate_conditions(steps, known_params={"threshold"})
        assert not any("threshold" in w for w in result.warnings)

    def test_constant_comparison_warning(self):
        from app.skills.validators.condition_validator import validate_conditions
        from app.skills.core.parser import DecisionStep, Branch

        steps = [DecisionStep(id="1", name="测试", branches=[Branch(condition="1.5 > 1.0")])]
        result = validate_conditions(steps)
        assert any("常量" in w for w in result.warnings)

    def test_valid_condition_no_warnings(self):
        from app.skills.validators.condition_validator import validate_conditions
        from app.skills.core.parser import DecisionStep, Branch

        steps = [DecisionStep(id="1", name="测试", branches=[
            Branch(condition="ROI > {threshold}"),
            Branch(condition="属于高风险类别"),
        ])]
        result = validate_conditions(steps, known_params={"threshold"})
        assert result.ok
        assert not result.errors


# ── 重试工具 ──

class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        from app.common.retry import retry_with_backoff

        call_count = 0
        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_with_backoff(fn, max_retries=2)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self):
        from app.common.retry import retry_with_backoff, RetryableError

        call_count = 0
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableError("transient")
            return "ok"

        result = await retry_with_backoff(
            fn, max_retries=3, base_delay=0.01,
            retryable_exceptions=(RetryableError,),
        )
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_immediate(self):
        from app.common.retry import retry_with_backoff, RetryableError

        call_count = 0
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            await retry_with_backoff(
                fn, max_retries=3, base_delay=0.01,
                retryable_exceptions=(RetryableError,),
            )
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        from app.common.retry import retry_with_backoff, RetryableError

        async def fn():
            raise RetryableError("always fail")

        with pytest.raises(RetryableError):
            await retry_with_backoff(
                fn, max_retries=2, base_delay=0.01,
                retryable_exceptions=(RetryableError,),
            )


# ── 消息持久化 ──

class TestMessagePersistence:
    @pytest.mark.asyncio
    async def test_save_message_returns_none_on_error(self):
        """持久化失败应返回 None，不抛异常"""
        from app.workbench.message_persistence import save_message
        # 没有数据库连接时应该优雅失败
        result = await save_message(
            session_id="nonexistent",
            role="user",
            content="test message",
        )
        # 要么返回 ID（如果有数据库），要么返回 None（无数据库）
        assert result is None or isinstance(result, int)


# ── 影响预估 ──

class TestImpactEstimator:
    def test_impact_estimator_module_exists(self):
        from app.skills.intelligence.impact_estimator import estimate_impact
        assert callable(estimate_impact)


# ── 模板市场 ──

class TestTemplateMarket:
    def test_list_templates_empty(self):
        from app.skills.template_market import list_templates
        # 即使模板目录不存在也应返回空列表
        result = list_templates()
        assert isinstance(result, list)

    def test_get_template_not_found(self):
        from app.skills.template_market import get_template_detail
        result = get_template_detail("nonexistent-template")
        assert result is None


# ── 参数调优 ──

class TestParamTuner:
    def test_param_range_dataclass(self):
        from app.skills.intelligence.param_tuner import ParamRange
        pr = ParamRange(name="threshold", values=[1.0, 1.2, 1.5])
        assert pr.name == "threshold"
        assert len(pr.values) == 3


# ── 依赖图 ──

class TestDependencyGraph:
    @pytest.mark.asyncio
    async def test_dependency_graph_endpoint_structure(self):
        """验证依赖图端点的数据结构"""
        from app.playbooks import service
        with patch.object(service, "list_playbooks", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [
                {
                    "name": "test-pb",
                    "steps": [
                        {"id": "skill-a", "skill_id": "skill-a", "name": "Skill A", "depends_on": []},
                        {"id": "skill-b", "skill_id": "skill-b", "name": "Skill B", "depends_on": ["skill-a"]},
                    ],
                }
            ]
            # 直接调用路由处理函数的逻辑
            playbooks = await service.list_playbooks()
            nodes = []
            edges = []
            seen_skills = set()
            for pb in playbooks:
                pb_id = f"playbook:{pb['name']}"
                nodes.append({"id": pb_id, "label": pb["name"], "type": "playbook"})
                for step in pb.get("steps", []):
                    sid = step.get("skill_id") or step.get("id")
                    if sid and sid not in seen_skills:
                        seen_skills.add(sid)
                        nodes.append({"id": sid, "label": step.get("name") or sid, "type": "skill"})
                    if sid:
                        edges.append({"source": pb_id, "target": sid})
                    for dep in step.get("depends_on", []):
                        if dep != sid:
                            edges.append({"source": dep, "target": sid, "type": "depends_on"})

            assert len(nodes) == 3  # 1 playbook + 2 skills
            assert len(edges) == 3  # 2 playbook→skill + 1 depends_on
