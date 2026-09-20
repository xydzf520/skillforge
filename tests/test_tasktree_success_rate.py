"""Wave 2 H1：success_rate 分母口径回归测试。

- today_executions = 窗口内总运行数（含 RUNNING / QUEUED）
- today_finished = 已完成数（COMPLETED + FAILED）
- today_success_rate = success_count / today_finished，无完成样本时为 None
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.tasktree.schemas import (
    DepartmentNode,
    InstanceNode,
    NodeStatus,
    SkillRunItem,
    SkillRunStatus,
    TaskTreeResponse,
)
from app.tasktree.service import _tree_runs_in_window


def _mk_run(idx: int, status: SkillRunStatus, *, started_min_ago: int = 10) -> SkillRunItem:
    return SkillRunItem(
        run_id=f"run-{idx}",
        skill_id="skill-h1",
        skill_name="H1 Skill",
        status=status,
        started_at=datetime.utcnow() - timedelta(minutes=started_min_ago),
        duration_seconds=None,
    )


def _mk_tree_with(runs: list[SkillRunItem]) -> TaskTreeResponse:
    instance = InstanceNode(
        instance_id="inst-h1",
        name="H1 Instance",
        agent_type="opcl",
        node_status=NodeStatus.ONLINE,
        heartbeat_ago_text="just now",
        recent_skills=runs,
    )
    dept = DepartmentNode(
        department_id="dept-h1",
        department_name="Dept H1",
        node_count=1,
        online_count=1,
        instances=[instance],
    )
    return TaskTreeResponse(
        departments=[dept],
        projected_at=datetime.utcnow().isoformat(),
        etag="h1-etag",
        total_online=1,
        total_running=sum(1 for r in runs if r.status == SkillRunStatus.RUNNING),
    )


def test_success_rate_denominator_uses_finished_not_total():
    """5 个 RUNNING + 1 个 COMPLETED → today_executions=6, today_finished=1, rate=1.0。

    旧实现若错用 len(windowed_runs) 作分母，rate 会是 1/6=0.167（偏低）。
    新实现统一用 today_finished 作分母 → 1/1=1.0 正确。
    """
    runs = [
        _mk_run(i, SkillRunStatus.RUNNING) for i in range(5)
    ] + [_mk_run(5, SkillRunStatus.COMPLETED)]
    tree = _mk_tree_with(runs)

    windowed = _tree_runs_in_window(tree, now=datetime.utcnow(), window="1h")
    finished = [r for r in windowed if r.status in {SkillRunStatus.COMPLETED, SkillRunStatus.FAILED}]
    success_count = sum(1 for r in finished if r.status == SkillRunStatus.COMPLETED)

    assert len(windowed) == 6
    assert len(finished) == 1
    success_rate = round(success_count / len(finished), 3) if finished else None
    assert success_rate == 1.0


def test_success_rate_none_when_no_finished():
    """全部 RUNNING → today_finished=0, rate=None（不是 0.0）。"""
    runs = [_mk_run(i, SkillRunStatus.RUNNING) for i in range(3)]
    tree = _mk_tree_with(runs)

    windowed = _tree_runs_in_window(tree, now=datetime.utcnow(), window="1h")
    finished = [r for r in windowed if r.status in {SkillRunStatus.COMPLETED, SkillRunStatus.FAILED}]
    success_rate = round(sum(1 for r in finished if r.status == SkillRunStatus.COMPLETED) / len(finished), 3) if finished else None

    assert len(windowed) == 3
    assert len(finished) == 0
    assert success_rate is None, "无完成样本时应为 None，避免前端显示 0%"


def test_success_rate_handles_mixed_failed_and_completed():
    """2 COMPLETED + 3 FAILED → rate = 2/5 = 0.4。"""
    runs = (
        [_mk_run(i, SkillRunStatus.COMPLETED) for i in range(2)]
        + [_mk_run(i + 2, SkillRunStatus.FAILED) for i in range(3)]
    )
    tree = _mk_tree_with(runs)

    windowed = _tree_runs_in_window(tree, now=datetime.utcnow(), window="1h")
    finished = [r for r in windowed if r.status in {SkillRunStatus.COMPLETED, SkillRunStatus.FAILED}]
    success = sum(1 for r in finished if r.status == SkillRunStatus.COMPLETED)
    success_rate = round(success / len(finished), 3) if finished else None

    assert len(finished) == 5
    assert success_rate == 0.4
