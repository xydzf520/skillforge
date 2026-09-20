"""SKILL.md 校验器集合。

来源: tripleyak/SkillForge (MIT) 的 quick_validate.py + validate-skill.py + _constants.py
我们做了 3 处适配:
  1. ALLOWED_PROPERTIES 扩展了 SkillForge 自有字段 (department/approval_level/reviewer 等)
  2. 标准里要求的 "Triggers" 章节 (Claude Code 风格的触发短语) 在 SkillForge 用 cron/manual,
     所以这部分校验降级为 warning, 不阻塞
  3. 入口改为 Python 函数, 不依赖 sys.argv / sys.exit, 便于服务层调用

参考: https://github.com/tripleyak/SkillForge
"""

from .quick_validator import quick_validate, QuickValidationResult
from .structural_validator import (
    SkillValidator,
    StructuralValidationReport,
    structural_validate,
)

__all__ = [
    "quick_validate",
    "QuickValidationResult",
    "SkillValidator",
    "StructuralValidationReport",
    "structural_validate",
]
