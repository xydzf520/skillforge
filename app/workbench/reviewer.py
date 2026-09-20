"""AI Reviewer — 审批辅助：业务语义摘要 + 影响模拟 + 6 维风险评估。

给审批人看的不是原始 diff，而是：
1. 业务语义摘要（改了什么 / 影响谁 / 放宽还是收紧）
2. 影响模拟（前后结果对比）
3. 风险评估卡（6 维度打分 + 审批建议）
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import logging

from app.common.ai import call_llm
from app.skills.core.parser import SkillStructured, skill_parser
from app.skills.core.git_service import git_service
from app.common.time_utils import now_bjt

logger = logging.getLogger(__name__)


@dataclass
class ChangeSummary:
    """变更语义摘要。"""
    changed_modules: list[str] = field(default_factory=list)
    logic_change: str = ""          # 判定逻辑的变化
    direction: str = ""             # 放宽/收紧/中性
    estimated_impact: str = ""      # 影响量级
    business_meaning: str = ""      # 对业务的意义

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskAssessment:
    """6 维风险评估。"""
    behavior_expansion: str = "low"      # 行为扩张
    false_positive: str = "low"          # 误伤
    drift: str = "low"                   # 漂移
    dependency: str = "low"              # 依赖
    test_coverage: str = "adequate"      # 测试不足
    cross_skill_conflict: str = "none"   # 跨 Skill 冲突

    recommendation: str = "approve"      # approve / request_change / reject / dual_review
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultDistribution:
    """§5.3 前后结果 diff — 动作/结论分布。"""
    before: dict[str, int] = field(default_factory=dict)   # {action: count}
    after: dict[str, int] = field(default_factory=dict)
    sample_size_before: int = 0
    sample_size_after: int = 0
    window_days: int = 7

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewerReport:
    """审批辅助报告。"""
    skill_id: str
    summary: ChangeSummary = field(default_factory=ChangeSummary)
    risk: RiskAssessment = field(default_factory=RiskAssessment)
    result_diff: ResultDistribution = field(default_factory=ResultDistribution)  # §5.3
    one_line: str = ""              # 一句话总结（给审批人）
    prompt_hash: str = ""           # F3 - 本次审查使用的 reviewer prompt hash

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "summary": self.summary.to_dict(),
            "risk": self.risk.to_dict(),
            "result_diff": self.result_diff.to_dict(),
            "one_line": self.one_line,
            "prompt_hash": self.prompt_hash,
        }


def _diff_modules(before: SkillStructured, after: SkillStructured) -> list[str]:
    """识别哪些模块有变化。"""
    changed = []
    if (before.purpose or "") != (after.purpose or ""):
        changed.append("goal")
    if before.steps != after.steps:
        changed.append("rules")
    if before.output_definition != after.output_definition:
        changed.append("output")
    if before.test_cases != after.test_cases:
        changed.append("tests")
    if before.antipatterns != after.antipatterns:
        changed.append("antipatterns")
    return changed


# F3: REVIEWER_SYSTEM_PROMPT 已迁移到 app/common/prompts/reviewer@v1.md
def _get_reviewer_prompt() -> tuple[str, str]:
    try:
        from app.common.prompt_registry import prompt_registry
        return prompt_registry.build("reviewer")
    except KeyError:
        return "你是 SkillForge 审批助手，请把 Skill 变更翻译为业务语义摘要并评估风险。", ""


async def _compute_result_distribution(
    skill_id: str,
    *,
    window_days: int = 7,
    split_timestamp: datetime | None = None,
) -> ResultDistribution:
    """§5.3 从 DecisionLog 统计前后结果分布。

    split_timestamp: 以此时间点为界，之前为 before，之后为 after。
                     默认为 now - window_days（把最近 window_days 视为 after）。
    """
    dist = ResultDistribution(window_days=window_days)
    try:
        from sqlalchemy import select
        from app.database import async_session_factory
        from app.execution.models import DecisionLog
    except Exception:
        return dist

    now = now_bjt()
    if split_timestamp is None:
        split_timestamp = now - timedelta(days=window_days // 2 or 1)
    before_start = split_timestamp - timedelta(days=window_days)
    after_end = split_timestamp + timedelta(days=window_days)

    try:
        async with async_session_factory() as session:
            stmt = (
                select(DecisionLog.suggested_action, DecisionLog.created_at)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .where(DecisionLog.created_at >= before_start)
                .where(DecisionLog.created_at <= after_end)
                .limit(2000)
            )
            rows = (await session.execute(stmt)).all()
    except Exception as e:
        logger.debug("result_distribution 查询失败: {}", e)
        return dist

    for action_json, created_at in rows:
        if not isinstance(action_json, dict) or not created_at:
            continue
        # 提取结论/动作作为分桶标签
        label = (
            action_json.get("conclusion")
            or action_json.get("action")
            or action_json.get("level")
            or "unknown"
        )
        label = str(label)[:40]

        if created_at < split_timestamp:
            dist.before[label] = dist.before.get(label, 0) + 1
            dist.sample_size_before += 1
        else:
            dist.after[label] = dist.after.get(label, 0) + 1
            dist.sample_size_after += 1

    return dist


async def review_change(
    skill_id: str,
    before_md: str,
    after_md: str,
) -> ReviewerReport:
    """对 Skill 的变更生成审批辅助报告。"""
    report = ReviewerReport(skill_id=skill_id)

    try:
        before = skill_parser.parse(before_md) if before_md else None
        after = skill_parser.parse(after_md) if after_md else None
    except Exception as e:
        logger.warning(f"解析失败: {e}")
        return report

    if not after:
        return report

    # 构造 diff 描述
    changed_modules = _diff_modules(before, after) if before else ["(new)"]
    report.summary.changed_modules = changed_modules

    # 调 LLM 生成语义摘要
    user_prompt = f"""# 变更前 SKILL.md
{before_md[:3000] if before_md else '（新建）'}

