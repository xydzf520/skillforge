"""Skill 质量与校验路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import invalidate_skill
from app.common.exceptions import AppError
from app.database import get_db
from app.skills.lifecycle import service
from app.skills.tooling import validation_service
from app.skills.core.service_shared import ensure_skill_access

router = APIRouter()


class CompareParamsRequest(BaseModel):
    new_params: dict


class UpdateParamsRequest(BaseModel):
    params: dict


class ValidateBlockRequest(BaseModel):
    block_type: str
    content: dict | list


class ValidateRuleRequest(BaseModel):
    """L3-C 规则抽屉单条规则校验请求。"""
    step_id: str
    condition: str = ""
    verdict: str = ""
    action: str = ""
    next_step: str | None = None


_MODULE_TO_BLOCK_TYPE = {
    "meta": "frontmatter",
    "goal": "purpose",
    "rules": "steps",
    "params": "params",
    "output_table": "output_definition",
    "antipatterns": "antipatterns",
    "data_inputs": "data_inputs",
    "test_cases": "test_cases",
}


def _extract_module_payload(data: dict, module_name: str):
    parsed = data.get("parsed") or {}
    if module_name == "meta":
        return parsed.get("frontmatter") or {}
    if module_name == "goal":
        return parsed.get("purpose") or ""
    if module_name == "rules":
        return parsed.get("steps") or []
    if module_name == "params":
        policy_pack = data.get("policy_pack") or {}
        return [{"name": key, "default_value": value} for key, value in policy_pack.items()]
    if module_name == "output_table":
        return parsed.get("output_definition") or []
    if module_name == "antipatterns":
        return parsed.get("antipatterns") or []
    if module_name == "data_inputs":
        return parsed.get("data_inputs") or []
    if module_name == "test_cases":
        return parsed.get("test_cases") or []
    raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的 module: {module_name}"})


def _build_structured_kwargs_for_module(module_name: str, content):
    if module_name == "meta":
        return {"frontmatter": content if isinstance(content, dict) else {}}
    if module_name == "goal":
        if isinstance(content, dict):
            return {"purpose": content.get("text", "")}
        return {"purpose": str(content or "")}
    if module_name == "rules":
        return {"steps": content if isinstance(content, list) else []}
    if module_name == "params":
        items = content if isinstance(content, list) else []
        policy_pack = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if "default_value" in item:
                policy_pack[name] = item.get("default_value")
            else:
                policy_pack[name] = item.get("default_value_str")
        return {"policy_pack": policy_pack}
    if module_name == "output_table":
        return {"output_definition": content if isinstance(content, list) else []}
    if module_name == "antipatterns":
        return {"antipatterns": content if isinstance(content, list) else []}
    if module_name == "data_inputs":
        return {"data_inputs": content if isinstance(content, list) else []}
    if module_name == "test_cases":
        return {"test_cases": content if isinstance(content, list) else []}
    raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的 module: {module_name}"})


@router.get("/{skill_id}/drift-check")
async def drift_check(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await validation_service.check_data_drift(db, skill_id)


@router.get("/{skill_id}/history")
async def get_history(
    skill_id: str,
    max_count: int = Query(20, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await service.get_skill_history(skill_id, max_count)


@router.get("/{skill_id}/diff")
async def get_diff(
    skill_id: str,
    commit_a: str = Query("HEAD~1"),
    commit_b: str = Query("HEAD"),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return {"diff": await service.get_skill_diff(skill_id, commit_a, commit_b)}


@router.post("/{skill_id}/compare-params")
async def compare_params(
    skill_id: str,
    body: CompareParamsRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await validation_service.compare_params(db, skill_id, body.new_params)


@router.put("/{skill_id}/params")
async def update_params(
    skill_id: str,
    body: UpdateParamsRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")
    result = await service.update_params(db, skill_id, body.params, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/{skill_id}/modules/{module_name}")
async def read_skill_module(
    skill_id: str,
    module_name: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    return {"skill_id": skill_id, "module": module_name, "data": _extract_module_payload(data, module_name)}


@router.post("/{skill_id}/modules/{module_name}/validate")
async def validate_skill_module(
    skill_id: str,
    module_name: str,
    body: dict,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "read")
    block_type = _MODULE_TO_BLOCK_TYPE.get(module_name)
    if not block_type:
        raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的 module: {module_name}"})
    return await validation_service.validate_block(db, skill_id, block_type, body.get("content"))


@router.post("/{skill_id}/modules/{module_name}/apply")
async def apply_skill_module_patch(
    skill_id: str,
    module_name: str,
    body: dict,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_skill(db, skill_id)
    await ensure_skill_access(db, skill_id, current_user, "edit")
    kwargs = _build_structured_kwargs_for_module(module_name, body.get("content"))
    result = await service.save_skill_structured(db, skill_id=skill_id, user_id=current_user.id, **kwargs)
    if not result.get("noop"):
        try:
            await invalidate_skill(skill_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{skill_id}/validate-block")
async def validate_block(
    skill_id: str,
    body: ValidateBlockRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await validation_service.validate_block(db, skill_id, body.block_type, body.content)


@router.post("/{skill_id}/validate-rule")
async def validate_skill_rule(
    skill_id: str,
    body: ValidateRuleRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """L3-C 规则抽屉单条规则后端校验。

    校验单条规则（condition + verdict + action + next_step）相对于
    当前决策树结构的合法性，返回 {valid, errors, warnings, hints}。
    """
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await validation_service.validate_rule(
        db,
        skill_id,
        step_id=body.step_id,
        condition=body.condition,
        verdict=body.verdict,
        action=body.action,
        next_step=body.next_step,
    )


@router.post("/{skill_id}/validate")
@router.post("/{skill_id}/validate-all")
async def validate_skill(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await validation_service.validate_skill(db, skill_id)


@router.get("/{skill_id}/guardian/anomalies")
async def guardian_anomalies_endpoint(
    skill_id: str,
    window_hours: int = 24,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.guardian import detect_anomalies

    anomalies = await detect_anomalies(db, skill_id, window_hours=window_hours)
    return {"skill_id": skill_id, "anomaly_count": len(anomalies), "anomalies": [item.to_dict() for item in anomalies]}


@router.post("/{skill_id}/guardian/root-cause")
async def guardian_root_cause_endpoint(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.guardian import analyze_root_cause, detect_anomalies

    anomalies = await detect_anomalies(db, skill_id)
    if not anomalies:
        return {"skill_id": skill_id, "reports": [], "message": "未检测到异常"}
    reports = []
    for item in anomalies[:3]:
        reports.append((await analyze_root_cause(db, skill_id, item)).to_dict())
    return {"skill_id": skill_id, "reports": reports}


@router.get("/{skill_id}/guardian/conflicts")
async def guardian_conflicts_endpoint(
    skill_id: str,
    scope: str = "same_department",
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.guardian import detect_conflicts

    conflicts = await detect_conflicts(db, skill_id=skill_id, scope=scope)
    return {"skill_id": skill_id, "conflict_count": len(conflicts), "conflicts": [item.to_dict() for item in conflicts]}


@router.get("/{skill_id}/guardian/drift")
async def guardian_drift_endpoint(
    skill_id: str,
    window_hours: int = 24,
    baseline_days: int = 14,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.guardian.drift_detector import detect_drift

    reports = await detect_drift(db, skill_id, window_hours=window_hours, baseline_days=baseline_days)
    return {"skill_id": skill_id, "drift_count": len(reports), "reports": [item.to_dict() for item in reports]}


@router.post("/{skill_id}/publish-readiness")
async def check_publish_readiness_endpoint(
    skill_id: str,
    include_ai: bool = True,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    from app.skills.lifecycle.publish_readiness import check_publish_readiness

    await ensure_skill_access(db, skill_id, current_user, "read")
    report = await check_publish_readiness(skill_id, db, include_ai_verify=include_ai)
    return report.to_dict()


@router.post("/{skill_id}/verify-ai")
async def verify_skill_ai_endpoint(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    from app.skills.core.git_service import git_service
    from app.skills.lint import lint_skill
    from app.skills.core.parser import skill_parser
    from app.workbench.verifier import verify_skill

    await ensure_skill_access(db, skill_id, current_user, "read")

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_MD_NOT_FOUND", 404)
    parsed = skill_parser.parse(skill_md)
    report = await verify_skill(skill_id, parsed)
    return report.to_dict()


@router.post("/{skill_id}/lint")
async def lint_skill_endpoint(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    from app.skills.core.git_service import git_service
    from app.skills.lint import lint_skill
    from app.skills.core.parser import skill_parser

    await ensure_skill_access(db, skill_id, current_user, "read")

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_MD_NOT_FOUND", 404)
    parsed = skill_parser.parse(skill_md)
    return lint_skill(skill_id, parsed).to_dict()


@router.get("/{skill_id}/health-score")
async def health_score(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.dashboard.service import get_skill_health_score

    return await get_skill_health_score(db, skill_id)


class ImpactEstimateRequest(BaseModel):
    """变更影响预估请求"""
    date_from: str = ""
    date_to: str = ""
    days: int = 7


@router.post("/{skill_id}/impact-estimate")
async def estimate_change_impact(
    skill_id: str,
    body: ImpactEstimateRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """变更影响预估：对比当前未发布版本与上一个发布版本在历史数据上的输出差异。"""
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.intelligence.impact_estimator import estimate_impact
    return await estimate_impact(db, skill_id, date_from=body.date_from, date_to=body.date_to, days=body.days)


class ParamTuneRequest(BaseModel):
    """批量参数调优请求"""
    params: list[dict]  # [{"name": "roi_threshold", "values": [1.0, 1.2, 1.5]}]
    date_from: str = ""
    date_to: str = ""
    days: int = 7


@router.post("/{skill_id}/param-tune")
async def batch_param_tuning(
    skill_id: str,
    body: ParamTuneRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """批量参数调优：网格搜索最优参数组合"""
    await ensure_skill_access(db, skill_id, current_user, "edit")
    from app.skills.intelligence.param_tuner import grid_search, ParamRange

    ranges = [ParamRange(name=p["name"], values=p["values"]) for p in body.params if "name" in p and "values" in p]
    if not ranges:
        raise AppError("PARAM_INVALID", 400, {"detail": "至少需要一个参数范围"})
    return await grid_search(db, skill_id, ranges, date_from=body.date_from, date_to=body.date_to, days=body.days)
