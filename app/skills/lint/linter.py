"""Skill 静态检查主引擎。"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal

from app.skills.core.parser import SkillStructured


Severity = Literal["error", "warning", "info"]


@dataclass
class LintIssue:
    """单个检查问题。"""
    rule: str                        # 规则 ID，如 "branch_completeness"
    severity: Severity               # error=发布阻断, warning=建议修, info=提示
    message: str                     # 人类可读的描述
    location: str = ""               # 问题位置（step_id / param_name / test_name）
    suggestion: str = ""             # 修复建议（可由 AI 填充）
    auto_fixable: bool = False       # 是否可自动修复

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LintReport:
    """检查报告。"""
    skill_id: str
    passed: bool                     # 是否通过（无 error）
    total_issues: int
    errors: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    infos: list[LintIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "passed": self.passed,
            "total_issues": self.total_issues,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "info_count": len(self.infos),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "infos": [i.to_dict() for i in self.infos],
        }

    def add(self, issue: LintIssue):
        if issue.severity == "error":
            self.errors.append(issue)
        elif issue.severity == "warning":
            self.warnings.append(issue)
        else:
            self.infos.append(issue)
        self.total_issues += 1


def lint_skill(skill_id: str, structured: SkillStructured) -> LintReport:
    """对 Skill 运行全部检查规则，返回报告。"""
    from app.skills.lint.rules import (
        check_branch_completeness,
        check_threshold_conflict,
        check_threshold_gap,
        check_unreachable_branch,
        check_term_consistency,
        check_test_coverage,
        check_param_usage,
        check_metadata,
    )

    report = LintReport(skill_id=skill_id, passed=True, total_issues=0)

    # 依次运行所有检查规则
    checkers = [
        check_metadata,               # 基础元信息
        check_branch_completeness,    # 分支完备性（兜底 else）
        check_unreachable_branch,     # 不可达分支
        check_threshold_conflict,     # 阈值冲突
        check_threshold_gap,          # 阈值空洞
        check_term_consistency,       # 术语一致性
        check_test_coverage,          # 测试覆盖
        check_param_usage,            # 参数使用
    ]

    for checker in checkers:
        try:
            issues = checker(structured)
            for issue in issues:
                report.add(issue)
        except Exception as e:
            report.add(LintIssue(
                rule=checker.__name__,
                severity="warning",
                message=f"检查规则内部错误: {e}",
            ))

    report.passed = len(report.errors) == 0
    return report
