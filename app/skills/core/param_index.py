"""参数反向索引：供 tooling / lifecycle / intelligence 共享的只读索引。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import cache_get, cache_set
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import SkillStructured, skill_parser


@dataclass
class ParamUsage:
    """单条参数引用记录。"""

    skill_id: str
    skill_name: str
    department: str | None
    step_id: str
    step_name: str
    branch_index: int
    condition: str
    conclusion: str
    action: str
    kind: str

    def to_dict(self) -> dict:
        return asdict(self)


_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][\w]*)\}")
_METRIC_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z_][\u4e00-\u9fff\w]*)\s*[<>]=?\s*(?:[-]?\d|\{)"
)

_ParamMap = dict[str, list[dict]]


def _extract_param_refs(condition: str) -> tuple[set[str], set[str]]:
    """从一行 condition 中提取 (placeholder 名集合, metric 名集合)。"""
    if not condition:
        return set(), set()
    placeholders = set(_PLACEHOLDER_RE.findall(condition))
    metrics = set(_METRIC_RE.findall(condition))
    metrics -= placeholders
    return placeholders, metrics


def _walk_skill(skill: Skill, structured: SkillStructured) -> dict[str, list[ParamUsage]]:
    """遍历单个 Skill 的所有 branch，返回 {param_name: [usages]}。"""
    bucket: dict[str, list[ParamUsage]] = {}

    def _push(name: str, kind: str, step, idx: int, branch):
        bucket.setdefault(name, []).append(
            ParamUsage(
                skill_id=skill.id,
                skill_name=skill.name,
                department=skill.department,
                step_id=step.id,
                step_name=step.name or step.id,
                branch_index=idx,
                condition=branch.condition or "",
                conclusion=branch.conclusion or "",
                action=branch.action or "",
                kind=kind,
            )
        )

    for step in structured.steps or []:
        for idx, branch in enumerate(step.branches or []):
            placeholders, metrics = _extract_param_refs(branch.condition or "")
            for name in placeholders:
                _push(name, "placeholder", step, idx, branch)
            for name in metrics:
                _push(name, "metric", step, idx, branch)

    return bucket


CACHE_KEY = "param_index:all"
CACHE_TTL = 1800


async def build_param_index(db: AsyncSession) -> _ParamMap:
    """扫全库构建索引。返回 {param_name: [usage_dict, ...]}。"""
    result = await db.execute(
        select(Skill).where(Skill.status.in_(["active", "shadow", "draft"]))
    )
    skills = result.scalars().all()

    merged: _ParamMap = {}
    for skill in skills:
        try:
            markdown = git_service.read_file(skill.id, "SKILL.md")
            if not markdown:
                continue
            structured = skill_parser.parse(markdown)
        except Exception:
            continue

        skill_bucket = _walk_skill(skill, structured)
        for name, usages in skill_bucket.items():
            merged.setdefault(name, []).extend(usage.to_dict() for usage in usages)

    return merged


async def get_param_index(db: AsyncSession, *, force: bool = False) -> _ParamMap:
    """获取（带缓存的）参数索引。"""
    if not force:
        try:
            cached = await cache_get(CACHE_KEY)
            if cached:
                return cached
        except Exception as exc:
            from loguru import logger

            logger.debug("param_index 缓存读失败: {}", exc)

    index = await build_param_index(db)
    try:
        await cache_set(CACHE_KEY, index, ttl=CACHE_TTL)
    except Exception as exc:
        from loguru import logger

        logger.debug("param_index 缓存写失败: {}", exc)
    return index


async def find_param_usages(
    db: AsyncSession,
    param_name: str,
    *,
    exclude_skill: str | None = None,
) -> list[dict]:
    """查询某个参数名在全库的使用位置。"""
    index = await get_param_index(db)
    usages = index.get(param_name, [])
    if exclude_skill:
        usages = [item for item in usages if item.get("skill_id") != exclude_skill]
    return usages


async def invalidate_param_index() -> None:
    """主动失效缓存（Skill 保存成功后调用）。"""
    try:
        from app.common.cache import cache_delete

        await cache_delete(CACHE_KEY)
    except Exception as exc:
        from loguru import logger

        logger.warning("param_index 缓存失效失败: {}", exc)
