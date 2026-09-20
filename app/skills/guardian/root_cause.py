"""异常归因分析 — 关联执行日志、版本 diff、数据分布，给出根因排序和修复建议。"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.guardian.anomaly_detector import Anomaly


@dataclass
class RootCause:
    """一个潜在根因。"""
    cause: str                       # 根因描述
    probability: float               # 0-1 概率
    category: str                    # "data_source" | "rule_change" | "param_change" | "dependency"
    evidence: dict = field(default_factory=dict)
    fix_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RootCauseReport:
    """归因报告。"""
    skill_id: str
    anomaly: dict                    # 原始异常
    causes: list[RootCause] = field(default_factory=list)
    summary: str = ""
    candidate_patches: list[dict] = field(default_factory=list)  # 候选修复

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "anomaly": self.anomaly,
            "causes": [c.to_dict() for c in self.causes],
            "summary": self.summary,
            "candidate_patches": self.candidate_patches,
        }


async def analyze_root_cause(
    db: AsyncSession,
    skill_id: str,
    anomaly: Anomaly,
) -> RootCauseReport:
    """对检测到的异常做归因分析。

    目前是基于启发式规则的归因（production 版本应该用 LLM + 统计分析）。
    """
    report = RootCauseReport(skill_id=skill_id, anomaly=anomaly.to_dict())

    # 启发式 1: 最近是否有 Skill 版本变更
    try:
        from app.skills.core.git_service import git_service
        history = git_service.log(skill_id, max_count=5)
        if history:
            recent_commit = history[0]
            # 如果最近有变更，高概率是变更导致（date 字段来自 git_service.log）
            report.causes.append(RootCause(
                cause=f"最近版本变更可能引入问题（{recent_commit.get('message', '')}）",
                probability=0.6,
                category="rule_change",
                evidence={"commit": recent_commit, "commit_date": recent_commit.get("date", "")},
                fix_suggestions=[
                    f"查看 commit {recent_commit.get('hash', '')[:7]} 的 diff",
                    "考虑回滚到前一版本",
                    "在影子环境验证回滚效果",
                ],
            ))
    except Exception as e:
        from loguru import logger
        logger.debug("root_cause git 历史读取失败 skill_id={}: {}", skill_id, e)

    # 启发式 2: 根据异常维度推测
    if anomaly.dimension == "overall" and anomaly.metric == "failure_rate":
        # 整体失败率上升 → 可能是数据源或依赖问题
        report.causes.append(RootCause(
            cause="数据源可能异常或延迟",
            probability=0.55,
            category="data_source",
            evidence={},
            fix_suggestions=[
                "检查上游数据源的新鲜度",
                "查看最近数据分布是否有漂移",
                "增加数据缺失的兜底分支",
            ],
        ))
    elif anomaly.dimension == "branch":
        # 某个分支命中率突变 → 数据分布变化
        report.causes.append(RootCause(
            cause=f"Step 命中分布变化，可能输入数据特征变了",
            probability=0.5,
            category="data_source",
            evidence={"affected_step": anomaly.segment},
            fix_suggestions=[
                f"检查该 Step 依赖的输入字段分布",
                "考虑调整该 Step 的阈值",
            ],
        ))

    # 根据概率排序
    report.causes.sort(key=lambda c: -c.probability)

    if report.causes:
        top = report.causes[0]
        report.summary = f"最可能的原因（{top.probability*100:.0f}%）: {top.cause}"
    else:
        report.summary = "未能识别明确根因，建议人工调查"

    # §6.4 候选修复 Patch 按 3 类组织：
    #   - param_tunable: 参数可调（调阈值、切档位）
    #   - rule_missing_branch: 规则缺支路（加 else、加人工分支）
    #   - data_source_issue: 数据源异常（降级、告警、新鲜度检查）
    if anomaly.severity in ("high", "critical"):
        report.candidate_patches.append({
            "category": "rule_missing_branch",
            "type": "emergency_stop",
            "description": "暂停自动执行，等待人工介入",
            "risk": "low",
        })

    if anomaly.dimension == "branch":
        # 分支命中突变 → 参数可调
        report.candidate_patches.append({
            "category": "param_tunable",
            "type": "adjust_threshold",
            "description": f"调整 {anomaly.segment} 的阈值以恢复正常命中分布",
            "risk": "medium",
        })

    if anomaly.metric == "failure_rate" and anomaly.dimension == "overall":
        # 整体失败率上升 → 数据源可能异常
        report.candidate_patches.append({
            "category": "data_source_issue",
            "type": "check_data_freshness",
            "description": "检查上游数据源新鲜度与字段缺失率",
            "risk": "low",
        })
        report.candidate_patches.append({
            "category": "data_source_issue",
            "type": "enable_fallback_source",
            "description": "启用备用数据源降级方案",
            "risk": "medium",
        })

    # 基于 causes 的 category 补充 rule_missing_branch 建议
    has_data_source_cause = any(c.category == "data_source" for c in report.causes)
    has_rule_cause = any(c.category == "rule_change" for c in report.causes)
    if has_data_source_cause:
        report.candidate_patches.append({
            "category": "rule_missing_branch",
            "type": "add_data_missing_branch",
            "description": "新增数据缺失/异常的兜底分支",
            "risk": "low",
        })
    if has_rule_cause:
        report.candidate_patches.append({
            "category": "param_tunable",
            "type": "rollback_previous_version",
            "description": "回滚到前一版本观察",
            "risk": "medium",
        })

    return report
