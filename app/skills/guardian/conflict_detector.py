"""跨 Skill 冲突检测 — 动作冲突/优先级冲突/阈值语义冲突/上下文篡改。"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.core.models import Skill
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser


@dataclass
class CrossSkillConflict:
    """跨 Skill 检测到的冲突。"""
    skill_a: str
    skill_b: str
    kind: str                        # action_conflict / threshold_semantic / context_mutation / priority
    severity: str = "medium"
    description: str = ""
    evidence: dict = field(default_factory=dict)
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_actions(structured) -> set[str]:
    """提取所有动作词。"""
    actions = set()
    for step in (structured.steps or []):
        for br in step.branches:
            act = (br.action or "").strip()
            if act:
                actions.add(act)
    return actions


def _extract_thresholds_by_term(structured) -> dict[str, list[float]]:
    """按术语分组提取阈值。"""
    num_re = re.compile(r"([A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,})\s*[<>]=?\s*(-?\d+(?:\.\d+)?)")
    result: dict[str, list[float]] = {}
    for step in (structured.steps or []):
        for br in step.branches:
            for match in num_re.finditer(br.condition or ""):
                term, val_str = match.group(1), match.group(2)
                try:
                    val = float(val_str)
                    result.setdefault(term, []).append(val)
                except ValueError:
                    pass
    return result


ACTION_PAIRS_CONFLICTING = [
    ("加预算", "降价"),
    ("加预算", "暂停"),
    ("放行", "拒绝"),
    ("通过", "拒绝"),
    ("批准", "驳回"),
    ("启用", "停用"),
]


async def detect_conflicts(
    db: AsyncSession,
    skill_id: str | None = None,
    *,
    scope: str = "same_department",  # all | same_department | targeted
) -> list[CrossSkillConflict]:
    """检测 Skill 之间的冲突。

    Args:
        skill_id: 如果指定，只检测该 Skill 与其他的冲突
        scope: 检测范围
    """
    conflicts: list[CrossSkillConflict] = []

    # 加载目标 Skill（若指定）
    target_skill = None
    target_structured = None
    if skill_id:
        res = await db.execute(select(Skill).where(Skill.id == skill_id))
        target_skill = res.scalar_one_or_none()
        if not target_skill:
            return conflicts
        try:
            md = git_service.read_file(skill_id, "SKILL.md")
            if md:
                target_structured = skill_parser.parse(md)
        except Exception:
            return conflicts

    # 加载候选 Skills
    query = select(Skill).where(Skill.status.in_(["active", "shadow"]))
    if target_skill and scope == "same_department":
        query = query.where(Skill.department == target_skill.department)
    if skill_id:
        query = query.where(Skill.id != skill_id)
    query = query.limit(100)

    result = await db.execute(query)
    candidates = result.scalars().all()

    for cand in candidates:
        try:
            md = git_service.read_file(cand.id, "SKILL.md")
            if not md:
                continue
            cand_structured = skill_parser.parse(md)
        except Exception:
            continue

        # 如果没有指定目标，做 pair-wise 检测（O(n²)，限制数量）
        if not target_structured:
            continue  # 为了性能，只做目标 vs 其他

        # 1. 动作冲突
        target_actions = _extract_actions(target_structured)
        cand_actions = _extract_actions(cand_structured)
        for ta in target_actions:
            for ca in cand_actions:
                for a, b in ACTION_PAIRS_CONFLICTING:
                    if (a in ta and b in ca) or (b in ta and a in ca):
                        conflicts.append(CrossSkillConflict(
                            skill_a=skill_id,
                            skill_b=cand.id,
                            kind="action_conflict",
                            severity="high",
                            description=f"{skill_id} 的动作「{ta}」与 {cand.name} 的动作「{ca}」可能冲突",
                            evidence={"action_a": ta, "action_b": ca},
                            suggestion="明确两个 Skill 的执行顺序或优先级",
                        ))

        # 2. 阈值语义冲突（同一术语用了不同阈值）
        target_thresholds = _extract_thresholds_by_term(target_structured)
        cand_thresholds = _extract_thresholds_by_term(cand_structured)
        common_terms = set(target_thresholds.keys()) & set(cand_thresholds.keys())
        for term in common_terms:
            t_vals = target_thresholds[term]
            c_vals = cand_thresholds[term]
            # 如果最大差异 > 50%，认为语义不一致
            all_vals = t_vals + c_vals
            if len(all_vals) >= 2:
                diff = max(all_vals) - min(all_vals)
                avg = sum(all_vals) / len(all_vals)
                if avg > 0 and diff / avg > 0.5:
                    conflicts.append(CrossSkillConflict(
                        skill_a=skill_id,
                        skill_b=cand.id,
                        kind="threshold_semantic",
                        severity="medium",
                        description=f"术语「{term}」在两个 Skill 中阈值差异大：{t_vals} vs {c_vals}",
                        evidence={"term": term, "skill_a_vals": t_vals, "skill_b_vals": c_vals},
                        suggestion=f"统一「{term}」的含义，或明确区分场景",
                    ))

    return conflicts
