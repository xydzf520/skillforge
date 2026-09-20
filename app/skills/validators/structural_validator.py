"""SKILL.md + 完整目录结构校验。

适用场景: git commit 之前 / 审核中心 reviewer 看 diff 之前 / 一键检查工具。
检查 frontmatter + 章节结构 + references/ + scripts/ + 行数。

适配自 tripleyak/SkillForge (MIT) scripts/validate-skill.py。
SkillForge 改动:
  1. 不依赖 sys.argv, 接受 Path 或字符串路径作为函数参数
  2. "Triggers" / "Process" / "Anti-Patterns" 这些 Claude Code 风格章节降级为 warning
     (SkillForge skill 用 cron/manual 触发, 没有这套结构)
  3. 行数硬上限 (SKILL_MD_LINES_HARD_LIMIT) 当 error 处理, 强制拆分到 references/
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ._constants import (
    ALLOWED_PROPERTIES,
    DESCRIPTION_MAX_LENGTH,
    KNOWN_TOOLS,
    NAME_MAX_LENGTH,
    NAME_REGEX,
    RECOMMENDED_PROPERTIES,
    SEMVER_REGEX,
    SKILLFORGE_REQUIRED_PROPERTIES,
    SKILL_MD_LINES_HARD_LIMIT,
    SKILL_MD_LINES_WARN,
    VALID_AGENT_TYPES,
    VALID_HOOK_EVENTS,
    VALID_HOOK_TYPES,
    VALID_RISK_LEVELS,
    VALID_TRIGGER_TYPES,
)


@dataclass
class StructuralValidationReport:
    ok: bool
    skill_id: str
    skill_md_path: str
    checks_passed: int = 0
    checks_total: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_detail(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def format_text(self) -> str:
        lines = [
            "=" * 60,
            f"Skill 结构校验: {self.skill_id}",
            "=" * 60,
            f"文件: {self.skill_md_path}",
            f"通过: {self.checks_passed}/{self.checks_total}",
        ]
        if self.errors:
            lines.append("\n--- 错误 ---")
            lines.extend(f"  ✗ {e}" for e in self.errors)
        if self.warnings:
            lines.append("\n--- 警告 ---")
            lines.extend(f"  ⚠ {w}" for w in self.warnings)
        if not self.errors and not self.warnings:
            lines.append("\n✓ 全部通过")
        return "\n".join(lines)


class SkillValidator:
    """完整结构校验器。

    用法:
        v = SkillValidator("/path/to/skills-repo/my-skill")
        report = v.validate()
        if not report.ok:
            print(report.format_text())
    """

    def __init__(self, skill_path: str | Path):
        self.skill_path = Path(skill_path)
        self.skill_md_path = self._find_skill_md()
        self.content: str = ""
        self.frontmatter: dict = {}
        self.report = StructuralValidationReport(
            ok=False,
            skill_id=self.skill_path.name,
            skill_md_path=str(self.skill_md_path),
        )

    def _find_skill_md(self) -> Path:
        for name in ("SKILL.md", "skill.md"):
            p = self.skill_path / name
            if p.exists():
                return p
        return self.skill_path / "SKILL.md"

    def _load(self) -> bool:
        if not self.skill_md_path.exists():
            self.report.errors.append(f"SKILL.md 不存在: {self.skill_md_path}")
            return False
        try:
            self.content = self.skill_md_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.report.errors.append(f"读取 SKILL.md 失败: {exc}")
            return False
        return True

    def _parse_frontmatter(self) -> bool:
        match = re.match(r"^---\r?\n(.*?)\r?\n---", self.content, re.DOTALL)
        if not match:
            self.report.errors.append("缺少 YAML frontmatter")
            return False
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            self.report.errors.append(f"frontmatter YAML 解析失败: {exc}")
            return False
        if parsed is None:
            self.frontmatter = {}
        elif not isinstance(parsed, dict):
            self.report.errors.append(
                f"frontmatter 必须是字典, 实际是 {type(parsed).__name__}"
            )
            return False
        else:
            self.frontmatter = parsed
        return True

    def _check(self, condition: bool, error_msg: str, *, warning: bool = False) -> bool:
        self.report.checks_total += 1
        if condition:
            self.report.checks_passed += 1
            return True
        if warning:
            self.report.warnings.append(error_msg)
        else:
            self.report.errors.append(error_msg)
        return False

    # ── frontmatter 校验 ──────────────────────────────────────────

    def _validate_frontmatter(self) -> None:
        # SkillForge 必填项 = Anthropic 必填（name/description）+ SkillForge 扩展必填（trigger_type/risk_level）
        for field_name in SKILLFORGE_REQUIRED_PROPERTIES:
            self._check(
                bool(self.frontmatter.get(field_name)),
                f"frontmatter 缺少必填字段: {field_name}",
            )

        # trigger_type / risk_level 值域校验 — 与 create_skill / 前端一致
        trigger_type = self.frontmatter.get("trigger_type")
        if trigger_type is not None:
            self._check(
                trigger_type in VALID_TRIGGER_TYPES,
                f"trigger_type 值无效: {trigger_type!r}（应为 {'/'.join(sorted(VALID_TRIGGER_TYPES))}）",
            )
        risk_level = self.frontmatter.get("risk_level")
        if risk_level is not None:
            self._check(
                risk_level in VALID_RISK_LEVELS,
                f"risk_level 值无效: {risk_level!r}（应为 {'/'.join(sorted(VALID_RISK_LEVELS))}）",
            )

        for field_name in RECOMMENDED_PROPERTIES:
            self._check(
                field_name in self.frontmatter,
                f"frontmatter 推荐补充字段: {field_name}",
                warning=True,
            )

        unexpected = set(self.frontmatter.keys()) - ALLOWED_PROPERTIES
        if unexpected:
            self._check(
                False,
                f"frontmatter 出现未知字段: {sorted(unexpected)}",
            )

        # name 校验
        # SkillForge 允许中文显示名; 仅纯 ASCII 名要求 hyphen-case
        name = self.frontmatter.get("name")
        if isinstance(name, str) and name:
            n = name.strip()
            if n.isascii():
                self._check(
                    bool(re.match(NAME_REGEX, n)) and "--" not in n,
                    f"ASCII name '{n}' 必须是 hyphen-case",
                )
            self._check(
                len(n) <= NAME_MAX_LENGTH,
                f"name 长度 {len(n)} 超过 {NAME_MAX_LENGTH}",
            )

        # description 校验
        desc = self.frontmatter.get("description")
        if isinstance(desc, str):
            self._check(
                "<" not in desc and ">" not in desc,
                "description 不允许出现尖括号",
            )
            self._check(
                len(desc) <= DESCRIPTION_MAX_LENGTH,
                f"description 长度 {len(desc)} 超过 {DESCRIPTION_MAX_LENGTH}",
                warning=True,
            )

        # version 应在 metadata 下而非根
        if "version" in self.frontmatter:
            self._check(
                False,
                "'version' 应放在 metadata 下而非根字段",
                warning=True,
            )

        # metadata.version semver
        meta = self.frontmatter.get("metadata")
        if isinstance(meta, dict):
            v = meta.get("version")
            if v:
                self._check(
                    bool(re.match(SEMVER_REGEX, str(v))),
                    f"metadata.version 不是 semver 格式: {v}",
                    warning=True,
                )

        # context / agent 校验
        if "context" in self.frontmatter:
            self._check(
                self.frontmatter["context"] == "fork",
                f"context 当前只支持 'fork', 实际 {self.frontmatter['context']}",
                warning=True,
            )

        if "agent" in self.frontmatter:
            self._check(
                self.frontmatter["agent"] in VALID_AGENT_TYPES,
                f"agent 必须是 {VALID_AGENT_TYPES} 之一",
                warning=True,
            )
            if self.frontmatter.get("context") != "fork":
                self._check(
                    False,
                    "agent 字段需要同时设置 context: fork",
                    warning=True,
                )

        if "user-invocable" in self.frontmatter:
            self._check(
                isinstance(self.frontmatter["user-invocable"], bool),
                "user-invocable 必须是布尔",
            )

        # SkillForge 扩展字段校验
        approval_level = self.frontmatter.get("approval_level")
        if approval_level is not None:
            try:
                level_int = int(approval_level)
                self._check(
                    0 <= level_int <= 3,
                    f"approval_level 必须在 0-3, 当前 {level_int}",
                )
            except (TypeError, ValueError):
                self._check(False, "approval_level 必须是整数")

        decision_mode = self.frontmatter.get("decision_mode")
        if decision_mode is not None:
            self._check(
                decision_mode in {"any_of", "all_of", "independent"},
                f"decision_mode 必须是 any_of/all_of/independent, 当前 {decision_mode}",
            )

        self._validate_allowed_tools()
        self._validate_hooks()

    def _validate_allowed_tools(self) -> None:
        if "allowed-tools" not in self.frontmatter:
            return
        v = self.frontmatter["allowed-tools"]
        if isinstance(v, str):
            tools = [t.strip() for t in v.split(",") if t.strip()]
        elif isinstance(v, list):
            tools = [str(t) for t in v]
        else:
            self._check(False, f"allowed-tools 必须是字符串或列表, 实际 {type(v).__name__}")
            return
        unknown = [t for t in tools if t not in KNOWN_TOOLS]
        if unknown:
            self._check(
                False,
                f"allowed-tools 出现未知工具: {unknown}",
                warning=True,
            )

    def _validate_hooks(self) -> None:
        if "hooks" not in self.frontmatter:
            return
        hooks = self.frontmatter["hooks"]
        if not isinstance(hooks, dict):
            self._check(False, f"hooks 必须是字典, 实际 {type(hooks).__name__}")
            return
        for hook_name, cfg in hooks.items():
            self._check(
                hook_name in VALID_HOOK_EVENTS,
                f"未知 hook 事件: {hook_name}",
            )
            if not isinstance(cfg, list):
                self._check(False, f"hook '{hook_name}' 配置必须是 list")
                continue
            for i, matcher_cfg in enumerate(cfg):
                if not isinstance(matcher_cfg, dict):
                    self._check(False, f"hook {hook_name}[{i}] 必须是 object")
                    continue
                if hook_name in {"PreToolUse", "PostToolUse"}:
                    self._check(
                        "matcher" in matcher_cfg,
                        f"hook {hook_name}[{i}] 缺 matcher",
                        warning=True,
                    )
                inner = matcher_cfg.get("hooks", [])
                if not isinstance(inner, list):
                    self._check(False, f"hook {hook_name}[{i}].hooks 必须是 list")
                    continue
                for j, ih in enumerate(inner):
                    if not isinstance(ih, dict):
                        continue
                    htype = ih.get("type")
                    if htype:
                        self._check(
                            htype in VALID_HOOK_TYPES,
                            f"hook type 必须是 {VALID_HOOK_TYPES}, 当前 {htype}",
                        )
                    if htype == "command":
                        self._check(
                            bool(ih.get("command")),
                            "command 类型 hook 必须有非空 command 字段",
                        )

    # ── 章节 / 文档结构校验 ──────────────────────────────────────

    def _validate_structure(self) -> None:
        # H1 标题
        has_h1 = bool(re.match(r"---.*?---\s*\n#\s+", self.content, re.DOTALL))
        self._check(has_h1, "frontmatter 后应有 H1 标题", warning=True)

        # 行数限制
        line_count = len(self.content.splitlines())
        if line_count > SKILL_MD_LINES_HARD_LIMIT:
            self._check(
                False,
                f"SKILL.md 行数 {line_count} 超过硬上限 {SKILL_MD_LINES_HARD_LIMIT}, "
                f"请把详细内容移到 references/",
            )
        elif line_count > SKILL_MD_LINES_WARN:
            self._check(
                False,
                f"SKILL.md 行数 {line_count} 超过推荐上限 {SKILL_MD_LINES_WARN}, "
                f"建议拆分到 references/",
                warning=True,
            )

    def _validate_references_dir(self) -> None:
        line_count = len(self.content.splitlines())
        if line_count <= 200:
            return
        refs = self.skill_path / "references"
        self._check(
            refs.exists() and any(refs.iterdir()),
            f"复杂 Skill (>{line_count}行) 建议有 references/ 目录",
            warning=True,
        )

    def _validate_scripts_dir(self) -> None:
        scripts_path = self.skill_path / "scripts"
        if not scripts_path.exists():
            python_examples = len(re.findall(r"python\s+scripts/", self.content))
            if python_examples > 0:
                self._check(
                    False,
                    "SKILL.md 引用了 scripts/ 但目录不存在",
                )
            return
        # 简单检查脚本头部规范
        for script in scripts_path.glob("*.py"):
            try:
                txt = script.read_text(encoding="utf-8")
            except Exception:
                continue
            if script.name.startswith("_"):
                continue
            has_shebang = txt.lstrip().startswith("#!/usr/bin/env python3")
            has_doc = '"""' in txt[:500] or "'''" in txt[:500]
            self._check(
                has_shebang and has_doc,
                f"scripts/{script.name} 应有 shebang 和 docstring",
                warning=True,
            )

    # ── 决策树可达性 + 重复 step ID 校验 ─────────────────────────

    def _validate_decision_steps(self) -> None:
        """校验决策树的 step ID 唯一性和分支引用可达性。"""
        from app.skills.core.parser import skill_parser

        parsed = skill_parser.parse(self.content)
        if not parsed.steps:
            return

        # 重复 step ID 检测
        seen_ids: dict[str, int] = {}
        for step in parsed.steps:
            sid = step.id
            if sid in seen_ids:
                self._check(False, f"决策树存在重复 step ID: Step {sid}")
            else:
                seen_ids[sid] = 1

        all_step_ids = set(seen_ids.keys())

        # 收集所有 next_step 引用
        dangling_refs: list[str] = []
        for step in parsed.steps:
            for branch in step.branches:
                if branch.next_step and branch.next_step not in all_step_ids:
                    dangling_refs.append(f"Step {step.id} 分支 '{branch.condition}' → Step {branch.next_step}")

        for ref in dangling_refs:
            self._check(False, f"决策树引用不存在的步骤: {ref}", warning=True)

        # 可达性分析：从第一个 step 出发，检测孤立节点
        if len(parsed.steps) > 1:
            step_map = {s.id: s for s in parsed.steps}
            reachable: set[str] = set()
            first_id = parsed.steps[0].id
            queue = [first_id]
            while queue:
                current = queue.pop(0)
                if current in reachable:
                    continue
                reachable.add(current)
                step = step_map.get(current)
                if step:
                    for branch in step.branches:
                        if branch.next_step and branch.next_step in all_step_ids:
                            queue.append(branch.next_step)

            orphans = all_step_ids - reachable
            for orphan in sorted(orphans):
                self._check(
                    False,
                    f"决策树孤立节点: Step {orphan} 无法从 Step {first_id} 到达",
                    warning=True,
                )

    # ── 入口 ─────────────────────────────────────────────────────

    def validate(self) -> StructuralValidationReport:
        if not self._load():
            return self.report
        if not self._parse_frontmatter():
            return self.report

        self._validate_frontmatter()
        self._validate_structure()
        self._validate_decision_steps()
        self._validate_references_dir()
        self._validate_scripts_dir()

        self.report.ok = not self.report.errors
        return self.report


def structural_validate(skill_path: str | Path) -> StructuralValidationReport:
    """便捷函数: 一行调用得到 report。"""
    return SkillValidator(skill_path).validate()
