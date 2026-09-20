"""Playbook Designer — 根据业务目标 + 现有 Skill 库推荐编排流程。

master plan §7.3：
  用户: "我要做高 ROI 投放的自动化"
    ↓
  AI 推荐 Playbook:
    Step 1: [EC-投放-01] 初筛（快速 ROI 检查）
      ↓ 绿灯
    Step 2: [EC-预算-02] 预算校验
      ↓ 通过
    Step 3: [EC-审批-03] 高金额自动审批
      ↓ 批准
    Step 4: [EC-执行-04] 执行投放

设计原则：
1. 先用低成本规则筛查，再用高成本模型
2. 金额超过阈值强制人工审批
3. 每一步都有回退路径
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm
from app.skills.core.models import Skill
from app.workbench.retrieval import find_similar_skills

logger = logging.getLogger(__name__)


@dataclass
class PlaybookStep:
    """推荐的 Playbook 一步（匹配 executor schema）。

    对应真实 Playbook step:
      {
        "id": "step_1",
        "skill_id": "ec-filter-01",
        "depends_on": ["step_0"],        # 前序依赖
        "on_failure": "terminate|retry", # 失败策略
      }
    """
    id: str                          # step_1 / step_2 ...
    skill_id: str
    skill_name: str = ""
    purpose: str = ""                # 此步为什么选这个 Skill
    depends_on: list[str] = field(default_factory=list)  # 前序 step id
    on_failure: str = "terminate"    # terminate / retry

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "purpose": self.purpose,
            "depends_on": list(self.depends_on),
            "on_failure": self.on_failure,
        }


@dataclass
class PlaybookDesign:
    """Playbook Designer 产出。"""
    goal: str = ""
    steps: list[PlaybookStep] = field(default_factory=list)
    reasoning: str = ""              # 编排理由
    candidate_skills: list[dict] = field(default_factory=list)  # 检索到的候选
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "reasoning": self.reasoning,
            "candidate_skills": self.candidate_skills,
            "warnings": self.warnings,
        }


# F3: DESIGNER_SYSTEM_PROMPT 已迁移到 app/common/prompts/playbook_designer@v1.md
def _get_designer_prompt() -> tuple[str, str]:
    try:
        from app.common.prompt_registry import prompt_registry
        return prompt_registry.build("playbook_designer")
    except KeyError:
        return "你是 Playbook 编排师，请根据业务目标从候选 Skill 中编排流程。", ""


async def _fetch_candidate_skills(db: AsyncSession, goal: str, limit: int = 8) -> list[dict]:
    """检索与目标相关的候选 Skill。优先用三重检索；降级到数据库全量。"""
    try:
        similar = await find_similar_skills(db, goal, limit=limit)
        if similar:
            out = []
            for s in similar:
                d = s.to_dict()
                # 补齐 name（retrieval 返回的是 SimilarSkill，里面可能有 name 字段）
                out.append({
                    "skill_id": d.get("skill_id") or d.get("id", ""),
                    "name": d.get("name", ""),
                    "description": d.get("description", ""),
                    "department": d.get("department", ""),
                    "score": d.get("score", 0),
                })
            return out
    except Exception as e:
        logger.warning(f"Playbook designer 检索失败: {e}")

    # 降级：取最近更新的 active/shadow Skill
    try:
        stmt = (
            select(Skill.id, Skill.name, Skill.description, Skill.department)
            .where(Skill.status.in_(["active", "shadow"]))
            .order_by(Skill.updated_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [
            {
                "skill_id": r[0],
                "name": r[1] or "",
                "description": r[2] or "",
                "department": r[3] or "",
                "score": 0.0,
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Playbook designer 降级检索也失败: {e}")
        return []


async def design_playbook(
    db: AsyncSession,
    goal: str,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> PlaybookDesign:
    """根据业务目标设计 Playbook。

    Args:
        goal: 用户用自然语言描述的业务目标
        user_id: （可选）调用方用户 ID，用于 F4 成本归因
        department: （可选）调用方部门，用于 F4 成本归因
    """
    design = PlaybookDesign(goal=goal)

    # 1. 检索候选 Skill
    candidates = await _fetch_candidate_skills(db, goal)
    design.candidate_skills = candidates

    if not candidates:
        design.warnings.append("未找到相关的 Skill，请先创建基础 Skill 再编排 Playbook")
        design.reasoning = "候选为空，无法编排"
        return design

    # 2. 调 LLM 编排
    candidate_list = "\n".join([
        f"- {c['skill_id']}：{c['name']} —— {c.get('description', '')[:80]}"
        for c in candidates
    ])
    user_prompt = f"""# 业务目标
{goal}

