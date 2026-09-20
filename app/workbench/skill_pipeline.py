"""Skill 骨架生成 pipeline — 串联 Architect + Retrieval + Generator + Verifier。

流程:
  1. [Architect] 4 轮采访收集需求
  2. [Retrieval] 三重检索找相似 Skill
  3. [Generator] 合成完整骨架
  4. [Lint + Verifier] 自动质检
  5. 返回骨架 + 可发布性报告
"""

from __future__ import annotations
from dataclasses import dataclass, field
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.telemetry import record_ttfr, record_first_pass_quality
from app.workbench.architect import synthesize_skill_from_interview
from app.workbench.retrieval import find_similar_skills, SimilarSkill
from app.workbench.verifier import verify_skill
from app.skills.lint import lint_skill
from app.skills.core.parser import skill_parser, SkillStructured

logger = logging.getLogger(__name__)


@dataclass
class GeneratedSkill:
    """pipeline 输出：完整的 Skill 骨架 + 质检报告。"""
    skill: dict = field(default_factory=dict)           # Skill 结构化数据
    similar_skills: list = field(default_factory=list)  # 相似 Skill 参考
    lint_report: dict = field(default_factory=dict)     # 静态检查
    verify_report: dict = field(default_factory=dict)   # AI 审查
    can_publish: bool = False
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "similar_skills": self.similar_skills,
            "lint_report": self.lint_report,
            "verify_report": self.verify_report,
            "can_publish": self.can_publish,
            "summary": self.summary,
        }


def _dict_to_structured(data: dict) -> SkillStructured:
    """将 generator 输出的 dict 转为 SkillStructured 用于质检。"""
    from app.skills.core.parser import DecisionStep, Branch, OutputItem, TestCase, Antipattern, DataInput

    meta = data.get("meta", {})
    structured = SkillStructured(
        frontmatter={
            "name": meta.get("name", ""),
            "description": meta.get("description", ""),
            "department": meta.get("department", ""),
            "trigger_type": meta.get("trigger_type", "manual"),
            "risk_level": meta.get("risk_level", "R2"),
        },
        purpose=data.get("goal", ""),
        steps=[
            DecisionStep(
                id=r.get("id", f"step_{i+1}"),
                name=r.get("name", ""),
                description=r.get("description", ""),
                branches=[
                    Branch(
                        condition=b.get("condition", ""),
                        conclusion=b.get("conclusion", ""),
                        action=b.get("action", ""),
                        next_step=b.get("next_step"),
                    )
                    for b in r.get("branches", [])
                ],
            )
            for i, r in enumerate(data.get("rules", []))
        ],
        output_definition=[
            OutputItem(
                name=o.get("name", ""),
                format=o.get("format", "text"),
                recipient=o.get("recipient", ""),
                approval_level=o.get("approval_level", ""),
            )
            for o in data.get("output_table", [])
        ],
        test_cases=[
            TestCase(
                name=t.get("name", ""),
                input_data=t.get("input_data", {}) or {},
                expected_output=t.get("expected_output", {}) or {},
                assert_rules=t.get("assert_rules", []) or [],
            )
            for t in data.get("test_cases", [])
        ],
        antipatterns=[
            Antipattern(
                scenario=a.get("scenario", ""),
                correct_action=a.get("correct_action", ""),
                source=a.get("source", ""),
            )
            for a in data.get("antipatterns", [])
        ],
        data_inputs=[
            DataInput(
                name=d.get("name", ""),
                source=d.get("source", ""),
                frequency=d.get("frequency", ""),
            )
            for d in data.get("data_inputs", [])
        ],
        custom_sections={},
        raw_sections={},
    )
    return structured


async def run_pipeline(
    db: AsyncSession,
    description: str,
    interview_answers: dict,
    *,
    include_ai_verify: bool = False,
) -> GeneratedSkill:
    """完整的骨架生成流程。

    Args:
        description: 用户初始业务描述
        interview_answers: 4 轮采访的答案
        include_ai_verify: 是否运行 AI 审查（较慢）
    """
    start_time = time.monotonic()
    result = GeneratedSkill()

    # Step 1: 三重检索找相似 Skill
    try:
        similar = await find_similar_skills(db, description, limit=3)
        result.similar_skills = [s.to_dict() for s in similar]
    except Exception as e:
        logger.warning(f"三重检索失败: {e}")

    # Step 2: LLM 合成骨架
    try:
        skill_dict = await synthesize_skill_from_interview(description, interview_answers)
        if not skill_dict or not isinstance(skill_dict, dict) or not skill_dict.get("meta"):
            # LLM 返回空 / 结构异常 → 走明确错误而不是硅然继续
            result.summary = "骨架生成失败：LLM 未返回有效 JSON（建议点'重试这一步'）"
            result.can_publish = False
            return result
        result.skill = skill_dict
    except Exception as e:
        logger.error(f"骨架合成失败: {e}")
        # v2.8.1 C5：把错误类型暴露给前端，方便显示"重试" vs "联系管理员"
        err_type = type(e).__name__
        result.summary = f"骨架生成失败（{err_type}）: {e}"
        result.skill = {"_error": {"type": err_type, "message": str(e)[:500]}}
        return result

    # Step 3: 静态检查
    try:
        structured = _dict_to_structured(result.skill)
        lint_report = lint_skill("(new)", structured)
        result.lint_report = lint_report.to_dict()
    except Exception as e:
        logger.warning(f"Lint 失败: {e}")

    # Step 4: AI 审查（可选）
    if include_ai_verify:
        try:
            verify_report = await verify_skill("(new)", structured)
            result.verify_report = verify_report.to_dict()
        except Exception as e:
            logger.warning(f"Verify 失败: {e}")

    # Step 5: 综合判定
    lint_passed = result.lint_report.get("passed", False) if result.lint_report else False
    verify_can = result.verify_report.get("can_publish", True) if result.verify_report else True
    result.can_publish = lint_passed and verify_can

    lint_errors = result.lint_report.get("error_count", 0) if result.lint_report else 0
    if result.can_publish:
        result.summary = "骨架已生成并通过质检"
    else:
        result.summary = f"骨架已生成，但有 {lint_errors} 个待修复问题"

    # §3.7 TTFR + 首版通过率埋点
    try:
        record_ttfr(time.monotonic() - start_time, source="architect")
        record_first_pass_quality(result.can_publish, source="architect")
    except Exception as e:
        logger.debug("skill_pipeline 埋点失败: %s", e)

    return result
