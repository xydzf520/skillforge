"""Playbook管理API路由"""

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import cache_delete_pattern, cache_get, cache_set
from app.config import settings
from app.database import get_db
from app.playbooks import service
from app.playbooks.executor import playbook_executor

router = APIRouter()


class SavePlaybookRequest(BaseModel):
    """保存Playbook的请求体"""
    name: str | None = None
    description: str | None = None
    department: str | None = None
    trigger: dict | None = None
    sla_minutes: int | None = None
    steps: list[dict]
    # 允许额外字段（如canvas布局数据）
    model_config = {"extra": "allow"}


class ValidatePlaybookRequest(BaseModel):
    """校验Playbook的请求体"""
    name: str | None = None
    steps: list[dict] = []
    model_config = {"extra": "allow"}


class RunPlaybookRequest(BaseModel):
    """执行Playbook的请求体"""
    params: dict = Field(default_factory=dict)
    sandbox: bool = False


@router.get("/dependency-graph")
async def dependency_graph(
    skill_id: str | None = Query(None, description="W1-D: 只返与该 skill 相关的 nodes+edges"),
    current_user: User = Depends(require_state_active),
):
    """获取 Playbook 的 Skill 依赖关系图数据（React Flow 格式）。

    W1-D：加 ?skill_id=X 参数。命中时仅返回 inbound（此 skill 被哪些 playbook 用到）+
    siblings（同 playbook 的其他 skill），让 Studio 的「依赖图」tab 加载快得多（O(相关 playbook) 而非 O(全部)）。
    不传参数时保持旧行为（返回全图，React Flow 画布用）。
    """
    playbooks = await service.list_playbooks()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for pb in playbooks:
        pb_name = pb.get("name", pb.get("file_name", ""))
        try:
            full_pb = await service.get_playbook(pb.get("file_name", pb_name))
        except Exception:
            continue

        steps = full_pb.get("steps", [])
        # W1-D: skill_id 过滤 —— 只处理包含该 skill 的 playbook
        if skill_id is not None:
            if not any(step.get("skill", step.get("id", "")) == skill_id for step in steps):
                continue

        for step in steps:
            sid = step.get("skill", step.get("id", ""))
            if sid and sid not in nodes:
                nodes[sid] = {
                    "id": sid,
                    "type": "skill",
                    "data": {"label": sid, "playbooks": []},
                    "position": {"x": len(nodes) * 200, "y": 0},
                }
            if sid:
                nodes[sid]["data"]["playbooks"].append(pb_name)

            for dep in step.get("depends_on", []):
                dep_skill = dep
                for s2 in steps:
                    if s2.get("id") == dep:
                        dep_skill = s2.get("skill", dep)
                        break
                edge_id = f"{dep_skill}->{sid}"
                edges.append({
                    "id": edge_id,
                    "source": dep_skill,
                    "target": sid,
                    "label": pb_name,
                })

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
    }


@router.get("/templates")
async def list_templates(
    current_user: User = Depends(require_state_active),
):
    """列出可用的Playbook模板"""
    from app.playbooks.service import list_templates as _list_templates
    return await _list_templates()


class DesignPlaybookRequest(BaseModel):
    """Playbook Designer 请求体"""
    goal: str = Field(..., min_length=4, max_length=1000)


@router.post("/design")
async def design_playbook_endpoint(
    body: DesignPlaybookRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    """§7.3 AI Playbook Designer — 根据业务目标推荐 Playbook 编排。"""
    from app.playbooks.designer import design_playbook
    # F4: 传入调用方 user/部门，便于 LLM 成本归因
    design = await design_playbook(
        db,
        body.goal,
        user_id=current_user.id,
        department=current_user.department,
    )
    return design.to_dict()


@router.get("/")
async def list_playbooks(
    current_user: User = Depends(require_state_active),
):
    """列出所有Playbook"""
    cache_key = "playbooks:list"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据源: {}", e)

    result = await service.list_playbooks()

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_PLAYBOOK)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


@router.post("/{name}/run")
async def run_playbook(
    name: str,
    body: RunPlaybookRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """执行Playbook（按DAG顺序调度所有Skill步骤）"""
    return await playbook_executor.run(
        playbook_name=name,
        params=body.params,
        sandbox=body.sandbox,
        triggered_by=f"manual:{current_user.id}",
    )


@router.get("/{name}")
async def get_playbook(
    name: str,
    current_user: User = Depends(require_state_active),
):
    """获取单个Playbook详情"""
    cache_key = f"playbooks:detail:{name}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据源: {}", e)

    result = await service.get_playbook(name)

    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_PLAYBOOK)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)

    return result


@router.post("/")
async def create_playbook(
    body: SavePlaybookRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """新建Playbook"""
    # 文件名取name字段，清理后作为文件名
    name = body.name or "untitled"
    data = body.model_dump(exclude_none=False)
    result = await service.save_playbook(name, data)
    try:
        await cache_delete_pattern("playbooks:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.put("/{name}")
async def update_playbook(
    name: str,
    body: SavePlaybookRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """更新Playbook"""
    data = body.model_dump(exclude_none=False)
    result = await service.save_playbook(name, data)
    try:
        await cache_delete_pattern("playbooks:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.delete("/{name}")
async def delete_playbook(
    name: str,
    current_user: User = Depends(require_role("admin")),
):
    """删除Playbook（仅admin）"""
    result = await service.delete_playbook(name)
    try:
        await cache_delete_pattern("playbooks:*")
    except Exception as e:
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{name}/publish")
async def publish_playbook(
    name: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
):
    """提交Playbook审核：校验后创建审核记录"""
    from app.playbooks.service import get_playbook, validate_playbook

    data = await get_playbook(name)

    # 先校验
    validation = await validate_playbook(data)
    if not validation["valid"]:
        from app.common.exceptions import AppError
        raise AppError("PLAYBOOK_INVALID", 400, detail={"errors": validation["errors"]})

    # 创建审核记录（复用现有 review 模块）
    from app.database import async_session_factory
    from app.reviews.service import create_review

    async with async_session_factory() as session:
        try:
            review = await create_review(
                db=session,
                skill_id=f"playbook:{name}",
                submitter=current_user.id,
                change_type="new_skill",
                reason=f"Playbook [{data.get('name', name)}] 提交审核",
                diff_summary=f"Playbook {name}: {len(data.get('steps', []))} 个步骤",
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return {"message": "已提交审核", "review_id": review.get("review_id")}


@router.get("/{name}/mermaid")
async def get_playbook_mermaid(
    name: str,
    current_user: User = Depends(require_state_active),
):
    """获取 Playbook 的 Mermaid 流程图语法"""
    from app.common.mermaid import playbook_to_mermaid
    data = await service.get_playbook(name)
    mermaid_code = playbook_to_mermaid(data)
    return {"mermaid": mermaid_code}


@router.post("/{name}/validate")
async def validate_playbook(
    name: str,
    current_user: User = Depends(require_state_active),
):
    """校验已存在的Playbook结构"""
    data = await service.get_playbook(name)
    return await service.validate_playbook(data)


@router.post("/validate")
async def validate_playbook_data(
    body: ValidatePlaybookRequest,
    current_user: User = Depends(require_state_active),
):
    """校验提交的Playbook数据（无需先保存）"""
    data = body.model_dump(exclude_none=False)
    return await service.validate_playbook(data)


    # 依赖图已在上方 dependency_graph() 实现（React Flow 格式）
