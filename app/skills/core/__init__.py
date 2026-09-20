"""app.skills.core — Skill 体系的共享基础层。

这里承载跨子域复用的稳定契约：模型、id 生成、解析、git、成员/访问校验，
以及被 `tooling` / `lifecycle` / `intelligence` 共同消费的只读 prompt / index。
顶层 `app.skills.members` / `app.skills.intelligence.*` 中仍有兼容别名，但规范入口在这里。
"""

# 常用符号聚合导出（让 `from app.skills.core import Skill, skill_parser` 直接可用）
from app.skills.core.id_gen import (  # noqa: F401
    AUTO_PREFIXES,
    SKILL_ID_PATTERN,
    classify_skill_id,
    gen_for_test,
    gen_from_fork,
    gen_from_import,
    gen_from_name,
    is_e2e_test_id,
    validate_skill_id,
)
from app.skills.core.models import Skill, SkillLock, UserSkillPin  # noqa: F401
from app.skills.core.parser import skill_parser, SkillParser, SkillStructured  # noqa: F401
from app.skills.core.access import (  # noqa: F401
    SkillMember,
    SkillTag,
    get_skill_member,
    list_user_member_skill_ids,
    list_user_member_scope,
)
from app.skills.core.methodology import (  # noqa: F401
    ELEVEN_LENSES,
    ENHANCED_SYSTEM_PROMPT,
    PHASE_0_TRIAGE_PROMPT,
    render_phase_1_prompt,
)
from app.skills.core.param_index import (  # noqa: F401
    build_param_index,
    find_param_usages,
    get_param_index,
    invalidate_param_index,
)
from app.skills.core.service_shared import (  # noqa: F401
    ensure_skill_access,
    ensure_skill_department_access,
    parse_approval_level,
)
