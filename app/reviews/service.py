"""
审核服务：创建审核请求、通过/驳回、评论。
审核通过后触发 Git tag + 同步到 OpenClaw。
审核创建时根据Skill的approval_level发起L2/L3钉钉审批流。
审核结果（通过/驳回）推送通知给提交人。
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from loguru import logger
from sqlalchemy import and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.reviews.models import Review, ReviewComment
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.auth.models import User


STATIC_CHECK_OVERRIDE_MIN_REASON_LEN = 20


def review_gate_error_payload(gate: dict) -> dict:
    items = [
        item for item in (gate.get("items") or [])
        if isinstance(item, dict) and item.get("severity") == "block" and not item.get("passed")
    ]
    reasons = [
        {
            "key": item.get("key"),
            "label": item.get("label"),
            "detail": item.get("detail"),
            "suggestion": item.get("suggestion"),
        }
        for item in items
    ]
    labels = [str(item.get("label")) for item in items if item.get("label")]
    return {
        "reason": "提交审核 gate 未通过",
        "score": gate.get("score"),
        "threshold": gate.get("threshold"),
        "items": gate.get("items") or [],
        "missing": reasons,
        "message": "提交审核前缺少：" + "、".join(labels) if labels else gate.get("message", "提交审核 gate 未通过"),
        "suggestion": "请按缺失项补齐后重新提交审核。",
    }


def _extract_review_feedback(review: Review) -> dict:
    """从独立字段读取审核反馈。"""
    feedback = review.review_feedback
    return feedback if isinstance(feedback, dict) else {}


def _store_review_feedback(review: Review, **fields) -> dict:
    """将审核反馈写入独立字段。"""
    feedback = dict(review.review_feedback) if isinstance(review.review_feedback, dict) else {}
    for key, value in fields.items():
        if value in (None, ""):
            feedback.pop(key, None)
        else:
            feedback[key] = value
    review.review_feedback = feedback or None
    return feedback


def _sync_feedback_fields(sync_result: dict | None) -> dict:
    """把同步结果压成 review_feedback 中稳定、可读的状态字段。"""
    sync_result = sync_result if isinstance(sync_result, dict) else {}
    failed = [
        item for item in sync_result.get("instances", [])
        if isinstance(item, dict) and not item.get("ok")
    ]
    messages: list[str] = []
    if sync_result.get("push_ok") is False:
        messages.append("Git push 失败")
    for item in failed:
        label = item.get("name") or item.get("instance_id") or item.get("instance") or "unknown"
        reload_detail = item.get("reload") if isinstance(item.get("reload"), dict) else {}
        error = item.get("error") or reload_detail.get("error") or "同步失败"
        reload_status = reload_detail.get("status")
        if reload_status:
            messages.append(f"{label}: {error} (reload={reload_status})")
        else:
            messages.append(f"{label}: {error}")
    return {
        "sync_status": "ok" if sync_result.get("all_ok") else "partial_failure",
        "sync_detail": sync_result,
        "sync_message": "；".join(messages) if messages else "同步未完全成功",
    }


def _schedule_feedback_fields(schedule_result: dict | None) -> dict:
    schedule_result = schedule_result if isinstance(schedule_result, dict) else {}
    return {
        "schedule_status": "ok",
        "schedule_detail": schedule_result,
        "schedule_message": schedule_result.get("message") or "定时任务已更新",
    }


def _verify_feedback_fields(verify_result: dict | None) -> dict:
    verify_result = verify_result if isinstance(verify_result, dict) else {}
    status = str(verify_result.get("status") or "skipped")
    default_message = {
        "ok": "运行验证成功",
        "failed": str(verify_result.get("error") or "运行验证失败"),
        "skipped": str(verify_result.get("reason") or "运行验证已跳过"),
    }.get(status, status)
    return {
        "verify_status": status,
        "verify_detail": verify_result,
        "verify_message": verify_result.get("message") or default_message,
    }


def _normalize_publish_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


async def enforce_static_detection_gate(
    *,
    skill_id: str,
    actor_id: str,
    target_type: str,
    target_id: str,
    source: str,
    override: bool = False,
    override_reason: str = "",
) -> dict:
    """Block direct LLM SDK/API use unless an audited reviewer override is supplied."""
    from app.reviews.static_checks import scan_skill_static_checks, static_check_error_detail

    report = await asyncio.to_thread(scan_skill_static_checks, skill_id)
    payload = report.to_dict()
    if report.passed:
        return payload

    if not override:
        raise AppError("STATIC_CHECK_BLOCKED", 409, static_check_error_detail(report))

    normalized_reason = str(override_reason or "").strip()
    if len(normalized_reason) < STATIC_CHECK_OVERRIDE_MIN_REASON_LEN:
        raise AppError(
            "REVIEW_OVERRIDE_NO_REASON",
            400,
            {
                "min_length": STATIC_CHECK_OVERRIDE_MIN_REASON_LEN,
                "actual_length": len(normalized_reason),
            },
        )

    await audit.log(
        actor_id,
        "review_static_check_override",
        target_type,
        target_id,
        detail={
            "skill_id": skill_id,
            "source": source,
            "override_reason": normalized_reason,
            "static_detection": payload,
        },
    )
    return payload


async def _resolve_sync_target(
    skill_id: str,
    instance_id: str | None,
    *,
    actor: Any | None = None,
) -> dict | None:
    normalized = _normalize_publish_text(instance_id)
    if not normalized:
        return None
    from app.execution.sync_service import sync_service

    targets = await sync_service.list_sync_targets(skill_id, actor=actor)
    target = next(
        (
            item for item in targets
            if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized
        ),
        None,
    )
    if not target:
        raise AppError("SYNC_TARGET_NOT_FOUND", 404, {"detail": f"未找到运行终端: {normalized}"})
    if not target.get("selectable", False):
        raise AppError(
            "AUTH_PERMISSION_DENIED",
            403,
            {"detail": target.get("reason") or "无权选择该运行终端"},
        )
    return target


async def _apply_skill_publish_config(
    *,
    skill: Skill,
    reviewer: str,
    runtime_instance_id: str | None = None,
    cron_expression: str | None = None,
) -> dict:
    from apscheduler.triggers.cron import CronTrigger

    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser
    from app.skills.core.service_shared import sync_skill_fields_from_frontmatter

    runtime_instance_id = _normalize_publish_text(runtime_instance_id)
    cron_expression = _normalize_publish_text(cron_expression)
    if cron_expression:
        try:
            CronTrigger.from_crontab(cron_expression)
        except Exception as exc:  # noqa: BLE001
            raise AppError("PARAM_INVALID", 400, {"detail": f"cron 表达式无效: {exc}"}) from exc

    previous_trigger_type = skill.trigger_type
    previous_trigger_expression = skill.trigger_expression
    config_commit: str | None = None
    effective_instance_id = runtime_instance_id

    async with git_service.skill_advisory_lock(skill.id):
        skill_md = git_service.read_file(skill.id, "SKILL.md")
        if skill_md is None:
            raise AppError("SKILL_FILE_NOT_FOUND", 404, {"detail": f"{skill.id}/SKILL.md 不存在"})

        structured = skill_parser.parse(skill_md)
        frontmatter = dict(structured.frontmatter or {})
        changed = False

        if runtime_instance_id and frontmatter.get("instance_id") != runtime_instance_id:
            frontmatter["instance_id"] = runtime_instance_id
            changed = True

        if cron_expression and (
            frontmatter.get("trigger_type") != "cron"
            or frontmatter.get("trigger_expression") != cron_expression
        ):
            frontmatter["trigger_type"] = "cron"
            frontmatter["trigger_expression"] = cron_expression
            changed = True

        effective_instance_id = _normalize_publish_text(frontmatter.get("instance_id")) or runtime_instance_id
        structured.frontmatter = frontmatter
        sync_skill_fields_from_frontmatter(skill, frontmatter)

        if changed:
            git_service.write_file(skill.id, "SKILL.md", skill_parser.render(structured))
            config_commit = git_service.commit_all(
                "审核通过前更新运行配置",
                reviewer,
                skill_id=skill.id,
            )

    schedule_action = None
    if cron_expression:
        schedule_action = (
            "update"
            if previous_trigger_type == "cron" and previous_trigger_expression
            else "start"
        )

    return {
        "runtime_instance_id": effective_instance_id,
        "cron_expression": cron_expression,
        "schedule_action": schedule_action,
        "config_commit": config_commit,
    }


def _sync_instance_succeeded(sync_result: dict | None, instance_id: str | None) -> bool:
    normalized = _normalize_publish_text(instance_id)
    if not normalized or not isinstance(sync_result, dict):
        return False
    instances = sync_result.get("instances")
    if not isinstance(instances, list):
        return False
    for item in instances:
        if not isinstance(item, dict):
            continue
        current_id = str(item.get("instance_id") or item.get("id") or "").strip()
        if current_id == normalized:
            return bool(item.get("ok"))
    return False


async def _verify_runtime_execution(skill_id: str, *, instance_id: str) -> dict:
    from app.execution.execution_service import execution_service
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    verify_timeout = 300
    try:
        skill_md = git_service.read_file(skill_id, "SKILL.md")
        if skill_md:
            structured = skill_parser.parse(skill_md)
            frontmatter = dict(structured.frontmatter or {})
            configured_timeout = (
                frontmatter.get("verify_script_timeout")
                or frontmatter.get("script_timeout")
                or frontmatter.get("execution_timeout")
            )
            if configured_timeout not in (None, ""):
                verify_timeout = max(60, min(int(configured_timeout), 600))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 verify timeout 失败，回退默认值 skill={} err={}", skill_id, exc)

    try:
        result = await execution_service.execute_skill(
            skill_id,
            params={
                "_execution_backend": "bridge_script",
                "_aiclaw_instance_id": instance_id,
                "_script_timeout": verify_timeout,
            },
            sandbox=True,
            triggered_by="review_verify",
            run_mode="sandbox_test",
        )
        return {
            "status": "ok",
            "instance_id": instance_id,
            "run_id": result.get("run_id"),
            "sample_used": result.get("sample_used"),
            "run_mode": result.get("run_mode"),
            "message": f"已在 {instance_id} 完成沙箱验证",
        }
    except AppError as exc:
        return {
            "status": "failed",
            "instance_id": instance_id,
            "error_code": exc.code,
            "error": exc.message,
            "detail": exc.detail or {},
            "message": exc.message,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "instance_id": instance_id,
            "error_code": "VERIFY_EXECUTION_FAILED",
            "error": str(exc),
            "detail": {},
            "message": str(exc),
        }


def _can_self_approve_review(actor: Any | None) -> bool:
    if actor is None:
        return False
    role = str(getattr(actor, "role", "") or "")
    return bool(getattr(actor, "can_view_all", False)) or role in {"admin", "system_admin"}


def system_auto_publish_actor() -> Any:
    """Synthetic reviewer used when the platform security switch auto-publishes."""
    return SimpleNamespace(
        id="svc_skillforge",
        username="svc_skillforge",
        name="SkillForge 系统",
        role="system_admin",
        department=None,
        can_view_all=True,
    )


async def approve_review_as_system(
    db: AsyncSession,
    review_id: int,
    *,
    runtime_instance_id: str | None = None,
    cron_expression: str | None = None,
    verify_after_sync: bool = True,
) -> dict:
    actor = system_auto_publish_actor()
    return await approve_review(
        db,
        review_id,
        actor.id,
        runtime_instance_id=runtime_instance_id,
        cron_expression=cron_expression,
        verify_after_sync=verify_after_sync,
        actor=actor,
    )


async def _resolve_duplicate_pending_reviews_for_same_commit(
    db: AsyncSession,
    *,
    approved_review: Review,
    reviewer: str,
) -> list[int]:
    """Close duplicate pending reviews for the exact same Skill commit.

    A Codex CLI submit and a SkillStudio submit can create two review rows for
    the same ``skill_id`` + ``git_commit_after``. Once one has passed the normal
    review state machine, the other should not keep showing as pending.
    """
    commit = _normalize_publish_text(approved_review.git_commit_after)
    if not commit:
        return []

    rows = (
        await db.execute(
            select(Review)
            .where(Review.skill_id == approved_review.skill_id)
            .where(Review.id != approved_review.id)
            .where(Review.status == "pending")
            .where(Review.git_commit_after == commit)
        )
    ).scalars().all()
    if not rows:
        return []

    decided_at = approved_review.decided_at or now_bjt()
    resolved_ids: list[int] = []
    for row in rows:
        row.status = "approved"
        row.reviewer = reviewer
        row.decided_at = decided_at
        _store_review_feedback(
            row,
            duplicate_resolved_by_review_id=approved_review.id,
            duplicate_resolution="same_commit_approved",
        )
        resolved_ids.append(row.id)

    try:
        from app.codex.models import CodexSkillSubmission

        submissions = (
            await db.execute(
                select(CodexSkillSubmission).where(
                    CodexSkillSubmission.review_id.in_(resolved_ids)
                )
            )
        ).scalars().all()
        for submission in submissions:
            submission.status = "approved"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "同步 Codex submission 审核状态失败 skill={} reviews={} err={}",
            approved_review.skill_id,
            resolved_ids,
            exc,
        )

    await db.flush()
    await audit.log(
        reviewer,
        "review.duplicate_resolved",
        "review",
        str(approved_review.id),
        detail={
            "skill_id": approved_review.skill_id,
            "git_commit_after": commit,
            "resolved_review_ids": resolved_ids,
        },
    )
    return resolved_ids


def _is_playbook_target(target_id: str) -> bool:
    return bool(target_id) and target_id.startswith("playbook:")


def _playbook_name_from_target(target_id: str) -> str:
    return target_id.split(":", 1)[1] if _is_playbook_target(target_id) else ""


def _git_logs_for_path(path: str, *, max_count: int = 20) -> list[dict]:
    commits = list(git_service.repo.iter_commits(paths=path, max_count=max_count))
    return [
        {
            "hash": c.hexsha[:8],
            "hash_full": c.hexsha,
            "message": c.message.strip().split("\n")[0],
            "author": c.author.name,
            "date": isoformat_bjt(datetime.fromtimestamp(c.committed_date, timezone.utc)),
        }
        for c in commits
    ]


def _git_diff_for_path(path: str, commit_a: str = "HEAD~1", commit_b: str = "HEAD") -> str:
    try:
        return git_service.repo.git.diff(commit_a, commit_b, "--", path)
    except Exception:
        return ""


def _next_playbook_version(playbook_name: str) -> str:
    """返回下一个可用的 Playbook 版本号 (vN.M)。兼容历史的 vN.M.P 三段 tag。

    注意：本函数只读 git tag。**并发安全由调用方持有的 PG advisory lock 保证**
    （见 `_acquire_playbook_version_lock`）—— 同一 playbook 的多个 approve 必须
    串行进入"读 tag → 计算版本 → create_tag"这段临界区。
    """
    prefix = f"playbook/{playbook_name}/v"
    versions: list[tuple[int, int, int]] = []
    for tag in git_service.repo.tags:
        tag_name = str(tag)
        if not tag_name.startswith(prefix):
            continue
        raw_version = tag_name[len(prefix):]
        parts = raw_version.split(".")
        if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
            continue
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) == 3 else 0
        versions.append((major, minor, patch))

    if not versions:
        return "v0.1"
    major, minor, _patch = max(versions)
    return f"v{major}.{minor + 1}"


async def _acquire_playbook_version_lock(db: AsyncSession, playbook_name: str) -> None:
    """H7 修复：在事务结束前持有 PostgreSQL advisory xact lock，
    串行化同一 playbook 的版本号分配 + tag 创建。

    advisory_xact_lock 在事务 commit/rollback 时自动释放，无需手动 unlock。
    SQLite 等测试夹具不支持 → 优雅降级（不抛错），生产 PG 必走此路径。
    """
    # v2.0.16 H5：收口到 app/common/advisory_lock::acquire_xact_lock。
    from app.common.advisory_lock import acquire_xact_lock

    await acquire_xact_lock(db, f"playbook_version:{playbook_name}")


def _playbook_git_path(playbook_name: str) -> str:
    """返回 Playbook 相对 skills-repo 根的 git path (便于 git_service.repo 查询)。"""
    from app.playbooks.service import _find_playbook_path

    path = _find_playbook_path(playbook_name)
    if path is None:
        return f"playbooks/{playbook_name}.yaml"
    try:
        rel = path.resolve().relative_to(Path(git_service.repo.working_dir).resolve())
        return rel.as_posix()
    except ValueError:
        # 理论上不该走到这里 (迁移后 playbooks 必在 skills-repo 内)
        return f"playbooks/{playbook_name}.yaml"


async def _load_review_target(db: AsyncSession, target_id: str) -> dict:
    if _is_playbook_target(target_id):
        from app.playbooks.service import _find_playbook_path, get_playbook

        playbook_name = _playbook_name_from_target(target_id)
        playbook = await get_playbook(playbook_name)
        path = _find_playbook_path(playbook_name)
        if not path:
            raise AppError("PLAYBOOK_NOT_FOUND", 404)
        approval_level_raw = playbook.get("approval_level", 0)
        try:
            approval_level = int(approval_level_raw or 0)
        except (TypeError, ValueError):
            approval_level = 0
        approver = playbook.get("approver") or None
        return {
            "kind": "playbook",
            "id": target_id,
            "name": playbook_name,
            "display_name": playbook.get("name", playbook_name),
            "department": playbook.get("department", ""),
            "approval_level": approval_level,
            "approver": approver,
            "git_path": _playbook_git_path(playbook_name),
            "skill": None,
        }

    result = await db.execute(select(Skill).where(Skill.id == target_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    return {
        "kind": "skill",
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.name,
        "department": skill.department,
        "approval_level": skill.approval_level or 0,
        "approver": skill.approver or None,
        "git_path": skill.id,
        "skill": skill,
    }


async def create_review(
    db: AsyncSession,
    skill_id: str,
    submitter: str,
    change_type: str,
    diff_summary: str = "",
    diff_content: dict | None = None,
    reason: str = "",
    force_submit: bool = False,
    force_reason: str = "",
) -> dict:
    """创建审核请求"""
    force_reason = str(force_reason or "").strip()
    if force_submit and not force_reason:
        raise AppError("PARAM_INVALID", 400, {"detail": "force_reason is required"})

    target = await _load_review_target(db, skill_id)
    force_gate_payload: dict | None = None
    if target["kind"] == "skill":
        from app.skills.lifecycle.publish_readiness import check_publish_readiness

        try:
            readiness = await check_publish_readiness(skill_id, db, include_ai_verify=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("提交审核 gate 检查失败，降级放行 skill={} err={}", skill_id, exc)
            readiness = None
        if readiness is None:
            gate = {}
        else:
            gate = readiness.review_gate or {}
        if gate and not gate.get("can_submit_review", readiness.can_publish):
            force_gate_payload = review_gate_error_payload(gate)
            if not force_submit:
                raise AppError("PARAM_INVALID", 400, force_gate_payload)
            logger.warning(
                "用户强制提交审核: skill={} submitter={} score={} reason={}",
                skill_id,
                submitter,
                force_gate_payload.get("score"),
                force_reason or "-",
            )

    if force_submit:
        forced_diff = dict(diff_content or {})
        forced_diff["force_submit"] = {
            "enabled": True,
            "reason": force_reason or "用户确认强制提交审核",
            "gate": force_gate_payload,
        }
        diff_content = forced_diff
        if force_reason and force_reason not in reason:
            reason = f"{reason}\n\n[强制提交] {force_reason}".strip()

    # 获取当前和前一次 Git commit（前一次用于 semantic diff 对比）
    logs = (
        _git_logs_for_path(target["git_path"], max_count=2)
        if target["kind"] == "playbook"
        else git_service.log(skill_id=skill_id, max_count=2)
    )
    current_commit = logs[0]["hash_full"] if logs else None
    previous_commit = logs[1]["hash_full"] if len(logs) >= 2 else None

    review = Review(
        skill_id=skill_id,
        submitter=submitter,
        reviewer=target["approver"],
        change_type=change_type,
        diff_summary=diff_summary or f"{target['display_name']} {change_type}变更",
        diff_content=diff_content,
        reason=reason,
        status="pending",
        git_commit_before=previous_commit,
        git_commit_after=current_commit,
    )
    db.add(review)
    await db.flush()

    await audit.log(submitter, "review.submit", "review", str(review.id),
                    detail={"skill_id": skill_id, "change_type": change_type, "force_submit": force_submit})
    if force_submit:
        await audit.log(
            submitter,
            "review.force_submit",
            "review",
            str(review.id),
            detail={
                "skill_id": skill_id,
                "change_type": change_type,
                "force_reason": force_reason,
                "gate": force_gate_payload,
            },
        )

    # ── L2/L3审批流 + 审核通知 ──
    approval_level = target["approval_level"]
    actual_diff_summary = diff_summary or f"{target['display_name']} {change_type}变更"

    # 查询提交人姓名（用于审批表单）
    submitter_user = (await db.execute(
        select(User).where(User.id == submitter)
    )).scalar_one_or_none()
    submitter_name = submitter_user.name if submitter_user else submitter

    # 根据审批级别发起L2/L3钉钉审批
    if approval_level >= 2 and target["approver"]:
        # 查询审批人的钉钉ID
        approver_user = (await db.execute(
            select(User).where(User.id == target["approver"])
        )).scalar_one_or_none()

        if approver_user and approver_user.dingtalk_user_id:
            try:
                from app.dingtalk.approval import create_l2_approval, create_l3_approval

                submitter_dt_id = submitter_user.dingtalk_user_id if submitter_user else ""

                if approval_level == 2:
                    approval_result = await create_l2_approval(
                        skill_id=skill_id,
                        submitter_name=submitter_name,
                        change_summary=actual_diff_summary,
                        approver_dingtalk_id=approver_user.dingtalk_user_id,
                        submitter_dingtalk_id=submitter_dt_id,
                    )
                else:  # L3
                    approval_result = await create_l3_approval(
                        skill_id=skill_id,
                        submitter_name=submitter_name,
                        change_summary=actual_diff_summary,
                        approver_dingtalk_id=approver_user.dingtalk_user_id,
                        submitter_dingtalk_id=submitter_dt_id,
                    )

                # 保存审批实例ID，以便回调时关联
                if approval_result.get("ok"):
                    review.dingtalk_msg_id = approval_result.get("instance_id")
                    await db.flush()
                    logger.info(f"审批流创建成功: review={review.id} instance={review.dingtalk_msg_id}")
                else:
                    logger.warning(f"审批流创建失败: review={review.id} error={approval_result}")
            except Exception as e:
                logger.error(f"发起L{approval_level}审批异常: review={review.id} error={e}")
        else:
            logger.warning(f"审批人钉钉ID未配置: approver={target['approver']}")

    # 发送审核通知给相关审核人（所有级别都发通知）
    await _send_review_notification(db, review, target["approver"], submitter_name, actual_diff_summary)

    return {
        "review_id": review.id,
        "skill_id": skill_id,
        "reviewer": review.reviewer,
        "status": "pending",
        "force_submit": force_submit,
        "force_submit_gate": force_gate_payload,
    }


async def _send_review_notification(
    db: AsyncSession,
    review: Review,
    reviewer_id: str | None,
    submitter_name: str,
    diff_summary: str,
) -> None:
    """发送审核通知给审核人（通过outbox入队）"""
    from app.dingtalk.outbox import outbox
    from app.dingtalk.card_templates import build_review_notification

    # 确定通知接收人：优先skill.approver，否则通知target_users中的第一个
    if not reviewer_id:
        logger.info(f"审核 #{review.id} 无指定审核人，跳过钉钉通知")
        return

    # 查询审核人的钉钉ID
    reviewer_user = (await db.execute(
        select(User).where(User.id == reviewer_id)
    )).scalar_one_or_none()

    if not reviewer_user or not reviewer_user.dingtalk_user_id:
        logger.warning(f"审核人 {reviewer_id} 未找到或未配置钉钉ID，跳过通知")
        return

    payload = build_review_notification(
        review_id=review.id,
        skill_id=review.skill_id,
        change_type=review.change_type,
        submitter=submitter_name,
        diff_summary=diff_summary,
    )

    # [H1] 共享外层 db 事务: review 状态写入与 outbox 入队同一事务 commit/rollback,
    # 避免"审核已建但通知没发"或"通知发了但审核回滚"
    await outbox.enqueue(
        message_type="work_notice",
        recipient=reviewer_user.dingtalk_user_id,
        payload=payload,
        priority=1,  # 审批优先级最高
        related_type="review",
        related_id=str(review.id),
        session=db,
    )
    logger.info(f"审核通知已入队: review={review.id} to={reviewer_user.dingtalk_user_id}")


async def list_reviews(
    db: AsyncSession,
    status: str | None = None,
    skill_id: str | None = None,
    q: str | None = None,
    change_type: str | None = None,
    reviewer: str | None = None,
    submitter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 20,
    department: str | None = None,
) -> dict:
    """列出审核请求（分页+筛选+部门隔离）。

    H6 修复：Playbook review 的 skill_id 形如 ``playbook:xxx``，与 ``skills`` 表
    无法 INNER JOIN。原实现导致：
      a) 普通用户看不到本部门 Playbook 的审核（INNER JOIN 漏掉）；
      b) 切换到 OUTER JOIN 后，又会让跨部门 Playbook 全部可见。
    Playbook 仅以 YAML 文件存在（无 DB 表），SQL JOIN 不可行，
    改为：SQL 阶段先做 Skill 部门过滤，再用 Python 二次过滤补 Playbook。
    """
    def _change_type_values(raw: str | None) -> list[str]:
        value = (raw or "").strip()
        if not value:
            return []
        # 审核中心设计稿只暴露「新建 / 更新 / 修复」三组；历史数据里
        # params/logic/codex_submit 均是更新类，code/regression_case 归为修复类。
        groups = {
            "new_skill": ["new_skill"],
            "update": ["update", "params", "logic", "codex_submit"],
            "fix": ["fix", "bugfix", "code", "regression_case"],
        }
        return groups.get(value, [value])

    # ── 第一阶段：构造可比较的 Skill 部门过滤 SQL ──
    # 当 department 非空时，先把"属于本部门 Skill 的 review_id"挑出来，
    # 同时单独保留"全部 Playbook review_id"（暂不过滤），
    # 第二阶段在 Python 端用 YAML 部门字段对 Playbook review 做精确过滤。
    base_filters = []
    if status:
        base_filters.append(Review.status == status)
    if skill_id:
        base_filters.append(Review.skill_id == skill_id)
    search_text = (q or "").strip()
    if search_text:
        pattern = f"%{search_text}%"
        base_filters.append(or_(
            Review.skill_id.ilike(pattern),
            Review.diff_summary.ilike(pattern),
            Review.reason.ilike(pattern),
            Review.submitter.ilike(pattern),
            Review.reviewer.ilike(pattern),
        ))
    change_type_values = _change_type_values(change_type)
    if change_type_values:
        base_filters.append(Review.change_type.in_(change_type_values))
    if reviewer:
        base_filters.append(Review.reviewer == reviewer)
    if submitter:
        base_filters.append(Review.submitter == submitter)
    if date_from:
        try:
            base_filters.append(Review.created_at >= parse_bjt_datetime(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            upper = parse_bjt_datetime(date_to)
            if "T" not in date_to and " " not in date_to:
                upper = upper.replace(hour=23, minute=59, second=59, microsecond=999999)
            base_filters.append(Review.created_at <= upper)
        except ValueError:
            pass

    if department:
        # 第二阶段需要预先获取所有候选 Playbook review，按 YAML 部门做过滤。
        # 原始数据集 = 部门内 Skill review ∪ 部门内 Playbook review。
        # 先取出全量满足其它条件、且 (隶属于该部门 Skill 或 是 Playbook 标识)
        # 的 review id；Playbook 部门归属在 Python 阶段判定。
        skill_dept_subq = (
            select(Skill.id).where(Skill.department == department).subquery()
        )
        dept_filter = or_(
            Review.skill_id.in_(select(skill_dept_subq.c.id)),
            Review.skill_id.like("playbook:%"),
        )
        candidates_stmt = (
            select(Review)
            .where(and_(dept_filter, *base_filters))
            .order_by(Review.created_at.desc())
        )
        candidates = (await db.execute(candidates_stmt)).scalars().all()

        # Playbook YAML 部门匹配（缓存避免 N 次 IO）
        from app.playbooks.service import get_playbook
        pb_dept_cache: dict[str, str | None] = {}

        async def _playbook_dept(name: str) -> str | None:
            if name in pb_dept_cache:
                return pb_dept_cache[name]
            try:
                pb = await get_playbook(name)
                d = pb.get("department") or ""
            except Exception as exc:  # pragma: no cover
                logger.debug("Playbook 部门读取失败 name={} err={}", name, exc)
                d = None
            pb_dept_cache[name] = d
            return d

        filtered: list[Review] = []
        for r in candidates:
            if _is_playbook_target(r.skill_id):
                pb_name = _playbook_name_from_target(r.skill_id)
                pb_dept = await _playbook_dept(pb_name)
                if pb_dept == department:
                    filtered.append(r)
            else:
                # SQL in-clause 已保证 Skill 部门匹配
                filtered.append(r)

        total = len(filtered)
        page_items = filtered[(page - 1) * page_size: (page - 1) * page_size + page_size]
        reviews = page_items
    else:
        # 无部门隔离（admin 路径）：保持原有 SQL 分页
        stmt = select(Review)
        count_stmt = select(func.count(Review.id))
        for f in base_filters:
            stmt = stmt.where(f)
            count_stmt = count_stmt.where(f)

        total = (await db.execute(count_stmt)).scalar() or 0
        stmt = (
            stmt.order_by(Review.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        reviews = (await db.execute(stmt)).scalars().all()

    return {
        "total": total,
        "page": page,
        "items": [
            {
                "id": r.id,
                "skill_id": r.skill_id,
                "submitter": r.submitter,
                "reviewer": r.reviewer,
                "change_type": r.change_type,
                "diff_summary": r.diff_summary,
                "reason": r.reason,
                "status": r.status,
                "review_feedback": _extract_review_feedback(r),
                "created_at": isoformat_bjt(r.created_at),
                "decided_at": isoformat_bjt(r.decided_at),
            }
            for r in reviews
        ],
    }


async def get_review(db: AsyncSession, review_id: int) -> dict:
    """获取审核详情（含评论和diff）"""
    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", 404)

    # 获取评论
    comments_result = await db.execute(
        select(ReviewComment)
        .where(ReviewComment.review_id == review_id)
        .order_by(ReviewComment.created_at)
    )
    comments = comments_result.scalars().all()

    # 获取diff
    diff_text = ""
    if review.git_commit_before and review.git_commit_after:
        if _is_playbook_target(review.skill_id):
            from app.playbooks.service import _find_playbook_path

            playbook_path = _find_playbook_path(_playbook_name_from_target(review.skill_id))
            if playbook_path:
                diff_text = _git_diff_for_path(
                    str(playbook_path.as_posix()),
                    commit_a=review.git_commit_before,
                    commit_b=review.git_commit_after,
                )
        else:
            diff_text = git_service.diff(
                skill_id=review.skill_id,
                commit_a=review.git_commit_before,
                commit_b=review.git_commit_after,
            )

    # 查询关联 Skill 的部门（用于部门隔离）
    skill_department = None
    playbook_department = None
    if review.skill_id and not _is_playbook_target(review.skill_id):
        skill_result = await db.execute(select(Skill).where(Skill.id == review.skill_id))
        skill_row = skill_result.scalar_one_or_none()
        if skill_row:
            skill_department = skill_row.department
    elif _is_playbook_target(review.skill_id):
        from app.playbooks.service import get_playbook

        try:
            playbook = await get_playbook(_playbook_name_from_target(review.skill_id))
            playbook_department = playbook.get("department", "")
        except Exception:
            playbook_department = None

    return {
        "id": review.id,
        "skill_id": review.skill_id,
        "skill_department": skill_department,
        "playbook_department": playbook_department,
        "submitter": review.submitter,
        "reviewer": review.reviewer,
        "change_type": review.change_type,
        "diff_summary": review.diff_summary,
        "diff_content": review.diff_content,
        "review_feedback": _extract_review_feedback(review),
        "reason": review.reason,
        "status": review.status,
        "created_at": isoformat_bjt(review.created_at),
        "decided_at": isoformat_bjt(review.decided_at),
        "diff_text": diff_text,
        "comments": [
            {
                "id": c.id,
                "author": c.author,
                "content": c.content,
                "file_path": c.file_path,
                "line_number": c.line_number,
                "side": c.side,
                "created_at": isoformat_bjt(c.created_at),
            }
            for c in comments
        ],
    }


async def _apply_contract_drift_schema(
    db: AsyncSession,
    *,
    skill_id: str,
    inferred_schema: dict | None,
    sample_count: int,
    review_id: int,
    reviewer: str,
) -> None:
    """审核通过 contract_drift_apply 类型时,把 inferred_schema 落到 contract.json + 标记所有 drift 已处理。

    必须在 git tag 之前调用,使 tag 命中新的 commit。
    任何失败都抛 AppError,让 approve_review 的事务回滚 review.status。
    """
    import json
    from sqlalchemy import update as sa_update
    from app.execution.models import ContractDrift

    if not isinstance(inferred_schema, dict):
        raise AppError("REVIEW_DIFF_INVALID", 400, {"detail": "diff_content.inferred_schema 缺失或非 dict"})

    contract_raw = git_service.read_file(skill_id, "contract.json")
    if not contract_raw:
        raise AppError("SKILL_FILE_NOT_FOUND", 404, {"detail": f"{skill_id}/contract.json not found"})
    try:
        contract = json.loads(contract_raw)
    except json.JSONDecodeError as e:
        raise AppError("CONTRACT_JSON_INVALID", 500, {"detail": f"contract.json 解析失败: {e}"})

    contract["output_schema"] = inferred_schema
    new_content = json.dumps(contract, ensure_ascii=False, indent=2)
    git_service.write_file(skill_id, "contract.json", new_content)
    git_service.commit_all(
        f"drift: apply inferred output_schema (review #{review_id}, {sample_count} samples)",
        reviewer,
        skill_id=skill_id,
    )

    # 该 skill 所有未处理的 drift 标为 acknowledged
    await db.execute(
        sa_update(ContractDrift)
        .where(ContractDrift.skill_id == skill_id, ContractDrift.acknowledged == False)  # noqa: E712
        .values(
            acknowledged=True,
            acknowledged_by=reviewer,
            acknowledged_at=now_bjt(),
        )
    )

    await audit.log(
        reviewer, "review.contract_drift_apply", "skill", skill_id,
        detail={"review_id": review_id, "sample_count": sample_count},
    )


async def approve_review(
    db: AsyncSession,
    review_id: int,
    reviewer: str,
    rating: float | None = None,
    feedback_type: str = "",
    runtime_instance_id: str | None = None,
    cron_expression: str | None = None,
    verify_after_sync: bool = True,
    actor: Any | None = None,
    static_check_override: bool = False,
    static_check_override_reason: str = "",
) -> dict:
    """审核通过：更新状态 + Git tag + 更新Skill版本"""
    # 行锁防双重审批 TOCTOU：两个并发 approve 必须串行经过 status 检查
    result = await db.execute(
        select(Review).where(Review.id == review_id).with_for_update()
    )
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", 404)
    if review.status != "pending":
        raise AppError("REVIEW_ALREADY_DECIDED", 400)
    self_approved = review.submitter == reviewer
    if self_approved and not _can_self_approve_review(actor):
        raise AppError("REVIEW_SELF_APPROVE", 400)

    static_detection: dict | None = None
    if not _is_playbook_target(review.skill_id):
        static_detection = await enforce_static_detection_gate(
            skill_id=review.skill_id,
            actor_id=reviewer,
            target_type="review",
            target_id=str(review_id),
            source="review_approve",
            override=static_check_override,
            override_reason=static_check_override_reason,
        )

    runtime_instance_id = _normalize_publish_text(runtime_instance_id)
    cron_expression = _normalize_publish_text(cron_expression)

    review.status = "approved"
    review.reviewer = reviewer
    review.decided_at = now_bjt()
    feedback = _store_review_feedback(
        review,
        rating=rating,
        feedback_type=feedback_type,
        runtime_instance_id=runtime_instance_id,
        cron_expression=cron_expression,
        verify_requested=True if verify_after_sync else None,
        self_approved_by_admin=True if self_approved else None,
        static_detection={
            "passed": static_detection.get("passed"),
            "finding_count": static_detection.get("finding_count", 0),
            "override": bool(static_check_override),
        } if static_detection and not static_detection.get("passed") else None,
    )

    if _is_playbook_target(review.skill_id):
        if runtime_instance_id or cron_expression:
            raise AppError("PARAM_INVALID", 400, {"detail": "Playbook 审核暂不支持指定运行终端或定时任务"})
        playbook_name = _playbook_name_from_target(review.skill_id)
        # H7：进入 (read tags → compute version → create_tag) 临界区前先抢锁
        await _acquire_playbook_version_lock(db, playbook_name)
        new_version = _next_playbook_version(playbook_name)
        tag_name = f"playbook/{playbook_name}/{new_version}"
        try:
            git_service.tag(tag_name, f"审核通过 by {reviewer}")
        except Exception as e:
            logger.error(f"Playbook Git tag失败，回滚审核状态: {tag_name} error={e}")
            review.status = "pending"
            review.decided_at = None
            await db.flush()
            await audit.log(
                reviewer,
                "review.tag_failed",
                "review",
                str(review_id),
                detail={"tag": tag_name, "error": str(e)},
            )
            raise AppError("REVIEW_TAG_FAILED", 500, {"detail": f"Git tag 创建失败: {e}"}) from e

        # Playbook 同步到远端 + 通知 OpenClaw reload
        sync_warning = None
        sync_result: dict | None = None
        try:
            from app.execution.sync_service import sync_service
            sync_result = await sync_service.sync_playbook_after_approval(playbook_name)
            if not sync_result.get("all_ok"):
                sync_warning = "Playbook 同步未完全成功，请检查 OpenClaw 状态或联系管理员"
                logger.warning(f"Playbook 同步未完全成功: {playbook_name} result={sync_result}")
                _store_review_feedback(review, **_sync_feedback_fields(sync_result))
                if not sync_result.get("push_ok"):
                    git_service.delete_tag(tag_name)
                    logger.info(f"已删除本地 tag {tag_name}（push 失败回滚）")
                await audit.log(reviewer, "review.sync_partial", "review", str(review_id),
                                detail={"skill_id": review.skill_id, "sync_result": sync_result})
            else:
                _store_review_feedback(review, sync_status="ok", sync_detail=sync_result, sync_message="同步成功")
        except Exception as e:
            sync_warning = "Playbook 同步异常，请检查网络连接或联系管理员"
            logger.error(f"Playbook 同步异常: {playbook_name} error={e}")
            _store_review_feedback(review, sync_status="error", sync_detail={"error": str(e)}, sync_message=str(e))
            git_service.delete_tag(tag_name)
            await audit.log(reviewer, "review.sync_failed", "review", str(review_id),
                            detail={"skill_id": review.skill_id, "error": str(e)})

        await db.flush()
        await audit.log(
            reviewer,
            "review.approve",
            "review",
            str(review_id),
            detail={"skill_id": review.skill_id, **feedback},
        )
        await _send_review_result_notification(db, review, reviewer)
        result = {
            "review_id": review_id,
            "status": "approved",
            "new_version": new_version,
            "review_feedback": _extract_review_feedback(review),
            "sync_result": sync_result,
        }
        if sync_warning:
            result["sync_warning"] = sync_warning
        return result

    # ── 契约偏离反向覆盖：批准时把 inferred_schema 写入 contract.json + commit ──
    # 必须在 git tag 之前 commit,这样 tag 会包含新 commit。
    drift_apply = (
        review.diff_content
        and isinstance(review.diff_content, dict)
        and review.diff_content.get("reason") == "contract_drift_apply"
    )
    if drift_apply:
        await _apply_contract_drift_schema(
            db,
            skill_id=review.skill_id,
            inferred_schema=review.diff_content.get("inferred_schema"),
            sample_count=review.diff_content.get("sample_count", 0),
            review_id=review_id,
            reviewer=reviewer,
        )

    # 更新Skill状态和版本 — 行锁防两个 review 并发 approve 同一 skill 读到同一 current_version
    skill_result = await db.execute(
        select(Skill).where(Skill.id == review.skill_id).with_for_update()
    )
    skill = skill_result.scalar_one_or_none()
    selected_sync_target: dict | None = None
    publish_config = {
        "runtime_instance_id": runtime_instance_id,
        "cron_expression": cron_expression,
        "schedule_action": None,
        "config_commit": None,
    }
    effective_runtime_instance_id = runtime_instance_id

    if skill and (runtime_instance_id or cron_expression):
        selected_sync_target = await _resolve_sync_target(
            review.skill_id,
            runtime_instance_id,
            actor=actor,
        ) if runtime_instance_id else None
        publish_config = await _apply_skill_publish_config(
            skill=skill,
            reviewer=reviewer,
            runtime_instance_id=runtime_instance_id,
            cron_expression=cron_expression,
        )
        effective_runtime_instance_id = publish_config.get("runtime_instance_id") or runtime_instance_id
        feedback = _store_review_feedback(
            review,
            runtime_instance_id=effective_runtime_instance_id,
            cron_expression=publish_config.get("cron_expression"),
            config_commit=publish_config.get("config_commit"),
        )

    if skill:
        # 计算新版本号
        current_ver = skill.current_version
        if not current_ver:
            major, minor = 0, 0
        else:
            _major, _minor = current_ver.lstrip("v").split(".")
            major, minor = int(_major), int(_minor)
        new_version = f"v{major}.{minor + 1}"

        previous_status = skill.status
        skill.current_version = new_version
        if skill.status in ("draft", "shadow"):
            skill.status = "active"
        skill.updated_at = now_bjt()

        # Git tag（失败则回滚 review 状态，保证原子性）
        tag_name = f"{review.skill_id}/{new_version}"
        try:
            git_service.tag(tag_name, f"审核通过 by {reviewer}")
        except Exception as e:
            logger.error(f"Git tag失败，回滚审核状态: {tag_name} error={e}")
            review.status = "pending"
            review.decided_at = None
            skill.current_version = current_ver  # 回滚版本号
            skill.status = previous_status
            await db.flush()
            await audit.log(reviewer, "review.tag_failed", "review", str(review_id),
                            detail={"tag": tag_name, "error": str(e)})
            raise AppError("REVIEW_TAG_FAILED", 500, {"detail": f"Git tag 创建失败: {e}"}) from e

    await db.flush()

    schedule_result: dict | None = None
    schedule_warning: str | None = None
    if skill and publish_config.get("schedule_action") and publish_config.get("cron_expression"):
        try:
            from app.tasktree.schedule_query import update_skill_schedule

            schedule_result = await update_skill_schedule(
                db,
                review.skill_id,
                str(publish_config["schedule_action"]),
                str(publish_config["cron_expression"]),
                reviewer,
            )
            _store_review_feedback(review, **_schedule_feedback_fields(schedule_result))
            await db.flush()
        except Exception as e:
            schedule_warning = "定时任务保存失败，请检查 cron 配置或联系管理员"
            logger.error("Skill 定时任务更新失败: {} error={}", review.skill_id, e)
            _store_review_feedback(
                review,
                schedule_status="error",
                schedule_detail={"error": str(e)},
                schedule_message=str(e),
            )
            await db.flush()

    approve_detail = {"skill_id": review.skill_id, **_extract_review_feedback(review)}
    if skill:
        approve_detail.update({
            "previous_status": previous_status,
            "status": skill.status,
        })
    await audit.log(reviewer, "review.approve", "review", str(review_id), detail=approve_detail)

    # 生成 Hermes Agent 兼容文件 + 提交到 Git（非阻断：失败只 log）
    if skill:
        try:
            from app.skills.integrations.hermes_adapter import generate_hermes_files
            hermes_result = generate_hermes_files(review.skill_id)
            if hermes_result.get("hermes_md"):
                # Hermes 文件入 Git，确保 push 能带上
                # 用 asyncio.to_thread 防同步 GitPython 冻结事件循环
                hermes_paths = [
                    f"{review.skill_id}/{path}"
                    for path in hermes_result.get("files", [])
                    if isinstance(path, str) and path.strip()
                ]
                await asyncio.to_thread(
                    git_service.commit_all,
                    message=f"生成 Hermes 兼容文件 ({', '.join(hermes_result['files'])})",
                    author="SkillForge",
                    paths=hermes_paths,
                    validate=False,
                )
                logger.info(f"Hermes 兼容文件已生成并提交: {review.skill_id}")
        except Exception as e:
            logger.warning(f"Hermes 兼容文件生成失败（不影响发布）: {e}")

    # 同步到Git远程 + 通知OpenClaw reload
    sync_warning = None
    sync_result: dict | None = None
    verify_warning: str | None = None
    verify_result: dict | None = None
    if skill:
        try:
            from app.execution.sync_service import sync_service
            sync_result = await sync_service.sync_after_approval(
                review.skill_id,
                version_tag=tag_name,
                review_id=review_id,
                target_instance_ids=[selected_sync_target["id"]] if selected_sync_target else None,
                actor=actor,
            )
            if not sync_result.get("all_ok"):
                sync_warning = "Skill 同步未完全成功，请检查 OpenClaw 状态或联系管理员"
                logger.warning(f"Skill同步未完全成功: {review.skill_id} result={sync_result}")
                # 持久化同步状态到 review_feedback，避免静默吞错
                _store_review_feedback(review, **_sync_feedback_fields(sync_result))
                await db.flush()
                # push 失败时删除本地 tag，防止本地与远程不一致
                if not sync_result.get("push_ok"):
                    git_service.delete_tag(tag_name)
                    logger.info(f"已删除本地 tag {tag_name}（push 失败回滚）")
                await audit.log(reviewer, "review.sync_partial", "review", str(review_id),
                                detail={"skill_id": review.skill_id, "sync_result": sync_result})
            else:
                _store_review_feedback(review, sync_status="ok", sync_detail=sync_result, sync_message="同步成功")
                await db.flush()
                await audit.log(reviewer, "review.sync_ok", "review", str(review_id),
                                detail={"skill_id": review.skill_id, "sync_result": sync_result})
        except Exception as e:
            sync_warning = "Skill 同步异常，请检查网络连接或联系管理员"
            logger.error(f"Skill同步异常: {review.skill_id} error={e}")
            _store_review_feedback(review, sync_status="error", sync_detail={"error": str(e)}, sync_message=str(e))
            await db.flush()
            git_service.delete_tag(tag_name)
            await audit.log(reviewer, "review.sync_failed", "review", str(review_id),
                            detail={"skill_id": review.skill_id, "error": str(e)})

        if verify_after_sync:
            if not effective_runtime_instance_id:
                verify_result = {
                    "status": "skipped",
                    "reason": "no_runtime_instance",
                    "message": "未选择运行终端，已跳过运行验证",
                }
            elif not selected_sync_target:
                verify_result = {
                    "status": "skipped",
                    "instance_id": effective_runtime_instance_id,
                    "reason": "runtime_not_resolved",
                    "message": "未解析到可验证的运行终端，已跳过运行验证",
                }
            elif str(selected_sync_target.get("agent_type") or "") != "aiclaw":
                verify_result = {
                    "status": "skipped",
                    "instance_id": effective_runtime_instance_id,
                    "reason": "unsupported_agent_type",
                    "message": "当前终端不是 bridge_script 运行终端，已跳过运行验证",
                }
            elif not bool(selected_sync_target.get("bridge_online")):
                verify_result = {
                    "status": "skipped",
                    "instance_id": effective_runtime_instance_id,
                    "reason": "bridge_offline",
                    "message": "当前终端离线，已跳过运行验证",
                }
            elif not isinstance(sync_result, dict):
                verify_result = {
                    "status": "skipped",
                    "instance_id": effective_runtime_instance_id,
                    "reason": "sync_failed",
                    "message": "同步未完成，已跳过运行验证",
                }
            elif sync_result and not _sync_instance_succeeded(sync_result, effective_runtime_instance_id):
                verify_result = {
                    "status": "skipped",
                    "instance_id": effective_runtime_instance_id,
                    "reason": "sync_not_ready",
                    "message": "目标终端同步未成功，已跳过运行验证",
                }
            else:
                verify_result = await _verify_runtime_execution(
                    review.skill_id,
                    instance_id=effective_runtime_instance_id,
                )
                if verify_result.get("status") == "failed":
                    verify_warning = "运行验证失败，请检查终端脚本或实例状态"

            _store_review_feedback(review, **_verify_feedback_fields(verify_result))
            await db.flush()

    # 审核结果通知推送给提交人
    await _send_review_result_notification(db, review, reviewer)

    result = {
        "review_id": review_id,
        "status": "approved",
        "new_version": new_version if skill else None,
        "review_feedback": _extract_review_feedback(review),
        "sync_result": sync_result,
    }
    if schedule_result is not None:
        result["schedule_result"] = schedule_result
    if verify_result is not None:
        result["verify_result"] = verify_result
    if sync_warning:
        result["sync_warning"] = sync_warning
    if schedule_warning:
        result["schedule_warning"] = schedule_warning
    if verify_warning:
        result["verify_warning"] = verify_warning
    resolved_duplicate_review_ids = await _resolve_duplicate_pending_reviews_for_same_commit(
        db,
        approved_review=review,
        reviewer=reviewer,
    )
    if resolved_duplicate_review_ids:
        result["resolved_duplicate_review_ids"] = resolved_duplicate_review_ids
    return result


async def reject_review(
    db: AsyncSession,
    review_id: int,
    reviewer: str,
    reason: str = "",
    reject_reason: str = "",
) -> dict:
    """审核驳回"""
    # 行锁防并发驳回/批准互斥
    result = await db.execute(
        select(Review).where(Review.id == review_id).with_for_update()
    )
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", 404)
    if review.status != "pending":
        raise AppError("REVIEW_ALREADY_DECIDED", 400)

    review.status = "rejected"
    review.reviewer = reviewer
    review.decided_at = now_bjt()
    feedback = _store_review_feedback(review, reject_reason=reject_reason)
    await db.flush()

    # 驳回原因作为评论
    if reason or reject_reason:
        prefix = f"驳回原因[{reject_reason}]" if reject_reason else "驳回原因"
        comment = ReviewComment(
            review_id=review_id,
            author=reviewer,
            content=f"{prefix}: {reason}" if reason else prefix,
        )
        db.add(comment)
        await db.flush()

    await audit.log(reviewer, "review.reject", "review", str(review_id),
                    detail={"skill_id": review.skill_id, "reason": reason, **feedback})

    # 审核结果通知推送给提交人
    await _send_review_result_notification(db, review, reviewer)

    return {"review_id": review_id, "status": "rejected", "review_feedback": feedback}


async def add_comment(
    db: AsyncSession,
    review_id: int,
    author: str,
    content: str,
    file_path: str | None = None,
    line_number: int | None = None,
    side: str | None = None,
) -> dict:
    """添加审核评论（支持行级评论）"""
    if not author or not content or not content.strip():
        raise AppError("PARAM_INVALID", 400)

    result = await db.execute(select(Review).where(Review.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise AppError("REVIEW_NOT_FOUND", 404)

    comment = ReviewComment(
        review_id=review_id,
        author=author,
        content=content,
        file_path=file_path,
        line_number=line_number,
        side=side,
    )
    db.add(comment)
    await db.flush()

    await audit.log(author, "review.comment", "review", str(review_id))

    return {
        "comment_id": comment.id,
        "file_path": file_path,
        "line_number": line_number,
    }


async def _send_review_result_notification(
    db: AsyncSession,
    review: Review,
    reviewer: str,
) -> None:
    """审核结果（通过/驳回）推送通知给提交人（钉钉 + 站内通知）"""
    from app.dingtalk.outbox import outbox
    from app.dingtalk.card_templates import build_review_result

    # 提取驳回原因（如有）
    feedback = _extract_review_feedback(review)
    reject_reason = feedback.get("reject_reason", "")

    # 查询提交人
    submitter_user = (await db.execute(
        select(User).where(User.id == review.submitter)
    )).scalar_one_or_none()

    # 站内通知（所有用户都能收到，不依赖钉钉）
    try:
        from app.notifications.models import Notification
        status_text = "已通过" if review.status == "approved" else f"已驳回（{reject_reason}）" if reject_reason else "已驳回"
        notification = Notification(
            user_id=review.submitter,
            title=f"审核结果：{review.skill_id} {status_text}",
            content=f"审核人 {reviewer} 已{'通过' if review.status == 'approved' else '驳回'}了您提交的 {review.skill_id} 变更。" + (f"\n驳回原因：{reject_reason}" if reject_reason else ""),
            link=f"/review/{review.id}",
            category="review_result",
        )
        db.add(notification)
    except Exception as e:
        logger.warning(f"站内通知创建失败: {e}")

    # 钉钉通知
    if not submitter_user or not submitter_user.dingtalk_user_id:
        logger.info(f"提交人 {review.submitter} 未配置钉钉ID，仅站内通知")
        return

    payload = build_review_result(
        review_id=review.id,
        skill_id=review.skill_id,
        status=review.status,
        reviewer=reviewer,
    )
    # 驳回时在 payload 中加入原因
    if review.status == "rejected" and reject_reason:
        if isinstance(payload, dict) and "markdown" in payload:
            payload["markdown"] += f"\n\n**驳回原因:** {reject_reason}"

    # [H1] 共享外层 db 事务: 审核结果状态变更与提交人通知同一事务
    await outbox.enqueue(
        message_type="work_notice",
        recipient=submitter_user.dingtalk_user_id,
        payload=payload,
        priority=2,
        related_type="review",
        related_id=str(review.id),
        session=db,
    )
    logger.info(f"审核结果通知已入队: review={review.id} status={review.status} to={submitter_user.dingtalk_user_id}")
