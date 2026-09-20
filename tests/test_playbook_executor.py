"""Playbook执行引擎测试"""

import pytest
from unittest.mock import AsyncMock, patch

from app.playbooks.executor import PlaybookExecutor


# ===== 拓扑排序测试 =====


def test_topological_sort_linear():
    """线性依赖：step_1 → step_2 → step_3"""
    executor = PlaybookExecutor()
    steps = [
        {"id": "step_3", "skill_id": "s3", "depends_on": [{"step_id": "step_2"}]},
        {"id": "step_1", "skill_id": "s1"},
        {"id": "step_2", "skill_id": "s2", "depends_on": [{"step_id": "step_1"}]},
    ]
    sorted_steps = executor._topological_sort(steps)
    ids = [s["id"] for s in sorted_steps]

    assert ids.index("step_1") < ids.index("step_2")
    assert ids.index("step_2") < ids.index("step_3")


def test_topological_sort_no_deps():
    """无依赖的steps，保持原有顺序（入度都为0）"""
    executor = PlaybookExecutor()
    steps = [
        {"id": "a", "skill_id": "sa"},
        {"id": "b", "skill_id": "sb"},
        {"id": "c", "skill_id": "sc"},
    ]
    sorted_steps = executor._topological_sort(steps)
    ids = [s["id"] for s in sorted_steps]

    # 所有都没有依赖，三个都应该在结果中
    assert set(ids) == {"a", "b", "c"}


def test_topological_sort_diamond():
    """菱形依赖：step_1 → step_2, step_3 → step_4"""
    executor = PlaybookExecutor()
    steps = [
        {"id": "step_4", "skill_id": "s4", "depends_on": ["step_2", "step_3"]},
        {"id": "step_2", "skill_id": "s2", "depends_on": ["step_1"]},
        {"id": "step_3", "skill_id": "s3", "depends_on": ["step_1"]},
        {"id": "step_1", "skill_id": "s1"},
    ]
    sorted_steps = executor._topological_sort(steps)
    ids = [s["id"] for s in sorted_steps]

    assert ids.index("step_1") < ids.index("step_2")
    assert ids.index("step_1") < ids.index("step_3")
    assert ids.index("step_2") < ids.index("step_4")
    assert ids.index("step_3") < ids.index("step_4")


def test_topological_sort_cycle_detected():
    """循环依赖应抛出AppError"""
    from app.common.exceptions import AppError

    executor = PlaybookExecutor()
    steps = [
        {"id": "a", "skill_id": "sa", "depends_on": ["b"]},
        {"id": "b", "skill_id": "sb", "depends_on": ["a"]},
    ]
    with pytest.raises(AppError) as exc_info:
        executor._topological_sort(steps)
    assert exc_info.value.code == "PLAYBOOK_INVALID"


def test_topological_sort_string_deps():
    """依赖以字符串列表形式给出"""
    executor = PlaybookExecutor()
    steps = [
        {"id": "step_2", "skill_id": "s2", "depends_on": ["step_1"]},
        {"id": "step_1", "skill_id": "s1"},
    ]
    sorted_steps = executor._topological_sort(steps)
    ids = [s["id"] for s in sorted_steps]
    assert ids == ["step_1", "step_2"]


def test_topological_sort_single_string_dep():
    """依赖以单个字符串形式给出"""
    executor = PlaybookExecutor()
    steps = [
        {"id": "step_2", "skill_id": "s2", "depends_on": "step_1"},
        {"id": "step_1", "skill_id": "s1"},
    ]
    sorted_steps = executor._topological_sort(steps)
    ids = [s["id"] for s in sorted_steps]
    assert ids == ["step_1", "step_2"]


# ===== 条件评估测试 =====


def test_evaluate_condition_equals_true():
    """测试 == true 条件"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"has_red": True}},
    }
    assert executor._evaluate_condition("steps.step_1.output.has_red == true", step_outputs) is True


def test_evaluate_condition_equals_false():
    """测试值为false时 == true 应返回False"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"has_red": False}},
    }
    assert executor._evaluate_condition("steps.step_1.output.has_red == true", step_outputs) is False


def test_evaluate_condition_greater_than():
    """测试 > 运算符"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"red_count": 5}},
    }
    assert executor._evaluate_condition("steps.step_1.output.red_count > 0", step_outputs) is True
    assert executor._evaluate_condition("steps.step_1.output.red_count > 10", step_outputs) is False


def test_evaluate_condition_less_equal():
    """测试 <= 运算符"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"score": 80}},
    }
    assert executor._evaluate_condition("steps.step_1.output.score <= 80", step_outputs) is True
    assert executor._evaluate_condition("steps.step_1.output.score <= 79", step_outputs) is False


