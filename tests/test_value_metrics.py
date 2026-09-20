from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _row(**kwargs):
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_estimate_value_computes_metrics_and_uses_ai(monkeypatch):
    from app.tasktree import value_service
    from app.skills.core.models import Skill

    skill = Skill(id="EC-ROI-01", name="Skill-EC-ROI-01", department="EC", status="active")
    db = AsyncMock()
    db.get = AsyncMock(return_value=skill)

    now = datetime.utcnow()
    runs = [
        _row(
            run_id="run-1",
            run_status="completed",
            started_at=now - timedelta(minutes=15),
            completed_at=now - timedelta(minutes=10),
            duration_ms=5 * 60 * 1000,
        ),
        _row(
            run_id="run-2",
            run_status="failed",
            started_at=now - timedelta(minutes=40),
            completed_at=now - timedelta(minutes=30),
            duration_ms=10 * 60 * 1000,
        ),
    ]
    decision_logs = [
        _row(user_action="completed", approval_status="approved"),
        _row(user_action="rejected", approval_status="rejected"),
    ]

    monkeypatch.setattr(value_service, "_table_columns", AsyncMock(return_value=set()))
    monkeypatch.setattr(value_service, "_load_skill_runs", AsyncMock(return_value=runs))
    monkeypatch.setattr(value_service, "_load_skill_decision_logs", AsyncMock(return_value=decision_logs))
    monkeypatch.setattr(value_service, "_load_skill_token_cost", AsyncMock(return_value=3.5))
    monkeypatch.setattr(value_service, "_load_manual_baselines", AsyncMock(return_value={}))
    monkeypatch.setattr(value_service, "_load_skill_default_baseline", AsyncMock(return_value=None))

    captured = {}

    async def fake_llm(cache_key, system, user, **kwargs):
        captured["cache_key"] = cache_key
        captured["system"] = system
        captured["user"] = user
        captured["kwargs"] = kwargs
        return "建议继续投入并复制到相似场景。"

    monkeypatch.setattr(value_service, "call_llm_cached", fake_llm)

    result = await value_service.estimate_value(
        db,
        "EC-ROI-01",
        period_days=30,
        hourly_cost_usd=60.0,
        user_id="u1",
        department="EC",
    )

    assert result["skill_id"] == "EC-ROI-01"
    assert result["run_count"] == 2
    assert result["completed_runs"] == 1
    assert result["failed_runs"] == 1
    assert result["saved_hours"] == 0.17
    assert result["estimated_cost_saving"] == 10.0
    assert result["risk_events_prevented"] == 2
    assert result["human_takeover_rate"] == 0.5
    assert result["token_cost_period"] == 3.5
    assert result["recommendation"] == "建议继续投入并复制到相似场景。"
    assert captured["cache_key"].startswith("tasktree:value:EC-ROI-01")
    assert "统计数据" in captured["user"]


