"""Swarm 4-Agent 协作生成 — 复杂 Skill 的并行多 Agent 编排。

主方案：master plan §8.4。
- Explorer Agent — 搜索相似 Skill + motif（复用 retrieval）
- Rules Agent   — 生成 if-then 树（复用 architect synthesize）
- Tests Agent   — 生成测试用例（骨架内已含，可补充）
- Verifier Agent — 对抗审查（复用 verifier）
- Leader        — 合并 4 个中间产物为最终 Skill

关键约束：不让多个 Agent 同时改 SKILL.md，Leader 统一合并。
"""

from __future__ import annotations
from dataclasses import dataclass, field
import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.telemetry import record_ttfr
from app.workbench.architect import synthesize_skill_from_interview
from app.workbench.retrieval import find_similar_skills
from app.workbench.verifier import verify_skill
from app.skills.lint import lint_skill
from app.skills.intelligence.motif_library import get_motif_library
from app.workbench.skill_pipeline import _dict_to_structured

logger = logging.getLogger(__name__)


@dataclass
class SwarmResult:
    """Swarm 4-Agent 协作产出。"""
    skill: dict = field(default_factory=dict)            # 最终 Skill 骨架
    explorer_findings: dict = field(default_factory=dict)  # Explorer 结果
    rules_draft: dict = field(default_factory=dict)       # Rules Agent 产出
    tests_notes: list = field(default_factory=list)      # Tests Agent 产出
    verifier_issues: list = field(default_factory=list)  # Verifier Agent 产出
    lint_report: dict = field(default_factory=dict)      # Lint 结果
    can_publish: bool = False
    leader_summary: str = ""                              # Leader 合并说明

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "explorer_findings": self.explorer_findings,
            "rules_draft": self.rules_draft,
            "tests_notes": self.tests_notes,
            "verifier_issues": self.verifier_issues,
            "lint_report": self.lint_report,
            "can_publish": self.can_publish,
            "leader_summary": self.leader_summary,
        }


async def _explorer_agent(db: AsyncSession, description: str) -> dict:
    """并行：三重检索 + motif 提取。"""
    similar_task = find_similar_skills(db, description, limit=3)
    motif_task = get_motif_library(db)
    try:
        similar, motifs = await asyncio.gather(similar_task, motif_task, return_exceptions=True)
    except Exception as e:
        logger.warning(f"Explorer agent 失败: {e}")
        return {"similar_skills": [], "motifs": []}

    if isinstance(similar, Exception):
        similar = []
    if isinstance(motifs, Exception):
        motifs = []

    similar_dicts = [s.to_dict() for s in (similar or [])]
    motif_dicts = [m for m in (motifs or []) if isinstance(m, dict) and m.get("example_skill_ids")]

    return {
        "similar_skills": similar_dicts,
        "motifs": motif_dicts[:5],  # 只取有示例的前 5 个
    }


async def _rules_agent(description: str, answers: dict) -> dict:
    """Rules Agent：通过 architect 合成骨架。"""
    try:
        return await synthesize_skill_from_interview(description, answers)
    except Exception as e:
        logger.warning(f"Rules agent 失败: {e}")
        return {}


def _tests_agent(skill_dict: dict) -> list[str]:
    """Tests Agent：检查测试覆盖并列出补充建议（同步，纯规则）。

    这里不再额外跑 LLM — Rules Agent 已经在骨架中写入了 test_cases。
    如果发现分支缺测试，给出补充建议文本。
    """
    notes = []
    rules = skill_dict.get("rules") or []
    tests = skill_dict.get("test_cases") or []
    total_branches = sum(len(r.get("branches", [])) for r in rules)
    if total_branches == 0:
        return notes
    coverage = len(tests) / total_branches if total_branches else 0
    if coverage < 1.0:
        notes.append(
            f"测试覆盖率 {round(coverage * 100)}% ({len(tests)}/{total_branches})，"
            f"建议为未覆盖的 {max(total_branches - len(tests), 0)} 条分支补充测试"
        )
    if not skill_dict.get("antipatterns"):
        notes.append("尚无反例，建议补充 2-3 个容易误判的场景")
    return notes


