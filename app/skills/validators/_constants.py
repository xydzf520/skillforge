"""SKILL.md 校验常量。

基础常量来自 tripleyak/SkillForge (MIT 协议) 的 _constants.py。
SKILLFORGE_EXTENSION_PROPERTIES 是我们项目自有的扩展字段。

合并后的 ALLOWED_PROPERTIES 既包含 Anthropic Agent Skills 标准字段, 也包含 SkillForge 平台扩展字段,
让两边的工具都能消费同一个 SKILL.md 文件。
"""

# ===========================================================================
# Anthropic Agent Skills 标准字段
# ===========================================================================

# 必填
REQUIRED_PROPERTIES = {
    "name",          # Skill 标识 (hyphen-case, 最多 64 字)
    "description",   # 触发描述 (最多 1024 字, 不能含 < >)
}

# 标准可选
OPTIONAL_PROPERTIES = {
    "license",         # 许可证 (MIT / Apache-2.0 ...)
    "allowed-tools",   # 工具白名单 (逗号分隔字符串或 list)
    "metadata",        # 自定义嵌套字段
    "model",           # 指定 Claude 模型
    "context",         # 'fork' 表示隔离子代理
    "agent",           # 子代理类型
    "hooks",           # 生命周期钩子
    "user-invocable",  # 是否在斜杠菜单可见
    "compatibility",   # tripleyak 没列, 但 Anthropic 实际支持
}

# ===========================================================================
# SkillForge 平台扩展字段 (我们的额外约定)
# ===========================================================================

SKILLFORGE_EXTENSION_PROPERTIES = {
    "department",          # 所属部门 (EC/retail/...)
    "role",                # 业务角色 (运营/算法/...)
    "instance_id",         # 指定运行节点（openclaw/aiclaw bridge instance）
    "runtime",             # 远端执行运行时配置 (openclaw_agent / hybrid / bridge_script)
    "verify_script_timeout",  # 审核通过后的沙箱验证超时（秒）
    "script_timeout",      # 运行脚本超时（秒）
    "execution_timeout",   # 兼容历史字段，执行超时（秒）
    "trigger_type",        # cron / manual / event — 必填，SkillForge 扩展必填项
    "trigger_expression",  # cron 表达式 / event key
    "risk_level",          # R1-R4 — 必填，SkillForge 扩展必填项
    "approval_level",      # 0-3, 决定是否生成待办
    "target_users",        # 钉钉推送目标 user_id 列表
    "owner",               # Skill 负责人 user_id
    "approver",            # Skill 审核人 user_id
    "reviewer",            # AI 待办 reviewer user_id 列表
    "reviewer_role",       # 按角色解析 reviewer
    "decision_mode",       # any_of / all_of / independent
    "data_sources",        # 依赖的数据源 ID
    "dingtalk",            # 钉钉相关配置 (按部门告警 webhook 等)
}

# SkillForge 平台必填项（Anthropic 标准必填 + SkillForge 扩展必填）
# 统一前后端校验：前端 skill-md-validator / 后端 structural_validator /
# 后端 create_skill 都视这些为 error 级别
SKILLFORGE_REQUIRED_PROPERTIES = REQUIRED_PROPERTIES | {"trigger_type", "risk_level"}

# 全部允许的字段
ALLOWED_PROPERTIES = (
    REQUIRED_PROPERTIES | OPTIONAL_PROPERTIES | SKILLFORGE_EXTENSION_PROPERTIES
)

# 推荐字段 (缺失只 warning)
RECOMMENDED_PROPERTIES = {"license"}

# trigger_type 合法值（前后端对齐）
VALID_TRIGGER_TYPES = {"manual", "cron", "event"}
# risk_level 合法值（前后端对齐）
VALID_RISK_LEVELS = {"R1", "R2", "R3", "R4"}

# ===========================================================================
# Anthropic 子代理 / hooks 相关
# ===========================================================================

VALID_AGENT_TYPES = {"Explore", "Plan", "general-purpose"}
VALID_HOOK_EVENTS = {"PreToolUse", "PostToolUse", "Stop"}
VALID_HOOK_TYPES = {"command", "prompt"}

KNOWN_TOOLS = {
    "Read", "Glob", "Grep", "Write", "Edit",
    "Bash", "Task", "WebFetch", "WebSearch",
    "TodoWrite", "NotebookEdit", "AskUserQuestion",
}

# ===========================================================================
# 字段约束
# ===========================================================================

NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 1024

# Skill name: 必须以小写字母开头, 只能含小写字母 / 数字 / 连字符, 不能连续连字符
NAME_REGEX = r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$"

SEMVER_REGEX = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$"

FRONTMATTER_REGEX = r"^---\r?\n(.*?)\r?\n---"

# ===========================================================================
# SkillForge 内容尺寸约束 (从 tripleyak Phase 4 综合评审借鉴)
# ===========================================================================

# SKILL.md 主文件超过这个行数会触发 warning, 提示拆分到 references/
SKILL_MD_LINES_WARN = 500
# SKILL.md 主文件超过这个行数直接 fail, 强制拆分
SKILL_MD_LINES_HARD_LIMIT = 1000