@pytest.mark.asyncio
async def test_estimate_value_falls_back_when_ai_unavailable(monkeypatch):
    from app.tasktree import value_service
    from app.skills.core.models import Skill

    skill = Skill(id="EC-ROI-02", name="Skill-EC-ROI-02", department="EC", status="active")
    db = AsyncMock()
    db.get = AsyncMock(return_value=skill)

    now = datetime.utcnow()
    runs = [
        _row(
            run_id="run-3",
            run_status="completed",
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=10),
            duration_ms=10 * 60 * 1000,
        )
    ]

    monkeypatch.setattr(value_service, "_table_columns", AsyncMock(return_value=set()))
    monkeypatch.setattr(value_service, "_load_skill_runs", AsyncMock(return_value=runs))
    monkeypatch.setattr(value_service, "_load_skill_decision_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(value_service, "_load_skill_token_cost", AsyncMock(return_value=0.0))
    monkeypatch.setattr(value_service, "_load_manual_baselines", AsyncMock(return_value={}))
    monkeypatch.setattr(value_service, "_load_skill_default_baseline", AsyncMock(return_value=None))

    async def fake_llm(*args, **kwargs):
        return None

    monkeypatch.setattr(value_service, "call_llm_cached", fake_llm)

    result = await value_service.estimate_value(db, "EC-ROI-02", include_ai=True)

    assert "继续投入" in result["recommendation"] or "收益为正" in result["recommendation"]
    assert result["saved_hours"] >= 0


@pytest.mark.asyncio
async def test_department_value_summary_aggregates_usage_and_skills(monkeypatch):
    from app.tasktree import value_service

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [
                SimpleNamespace(id="EC-ROI-03", name="Skill-EC-ROI-03"),
                SimpleNamespace(id="EC-ROI-04", name="Skill-EC-ROI-04"),
            ]
        )
    )

    async def fake_bulk_load(db_obj, *, skill_ids, since, hourly_cost_usd, fallback_baseline_minutes):
        return {
            "EC-ROI-03": {
                "skill_id": "EC-ROI-03",
                "run_count": 2,
                "completed_runs": 2,
                "failed_runs": 0,
                "decision_count": 4,
                "completed_decisions": 3,
                "human_takeover_rate": 0.1,
                "saved_hours": 2.0,
                "estimated_cost_saving": 120.0,
                "risk_events_prevented": 1,
                "token_cost_period": 3.0,
                "baseline_minutes": 15.0,
                "average_run_minutes": 5.0,
                "recommendation": "",
            },
            "EC-ROI-04": {
                "skill_id": "EC-ROI-04",
                "run_count": 1,
                "completed_runs": 0,
                "failed_runs": 1,
                "decision_count": 2,
                "completed_decisions": 0,
                "human_takeover_rate": 0.4,
                "saved_hours": 0.0,
                "estimated_cost_saving": 0.0,
                "risk_events_prevented": 1,
                "token_cost_period": 1.0,
                "baseline_minutes": 15.0,
                "average_run_minutes": 0.0,
                "recommendation": "",
            },
        }

    monkeypatch.setattr(value_service, "_bulk_load_skill_metrics", fake_bulk_load)
    monkeypatch.setattr(value_service, "_count_decision_logs", AsyncMock(return_value=5))
    monkeypatch.setattr(value_service, "_count_rejected_decisions", AsyncMock(return_value=2))
    monkeypatch.setattr(value_service, "_load_department_token_cost_today", AsyncMock(return_value=8.25))
    monkeypatch.setattr(value_service, "_ai_department_recommendation", AsyncMock(return_value=None))

    summary = await value_service.get_department_value_summary(
        db,
        "EC",
        period_days=30,
        include_ai=False,
    )

    assert summary["department"] == "EC"
    assert summary["skill_count"] == 2
    assert summary["token_cost_today"] == 8.25
    assert summary["saved_hours"] == 2.0
    assert summary["skills"][0]["skill_id"] == "EC-ROI-03"


@pytest.mark.asyncio
async def test_department_value_summary_bulk_path_single_sql(monkeypatch):
    """N+1 回归：10 个 skill 不得多次调用 estimate_value（bulk 路径）。"""
    from app.tasktree import value_service

    db = AsyncMock()
    skill_rows = [SimpleNamespace(id=f"SK-{i}", name=f"Skill-{i}") for i in range(10)]
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: skill_rows))

    bulk_calls = 0
    per_skill_calls = 0

    async def fake_bulk(db_obj, *, skill_ids, since, hourly_cost_usd, fallback_baseline_minutes):
        nonlocal bulk_calls
        bulk_calls += 1
        return {
            sid: {
                "skill_id": sid,
                "run_count": 1,
                "completed_runs": 1,
                "failed_runs": 0,
                "decision_count": 0,
                "completed_decisions": 0,
                "human_takeover_rate": 0.0,
                "saved_hours": 0.1,
                "estimated_cost_saving": 3.0,
                "risk_events_prevented": 0,
                "token_cost_period": 0.1,
                "baseline_minutes": 15.0,
                "average_run_minutes": 5.0,
                "recommendation": "",
            }
            for sid in skill_ids
        }

    async def fake_estimate_value(*args, **kwargs):
        nonlocal per_skill_calls
        per_skill_calls += 1
        return {
            "skill_id": args[1] if len(args) > 1 else kwargs.get("skill_id", ""),
            "run_count": 0,
            "completed_runs": 0,
            "failed_runs": 0,
            "saved_hours": 0.0,
            "estimated_cost_saving": 0.0,
            "risk_events_prevented": 0,
            "human_takeover_rate": 0.0,
            "token_cost_period": 0.0,
        }

    monkeypatch.setattr(value_service, "_bulk_load_skill_metrics", fake_bulk)
    monkeypatch.setattr(value_service, "estimate_value", fake_estimate_value)
    monkeypatch.setattr(value_service, "_count_decision_logs", AsyncMock(return_value=0))
    monkeypatch.setattr(value_service, "_count_rejected_decisions", AsyncMock(return_value=0))
    monkeypatch.setattr(value_service, "_load_department_token_cost_today", AsyncMock(return_value=0.0))
    monkeypatch.setattr(value_service, "_ai_department_recommendation", AsyncMock(return_value=None))

    summary = await value_service.get_department_value_summary(
        db,
        "EC",
        period_days=30,
        include_ai=False,
    )

    assert summary["skill_count"] == 10
    # 单次 SQL：bulk 恰好调用 1 次，estimate_value 不被调用
    assert bulk_calls == 1
    assert per_skill_calls == 0


