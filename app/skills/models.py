"""兼容层：保留 `app.skills.models` 旧导入路径。"""

from app.skills.asset_models import SkillInstance, SkillLineage, SkillRelease, SkillTemplate
from app.skills.core.models import Skill, SkillLock, UserSkillPin

__all__ = [
    "Skill",
    "SkillLock",
    "UserSkillPin",
    "SkillTemplate",
    "SkillInstance",
    "SkillRelease",
    "SkillLineage",
]
