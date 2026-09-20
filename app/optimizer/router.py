"""Optimizer API 端点"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.database import get_db
from app.optimizer.schemas import CreateSessionRequest, CreateBenchmarkPackRequest

router = APIRouter(prefix="/optimizer", tags=["优化器"])


# ── 会话 ──

@router.post("/sessions")
async def create_session(
    skill_id: str,
    req: CreateSessionRequest,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建优化会话"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.create_session(
        skill_id=skill_id, name=req.name, goal=req.goal,
        config={
            "editable_zones": req.editable_zones,
            "frozen_zones": req.frozen_zones,
            "budget": {
                "max_iterations": req.max_iterations,
                "max_tokens": req.max_tokens,
                "max_sandbox_runs": req.max_sandbox_runs,
            },
        },
        benchmark_pack_id=req.benchmark_pack_id,
        user_id=user.id, db=db,
    )


@router.get("/sessions")
async def list_sessions(
    skill_id: str | None = None,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出优化会话"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.list_sessions(skill_id=skill_id, db=db)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取会话详情"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.get_session(session_id=session_id, db=db)


@router.post("/sessions/{session_id}/start")
async def start_session(
    session_id: str,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """启动优化循环"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.start(session_id=session_id, db=db)


@router.post("/sessions/{session_id}/pause")
async def pause_session(
    session_id: str,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """暂停优化"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.pause(session_id=session_id, db=db)


# ── 候选 ──

@router.get("/sessions/{session_id}/candidates")
async def list_candidates(
    session_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出候选版本"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.list_candidates(session_id=session_id, db=db)


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取候选详情"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.get_candidate(candidate_id=candidate_id, db=db)


@router.post("/candidates/{candidate_id}/promote")
async def promote_candidate(
    candidate_id: str,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """晋升候选版本（写入 git + 提交审核）"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.promote(candidate_id=candidate_id, user_id=user.id, db=db)


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str,
    reason: str = "",
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """拒绝候选版本"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.reject_candidate(
        candidate_id=candidate_id, reason=reason, db=db,
    )


# ── 评测包 ──

@router.post("/benchmark-packs")
async def create_benchmark_pack(
    skill_id: str,
    req: CreateBenchmarkPackRequest,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建评测包"""
    from app.optimizer.benchmark_builder import benchmark_builder
    return await benchmark_builder.build(
        skill_id=skill_id, name=req.name, source=req.source,
        description=req.description, max_cases=req.max_cases,
        min_rating=req.min_rating, user_id=user.id, db=db,
    )


@router.get("/benchmark-packs/{pack_id}")
async def get_benchmark_pack(
    pack_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取评测包详情"""
    from app.optimizer.benchmark_builder import benchmark_builder
    return await benchmark_builder.get_pack(pack_id=pack_id, db=db)


@router.post("/benchmark-packs/{pack_id}/freeze")
async def freeze_benchmark_pack(
    pack_id: str,
    user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """冻结评测包"""
    from app.optimizer.benchmark_builder import benchmark_builder
    return await benchmark_builder.freeze(pack_id=pack_id, db=db)


@router.get("/benchmark-runs/{run_id}")
async def get_benchmark_run(
    run_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取评测运行结果"""
    from app.optimizer.benchmark_runner import benchmark_runner
    return await benchmark_runner.get_run(run_id=run_id, db=db)


# ── 报告 + Shadow ──

@router.get("/sessions/{session_id}/report")
async def get_report(
    session_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取优化报告"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.get_report(session_id=session_id, db=db)


@router.post("/candidates/{candidate_id}/check-shadow")
async def check_shadow(
    candidate_id: str,
    user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """检查候选版本的 shadow 状态"""
    from app.optimizer.controller import optimizer_controller
    return await optimizer_controller.check_shadow_and_auto_promote(
        candidate_id=candidate_id, db=db,
    )


@router.get("/sessions/{session_id}/live-status")
async def live_status(
    session_id: str,
    user: User = Depends(require_state_active),
):
    """实时状态（从 Redis 缓存读取，低延迟）"""
    try:
        from app.common.cache import cache_get
        data = await cache_get(f"optimizer:session:{session_id}:status")
        if data:
            return data
    except Exception as e:
        from loguru import logger
        logger.debug("optimizer status 缓存读失败 session={}: {}", session_id, e)
    return {"status": "no_cache"}
