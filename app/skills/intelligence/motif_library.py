"""Motif 库 — 从现有 Skill 中提取可复用的规则模式。

五大 motif:
1. **边界保护** — 任何极端值都走人工复核
2. **阶梯决策** — 绿/黄/红三级
3. **数据兜底** — 数据缺失时的默认行为
4. **冲突消解** — 多规则冲突时的优先级规则
5. **阶段流程** — 按顺序执行多个步骤
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.core.models import Skill
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser, SkillStructured


@dataclass
class Motif:
    """一个可复用模式。"""
    id: str
    name: str                        # 模式名称
    description: str                 # 说明
    category: str                    # threshold / flow / safety / resolution / fallback
    example_skill_ids: list[str] = field(default_factory=list)  # 示例 Skill
    template: dict = field(default_factory=dict)  # 可直接应用的模板结构

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════
# Motif 识别器
# ═══════════════════════════════════════════════════════

_MISSING_KW = ["缺失", "为空", "null", "无数据", "异常"]
_MANUAL_KW = ["人工", "复核", "审批", "介入"]


def _is_ladder_decision(structured: SkillStructured) -> bool:
    """检测阶梯决策：3+ 分支用阈值比较区分等级。"""
    for step in (structured.steps or []):
        if len(step.branches) < 3:
            continue
        has_gt = sum(1 for b in step.branches if ">" in (b.condition or ""))
        has_lt = sum(1 for b in step.branches if "<" in (b.condition or ""))
        if has_gt >= 1 and has_lt >= 1:
            return True
    return False


def _is_boundary_protection(structured: SkillStructured) -> bool:
    """检测边界保护：极端值走人工。"""
    for step in (structured.steps or []):
        for br in step.branches:
            cond = (br.condition or "").lower()
            action = (br.action or "").lower()
            concl = (br.conclusion or "").lower()
            if any(kw in action or kw in concl for kw in _MANUAL_KW):
                return True
    return False


def _is_data_fallback(structured: SkillStructured) -> bool:
    """检测数据兜底：有分支处理缺失情况。"""
    for step in (structured.steps or []):
        for br in step.branches:
            cond = (br.condition or "").lower()
            if any(kw in cond for kw in _MISSING_KW):
                return True
    return False


def _is_stage_flow(structured: SkillStructured) -> bool:
    """检测阶段流程：3+ step，每个 step 简单（1-2 分支）。"""
    steps = structured.steps or []
    if len(steps) < 3:
        return False
    return all(len(s.branches) <= 2 for s in steps)


def _is_conflict_resolution(structured: SkillStructured) -> bool:
    """检测冲突消解：有明确的优先级或 next_step 编排。"""
    for step in (structured.steps or []):
        for br in step.branches:
            if br.next_step:
                return True
    return False


# ═══════════════════════════════════════════════════════
# Motif 模板
# ═══════════════════════════════════════════════════════

MOTIF_TEMPLATES = {
    "ladder_decision": {
        "name": "阶梯决策",
        "category": "threshold",
        "description": "用阈值区分绿/黄/红三级，常用于评分、健康度判断",
        "template": {
            "id": "step_ladder",
            "name": "阶梯判定",
            "branches": [
                {"condition": "{metric} > {high_threshold}", "conclusion": "绿灯", "action": "{green_action}", "next_step": None},
                {"condition": "{metric} > {low_threshold}", "conclusion": "黄灯", "action": "{yellow_action}", "next_step": None},
                {"condition": "其他情况", "conclusion": "红灯", "action": "{red_action}", "next_step": None},
            ],
        },
    },
    "boundary_protection": {
        "name": "边界保护",
        "category": "safety",
        "description": "极端值或异常情况走人工复核，避免自动处理高风险 case",
        "template": {
            "id": "step_boundary",
            "name": "边界检查",
            "branches": [
                {"condition": "{metric} 超出安全范围", "conclusion": "异常", "action": "人工复核", "next_step": None},
                {"condition": "其他情况", "conclusion": "正常", "action": "继续自动处理", "next_step": "step_next"},
            ],
        },
    },
    "data_fallback": {
        "name": "数据兜底",
        "category": "fallback",
        "description": "数据缺失/异常时的默认行为，避免 null 导致崩溃",
        "template": {
            "id": "step_fallback",
            "name": "数据完整性检查",
            "branches": [
                {"condition": "{field} 缺失 或 异常", "conclusion": "数据异常", "action": "使用默认值或跳过", "next_step": None},
                {"condition": "其他情况", "conclusion": "数据正常", "action": "继续", "next_step": "step_next"},
            ],
        },
    },
    "stage_flow": {
        "name": "阶段流程",
        "category": "flow",
        "description": "按顺序执行多个步骤，每步有明确的成功标准",
        "template": {
            "id": "stage_1",
            "name": "阶段 1",
            "branches": [
                {"condition": "条件满足", "conclusion": "完成", "action": "", "next_step": "stage_2"},
                {"condition": "其他情况", "conclusion": "失败", "action": "", "next_step": None},
            ],
        },
    },
    "conflict_resolution": {
        "name": "冲突消解",
        "category": "resolution",
        "description": "多规则冲突时的优先级规则，通过 next_step 显式编排",
        "template": {
            "id": "step_priority",
            "name": "优先级判定",
            "branches": [
                {"condition": "高优先规则", "conclusion": "按高优先处理", "action": "", "next_step": None},
                {"condition": "其他情况", "conclusion": "按普通处理", "action": "", "next_step": "step_normal"},
            ],
        },
    },
}


# ═══════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════

async def extract_motifs_from_library(
    db: AsyncSession,
    limit: int = 50,
) -> dict[str, Motif]:
    """从 Skill 库中识别每种 motif 的示例。"""
    result = await db.execute(
        select(Skill).where(Skill.status.in_(["active", "shadow"])).limit(limit)
    )
    skills = result.scalars().all()

    detectors = {
        "ladder_decision": _is_ladder_decision,
        "boundary_protection": _is_boundary_protection,
        "data_fallback": _is_data_fallback,
        "stage_flow": _is_stage_flow,
        "conflict_resolution": _is_conflict_resolution,
    }

    motifs: dict[str, Motif] = {}
    for motif_id, tpl in MOTIF_TEMPLATES.items():
        motifs[motif_id] = Motif(
            id=motif_id,
            name=tpl["name"],
            description=tpl["description"],
            category=tpl["category"],
            template=tpl["template"],
        )

    for skill in skills:
        try:
            md = git_service.read_file(skill.id, "SKILL.md")
            if not md:
                continue
            structured = skill_parser.parse(md)
        except Exception:
            continue

        for motif_id, detector in detectors.items():
            if detector(structured) and len(motifs[motif_id].example_skill_ids) < 3:
                motifs[motif_id].example_skill_ids.append(skill.id)

    return motifs


async def get_motif_library(db: AsyncSession) -> list[dict]:
    """获取完整 Motif 库。"""
    motifs = await extract_motifs_from_library(db)
    return [m.to_dict() for m in motifs.values()]
