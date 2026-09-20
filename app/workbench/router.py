"""Workbench API 主路由 — 汇聚子路由 + 保留通用端点。"""

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import role_matches_any
from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.exceptions import AppError
from app.database import get_db
from app.workbench.schemas import (
    ChatResponse,
    WorkbenchCreateSkillFromDraftRequest,
    WorkbenchCreateSkillFromDraftResponse,
    WorkbenchGenerateDraftRequest,
    WorkbenchGenerateDraftResponse,
    WorkbenchReferenceListResponse,
)
from app.workbench.service import workbench_service
from app.workbench.router_support import check_skill_read_access

# ── 子路由汇聚 ──────────────────────────────────────────────
from app.workbench.router_session import router as session_router
from app.workbench.router_chat import router as chat_router
from app.workbench.router_patch import router as patch_router
from app.workbench.router_contract import router as contract_router

router = APIRouter()
router.include_router(session_router)
router.include_router(chat_router)
router.include_router(patch_router)
router.include_router(contract_router)


async def _check_skill_department(skill_id: str, current_user: User, db: AsyncSession):
    """加载 Skill 并校验部门权限，供需要 skill_id 的端点复用。"""
    await check_skill_read_access(skill_id, current_user, db)


# ═══════════════════════════════════════════════════════════════
# 草稿 / 模板 / 引用
# ═══════════════════════════════════════════════════════════════


