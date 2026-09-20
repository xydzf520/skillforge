"""可发布性报告 — 整合 lint + verifier + 测试覆盖等质量门禁。

用于提交审核/发布前的自动质检。
can_publish=False 时阻断发布流程。
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import json
import re

from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser
from app.skills.lint import lint_skill, LintReport
from app.workbench.verifier import verify_skill, VerifyReport
from app.common.telemetry import record_first_pass_quality, record_publish_blocked
from app.workbench.task_contract import build_gate_status, merge_review_state
from app.common.time_utils import now_bjt


REVIEW_GATE_BLOCK_THRESHOLD = 70


@dataclass
class PublishReadinessReport:
    """发布就绪报告。"""
    skill_id: str
    can_publish: bool                    # 总体是否可发布
    blocker_count: int                   # 阻断发布的问题数
    warning_count: int                   # 警告数
    lint: dict = field(default_factory=dict)      # Lint 报告
    static_detection: dict = field(default_factory=dict)  # 运行代码静态检测
    schema_validation: dict = field(default_factory=dict)  # output_schema 校验
    verify: dict = field(default_factory=dict)    # Verifier 报告
    replay: dict = field(default_factory=dict)    # §3.5 历史回放 sanity
    implementation: dict = field(default_factory=dict)  # scripts/main.py 实现门禁
    task_contract: dict = field(default_factory=dict)  # v7 TaskContract 门禁
    review_gate: dict = field(default_factory=dict)  # 提交审核前门禁
    cross_skill_conflicts: list[dict] = field(default_factory=list)  # 跨 Skill 规则冲突
    blockers: list[dict] = field(default_factory=list)  # 阻断问题汇总
    summary: str = ""                    # 一句话总结

    def to_dict(self) -> dict:
        return asdict(self)


async def _replay_sanity_check(skill_id: str, *, days: int = 7) -> dict:
    """§3.5 历史回放 sanity 检查。

    轻量版：不实际重跑（太贵），而是验证最近样本数量 + 采纳率。
    用于发布门禁判断："这个 Skill 最近是不是在正常工作"。

    返回：
      {
        "status": "ok" | "insufficient" | "degraded" | "skipped",
        "sample_size": int,
        "adoption_rate": float,
        "window_days": days,
        "message": str,
      }
    """
    try:
        from sqlalchemy import select, func
        from app.database import async_session_factory
        from app.execution.models import DecisionLog
    except Exception:
        return {"status": "skipped", "message": "执行表不可用"}

    since = now_bjt() - timedelta(days=days)
    try:
        async with async_session_factory() as session:
            stmt = (
                select(DecisionLog.user_action, func.count(DecisionLog.id))
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .where(DecisionLog.created_at >= since)
                .group_by(DecisionLog.user_action)
            )
            rows = (await session.execute(stmt)).all()
    except Exception as e:
        return {"status": "skipped", "message": f"查询失败: {e}"}

    counts = {row[0] or "unknown": row[1] for row in rows}
    total = sum(counts.values())
    completed = counts.get("completed", 0)
    rejected = counts.get("rejected", 0)

    if total == 0:
        # 新 Skill 没有历史数据 — 不阻断发布但给提示
        return {
            "status": "skipped",
            "sample_size": 0,
            "adoption_rate": 0.0,
            "window_days": days,
            "message": f"最近 {days} 天无执行记录，跳过回放检查",
        }

    if total < 10:
        return {
            "status": "insufficient",
            "sample_size": total,
            "adoption_rate": round(completed / total, 3),
            "window_days": days,
            "message": f"样本不足（{total} < 10），回放结果仅供参考",
        }

    adoption_rate = completed / total
    rejection_rate = rejected / total

    # 判断是否 degraded：拒绝率 > 50% 或 采纳率 < 30%
    if rejection_rate > 0.5 or adoption_rate < 0.3:
        return {
            "status": "degraded",
            "sample_size": total,
            "adoption_rate": round(adoption_rate, 3),
            "rejection_rate": round(rejection_rate, 3),
            "window_days": days,
            "message": (
                f"最近 {days} 天采纳率 {int(adoption_rate * 100)}%、"
                f"驳回率 {int(rejection_rate * 100)}%，存在回放偏差"
            ),
        }

    return {
        "status": "ok",
        "sample_size": total,
        "adoption_rate": round(adoption_rate, 3),
        "window_days": days,
        "message": f"最近 {days} 天 {total} 条决策，采纳率 {int(adoption_rate * 100)}%",
    }


async def _task_contract_gate_check(skill_id: str) -> dict:
    """v7 TaskContract 门禁检查。

    优先读数据库中的已绑定草稿；若没有，则回退到技能目录中的 task-review-state.json。
    """
    try:
        from sqlalchemy import select
        from app.database import async_session_factory
        from app.workbench.models import SkillStudioDraft, SkillStudioPreview

        async with async_session_factory() as session:
            draft = (
                await session.execute(
                    select(SkillStudioDraft)
                    .where(SkillStudioDraft.skill_id == skill_id)
                    .order_by(SkillStudioDraft.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if draft and draft.contract_json:
                preview_exists = (
                    await session.execute(
                        select(SkillStudioPreview.cache_key)
                        .where(SkillStudioPreview.skill_id == skill_id)
                        .limit(1)
                    )
                ).first() is not None
                review_state = merge_review_state(draft.contract_json, draft.review_state_json or {})
                gate = build_gate_status(
                    draft.contract_json,
                    review_state,
                    {"success": preview_exists or bool(review_state.get("preview", {}).get("approved"))},
                )
                return {
                    "status": "ok" if gate["can_publish"] else "blocked",
                    "gate": gate,
                    "checkpoints": [review_state[k] for k in review_state.keys()],
                    "message": "TaskContract 门禁通过" if gate["can_publish"] else "TaskContract 门禁未完成",
                }
    except Exception as e:
        from loguru import logger
        logger.warning("TaskContract 门禁评估异常 skill={}: {}", skill_id, e)

    state_raw = git_service.read_file(skill_id, "task-review-state.json")
    if state_raw:
        try:
            payload = json.loads(state_raw)
            gate = payload.get("gate") or {}
            if gate:
                return {
                    "status": "ok" if gate.get("can_publish") else "blocked",
                    "gate": gate,
                    "checkpoints": payload.get("checkpoints") or [],
                    "message": "TaskContract 门禁通过" if gate.get("can_publish") else "TaskContract 门禁未完成",
                }
        except Exception:
            return {"status": "blocked", "message": "task-review-state.json 格式错误"}

    contract_raw = git_service.read_file(skill_id, "task-contract.json")
    if contract_raw:
        return {
            "status": "blocked",
            "message": "存在 task-contract.json，但缺少确认记录",
        }

    return {
        "status": "skipped",
        "message": "未检测到 v7 TaskContract 产物，跳过门禁",
    }


def _read_contract_json(skill_id: str) -> tuple[dict, list[str]]:
    errors: list[str] = []
    for path in ("contract.json", "task-contract.json"):
        raw = git_service.read_file(skill_id, path)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            errors.append(f"{path} 不是合法 JSON")
            continue
        if isinstance(parsed, dict):
            return parsed, errors
        errors.append(f"{path} 必须是 JSON object")
    return {}, errors


def _has_runtime_acquisition_path(contract: dict, script: str, skill_md: str) -> bool:
    from app.common.contract_schema import lint_runtime_data_acquisition

    if lint_runtime_data_acquisition(contract, script, skill_md_text=skill_md):
        return False
    text = script or ""
    uses_payload = bool(
        re.search(r"\bpayload\s*(?:\.get\(|\[|=|\bor\b)", text)
        or re.search(r"def\s+main\s*\(\s*payload\b", text)
    )
    uses_sdk = "skillforge_sdk" in text or "SkillForge(" in text
    has_collect_inputs = bool(re.search(r"^\s*def\s+collect_inputs\s*\(", text, re.M))
    return uses_payload or uses_sdk or has_collect_inputs


async def _review_submission_gate_check(skill_id: str, db, skill_md: str) -> dict:
    """提交审核专用 gate：给出 warn/block 分级和健康分。"""
    from app.common.contract_schema import get_output_schema, lint_output_schema

    items: list[dict] = []

    def add_item(key: str, label: str, passed: bool, severity: str, detail: str, suggestion: str) -> None:
        items.append({
            "key": key,
            "label": label,
            "passed": passed,
            "severity": severity,
            "detail": detail,
            "suggestion": suggestion,
        })

    logs = git_service.log(skill_id=skill_id, max_count=1)
    add_item(
        "git_commit",
        "Git commit",
        bool(logs),
        "block",
        "已找到最近一次提交" if logs else "缺少该 Skill 的 Git commit",
        "先保存并提交当前 Skill 改动，再提交审核。",
    )

    try:
        from sqlalchemy import select
        from app.common.audit import AuditLog
        from app.execution.models import DecisionLog

        sandbox_run = (
            await db.execute(
                select(DecisionLog.id)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == True)  # noqa: E712
                .limit(1)
            )
        ).first() is not None
        test_run = (
            await db.execute(
                select(AuditLog.id)
                .where(AuditLog.target_type == "skill")
                .where(AuditLog.target_id == skill_id)
                .where(AuditLog.action.in_(("testing.run", "testing.run_stream", "execution.complete")))
                .limit(1)
            )
        ).first() is not None
    except Exception:
        sandbox_run = False
        test_run = False

    add_item(
        "test_or_sample_run",
        "测试/样例运行",
        sandbox_run or test_run,
        "block",
        "检测到测试或沙箱样例运行记录" if (sandbox_run or test_run) else "未检测到测试或样例运行记录",
        "在测试面板运行测试，或用样例参数执行一次沙箱运行。",
    )

    contract, contract_errors = _read_contract_json(skill_id)
    output_schema = get_output_schema(contract)
    schema_errors = lint_output_schema(output_schema) if isinstance(output_schema, dict) else ["缺少 output_schema"]
    add_item(
        "output_schema",
        "output_schema",
        isinstance(output_schema, dict) and not schema_errors and not contract_errors,
        "block",
        "output_schema 合法" if isinstance(output_schema, dict) and not schema_errors and not contract_errors else "；".join(contract_errors + schema_errors[:3]),
        "在 contract.json 中补齐合法 JSON Schema Draft-07 output_schema，并声明 required 字段。",
    )

    script = git_service.read_file(skill_id, "scripts/main.py") or ""
    acquisition_ok = bool(script.strip()) and _has_runtime_acquisition_path(contract, script, skill_md)
    add_item(
        "runtime_data_acquisition",
        "真实数据采集路径",
        acquisition_ok,
        "block",
        "已检测到运行时真实数据采集路径" if acquisition_ok else "缺少真实数据采集路径，或运行时仍依赖 fixture/sample",
        "实现 collect_inputs(payload)，payload 缺失真实数据时通过 SkillForge SDK 采集；不要读取 fixtures/sample_input.json 作为运行数据。",
    )

    block_count = sum(1 for item in items if item["severity"] == "block" and not item["passed"])
    warn_count = sum(1 for item in items if item["severity"] == "warn" and not item["passed"])
    score = max(0, 100 - block_count * 25 - warn_count * 10)
    return {
        "status": "ok" if score >= REVIEW_GATE_BLOCK_THRESHOLD and block_count == 0 else "blocked",
        "score": score,
        "threshold": REVIEW_GATE_BLOCK_THRESHOLD,
        "can_submit_review": score >= REVIEW_GATE_BLOCK_THRESHOLD and block_count == 0,
        "block_count": block_count,
        "warning_count": warn_count,
        "items": items,
        "message": "提交审核 gate 通过" if block_count == 0 else "提交审核 gate 未通过",
    }


def _implementation_gate_check(skill_id: str, skill_md: str | None = None) -> dict:
    """阻断空白骨架、fixture 运行数据和缺失采集路径的 Skill 发布。"""
    from app.common.contract_schema import (
        lint_placeholder_implementation,
        lint_runtime_data_acquisition,
    )

    script = git_service.read_file(skill_id, "scripts/main.py") or ""
    errors: list[str] = []
    if not script.strip():
        errors.append("缺少 scripts/main.py 或脚本为空")
    else:
        errors.extend(lint_placeholder_implementation(script))

    contract, contract_errors = _read_contract_json(skill_id)
    errors.extend(contract_errors)

    if script.strip():
        errors.extend(
            lint_runtime_data_acquisition(
                contract,
                script,
                skill_md_text=skill_md or "",
            )
        )

    if errors:
        return {
            "status": "blocked",
            "message": "scripts/main.py 实现不完整",
            "errors": errors,
        }
    return {"status": "ok", "message": "scripts/main.py 实现门禁通过"}


def _static_detection_gate_check(skill_id: str) -> dict:
    from app.reviews.static_checks import scan_skill_static_checks

    report = scan_skill_static_checks(skill_id)
    payload = report.to_dict()
    payload["status"] = "ok" if report.passed else "blocked"
    payload["message"] = "静态检测通过" if report.passed else "检测到直接 LLM SDK/API 调用"
    return payload


def _schema_validation_gate_check(skill_id: str) -> dict:
    from app.common.contract_schema import get_output_schema, lint_output_schema

    contract, contract_errors = _read_contract_json(skill_id)
    errors = list(contract_errors)
    output_schema = get_output_schema(contract)
    if not isinstance(output_schema, dict):
        errors.append("缺少 output_schema")
    else:
        errors.extend(lint_output_schema(output_schema))

    return {
        "status": "blocked" if errors else "ok",
        "message": "output_schema 校验通过" if not errors else "output_schema 校验未通过",
        "errors": errors,
    }


async def check_publish_readiness(
    skill_id: str,
    db,  # [H6] 强制参数 — 调用方必须传 session, 防止内部新开事务与外层嵌套 (PG 不支持)
    *,
    include_ai_verify: bool = True,
    total_timeout: float = 60.0,
) -> PublishReadinessReport:
    """运行完整质量门禁并返回可发布性报告。

    Args:
        skill_id: Skill ID
        db: 必传 AsyncSession — 跨 Skill 冲突检查复用此 session, 避免嵌套事务。
            旧版允许 db=None 自建事务, 在 publish endpoint 大事务内嵌套调用会导致
            PostgreSQL 不支持的"嵌套事务"无声失败。本次 [H6] 改为强制参数。
        include_ai_verify: 是否运行 AI 审查（较慢，默认 True）
        total_timeout: 并行 lint+replay+contract+verify 的总超时秒数, 防单任务挂起拖死

    Returns:
        PublishReadinessReport
    """
    if db is None:
        # 防御: 旧调用方未传 db 参数 — 抛异常而不是默默自建事务
        raise ValueError("check_publish_readiness 必须传入 db (AsyncSession), 不允许 None")
    report = PublishReadinessReport(
        skill_id=skill_id,
        can_publish=True,
        blocker_count=0,
        warning_count=0,
    )

    # 1. 读取 Skill（I/O 放到 to_thread 避免阻塞事件循环）
    skill_md = await asyncio.to_thread(git_service.read_file, skill_id, "SKILL.md")
    if not skill_md:
        report.can_publish = False
        report.blocker_count = 1
        report.summary = "SKILL.md 不存在"
        report.blockers.append({
            "source": "file",
            "severity": "critical",
            "message": "SKILL.md 文件不存在或为空",
        })
        return report

    parsed = await asyncio.to_thread(skill_parser.parse, skill_md)
    implementation_result = await asyncio.to_thread(_implementation_gate_check, skill_id, skill_md)

    # 2. 并行执行 lint/static/schema + verify（LLM）+ replay sanity（DB 查询）
    lint_task = asyncio.to_thread(lint_skill, skill_id, parsed)
    static_task = asyncio.to_thread(_static_detection_gate_check, skill_id)
    schema_task = asyncio.to_thread(_schema_validation_gate_check, skill_id)
    replay_task = _replay_sanity_check(skill_id)
    contract_task = _task_contract_gate_check(skill_id)
    review_gate_task = _review_submission_gate_check(skill_id, db, skill_md)
    verify_task = verify_skill(skill_id, parsed) if include_ai_verify else None

    # [H6] 总超时包住 gather, 防单任务挂起 (LLM 卡死等) 拖死整个发布门禁
    try:
        if verify_task is not None:
            lint_report, static_result, schema_result, replay_result, contract_result, review_gate_result, verify_result = await asyncio.wait_for(
                asyncio.gather(
                    lint_task,
                    static_task,
                    schema_task,
                    replay_task,
                    contract_task,
                    review_gate_task,
                    verify_task,
                    return_exceptions=True,
                ),
                timeout=total_timeout,
            )
        else:
            lint_report, static_result, schema_result, replay_result, contract_result, review_gate_result = await asyncio.wait_for(
                asyncio.gather(
                    lint_task,
                    static_task,
                    schema_task,
                    replay_task,
                    contract_task,
                    review_gate_task,
                    return_exceptions=True,
                ),
                timeout=total_timeout,
            )
            verify_result = None
    except asyncio.TimeoutError:
        # 超时 → 把所有结果当成异常, 让下面的处理逻辑各自降级
        # cancel 未完成的 task, 避免后台泄漏
        for t in (lint_task, static_task, schema_task, replay_task, contract_task, review_gate_task):
            if hasattr(t, "cancel"):
                t.cancel()
        if verify_task is not None and hasattr(verify_task, "cancel"):
            verify_task.cancel()
        timeout_exc = asyncio.TimeoutError(f"publish readiness check timeout ({total_timeout}s)")
        lint_report = timeout_exc
        static_result = timeout_exc
        schema_result = timeout_exc
        replay_result = timeout_exc
        contract_result = timeout_exc
        review_gate_result = timeout_exc
        verify_result = timeout_exc if verify_task is not None else None

    # 3. 处理 lint 结果
    if isinstance(lint_report, Exception):
        report.lint = {"error": str(lint_report)}
        lint_errors_count = 0
        lint_warnings_count = 0
    else:
        report.lint = lint_report.to_dict()
        for issue in lint_report.errors:
            report.blockers.append({
                "source": "lint",
                "rule": issue.rule,
                "severity": "error",
                "message": issue.message,
                "location": issue.location,
                "suggestion": issue.suggestion,
            })
        lint_errors_count = len(lint_report.errors)
        lint_warnings_count = len(lint_report.warnings)

    report.blocker_count = lint_errors_count
    report.warning_count = lint_warnings_count

    # 3.2 静态检测：Skill 运行代码和 scripts/*_mcp_server.py 禁止直连 LLM SDK/API
    if isinstance(static_result, Exception):
        report.static_detection = {"status": "blocked", "message": f"静态检测失败: {static_result}", "findings": []}
        report.blockers.append({
            "source": "static_detection",
            "severity": "high",
            "message": report.static_detection["message"],
        })
        report.blocker_count += 1
    else:
        report.static_detection = static_result or {}
        if report.static_detection.get("status") == "blocked":
            findings = report.static_detection.get("findings") or []
            for finding in findings:
                report.blockers.append({
                    "source": "static_detection",
                    "rule": finding.get("rule"),
                    "severity": finding.get("severity", "error"),
                    "message": finding.get("message"),
                    "location": f"{finding.get('path')}:{finding.get('line')}",
                    "suggestion": "改为通过 SkillForge SDK / 平台 intelligence 入口调用 LLM。",
                })
            report.blocker_count += max(1, len(findings))

    # 3.3 Schema 校验：发布门禁直接检查 output_schema 自身合法性
    if isinstance(schema_result, Exception):
        report.schema_validation = {"status": "blocked", "message": f"schema 校验失败: {schema_result}", "errors": []}
        report.blockers.append({
            "source": "schema_validation",
            "severity": "high",
            "message": report.schema_validation["message"],
        })
        report.blocker_count += 1
    else:
        report.schema_validation = schema_result or {}
        if report.schema_validation.get("status") == "blocked":
            errors = report.schema_validation.get("errors") or []
            for err in errors:
                report.blockers.append({
                    "source": "schema_validation",
                    "severity": "high",
                    "message": str(err),
                })
            report.blocker_count += max(1, len(errors))

    # 3.5 处理脚本实现门禁 — 空白骨架/假数据采集直接阻断发布
    report.implementation = implementation_result or {}
    if report.implementation.get("status") == "blocked":
        for err in report.implementation.get("errors") or []:
            report.blockers.append({
                "source": "implementation",
                "severity": "high",
                "message": err,
            })
            report.blocker_count += 1

    # 4. 处理 verify 结果
    if verify_result is not None:
        if isinstance(verify_result, Exception):
            report.verify = {"error": str(verify_result)}
        else:
            report.verify = verify_result.to_dict()
            for issue in verify_result.issues:
                if issue.severity in ("critical", "high"):
                    report.blockers.append({
                        "source": "verify",
                        "category": issue.category,
                        "severity": issue.severity,
                        "message": issue.title,
                        "description": issue.description,
                        "location": issue.location,
                        "suggestion": issue.suggestion,
                        "confidence": issue.confidence,
                    })
                    report.blocker_count += 1
                else:
                    report.warning_count += 1

    # 5. 处理 replay sanity — degraded 作为阻断，其他作为提示
    if isinstance(replay_result, Exception):
        report.replay = {"status": "skipped", "message": f"回放检查失败: {replay_result}"}
    else:
        report.replay = replay_result or {}
        if report.replay.get("status") == "degraded":
            report.blockers.append({
                "source": "replay",
                "severity": "high",
                "message": report.replay.get("message", "历史回放显示行为偏差"),
                "sample_size": report.replay.get("sample_size", 0),
                "adoption_rate": report.replay.get("adoption_rate", 0),
            })
            report.blocker_count += 1

    # 5.5 处理 TaskContract 门禁
    if isinstance(contract_result, Exception):
        report.task_contract = {"status": "skipped", "message": f"TaskContract 检查失败: {contract_result}"}
    else:
        report.task_contract = contract_result or {}
        if report.task_contract.get("status") == "blocked":
            gate = report.task_contract.get("gate") or {}
            report.blockers.append({
                "source": "task_contract",
                "severity": "high",
                "message": report.task_contract.get("message", "TaskContract 门禁未完成"),
                "pending_checkpoints": [
                    item.get("label") for item in (gate.get("items") or []) if not item.get("passed")
                ],
            })
            report.blocker_count += 1

    # 5.6 跨 Skill 规则冲突门禁
    # [H6] db 已强制为参数, 不再有 None 分支; 走 force=True 跳过缓存确保发布门禁
    # 看到的是最实时的冲突状态 (300s TTL 缓存对发布前的几秒延迟没意义)
    try:
        from app.skills.lifecycle.cross_skill_conflict import conflicts_for_skill
        cross_conflicts = await conflicts_for_skill(db, skill_id, force=True)
        report.cross_skill_conflicts = cross_conflicts
        if cross_conflicts:
            for conflict in cross_conflicts:
                rule_a = conflict.get("rule_a", {})
                rule_b = conflict.get("rule_b", {})
                # 当前 skill 在 rule_a 还是 rule_b
                other = rule_b if rule_a.get("skill_id") == skill_id else rule_a
                report.blockers.append({
                    "source": "cross_skill_conflict",
                    "severity": "high",
                    "message": (
                        f"与 Skill「{other.get('skill_name')}」对 {conflict.get('metric')} "
                        f"的「{conflict.get('overlap')}」区间结论相反"
                    ),
                    "metric": conflict.get("metric"),
                    "other_skill_id": other.get("skill_id"),
                    "other_skill_name": other.get("skill_name"),
                    "overlap": conflict.get("overlap"),
                })
                report.blocker_count += 1
    except Exception as e:
        from loguru import logger as _log
        _log.warning(f"跨 Skill 冲突检查失败 skill={skill_id}: {e}")

    # 5.7 提交审核 gate：健康分低于 70 或 block 项未过，阻断提交审核。
    if isinstance(review_gate_result, Exception):
        report.review_gate = {
            "status": "blocked",
            "score": 0,
            "threshold": REVIEW_GATE_BLOCK_THRESHOLD,
            "can_submit_review": False,
            "message": f"提交审核 gate 检查失败: {review_gate_result}",
            "items": [],
        }
        report.blockers.append({
            "source": "review_gate",
            "severity": "high",
            "message": report.review_gate["message"],
        })
        report.blocker_count += 1
    else:
        report.review_gate = review_gate_result or {}
        if not report.review_gate.get("can_submit_review"):
            failed = [
                item.get("label")
                for item in (report.review_gate.get("items") or [])
                if isinstance(item, dict) and not item.get("passed") and item.get("severity") == "block"
            ]
            report.blockers.append({
                "source": "review_gate",
                "severity": "high",
                "message": report.review_gate.get("message", "提交审核 gate 未通过"),
                "score": report.review_gate.get("score"),
                "failed_items": failed,
            })
            report.blocker_count += 1

    # 6. 总体判定
    report.can_publish = report.blocker_count == 0

    # 埋点：首版通过率 + 发布阻断
    try:
        record_first_pass_quality(report.can_publish, source="publish_readiness")
        if not report.can_publish:
            record_publish_blocked(reason="quality_gate")
    except Exception as e:
        from loguru import logger
        logger.debug("publish_readiness 埋点上报失败: {}", e)

    if report.can_publish:
        report.summary = f"可发布。通过全部质量检查（{report.warning_count} 个警告可忽略）"
    else:
        report.summary = f"不可发布。发现 {report.blocker_count} 个阻断问题，修复后重试"

    return report
