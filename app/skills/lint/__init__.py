"""SkillForge 静态规则检查引擎 (skillforge-lint)。

纯规则引擎，不依赖 LLM，用于在保存/发布前自动发现 Skill 质量问题。

Phase 1 目标：减少基线中 53% 缺兜底分支、79% 无测试、89% 无反例。
"""

from app.skills.lint.linter import lint_skill, LintReport, LintIssue
from app.skills.lint.rules import ELSE_KEYWORDS, has_else_branch

__all__ = ["lint_skill", "LintReport", "LintIssue", "ELSE_KEYWORDS", "has_else_branch"]
