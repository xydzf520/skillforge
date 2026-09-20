"""SkillMember / SkillTag 兼容 shim 测试。"""

from app.skills.core.access import SkillMember as CoreSkillMember
from app.skills.core.access import SkillTag as CoreSkillTag
from app.skills.members import SkillMember, SkillTag


def test_members_shim_reexports_core_models():
    assert SkillMember is CoreSkillMember
    assert SkillTag is CoreSkillTag
    assert SkillMember.__tablename__ == "skill_members"
    assert SkillTag.__tablename__ == "skill_tags"