def test_evaluate_condition_not_equal():
    """测试 != 运算符"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"status": "error"}},
    }
    assert executor._evaluate_condition("steps.step_1.output.status != completed", step_outputs) is True
    assert executor._evaluate_condition("steps.step_1.output.status != error", step_outputs) is False


def test_evaluate_condition_string_equals():
    """测试字符串相等比较"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"status": "completed"}},
    }
    assert executor._evaluate_condition("steps.step_1.output.status == completed", step_outputs) is True


def test_evaluate_condition_missing_path():
    """路径不存在时，比较应返回False"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {}},
    }
    # None == true → False
    assert executor._evaluate_condition("steps.step_1.output.nonexistent == true", step_outputs) is False


def test_evaluate_condition_nested_path():
    """嵌套路径取值"""
    executor = PlaybookExecutor()
    step_outputs = {
        "step_1": {"output": {"result": {"detail": {"count": 42}}}},
    }
    assert executor._evaluate_condition("steps.step_1.output.result.detail.count > 40", step_outputs) is True


def test_evaluate_condition_malformed():
    """格式不正确的条件表达式默认返回True"""
    executor = PlaybookExecutor()
    assert executor._evaluate_condition("this is not a valid condition", {}) is True


# ===== 依赖检查测试 =====


def test_check_dependencies_no_deps():
    """无依赖的step，始终返回True"""
    executor = PlaybookExecutor()
    step = {"id": "step_1", "skill_id": "s1"}
    met, reason = executor._check_dependencies(step, {}, {})
    assert met is True


def test_check_dependencies_met():
    """依赖已完成且条件满足"""
    executor = PlaybookExecutor()
    step = {
        "id": "step_2",
        "depends_on": [{"step_id": "step_1", "condition": "steps.step_1.output.ok == true"}],
    }
    step_outputs = {"step_1": {"output": {"ok": True}}}
    step_statuses = {"step_1": "completed"}

    met, reason = executor._check_dependencies(step, step_outputs, step_statuses)
    assert met is True


def test_check_dependencies_failed_dep():
    """依赖步骤失败，不满足"""
    executor = PlaybookExecutor()
    step = {"id": "step_2", "depends_on": ["step_1"]}
    step_outputs = {"step_1": {"output": {}}}
    step_statuses = {"step_1": "failed"}

    met, reason = executor._check_dependencies(step, step_outputs, step_statuses)
    assert met is False
    assert "失败" in reason


def test_check_dependencies_condition_not_met():
    """依赖已完成但条件不满足"""
    executor = PlaybookExecutor()
    step = {
        "id": "step_2",
        "depends_on": [{"step_id": "step_1", "condition": "steps.step_1.output.flag == true"}],
    }
    step_outputs = {"step_1": {"output": {"flag": False}}}
    step_statuses = {"step_1": "completed"}

    met, reason = executor._check_dependencies(step, step_outputs, step_statuses)
    assert met is False
    assert "条件不满足" in reason


# ===== 完整执行流程测试（mock） =====


@pytest.mark.asyncio
async def test_run_playbook_success():
    """测试完整Playbook执行流程（mock所有外部依赖）"""
    executor = PlaybookExecutor()

    # mock get_playbook
    mock_playbook = {
        "name": "test-playbook",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a", "timeout": 60},
            {"id": "step_2", "skill_id": "skill-b", "timeout": 60, "depends_on": ["step_1"]},
        ],
    }

    # mock execution_service.execute_skill 返回成功结果
    mock_exec_result = {"run_id": "r-123", "output": {"message": "ok"}}

    with patch("app.playbooks.executor._sf") as mock_sf, \
         patch("app.playbooks.executor.execution_service") as mock_exec_svc, \
         patch("app.playbooks.executor.audit") as mock_audit, \
         patch("app.playbooks.service.get_playbook", new_callable=AsyncMock, return_value=mock_playbook):

        # mock数据库session（不实际写入）
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        # 让select查询返回一个mock的ExecutionRun
        from unittest.mock import MagicMock
        mock_run = MagicMock()
        mock_run.status = "running"
        mock_result_obj = MagicMock()
        mock_result_obj.scalar_one.return_value = mock_run
        mock_session.execute = AsyncMock(return_value=mock_result_obj)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_sf.return_value = MagicMock(return_value=mock_session)
        mock_exec_svc.execute_skill = AsyncMock(return_value=mock_exec_result)
        mock_audit.log = AsyncMock()

        result = await executor.run("test-playbook", params={"key": "val"}, sandbox=True)

    assert result["playbook"] == "test-playbook"
    assert result["status"] == "completed"
    assert result["total_steps"] == 2
    assert result["completed_steps"] == 2
    assert "step_1" in result["steps"]
    assert "step_2" in result["steps"]
    assert mock_exec_svc.execute_skill.call_count == 2


@pytest.mark.asyncio
async def test_run_playbook_step_failure_terminate():
    """step失败 + on_failure=terminate → 整体失败"""
    executor = PlaybookExecutor()

    mock_playbook = {
        "name": "fail-playbook",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a", "timeout": 60, "on_failure": "terminate"},
            {"id": "step_2", "skill_id": "skill-b", "timeout": 60, "depends_on": ["step_1"]},
        ],
    }

    with patch("app.playbooks.executor._sf") as mock_sf, \
         patch("app.playbooks.executor.execution_service") as mock_exec_svc, \
         patch("app.playbooks.executor.audit") as mock_audit, \
         patch("app.playbooks.service.get_playbook", new_callable=AsyncMock, return_value=mock_playbook):

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        from unittest.mock import MagicMock
        mock_run = MagicMock()
        mock_run.status = "running"
        mock_result_obj = MagicMock()
        mock_result_obj.scalar_one.return_value = mock_run
        mock_session.execute = AsyncMock(return_value=mock_result_obj)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_sf.return_value = MagicMock(return_value=mock_session)

        # 第一个step执行失败
        mock_exec_svc.execute_skill = AsyncMock(side_effect=Exception("OpenClaw连接失败"))
        mock_audit.log = AsyncMock()

        result = await executor.run("fail-playbook")

    assert result["status"] == "failed"
    assert result["completed_steps"] == 0
    # step_2不应被执行（因为step_1终止了整个流程）
    assert "step_2" not in result["steps"]


@pytest.mark.asyncio
async def test_run_playbook_step_failure_skip():
    """step失败 + on_failure=skip → 继续执行后续step"""
    executor = PlaybookExecutor()

    mock_playbook = {
        "name": "skip-playbook",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a", "timeout": 60, "on_failure": "skip"},
            {"id": "step_2", "skill_id": "skill-b", "timeout": 60},
        ],
    }

    with patch("app.playbooks.executor._sf") as mock_sf, \
         patch("app.playbooks.executor.execution_service") as mock_exec_svc, \
         patch("app.playbooks.executor.audit") as mock_audit, \
         patch("app.playbooks.service.get_playbook", new_callable=AsyncMock, return_value=mock_playbook):

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        from unittest.mock import MagicMock
        mock_run = MagicMock()
        mock_run.status = "running"
        mock_result_obj = MagicMock()
        mock_result_obj.scalar_one.return_value = mock_run
        mock_session.execute = AsyncMock(return_value=mock_result_obj)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_sf.return_value = MagicMock(return_value=mock_session)

        # step_1失败，step_2成功
        call_count = 0

        async def mock_execute(skill_id, params=None, sandbox=False, triggered_by=""):
            nonlocal call_count
            call_count += 1
            if skill_id == "skill-a":
                raise Exception("skill-a执行出错")
            return {"run_id": "r-ok", "output": {"msg": "success"}}

        mock_exec_svc.execute_skill = AsyncMock(side_effect=mock_execute)
        mock_audit.log = AsyncMock()

        result = await executor.run("skip-playbook")

    # 整体仍为completed（因为skip策略不终止）
    assert result["status"] == "completed"
    # step_1失败不计入completed_steps，step_2成功
    assert result["completed_steps"] == 1
    assert result["steps"]["step_1"]["status"] == "failed"
    assert result["steps"]["step_2"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_playbook_conditional_skip():
    """依赖条件不满足时跳过step"""
    executor = PlaybookExecutor()

    mock_playbook = {
        "name": "conditional-playbook",
        "steps": [
            {"id": "step_1", "skill_id": "skill-a", "timeout": 60},
            {
                "id": "step_2",
                "skill_id": "skill-b",
                "timeout": 60,
                "depends_on": [{"step_id": "step_1", "condition": "steps.step_1.output.need_diagnose == true"}],
            },
        ],
    }

    with patch("app.playbooks.executor._sf") as mock_sf, \
         patch("app.playbooks.executor.execution_service") as mock_exec_svc, \
         patch("app.playbooks.executor.audit") as mock_audit, \
         patch("app.playbooks.service.get_playbook", new_callable=AsyncMock, return_value=mock_playbook):

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        from unittest.mock import MagicMock
        mock_run = MagicMock()
        mock_run.status = "running"
        mock_result_obj = MagicMock()
        mock_result_obj.scalar_one.return_value = mock_run
        mock_session.execute = AsyncMock(return_value=mock_result_obj)
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        mock_sf.return_value = MagicMock(return_value=mock_session)

        # step_1返回 need_diagnose=false，所以step_2应被跳过
        mock_exec_svc.execute_skill = AsyncMock(return_value={
            "run_id": "r-1",
            "output": {"need_diagnose": False},
        })
        mock_audit.log = AsyncMock()

        result = await executor.run("conditional-playbook")

    assert result["status"] == "completed"
    assert result["steps"]["step_1"]["status"] == "completed"
    assert result["steps"]["step_2"]["status"] == "skipped"
    # execute_skill只调了1次（step_2被跳过）
    assert mock_exec_svc.execute_skill.call_count == 1