# 变更后 SKILL.md
{after_md[:3000]}

# 变更的模块
{', '.join(changed_modules)}

请生成审批辅助报告（JSON 格式）。
"""

    # §5.3 并行算前后结果分布
    import asyncio
    result_dist_task = _compute_result_distribution(skill_id)

    reviewer_system, reviewer_hash = _get_reviewer_prompt()
    report.prompt_hash = reviewer_hash  # F3: 记录使用的 prompt 版本
    try:
        llm_task = call_llm(
            system=reviewer_system,
            user=user_prompt,
            max_tokens=1500,
            temperature=0.2,
            json_mode=True,
        )
        resp, result_dist = await asyncio.gather(llm_task, result_dist_task, return_exceptions=True)
        if isinstance(result_dist, ResultDistribution):
            report.result_diff = result_dist
        if isinstance(resp, Exception):
            raise resp

        if isinstance(resp, dict):
            summary_data = resp.get("summary", {}) or {}
            risk_data = resp.get("risk", {}) or {}

            report.summary = ChangeSummary(
                changed_modules=summary_data.get("changed_modules", changed_modules),
                logic_change=summary_data.get("logic_change", ""),
                direction=summary_data.get("direction", ""),
                estimated_impact=summary_data.get("estimated_impact", ""),
                business_meaning=summary_data.get("business_meaning", ""),
            )
            report.risk = RiskAssessment(
                behavior_expansion=risk_data.get("behavior_expansion", "low"),
                false_positive=risk_data.get("false_positive", "low"),
                drift=risk_data.get("drift", "low"),
                dependency=risk_data.get("dependency", "low"),
                test_coverage=risk_data.get("test_coverage", "adequate"),
                cross_skill_conflict=risk_data.get("cross_skill_conflict", "none"),
                recommendation=risk_data.get("recommendation", "approve"),
                reasons=risk_data.get("reasons", []),
            )
            report.one_line = resp.get("one_line", "")
    except Exception as e:
        logger.warning(f"AI 审查失败: {e}")
        report.one_line = f"变更模块: {', '.join(changed_modules)}（AI 分析失败，需人工审阅）"

    return report
