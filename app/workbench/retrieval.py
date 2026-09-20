"""三重检索 — 从 Skill 库中学习模式。

1. 业务语义相似：目标描述的文本相似度（Jaccard / TF-IDF 轻量版）
2. 规则结构相似：AST 层面的分支数/step 数/参数数
3. 历史效果相似：优先推荐 active/shadow 状态且有测试用例的 Skill
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.core.models import Skill
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser
from app.skills.lint import has_else_branch


@dataclass
class SimilarSkill:
    """相似 Skill 结果。"""
    skill_id: str
    name: str
    department: str
    status: str
    semantic_score: float = 0.0      # 业务语义相似度
    structural_score: float = 0.0    # 规则结构相似度
    quality_score: float = 0.0       # 质量/效果得分
    total_score: float = 0.0
    reason: str = ""                 # 为什么推荐
    motifs: list[str] = field(default_factory=list)  # 可复用的模式

    def to_dict(self) -> dict:
        return asdict(self)


def _tokenize(text: str) -> set[str]:
    """简单中文分词：按字符 + 英文单词切分。"""
    if not text:
        return set()
    # 英文单词
    words = set(re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", text.lower()))
    # 中文双字符 ngram
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in chinese:
        for i in range(len(chunk) - 1):
            words.add(chunk[i:i+2])
    return words


def _semantic_similarity(q: str, text: str) -> float:
    """Jaccard 相似度。"""
    a = _tokenize(q)
    b = _tokenize(text)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _structural_features(structured) -> dict[str, Any]:
    """提取结构特征。"""
    return {
        "step_count": len(structured.steps or []),
        "total_branches": sum(len(s.branches) for s in (structured.steps or [])),
        "has_tests": bool(structured.test_cases),
        "has_antipatterns": bool(structured.antipatterns),
        "has_data_inputs": bool(structured.data_inputs),
    }


def _structural_similarity(target_profile: dict, candidate_profile: dict) -> float:
    """结构相似度：基于特征向量的相对距离。"""
    if not target_profile or not candidate_profile:
        return 0.0

    diffs = []
    for key in ("step_count", "total_branches"):
        a = target_profile.get(key, 0)
        b = candidate_profile.get(key, 0)
        if a == 0 and b == 0:
            diffs.append(1.0)
        else:
            diffs.append(1.0 - abs(a - b) / max(a, b, 1))

    # 二值特征
    for key in ("has_tests", "has_antipatterns", "has_data_inputs"):
        diffs.append(1.0 if target_profile.get(key) == candidate_profile.get(key) else 0.5)

    return sum(diffs) / len(diffs) if diffs else 0.0


def _quality_score(skill: Skill, structured) -> float:
    """质量得分：基于 Skill 状态、测试覆盖、反例完备性。"""
    score = 0.0
    # 状态加分
    if skill.status == "active":
        score += 0.4
    elif skill.status == "shadow":
        score += 0.3
    elif skill.status == "draft":
        score += 0.1

    # 有测试加分
    if structured.test_cases:
        score += 0.2
    if len(structured.test_cases or []) >= 3:
        score += 0.1

    # 有反例加分
    if structured.antipatterns:
        score += 0.15

    # 有完备兜底加分
    if structured.steps and all(has_else_branch(s.branches) for s in structured.steps):
        score += 0.15

    return min(score, 1.0)


def _extract_motifs(structured) -> list[str]:
    """从 Skill 结构中提取可复用的 motif 名称。"""
    motifs = []
    steps = structured.steps or []

    # 阶梯决策（3+ 分支，其中有阈值比较）
    for step in steps:
        if len(step.branches) >= 3:
            has_gt = any(">" in (b.condition or "") for b in step.branches)
            has_lt = any("<" in (b.condition or "") for b in step.branches)
            if has_gt and has_lt:
                motifs.append("阶梯决策")
                break

    # 数据兜底（有 else 分支且包含"缺失"、"默认"）
    for step in steps:
        for br in step.branches:
            cond = (br.condition or "").lower()
            if "缺失" in cond or "默认" in cond or "null" in cond:
                motifs.append("数据兜底")
                break
        if "数据兜底" in motifs:
            break

    # 阶段流程（多个 step，每个 step 只有 1-2 个分支）
    if len(steps) >= 3 and all(len(s.branches) <= 2 for s in steps):
        motifs.append("阶段流程")

    # 反例防护
    if structured.antipatterns and len(structured.antipatterns) >= 2:
        motifs.append("反例防护")

    return list(set(motifs))


async def find_similar_skills(
    db: AsyncSession,
    query: str,
    *,
    target_profile: dict | None = None,
    limit: int = 5,
    exclude_id: str | None = None,
) -> list[SimilarSkill]:
    """三重检索：返回 top-k 相似 Skill。

    Args:
        db: 数据库会话
        query: 业务描述文本
        target_profile: 可选的目标结构画像（来自 Blueprint）
        limit: 返回数量
        exclude_id: 排除的 Skill ID（通常是当前编辑的）
    """
    result = await db.execute(select(Skill).order_by(Skill.updated_at.desc()).limit(200))
    skills = result.scalars().all()

    scored: list[SimilarSkill] = []

    for skill in skills:
        if exclude_id and skill.id == exclude_id:
            continue

        # 读取 SKILL.md 并解析
        try:
            md = git_service.read_file(skill.id, "SKILL.md")
            if not md:
                continue
            structured = skill_parser.parse(md)
        except Exception:
            continue

        # 构造候选文本用于语义匹配
        candidate_text = " ".join([
            skill.name or "",
            skill.description or "",
            structured.purpose or "",
        ])

        semantic = _semantic_similarity(query, candidate_text)
        if semantic < 0.02 and target_profile is None:
            continue  # 语义太远直接跳过

        structural = 0.0
        if target_profile:
            candidate_profile = _structural_features(structured)
            structural = _structural_similarity(target_profile, candidate_profile)

        quality = _quality_score(skill, structured)

        # 加权总分
        total = semantic * 0.5 + structural * 0.3 + quality * 0.2

        motifs = _extract_motifs(structured)

        reason_parts = []
        if semantic > 0.3:
            reason_parts.append(f"业务语义相似 {semantic*100:.0f}%")
        elif semantic > 0.1:
            reason_parts.append("业务语义相关")
        if structural > 0.6:
            reason_parts.append(f"规则结构类似")
        if skill.status == "active":
            reason_parts.append("已上线")
        if motifs:
            reason_parts.append(f"复用模式: {', '.join(motifs[:2])}")

        scored.append(SimilarSkill(
            skill_id=skill.id,
            name=skill.name,
            department=skill.department or "",
            status=skill.status or "",
            semantic_score=round(semantic, 3),
            structural_score=round(structural, 3),
            quality_score=round(quality, 3),
            total_score=round(total, 3),
            reason=" · ".join(reason_parts) or "可参考",
            motifs=motifs,
        ))

    scored.sort(key=lambda x: -x.total_score)
    return scored[:limit]