@pytest.mark.asyncio
async def test_value_column_fallback_when_bulk_none(monkeypatch):
    """_bulk_load_skill_metrics 返回 None（列缺失/异常）时，回退 per-skill estimate_value。"""
    from app.tasktree import value_service

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [SimpleNamespace(id="SK-FB", name="Skill-FB")]
        )
    )

    async def fake_bulk_none(*args, **kwargs):
        return None

    calls = []

    async def fake_estimate_value(db_obj, skill_id, period_days, **kwargs):
        calls.append(skill_id)
        return {
            "skill_id": skill_id,
            "run_count": 1,
            "completed_runs": 1,
            "failed_runs": 0,
            "saved_hours": 1.0,
            "estimated_cost_saving": 30.0,
            "risk_events_prevented": 0,
            "human_takeover_rate": 0.0,
            "token_cost_period": 0.5,
        }

    monkeypatch.setattr(value_service, "_bulk_load_skill_metrics", fake_bulk_none)
    monkeypatch.setattr(value_service, "estimate_value", fake_estimate_value)
    monkeypatch.setattr(value_service, "_count_decision_logs", AsyncMock(return_value=0))
    monkeypatch.setattr(value_service, "_count_rejected_decisions", AsyncMock(return_value=0))
    monkeypatch.setattr(value_service, "_load_department_token_cost_today", AsyncMock(return_value=0.0))
    monkeypatch.setattr(value_service, "_ai_department_recommendation", AsyncMock(return_value=None))

    summary = await value_service.get_department_value_summary(
        db,
        "EC",
        period_days=30,
        include_ai=False,
    )

    assert summary["skill_count"] == 1
    assert calls == ["SK-FB"]


@pytest.mark.asyncio
async def test_bulk_load_skill_metrics_excludes_failed_runs_duration(monkeypatch):
    """[M5] avg_actual_min 必须只统计 completed run 的 duration，
    failed/timeout run 的超长 duration 不得污染分母。

    mock 两个 completed（100min、200min）+ 一个 failed（10000min）→
    expected avg_actual_min ≈ 150 而不是 3433。
    """
    from app.tasktree import value_service

    db = AsyncMock()
    # _get_cached_columns 返回空集合避免走 manual_baseline 分支
    monkeypatch.setattr(value_service, "_get_cached_columns", AsyncMock(return_value=set()))

    # 构造 _bulk_load_skill_metrics 下游 4 个 execute 返回的 rows：
    # 1) exec_stmt 聚合：只 completed run 的 duration_ms_sum 应进分母
    #    completed_runs=2, duration_ms_sum = (100+200)*60*1000 = 18_000_000
    exec_row = SimpleNamespace(
        skill_id="SK-M5",
        run_count=3,  # 2 completed + 1 failed
        completed_runs=2,
        failed_runs=1,
        duration_ms_sum=18_000_000,  # 300 min total
    )
    # 2) decision_stmt 返回空
    # 3) cost_stmt 返回空
    # 4) manual/skill_default 各返回空
    calls = {"n": 0}

    async def fake_execute(stmt, *args, **kwargs):
        calls["n"] += 1
        # 按顺序：第 1 次 = exec_stmt, 2 = decision_stmt, 3 = cost_stmt
        # 后续视 has_manual_baseline / has_skill_baseline 而定（这里都 False）
        if calls["n"] == 1:
            return SimpleNamespace(all=lambda: [exec_row])
        return SimpleNamespace(all=lambda: [])

    db.execute = fake_execute

    metrics = await value_service._bulk_load_skill_metrics(
        db,
        skill_ids=["SK-M5"],
        since=datetime.utcnow() - timedelta(days=30),
        hourly_cost_usd=30.0,
        fallback_baseline_minutes=60.0,
    )

    assert metrics is not None
    result = metrics["SK-M5"]
    # avg_actual_min = 300min / 2 completed = 150.0
    assert result["average_run_minutes"] == 150.0, (
        f"failed run duration 污染了 avg_actual_min: {result['average_run_minutes']}"
    )
    # baseline_min=60 < avg_actual_min=150 → saved_per_run_min=0 → saved_hours=0
    assert result["saved_hours"] == 0.0