async def _verifier_agent(skill_dict: dict) -> list[dict]:
    """Verifier Agent：对抗审查。"""
    if not skill_dict:
        return []
    try:
        structured = _dict_to_structured(skill_dict)
        report = await verify_skill("(swarm)", structured)
        return [i.to_dict() for i in report.issues]
    except Exception as e:
        logger.warning(f"Verifier agent 失败: {e}")
        return []


def _leader_merge(
    description: str,
    explorer: dict,
    rules: dict,
    tests_notes: list,
    verifier_issues: list,
) -> tuple[dict, str]:
    """Leader：合并 4 个中间产物为最终 Skill + 生成说明。

    规则：
    1. 以 Rules Agent 的骨架为主
    2. 将 Tests Agent 的建议追加到待办清单
    3. 将 Verifier Agent 的 high/critical 问题写入 antipatterns
    4. 在 meta.description 中记录借鉴的 Skill/motif（可追溯性）
    """
    skill = dict(rules) if rules else {}
    if not skill:
        return {}, "Rules Agent 未产出骨架，Swarm 放弃"

    # 将 verifier 的严重问题转成 antipattern 提醒
    existing_ap = skill.get("antipatterns") or []
    for issue in verifier_issues:
        if issue.get("severity") in ("critical", "high"):
            existing_ap.append({
                "scenario": issue.get("title", "未知问题"),
                "correct_action": issue.get("suggestion", ""),
                "source": f"Verifier Agent ({issue.get('category', 'unknown')})",
            })
    skill["antipatterns"] = existing_ap

    # 将 Explorer 借鉴的 Skill / motif 写入 meta
    meta = skill.get("meta") or {}
    references = []
    for s in explorer.get("similar_skills", [])[:3]:
        sid = s.get("skill_id") or s.get("id")
        if sid:
            references.append(sid)
    for m in explorer.get("motifs", [])[:3]:
        mid = m.get("id") or m.get("name")
        if mid:
            references.append(f"motif:{mid}")
    if references:
        meta["references"] = references
    skill["meta"] = meta

    # 合并说明
    summary_lines = [
        f"Swarm 4-Agent 协作生成，借鉴了 {len(explorer.get('similar_skills', []))} 个相似 Skill、"
        f"{len(explorer.get('motifs', []))} 个 motif",
        f"Tests Agent 补充建议: {len(tests_notes)} 条",
        f"Verifier Agent 发现问题: {len(verifier_issues)} 条（已将严重项写入反例）",
    ]
    return skill, "\n".join(summary_lines)


async def run_swarm(
    db: AsyncSession,
    description: str,
    answers: dict | None = None,
) -> SwarmResult:
    """运行完整 Swarm 4-Agent 流程。

    Explorer + Rules 并行启动。Verifier 依赖 Rules 的产出，所以在 Rules 完成后串行。
    Tests 是纯规则检查，在 Rules 完成后本地同步执行。
    """
    start_time = time.monotonic()
    result = SwarmResult()
    answers = answers or {}

    # Explorer + Rules 并行
    explorer_task = _explorer_agent(db, description)
    rules_task = _rules_agent(description, answers)
    explorer, rules_draft = await asyncio.gather(
        explorer_task, rules_task, return_exceptions=True,
    )
    if isinstance(explorer, Exception):
        explorer = {"similar_skills": [], "motifs": []}
    if isinstance(rules_draft, Exception):
        rules_draft = {}

    result.explorer_findings = explorer
    result.rules_draft = rules_draft

    # Tests（纯规则，同步）+ Verifier（依赖 Rules）
    tests_notes = _tests_agent(rules_draft)
    verifier_issues = await _verifier_agent(rules_draft)
    result.tests_notes = tests_notes
    result.verifier_issues = verifier_issues

    # Leader 合并
    final_skill, summary = _leader_merge(
        description, explorer, rules_draft, tests_notes, verifier_issues,
    )
    result.skill = final_skill
    result.leader_summary = summary

    # 最终 lint
    if final_skill:
        try:
            structured = _dict_to_structured(final_skill)
            lint_report = lint_skill("(swarm)", structured)
            result.lint_report = lint_report.to_dict()
            result.can_publish = lint_report.passed
        except Exception as e:
            logger.warning(f"Swarm 最终 lint 失败: {e}")

    # §3.7 TTFR 埋点
    try:
        record_ttfr(time.monotonic() - start_time, source="swarm")
    except Exception as e:
        logger.debug("swarm TTFR 埋点失败: %s", e)

    return result
