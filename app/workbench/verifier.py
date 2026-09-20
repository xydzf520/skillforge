"""skillforge-verifier — AI 对抗式 Skill 审查。

借鉴 aiclawcode/src/tools/AgentTool/built-in/verificationAgent.ts 的
"证据优先 + 对抗式质疑"思路，改写审查维度为 Skill 决策规则专用。

与 skillforge-lint 的区别：
- Lint 是静态规则检查（纯代码，无 LLM）
- Verifier 是语义级审查（LLM 理解规则含义找隐藏问题）

典型能发现的问题：
- 规则语义矛盾（字面上不冲突但逻辑上打架）
- 边界情况漏判
- 术语歧义（同一概念用了不同措辞）
- 反例缺失（业务常见的误判场景没被覆盖）
- 动作与条件不匹配（"高ROI"却给"降价"动作）
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal
import json
import logging

from app.common.ai import call_llm
from app.skills.core.parser import SkillStructured


logger = logging.getLogger(__name__)

Severity = Literal["critical", "high", "medium", "low"]


@dataclass
class VerifyIssue:
    """审查发现的问题。"""
    category: str                    # 问题类别：logic_conflict/boundary/ambiguity/missing_case/mismatch
    severity: Severity
    title: str                       # 简短标题
    description: str                 # 详细说明
    location: str = ""               # 位置（step_id / branch_index 等）
    evidence: str = ""               # 证据（引用的原文）
    suggestion: str = ""             # 修复建议
    confidence: float = 0.0          # AI 置信度 0-1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerifyReport:
    """审查报告。"""
    skill_id: str
    can_publish: bool                # 是否可发布（无 critical/high）
    total_issues: int
    issues: list[VerifyIssue] = field(default_factory=list)
    summary: str = ""                # 一句话总结
    model_id: str = ""               # 使用的模型
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "can_publish": self.can_publish,
            "total_issues": self.total_issues,
            "critical_count": sum(1 for i in self.issues if i.severity == "critical"),
            "high_count": sum(1 for i in self.issues if i.severity == "high"),
            "medium_count": sum(1 for i in self.issues if i.severity == "medium"),
            "low_count": sum(1 for i in self.issues if i.severity == "low"),
            "summary": self.summary,
            "issues": [i.to_dict() for i in self.issues],
            "model_id": self.model_id,
            "latency_ms": self.latency_ms,
        }


# F3: VERIFIER_SYSTEM_PROMPT 已迁移到 app/common/prompts/verifier@v1.md
# 通过 prompt_registry.build("verifier") 获取
def _get_verifier_prompt() -> tuple[str, str]:
    """返回 (rendered, hash)。failover：registry 未加载时返回最小 prompt"""
    try:
        from app.common.prompt_registry import prompt_registry
        return prompt_registry.build("verifier")
    except KeyError:
        return "你是 Skill 审查专家。请找出 Skill 中的逻辑冲突、边界问题、歧义等。", ""


def _build_user_prompt(structured: SkillStructured) -> str:
    """构造用户消息：把 Skill 结构化数据序列化给 LLM 审查。"""
    fm = structured.frontmatter or {}
    parts = [f"## Skill 基本信息\n名称: {fm.get('name', '未命名')}\n部门: {fm.get('department', '未设')}\n目标: {structured.purpose or fm.get('description', '未设')}\n"]

    if structured.steps:
        parts.append("\n## 决策规则")
        for step in structured.steps:
            parts.append(f"\n### Step {step.id}: {step.name}")
            if step.description:
                parts.append(step.description)
            for i, br in enumerate(step.branches):
                parts.append(f"  - 条件: {br.condition}")
                parts.append(f"    结论: {br.conclusion}")
                if br.action:
                    parts.append(f"    动作: {br.action}")
                if br.next_step:
                    parts.append(f"    下一步: {br.next_step}")

    if structured.output_definition:
        parts.append("\n## 输出定义")
        for o in structured.output_definition:
            parts.append(f"- {o.name} ({o.format}) → {o.recipient}")

    if structured.test_cases:
        parts.append(f"\n## 测试用例 ({len(structured.test_cases)} 个)")
        for tc in structured.test_cases[:5]:
            parts.append(f"- {tc.name}: {json.dumps(tc.input_data, ensure_ascii=False)[:100]} → {json.dumps(tc.expected_output, ensure_ascii=False)[:100]}")

    if structured.antipatterns:
        parts.append(f"\n## 反例 ({len(structured.antipatterns)} 个)")
        for ap in structured.antipatterns[:5]:
            parts.append(f"- 误判场景: {ap.scenario} → 正确做法: {ap.correct_action}")

    parts.append("\n## 请审查")
    parts.append("从 5 个维度（logic_conflict/boundary/ambiguity/missing_case/mismatch）找出这个 Skill 的问题。只报告真实问题，不要编造。")

    return "\n".join(parts)


async def verify_skill(skill_id: str, structured: SkillStructured) -> VerifyReport:
    """对 Skill 运行 AI 对抗式审查。

    返回 VerifyReport。失败时返回空报告（不阻断流程）。
    """
    import time
    start = time.monotonic()

    report = VerifyReport(skill_id=skill_id, can_publish=True, total_issues=0)

    try:
        user_prompt = _build_user_prompt(structured)
        verifier_system, _verifier_hash = _get_verifier_prompt()
        resp = await call_llm(
            system=verifier_system,
            user=user_prompt,
            max_tokens=2000,
            temperature=0.3,
            json_mode=True,
        )

        if not resp:
            report.summary = "AI 审查失败（无响应）"
            return report

        data = resp if isinstance(resp, dict) else {}
        issues_raw = data.get("issues", [])
        report.summary = data.get("summary", "")

        for item in issues_raw:
            if not isinstance(item, dict):
                continue
            try:
                issue = VerifyIssue(
                    category=item.get("category", "unknown"),
                    severity=item.get("severity", "medium"),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    location=item.get("location", ""),
                    evidence=item.get("evidence", ""),
                    suggestion=item.get("suggestion", ""),
                    confidence=float(item.get("confidence", 0.5)),
                )
                report.issues.append(issue)
            except (TypeError, ValueError) as e:
                logger.debug(f"跳过无效 issue: {e}")

        report.total_issues = len(report.issues)

        # 有 critical 或 high 时阻断发布
        blocking = [i for i in report.issues if i.severity in ("critical", "high")]
        report.can_publish = len(blocking) == 0

    except Exception as e:
        logger.warning(f"verify_skill 失败: {e}")
        report.summary = f"AI 审查失败: {e}"

    report.latency_ms = int((time.monotonic() - start) * 1000)
    return report
