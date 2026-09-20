"""快速 SKILL.md 校验 — frontmatter + 命名 + 长度。

适用场景: 用户在 Web 编辑器点保存 / 调 POST /api/skills 时。
速度 < 1ms, 不依赖文件系统 (接受字符串输入)。

适配自 tripleyak/SkillForge (MIT) scripts/quick_validate.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from ._constants import (
    ALLOWED_PROPERTIES,
    DESCRIPTION_MAX_LENGTH,
    FRONTMATTER_REGEX,
    NAME_MAX_LENGTH,
    NAME_REGEX,
    SKILLFORGE_REQUIRED_PROPERTIES,
    SKILL_MD_LINES_HARD_LIMIT,
    SKILL_MD_LINES_WARN,
    VALID_RISK_LEVELS,
    VALID_TRIGGER_TYPES,
)


@dataclass
class QuickValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    frontmatter: dict | None = None

    def to_detail(self) -> dict:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
        }


def quick_validate(skill_md: str) -> QuickValidationResult:
    """对 SKILL.md 字符串做快速 frontmatter 校验。

    不检查文件结构 / triggers / 章节, 那是 structural_validator 的活。
    """
    result = QuickValidationResult(ok=False)

    if not skill_md or not isinstance(skill_md, str):
        result.errors.append("SKILL.md 内容为空")
        return result

    if not skill_md.startswith("---"):
        result.errors.append("缺少 YAML frontmatter (文件必须以 --- 开头)")
        return result

    match = re.match(FRONTMATTER_REGEX, skill_md, re.DOTALL)
    if not match:
        result.errors.append("frontmatter 格式不合法 (需要 --- 包裹的 YAML)")
        return result

    frontmatter_text = match.group(1)
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        result.errors.append(f"frontmatter YAML 解析失败: {exc}")
        return result

    if frontmatter is None:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        result.errors.append(
            f"frontmatter 必须是字典, 实际是 {type(frontmatter).__name__}"
        )
        return result

    result.frontmatter = frontmatter

    # 1) 未知字段 — error (避免 typo 静默通过)
    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        result.errors.append(
            f"frontmatter 出现未知字段: {sorted(unexpected_keys)}。"
            f"允许的字段: {sorted(ALLOWED_PROPERTIES)}"
        )

    # 2) 必填字段 — SkillForge 扩展必填包含 trigger_type / risk_level
    for field_name in SKILLFORGE_REQUIRED_PROPERTIES:
        if field_name not in frontmatter or not frontmatter.get(field_name):
            result.errors.append(f"frontmatter 缺少必填字段: {field_name}")

    # trigger_type / risk_level 值域 — 与前端 skill-md-validator / create_skill 一致
    trigger_type = frontmatter.get("trigger_type")
    if trigger_type is not None and trigger_type not in VALID_TRIGGER_TYPES:
        result.errors.append(
            f"trigger_type 值无效: {trigger_type!r}（应为 {'/'.join(sorted(VALID_TRIGGER_TYPES))}）"
        )
    risk_level = frontmatter.get("risk_level")
    if risk_level is not None and risk_level not in VALID_RISK_LEVELS:
        result.errors.append(
            f"risk_level 值无效: {risk_level!r}（应为 {'/'.join(sorted(VALID_RISK_LEVELS))}）"
        )

    # 3) name 格式
    # SkillForge 用 skill_id (ASCII) 做唯一 ID, frontmatter.name 是显示名,
    # 中文 / Unicode 是合法的; 但纯 ASCII 名字仍然要求 hyphen-case 防 typo
    name = frontmatter.get("name")
    if isinstance(name, str) and name:
        name_clean = name.strip()
        is_pure_ascii = name_clean.isascii()
        if is_pure_ascii and not re.match(NAME_REGEX, name_clean):
            result.errors.append(
                f"name '{name_clean}' 不是 hyphen-case (小写字母开头, 只允许小写字母/数字/连字符)。"
                f"如需中文显示名直接用中文; 用英文请用 hyphen-case"
            )
        if is_pure_ascii and "--" in name_clean:
            result.errors.append(f"name '{name_clean}' 不允许连续连字符")
        if len(name_clean) > NAME_MAX_LENGTH:
            result.errors.append(
                f"name 长度 {len(name_clean)} 超过上限 {NAME_MAX_LENGTH}"
            )

    # 4) description 校验
    description = frontmatter.get("description")
    if isinstance(description, str):
        desc_clean = description.strip()
        if "<" in desc_clean or ">" in desc_clean:
            result.errors.append("description 不允许出现尖括号 (< >)")
        if len(desc_clean) > DESCRIPTION_MAX_LENGTH:
            result.warnings.append(
                f"description 长度 {len(desc_clean)} 超过推荐上限 {DESCRIPTION_MAX_LENGTH}"
            )

    # 5) approval_level 范围 (SkillForge 扩展)
    approval_level = frontmatter.get("approval_level")
    if approval_level is not None:
        try:
            level_int = int(approval_level)
            if level_int < 0 or level_int > 3:
                result.errors.append(
                    f"approval_level 必须在 0-3 之间, 当前 {level_int}"
                )
        except (TypeError, ValueError):
            result.errors.append(
                f"approval_level 必须是整数, 当前 {type(approval_level).__name__}"
            )

    # 6) decision_mode (SkillForge 扩展)
    decision_mode = frontmatter.get("decision_mode")
    if decision_mode is not None and decision_mode not in {
        "any_of", "all_of", "independent"
    }:
        result.errors.append(
            f"decision_mode 必须是 any_of/all_of/independent 之一, 当前 {decision_mode}"
        )

    # 7) 行数: 必须在 write_file 之前拦截, 防止文件被污染
    line_count = len(skill_md.splitlines())
    if line_count > SKILL_MD_LINES_HARD_LIMIT:
        result.errors.append(
            f"SKILL.md 行数 {line_count} 超过硬上限 {SKILL_MD_LINES_HARD_LIMIT}, "
            f"请把详细内容移到 references/"
        )
    elif line_count > SKILL_MD_LINES_WARN:
        result.warnings.append(
            f"SKILL.md 行数 {line_count} 超过推荐上限 {SKILL_MD_LINES_WARN}, "
            f"建议拆分到 references/"
        )

    result.ok = not result.errors
    return result
