"""静态业务不变量测试。

这些测试不是替代业务测试，而是防止后续 AI/人工把关键边界改回旧语义。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.execution import scheduler
from app.tasktree.schedule_query import SCHEDULE_TRIGGER_TYPES


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_project_git_does_not_track_skill_worktree() -> None:
    gitignore = _read(".gitignore").splitlines()

    assert "skills-repo/" in {line.strip() for line in gitignore}


def test_node_schedule_page_counts_only_node_schedule_runs() -> None:
    assert SCHEDULE_TRIGGER_TYPES == ("node_scheduler", "scheduler:fallback")


def test_scheduler_prefers_node_schedule_before_center_execution() -> None:
    source = inspect.getsource(scheduler._execute_skill_job)

    assert "node_schedule is not None" in source
    assert "跳过集中执行" in source
    assert 'triggered_by="scheduler"' in source


def test_watchdog_remote_fallback_precedes_center_fallback() -> None:
    source = inspect.getsource(scheduler._job_schedule_watchdog)

    assert "_trigger_remote_schedule_fallback" in source
    assert "allow_center_fallback" in source
    assert source.index("_trigger_remote_schedule_fallback") < source.index("await _execute_skill_job")


def test_schedule_ui_does_not_call_enabled_config_running() -> None:
    source = _read("web/src/pages/tasktree/components/ScheduleView.vue")

    assert "if (s === 'active') return '已启用'" in source
    assert "if (s === 'stopped') return '已停止'" in source
    assert "if (s === 'active') return '运行中'" not in source


def test_platform_labels_use_standard_case() -> None:
    paths = [
        "web/src/pages/tasktree/TaskTree.vue",
        "web/src/pages/tasktree/TaskTreeDetailDrawer.vue",
        "web/src/pages/tasktree/components/NodeDetailView.vue",
        "web/src/pages/tasktree/TaskTreeInstanceCard.vue",
        "web/src/pages/admin/AdminAgentDeviceDetail.vue",
    ]

    for path in paths:
        source = _read(path)
        assert "platform === 'linux') return 'Linux'" in source
        assert "platform === 'darwin') return 'macOS'" in source
        assert "platform === 'win32' || platform === 'windows') return 'Windows'" in source