@pytest.mark.asyncio
async def test_diagnose_failure_uses_ai_prompt_context(monkeypatch):
    from app.tasktree import ai_diagnose
    from app.execution.models import ExecutionRun
    from app.skills.core.models import Skill

    now = datetime.utcnow()
    run = ExecutionRun(
        id="run-5",
        status="failed",
        trigger_type="manual",
        started_at=now - timedelta(minutes=15),
        completed_at=now - timedelta(minutes=5),
        summary="summary",
    )
    skill = Skill(id="EC-DIAG-01", name="Skill-EC-DIAG-01", department="EC", status="active")
    db = AsyncMock()
    async def _get(model, pk):
        if model is ExecutionRun:
            return run
        if model is Skill:
            return skill
        return None
    db.get = AsyncMock(side_effect=_get)
    monkeypatch.setattr(
        ai_diagnose,
        "_load_primary_step",
        AsyncMock(
            return_value={
                "skill_id": skill.id,
                "skill_name": skill.name,
                "skill_department": skill.department,
                "status": "failed",
                "input_data": {"campaign": "spring"},
                "output_data": None,
                "duration_ms": 600000,
                "error_message": "missing field campaign_id",
                "started_at": now - timedelta(minutes=15),
                "completed_at": now - timedelta(minutes=5),
            }
        ),
    )
    monkeypatch.setattr(
        ai_diagnose,
        "_load_latest_decision_log",
        AsyncMock(
            return_value=SimpleNamespace(
                id=9,
                run_id="run-5",
                skill_id=skill.id,
                approval_status="rejected",
                approval_level=1,
                user_action="rejected",
                user_feedback="",
                reject_reason="missing campaign_id",
                business_impact=None,
                input_snapshot={"campaign": "spring"},
                output_result=None,
                created_at=now,
                token_count=80,
                model_id="gpt-4o",
            )
        ),
    )
    monkeypatch.setattr(ai_diagnose.git_service, "read_file", lambda *args, **kwargs: "# SKILL\npurpose: 处理投放异常")

    captured = {}

    async def fake_llm(cache_key, system, user, **kwargs):
        captured["cache_key"] = cache_key
        captured["system"] = system
        captured["user"] = user
        return "问题来自输入字段缺失，先补齐 campaign_id 再重试。"

    monkeypatch.setattr(ai_diagnose, "call_llm_cached", fake_llm)

    result = await ai_diagnose.diagnose_failure(db, "run-5", user_id="u1", department="EC")

    assert "campaign_id" in captured["user"]
    assert "SKILL" in captured["user"]
    assert result["text"] == "问题来自输入字段缺失，先补齐 campaign_id 再重试。"
    assert result["ai_available"] is True
    assert result["source"] == "llm"


@pytest.mark.asyncio
async def test_diagnose_failure_falls_back_when_ai_missing(monkeypatch):
    from app.tasktree import ai_diagnose
    from app.execution.models import ExecutionRun

    now = datetime.utcnow()
    run = ExecutionRun(
        id="run-6",
        status="failed",
        trigger_type="manual",
        started_at=now - timedelta(minutes=30),
        completed_at=now - timedelta(minutes=10),
        summary="summary",
    )
    db = AsyncMock()
    async def _get(model, pk):
        if model is ExecutionRun:
            return run
        return None
    db.get = AsyncMock(side_effect=_get)
    monkeypatch.setattr(
        ai_diagnose,
        "_load_primary_step",
        AsyncMock(
            return_value={
                "skill_id": "EC-DIAG-02",
                "skill_name": "Skill-EC-DIAG-02",
                "skill_department": "EC",
                "status": "failed",
                "input_data": {"foo": "bar"},
                "output_data": None,
                "duration_ms": 1200000,
                "error_message": "timeout waiting for gateway",
                "started_at": now - timedelta(minutes=30),
                "completed_at": now - timedelta(minutes=10),
            }
        ),
    )
    monkeypatch.setattr(ai_diagnose, "_load_latest_decision_log", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_diagnose.git_service, "read_file", lambda *args, **kwargs: "")

    async def fake_llm(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_diagnose, "call_llm_cached", fake_llm)

    result = await ai_diagnose.diagnose_failure(db, "run-6")

    assert result["ai_available"] is False
    assert result["source"] == "rule_engine"
    text = result["text"]
    assert "超时" in text or "timeout" in text.lower()
    assert "SKILL.md" in text or "重跑" in text