@router.post("/workbench/generate-draft", response_model=WorkbenchGenerateDraftResponse)
async def generate_workbench_draft(
    body: WorkbenchGenerateDraftRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """从自然语言生成 Skill 初稿。"""
    return await workbench_service.generate_draft(message=body.message, references=body.references)


@router.post("/workbench/create-skill", response_model=WorkbenchCreateSkillFromDraftResponse)
async def create_skill_from_draft(
    body: WorkbenchCreateSkillFromDraftRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """从草稿创建真实 Skill。"""
    from app.common.telemetry import record_creation_step

    # v2.8.2 D4：漏斗末端 —— commit 到达
    result = await workbench_service.create_skill_from_draft(body.skill, current_user.id, body.skill_id)
    record_creation_step("commit", source="architect")
    return result


# ===== v7 G3: 任务模板库 =====


@router.get("/workbench/templates")
async def list_templates(
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """列出所有任务模板（dingtalk-daily-report / data-anomaly-alert / 等）。"""
    from app.templates.registry import list_templates_async

    return {"templates": await list_templates_async()}


@router.get("/workbench/templates/{template_id}")
async def get_template(
    template_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """获取单个模板的元数据。"""
    from app.templates.registry import get_template_async

    template = await get_template_async(template_id)
    if not template:
        raise AppError("TEMPLATE_NOT_FOUND", 404, {"template_id": template_id})
    return template


@router.get("/workbench/references", response_model=WorkbenchReferenceListResponse)
async def list_workbench_references(
    skill_id: str | None = None,
    query: str = "",
    source_type: str = "all",
    module: str = "all",
    reference_mode: str = "all",
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """列出跨 Skill / 工作流引用（按用户部门过滤）。"""
    return await workbench_service.list_references(
        skill_id=skill_id,
        query=query,
        user_department=current_user.department if not current_user.can_view_all else None,
        source_type=source_type,
        module=module,
        reference_mode=reference_mode,
    )


# ═══════════════════════════════════════════════════════════════
# Phase 2: AI Skill Architect — 采访式创建
# ═══════════════════════════════════════════════════════════════


class ArchitectStartRequest(BaseModel):
    description: str


class ArchitectNextRequest(BaseModel):
    current_round: str
    description: str
    answers: dict
    skill_draft: dict = {}


class ArchitectSynthesizeRequest(BaseModel):
    description: str
    answers: dict


class ArchitectAssessRequest(BaseModel):
    description: str


class ArchitectPrepareWorkspaceRequest(BaseModel):
    description: str
    department: str | None = None


class ArchitectFinalizeCreationRequest(BaseModel):
    skill_id: str
    name: str
    department: str
    risk_level: str = "R2"


@router.post("/architect/assess")
async def architect_assess(
    body: ArchitectAssessRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """v2.11.3：LLM 判定描述完整度，决定直接合成还是采访。

    返回 `{tier, recommended_path, skill_complexity, confidence,
            covered_dimensions, missing_dimensions, rationale, user_hint}`

    tier: complete / partial / insufficient
    recommended_path: direct_synthesize / short_interview / full_interview

    **失败即显式拒绝**，不走启发式兜底：
    - LLM 网关超时 → 503 ARCHITECT_ASSESS_LLM_FAILED
    - Prompt 加载失败 → 500 ARCHITECT_ASSESS_PROMPT_MISSING
    - LLM 返回解析失败 → 502 ARCHITECT_ASSESS_BAD_RESPONSE / PARSE_FAILED

    前端看到错误后应给用户两个手动按钮（采访 / 直接生成）。
    """
    from app.common.exceptions import AppError
    from app.common.rate_limiter import chat_limiter
    from app.workbench.architect import assess_description

    # 防打字频率过快烧 LLM 配额
    allowed, limit_info = await chat_limiter.check(f"architect-assess:{current_user.id}")
    if not allowed:
        raise AppError(
            "ARCHITECT_ASSESS_RATE_LIMITED", 429,
            detail={"retry_after": limit_info.get("retry_after", 5)},
        )

    result = await assess_description(body.description or "")
    return result.to_dict()


@router.post("/architect/prepare-workspace")
async def architect_prepare_workspace(
    body: ArchitectPrepareWorkspaceRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """v2.11.4：为 AIClaw Code 驱动的创建路径准备空工作区。

    返回 `{skill_id, work_dir}`，前端拿 skill_id 去开
    `WS /skills/{skill_id}/workbench/coding/stream` (mode=create)。

    实现：
    1. 分配 draft-{8hex} 格式的临时 skill_id
    2. 在 skills-repo/ 下 mkdir 空目录
    3. 可选：硬链接 SKILL_OUTPUT_CONTRACT.md 到工作目录（prompt 要求 agent Read 它）
    4. 插入 Skill DB 行 status='draft'，便于会话 session_pool 按 skill_id 定位

    发布阶段调 /architect/finalize-creation 把 status 改成 active。
    """
    import secrets
    from sqlalchemy import select

    from app.common.exceptions import AppError
    from app.common.rate_limiter import architect_prepare_limiter
    from app.config import settings
    from app.skills.core.models import Skill

    # 用户维度限流 5 req/min: 防止前端 bug / 恶意脚本秒内批量建 draft
    allowed, limit_info = await architect_prepare_limiter.check(current_user.id)
    if not allowed:
        raise AppError(
            "ARCHITECT_PREPARE_RATE_LIMITED", 429,
            detail={
                "retry_after": limit_info.get("retry_after", 30),
                "limit": limit_info.get("limit", 5),
                "window_seconds": 60,
            },
        )

    description = (body.description or "").strip()
    if len(description) < 10:
        raise AppError(
            "ARCHITECT_PREPARE_DESCRIPTION_TOO_SHORT", 422,
            detail={"reason": "description 至少 10 字"},
        )

    # 随机 draft id（不冲突重试 5 次）
    skill_id = ""
    for _ in range(5):
        candidate = f"draft-{secrets.token_hex(4)}"
        existing = (
            await db.execute(select(Skill).where(Skill.id == candidate))
        ).scalar_one_or_none()
        if existing is None:
            skill_id = candidate
            break
    if not skill_id:
        raise AppError("ARCHITECT_PREPARE_ID_ALLOC_FAILED", 500)

    dept = (body.department or getattr(current_user, "department", None) or "").strip() or "未指定"

    # 创建空工作目录
    repo_dir = settings.skill_repo_dir
    work_dir = (repo_dir / skill_id).resolve()
    if not str(work_dir).startswith(str(repo_dir.resolve())):
        raise AppError("ARCHITECT_PREPARE_INVALID_PATH", 500)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 把 SKILL_OUTPUT_CONTRACT.md 硬链到工作目录，prompt 里 agent 必须 Read 它
    # 源文件在 app/coding_agent/（与 coding agent 逻辑绑在一起）
    from pathlib import Path as _P
    contract_src = _P(__file__).resolve().parent.parent / "coding_agent" / "SKILL_OUTPUT_CONTRACT.md"
    contract_dst = work_dir / "SKILL_OUTPUT_CONTRACT.md"
    if not contract_src.exists():
        raise AppError(
            "ARCHITECT_PREPARE_CONTRACT_MISSING", 500,
            detail={"reason": f"SKILL_OUTPUT_CONTRACT.md 未找到：{contract_src}"},
        )
    if not contract_dst.exists():
        try:
            import os
            os.link(contract_src, contract_dst)
        except OSError:
            # 跨设备 / 无权限时 copy 即可
            contract_dst.write_text(contract_src.read_text(encoding="utf-8"), encoding="utf-8")

    # 插入占位 Skill 行
    skill_row = Skill(
        id=skill_id,
        name=f"草稿 {skill_id[-8:]}",
        description=description[:500],
        department=dept,
        role="分析师",
        trigger_type="manual",
        risk_level="R2",
        owner=current_user.id,
        status="draft",
        visibility="private",
    )
    db.add(skill_row)
    # 把用户自己加成 owner 成员，否则非 admin 读不到自己的 private draft
    from app.skills.core.access import SkillMember
    db.add(SkillMember(
        skill_id=skill_id,
        user_id=current_user.id,
        role="owner",
        granted_by=current_user.id,
    ))
    await db.commit()

    from app.common.audit import audit
    try:
        await audit.log(
            user_id=current_user.id,
            action="architect.prepare_workspace",
            target_type="skill",
            target_id=skill_id,
            detail={"description_preview": description[:200]},
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        # 同一个值,两个字段并存: skill_id 为历史字段,新代码用 draft_id
        # (skill_drafts 主键叫 draft_id,此处的值也会作为临时 skill 行 id)
        "skill_id": skill_id,
        "draft_id": skill_id,
        "work_dir": str(work_dir),
        "department": dept,
    }


@router.post("/architect/finalize-creation")
async def architect_finalize_creation(
    body: ArchitectFinalizeCreationRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """v2.11.4：把 draft 态 Skill 转为正式发布。

    校验：
    - skill_id 存在且 status='draft' 且 owner == current_user
    - skills-repo/{id}/SKILL.md 文件存在（agent 必须写过）

    动作：
    - 更新 Skill 行：status='active'，覆盖 name/department/risk_level
    - git add + commit（通过 sync_service）
    - 通知 OpenClaw reload（异步）
    """
    from sqlalchemy import select

    from app.common.audit import audit
    from app.common.exceptions import AppError
    from app.config import settings
    from app.skills.core.models import Skill

    skill_id = body.skill_id.strip()
    if not skill_id.startswith("draft-"):
        raise AppError(
            "ARCHITECT_FINALIZE_INVALID_ID", 422,
            detail={"reason": "仅支持 draft-* 草稿 skill_id"},
        )

    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id))
    ).scalar_one_or_none()
    if skill is None:
        raise AppError("SKILL_NOT_FOUND", 404)
    if skill.owner != current_user.id and current_user.role not in ("admin", "system_admin"):
        raise AppError("SKILL_ACCESS_DENIED", 403)
    if skill.status != "draft":
        raise AppError(
            "ARCHITECT_FINALIZE_BAD_STATUS", 409,
            detail={"reason": f"Skill 当前状态 {skill.status}，不是 draft"},
        )

    # 部门 / 风险等级再校验一次（与 create_skill 口径一致）
    dept_clean = (body.department or "").strip()
    if not dept_clean or dept_clean == "未指定":
        raise AppError("PARAM_INVALID", 422, {"detail": "部门不能为空或'未指定'"})
    if body.risk_level not in ("R1", "R2", "R3", "R4"):
        raise AppError("PARAM_INVALID", 422, {"detail": f"风险等级非法: {body.risk_level!r}"})

    # SKILL.md 必须存在
    work_dir = (settings.skill_repo_dir / skill_id).resolve()
    skill_md_path = work_dir / "SKILL.md"
    if not skill_md_path.exists():
        raise AppError(
            "ARCHITECT_FINALIZE_NO_SKILL_MD", 422,
            detail={"reason": "AI 尚未写出 SKILL.md，不能发布"},
        )

    name_clean = (body.name or "").strip() or skill.name
    skill.name = name_clean[:100]
    skill.department = dept_clean
    skill.risk_level = body.risk_level
    skill.status = "active"
    await db.commit()

    # git commit + OpenClaw reload（尽最大努力，不阻塞主流程）
    try:
        from app.execution.sync_service import sync_service
        await sync_service.commit_and_sync(
            skill_id=skill_id,
            message=f"feat({skill_id}): 新建 Skill via coding agent",
            author=current_user.id,
            actor=current_user,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("finalize-creation git commit / sync 失败（不阻塞发布）: {}", e)

    try:
        await audit.log(
            user_id=current_user.id,
            action="architect.finalize_creation",
            target_type="skill",
            target_id=skill_id,
            detail={"name": skill.name, "department": dept_clean, "risk_level": body.risk_level},
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "skill_id": skill_id,
        "status": "active",
        "name": skill.name,
        "department": skill.department,
        "risk_level": skill.risk_level,
    }


@router.post("/architect/start")
async def architect_start(
    body: ArchitectStartRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """启动采访：输入业务描述，生成 Round 1 问题。"""
    from app.common.telemetry import record_creation_step
    from app.workbench.architect import start_interview

    # v2.8.2 D4：创建漏斗埋点 —— describe 步骤到达
    record_creation_step("describe", source="architect")
    round_data = await start_interview(body.description)
    record_creation_step("interview", source="architect")
    return round_data.to_dict()


@router.post("/architect/next")
async def architect_next(
    body: ArchitectNextRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
):
    """进入下一轮采访。"""
    from app.workbench.architect import next_round, InterviewSession
    session = InterviewSession(
        description=body.description,
        answers=body.answers,
        skill_draft=body.skill_draft,
    )
    next_data = await next_round(body.current_round, session)
    if not next_data:
        return {"done": True, "message": "采访结束，可以合成 Skill 了"}
    return next_data.to_dict()


@router.post("/architect/synthesize")
async def architect_synthesize(
    body: ArchitectSynthesizeRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """从采访答案合成完整 Skill 骨架 + 质检报告。"""
    from app.common.telemetry import record_creation_step
    from app.workbench.skill_pipeline import run_pipeline

    record_creation_step("synthesize", source="architect")
    result = await run_pipeline(db, body.description, body.answers, include_ai_verify=False)
    if result.skill and "_error" not in result.skill:
        record_creation_step("preview", source="architect")
    return result.to_dict()


# ═══════════════════════════════════════════════════════════════
# v2.9.1 H1 · Architect 流式骨架生成 WebSocket
#
# 对比现有：
#   - /architect/synthesize           : block HTTP，整个 pipeline 跑完才返
#   - /architect/progress/{draft_id}  : 轮询（v2.8.2 stub）
#   - 本端点                           : WS 实时 push 阶段事件（similar / synthesize / lint / done）
#
# 协议：
#   client → { description: "...", answers: {...}, mode: "basic"|"swarm" }
#   server → { type: "phase", phase: "similar_search",  message: "..." }
#   server → { type: "phase", phase: "synthesize",      message: "..." }
#   server → { type: "phase", phase: "lint",            message: "..." }
#   server → { type: "done",  result: { skill, lint_report, can_publish, ... } }
#   server → { type: "error", message: "..." }
# ═══════════════════════════════════════════════════════════════
from fastapi import WebSocket, WebSocketDisconnect
from contextlib import suppress as _suppress

from app.common.ws_auth import (
    WS_CODE_AUTH_REQUIRED,
    WS_CODE_OVERFLOW,
    WS_CODE_PERMISSION_DENIED,
    get_current_user_ws,
)
from app.common.ws_session import (
    spawn_json_heartbeat,
    spawn_ping_pong_listener,
    user_ws_session_limiter,
)


@router.websocket("/architect/stream")
async def architect_stream(websocket: WebSocket):
    """v2.9.1 H1：Architect/Swarm 流式骨架生成。"""
    await websocket.accept()

    user = await get_current_user_ws(websocket)
    if not user:
        await websocket.close(code=WS_CODE_AUTH_REQUIRED, reason="unauthorized")
        return
    if not role_matches_any(user, ("admin", "ai_engineer", "aibp")):
        await websocket.close(code=WS_CODE_PERMISSION_DENIED, reason="permission denied")
        return

    import asyncio as _asyncio

    async def _receive_first_payload() -> dict:
        deadline = _asyncio.get_running_loop().time() + 30
        while True:
            remaining = deadline - _asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _asyncio.TimeoutError
            msg = await _asyncio.wait_for(websocket.receive_json(), timeout=remaining)
            if isinstance(msg, dict) and str(msg.get("type") or "").lower() == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if not isinstance(msg, dict):
                raise ValueError("invalid payload")
            return msg

    try:
        payload = await _receive_first_payload()
    except _asyncio.TimeoutError:
        with _suppress(Exception):
            await websocket.send_json({"type": "error", "message": "首帧超时"})
            await websocket.close(code=1003)
        return
    except Exception:
        with _suppress(Exception):
            await websocket.close(code=1003, reason="invalid payload")
        return

    description = str(payload.get("description") or "").strip()
    answers = payload.get("answers") or {}
    mode = str(payload.get("mode") or "basic").lower()

    if not description:
        await websocket.send_json({"type": "error", "message": "description 不能为空"})
        await websocket.close(code=1003)
        return
    if len(description) > 50000:
        await websocket.send_json({"type": "error", "message": "description 不能超过 50000 字"})
        await websocket.close(code=1003)
        return

    from app.database import async_session_factory
    from app.common.audit import audit
    from app.common.rate_limiter import chat_limiter
    from app.common.telemetry import record_creation_step
    from app.skills.lint import lint_skill
    from app.workbench.architect import synthesize_skill_from_interview
    from app.workbench.retrieval import find_similar_skills
    from app.workbench.skill_pipeline import _dict_to_structured

    allowed, limit_info = await chat_limiter.check(f"architect-stream:{user.id}")
    if not allowed:
        await websocket.send_json({
            "type": "error",
            "message": f"请求过快，请 {limit_info['retry_after']} 秒后重试",
        })
        await websocket.close(code=1008)
        return

    lease = await user_ws_session_limiter.acquire("architect-stream", user.id)
    if not lease.acquired:
        await websocket.send_json({
            "type": "error",
            "message": f"该账号已有 {lease.count} 个 architect 流式任务正在运行，请先关闭旧连接",
        })
        await websocket.close(code=WS_CODE_OVERFLOW, reason="too many architect streams")
        return

    async def _send(event: dict) -> None:
        with _suppress(Exception):
            await websocket.send_json(event)

    heartbeat_task = spawn_json_heartbeat(websocket, lease=lease)
    control_task = spawn_ping_pong_listener(websocket)
    try:
        # Phase 1: 相似 Skill 检索
        await _send({"type": "phase", "phase": "similar_search", "message": "三重检索找相似 Skill..."})
        similar_list: list[dict] = []
        try:
            async with async_session_factory() as _db:
                similar = await find_similar_skills(_db, description, limit=3)
                similar_list = [s.to_dict() for s in similar]
        except Exception as e:  # noqa: BLE001
            logger.warning("WS stream similar_search 失败: {}", e)
        await _send({
            "type": "phase", "phase": "similar_ready",
            "message": f"找到 {len(similar_list)} 条相似 Skill",
            "similar_skills": similar_list,
        })

        # Phase 2: 合成骨架
        await _send({
            "type": "phase", "phase": "synthesize",
            "message": "Swarm 4-Agent 并行生成中..." if mode == "swarm" else "AI 合成骨架中...",
        })
        record_creation_step("synthesize", source=f"ws_{mode}")

        if mode == "swarm":
            from app.workbench.swarm import run_swarm
            async with async_session_factory() as _db:
                gen = await run_swarm(_db, description, answers)
            skill_dict = gen.skill
            lint_dict = gen.lint_report or {}
        else:
            skill_dict = await synthesize_skill_from_interview(description, answers)
            lint_dict = {}

        if not skill_dict or not isinstance(skill_dict, dict) or not skill_dict.get("meta"):
            await _send({
                "type": "error",
                "message": "LLM 未返回有效 JSON；建议换个 mode 或修改描述重试",
                "raw_skill": skill_dict,
            })
            await websocket.close(code=1011)
            return

        await _send({
            "type": "phase", "phase": "synthesize_ready",
            "message": "骨架生成完成",
            "skill": skill_dict,
        })

        # Phase 3: Lint（basic mode 可选；swarm mode 已做过）
        if mode != "swarm":
            await _send({"type": "phase", "phase": "lint", "message": "静态检查..."})
            try:
                structured = _dict_to_structured(skill_dict)
                lint_report = lint_skill("(new)", structured)
                lint_dict = lint_report.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.warning("WS stream lint 失败: {}", e)
                lint_dict = {"passed": False, "error": str(e)[:200]}

        lint_passed = bool(lint_dict.get("passed", False))
        record_creation_step("preview", source=f"ws_{mode}")

        # Phase 4: 完成
        try:
            await audit.log(
                user_id=user.id,
                action="workbench.architect_stream",
                target_type="skill_draft",
                target_id=mode,
                detail={
                    "mode": mode,
                    "description_length": len(description),
                    "similar_count": len(similar_list),
                    "lint_passed": lint_passed,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("architect/stream audit log 失败: {}", exc)
        await _send({
            "type": "done",
            "result": {
                "skill": skill_dict,
                "similar_skills": similar_list,
                "lint_report": lint_dict,
                "can_publish": lint_passed,
                "summary": "骨架已生成并通过质检" if lint_passed else "骨架已生成，有待修复问题",
            },
        })
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("architect/stream 异常")
        await _send({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
    finally:
        heartbeat_task.cancel()
        control_task.cancel()
        with _suppress(BaseException):
            await heartbeat_task
        with _suppress(BaseException):
            await control_task
        await lease.release()
        with _suppress(Exception):
            await websocket.close()


# v2.8.2 C4：契约驱动创建 —— 用户先写 task-contract（输入/输出/触发时机），
# 系统根据契约生成骨架 + 测试用例。跟 architect 采访互补：
#   architect = 我说一段需求，AI 问我细节
#   contract-first = 我写清楚接口，AI 补决策规则和反例
class ContractFirstRequest(BaseModel):
    task_contract: dict  # { meta, trigger, input_schema, output_schema, constraints... }


@router.post("/architect/from-contract")
async def architect_from_contract(
    body: ContractFirstRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """v2.8.2 C4：以 task-contract.json 作为输入，生成 Skill 骨架。

    contract 的必填字段：
    - meta.name / meta.department
    - trigger.type + trigger.expression
    - input.schema / output.schema

    系统基于这些生成决策规则（rules）+ 测试用例（test_cases）+ 反例。
    """
    from app.common.ai import call_llm_with_retry
    from app.common.telemetry import record_creation_step

    contract = body.task_contract or {}
    if not contract.get("meta") or not contract.get("input"):
        raise AppError(
            "PARAM_INVALID", 400,
            {"detail": "task_contract 必须含 meta 和 input 字段"},
        )

    record_creation_step("synthesize", source="contract_first")

    # 构造 prompt：把 contract 作为输入，要 LLM 生成 rules / test_cases / antipatterns
    import json as _json

    synth_prompt = (
        "基于用户给定的 task-contract（输入输出 + 触发时机明确），生成 Skill 骨架 JSON。\n\n"
        "# task_contract\n"
        f"{_json.dumps(contract, ensure_ascii=False, indent=2)}\n\n"
        "# 要求\n"
        "1. 基于 input schema 推断合理的决策分支\n"
        "2. 每个 step 必须有兜底分支\n"
        "3. 至少 3 个测试用例覆盖主要分支 + 边界\n"
        "4. 至少 2 个反例（误判场景）\n"
        "5. 输出格式同 /architect/synthesize（meta/goal/rules/params/output_table/test_cases/antipatterns）"
    )

    resp = await call_llm_with_retry(
        system="你是 SkillForge 的契约驱动骨架生成器，严格 JSON。",
        user=synth_prompt,
        max_tokens=3000,
        temperature=0.3,
        json_mode=True,
        description="contract_first_synthesize",
    )

    if not isinstance(resp, dict) or not resp.get("meta"):
        return {
            "skill": {"_error": {"type": "LLMInvalidResponse", "message": "LLM 未返回有效 JSON"}},
            "can_publish": False,
            "summary": "契约驱动生成失败，请重试或换 architect 采访模式",
        }

    record_creation_step("preview", source="contract_first")
    return {
        "skill": resp,
        "lint_report": {},
        "verify_report": {},
        "can_publish": True,
        "summary": "契约驱动骨架已生成",
    }


# v2.8.2 C1：流式创建进度 —— 目前以 SSE-friendly 的分阶段 response 实现，
# 真正的 WebSocket 流式留给 v2.9（需改 swarm 内部 agent 的中间结果发布机制）。
@router.get("/architect/progress/{draft_id}")
async def architect_progress(
    draft_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """轮询查询创建进度。前端 wizard 调用间隔 1-2s，直到 status='done'。

    返回 `{status, phase, message, result?}`：
    - status: pending | running | done | failed
    - phase: similar_search | synthesize | lint | verify
    """
    from sqlalchemy import select

    from app.workbench.models import SkillStudioDraft

    row = (
        await db.execute(
            select(SkillStudioDraft)
            .where(SkillStudioDraft.id == draft_id)
            .where(SkillStudioDraft.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if not row:
        raise AppError("DRAFT_NOT_FOUND", 404)

    gen_status = row.generation_status  # pending / running / ready / failed
    if gen_status == "failed":
        return {
            "status": "failed",
            "phase": "error",
            "message": str((row.error_detail or {}).get("message") or "unknown"),
        }
    if gen_status == "pending":
        return {
            "status": "pending",
            "phase": "queue",
            "message": "任务排队中，等待空闲槽位...",
        }
    if gen_status == "ready":
        return {
            "status": "done",
            "phase": "done",
            "message": "骨架已就绪，可以预览",
        }
    return {
        "status": "running",
        "phase": "synthesize",
        "message": "AI 合成中，请稍候...",
    }


@router.post("/architect/swarm-generate")
async def architect_swarm_generate(
    body: ArchitectSynthesizeRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """Swarm 4-Agent 并行协作生成（Explorer/Rules/Tests/Verifier + Leader 合并）。"""
    from app.common.telemetry import record_creation_step
    from app.workbench.swarm import run_swarm

    record_creation_step("synthesize", source="swarm")
    result = await run_swarm(db, body.description, body.answers)
    if result.skill and "_error" not in result.skill:
        record_creation_step("preview", source="swarm")
    return result.to_dict()


@router.post("/architect/find-similar")
async def architect_find_similar(
    body: ArchitectStartRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """三重检索：找相似的 Skill 作为参考。"""
    from app.workbench.retrieval import find_similar_skills
    similar = await find_similar_skills(db, body.description, limit=5)
    return {"items": [s.to_dict() for s in similar]}


# ═══════════════════════════════════════════════════════════════
# Phase 2: AI Change Coach — 事件驱动建议
# ═══════════════════════════════════════════════════════════════

class CoachEventRequest(BaseModel):
    event: str                       # branch_changed/param_changed/open_editor 等
    context: dict = {}


@router.post("/{skill_id}/coach/event")
async def coach_event(
    skill_id: str,
    body: CoachEventRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """Change Coach: 编辑事件触发建议。"""
    await _check_skill_department(skill_id, current_user, db)
    from app.workbench import coach_service

    return await coach_service.run_coach_event(
        skill_id=skill_id,
        event=body.event,
        context=body.context,
        db=db,
    )


class CoachAcceptedRequest(BaseModel):
    action: str


@router.post("/{skill_id}/coach/accepted")
async def coach_accepted(
    skill_id: str,
    body: CoachAcceptedRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """Coach 建议采纳埋点（用户点击动作按钮时上报）。"""
    await _check_skill_department(skill_id, current_user, db)
    from app.workbench import coach_service

    return coach_service.record_coach_accepted(body.action)


class EditDurationRequest(BaseModel):
    seconds: float


@router.post("/{skill_id}/telemetry/edit-duration")
async def record_edit_duration_endpoint(
    skill_id: str,
    body: EditDurationRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """编辑完成时长埋点。"""
    await _check_skill_department(skill_id, current_user, db)
    from app.common.telemetry import record_edit_duration
    record_edit_duration(body.seconds, skill_id=skill_id)
    return {"ok": True}


class CoverageDeltaRequest(BaseModel):
    old_coverage: float
    new_coverage: float


@router.post("/{skill_id}/telemetry/coverage-delta")
async def record_coverage_delta_endpoint(
    skill_id: str,
    body: CoverageDeltaRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """覆盖率提升埋点。"""
    await _check_skill_department(skill_id, current_user, db)
    from app.common.telemetry import record_coverage_delta
    record_coverage_delta(body.old_coverage, body.new_coverage, skill_id=skill_id)
    return {"ok": True}