# 候选 Skill 列表
{candidate_list}

请从候选列表中选 2-5 个 Skill 编排成一个 Playbook，按上面的输出格式返回。
"""

    designer_system, _designer_hash = _get_designer_prompt()
    # F4 成本追踪上下文：user/部门 + 本次 prompt hash
    _designer_cost_context = {
        "user_id": user_id,
        "department": department,
        "prompt_hash": _designer_hash or None,
    }
    try:
        resp = await call_llm(
            system=designer_system,
            user=user_prompt,
            max_tokens=1500,
            temperature=0.3,
            json_mode=True,
            call_source="playbook_designer",
            cost_context=_designer_cost_context,
        )
    except Exception as e:
        logger.warning(f"Playbook designer LLM 失败: {e}")
        resp = None

    if not isinstance(resp, dict):
        # 降级：直接按候选顺序串行编排（每一步依赖前一步）
        design.reasoning = "AI 编排不可用，按相关性顺序串行"
        design.warnings.append("降级策略：未做深度编排")
        for i, c in enumerate(candidates[:3]):
            step_id = f"step_{i + 1}"
            prev_id = f"step_{i}" if i > 0 else None
            design.steps.append(PlaybookStep(
                id=step_id,
                skill_id=c["skill_id"],
                skill_name=c["name"],
                purpose=f"按相关性排在第 {i + 1} 步",
                depends_on=[prev_id] if prev_id else [],
                on_failure="terminate",
            ))
        return design

    # 3. 解析 LLM 响应
    valid_skill_ids = {c["skill_id"] for c in candidates}
    name_by_id = {c["skill_id"]: c["name"] for c in candidates}
    assigned_step_ids: set[str] = set()
    for idx, step_data in enumerate(resp.get("steps", [])):
        sid = step_data.get("skill_id", "")
        if sid not in valid_skill_ids:
            design.warnings.append(f"LLM 编造了不存在的 skill_id: {sid}（已跳过）")
            continue

        # 规范化 step id（LLM 可能没给或格式不对）
        raw_id = step_data.get("id", "") or f"step_{len(assigned_step_ids) + 1}"
        step_id = raw_id if raw_id.startswith("step_") else f"step_{len(assigned_step_ids) + 1}"

        # 校验 depends_on：只能引用已出现过的 step id
        raw_deps = step_data.get("depends_on") or []
        if not isinstance(raw_deps, list):
            raw_deps = [raw_deps]
        deps = [d for d in raw_deps if isinstance(d, str) and d in assigned_step_ids]
        dropped_deps = [d for d in raw_deps if d not in assigned_step_ids]
        if dropped_deps:
            design.warnings.append(f"步骤 {step_id} 的依赖 {dropped_deps} 不是前序步骤，已丢弃")

        # on_failure 只能是白名单
        on_failure = step_data.get("on_failure", "terminate")
        if on_failure not in ("terminate", "retry"):
            on_failure = "terminate"

        design.steps.append(PlaybookStep(
            id=step_id,
            skill_id=sid,
            skill_name=name_by_id.get(sid, ""),
            purpose=step_data.get("purpose", ""),
            depends_on=deps,
            on_failure=on_failure,
        ))
        assigned_step_ids.add(step_id)

    design.reasoning = resp.get("reasoning", "")
    design.warnings.extend([w for w in resp.get("warnings", []) if isinstance(w, str)])

    if not design.steps:
        design.warnings.append("LLM 未产出有效步骤")

    return design
