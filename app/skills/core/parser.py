"""
SKILL.md ↔ 结构化JSON 双向解析器。
核心职责：
  parse()  — SKILL.md全文 → 结构化dict（给前端渲染表单/决策树）
  render() — 结构化dict → SKILL.md全文（保存时写回Git）

v2.8.1 D3：parse 结果按 content-hash 做进程内 LRU 缓存，避免同一份
SKILL.md 在 mermaid / generate-tests / validate-antipattern / hall profile 等
多端点里反复解析。缓存上限 128 条（约 ~6MB），LRU 淘汰。
"""

import copy
import hashlib
import re
import json
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    pass

from loguru import logger


@dataclass
class Branch:
    """决策树条件分支"""
    condition: str = ""
    conclusion: str = ""
    action: str = ""
    next_step: str | None = None


@dataclass
class DecisionStep:
    """决策树步骤"""
    id: str = ""
    name: str = ""
    description: str = ""
    branches: list[Branch] = field(default_factory=list)


@dataclass
class Antipattern:
    """反例"""
    scenario: str = ""
    correct_action: str = ""
    source: str = ""


@dataclass
class OutputItem:
    """输出定义项"""
    name: str = ""
    format: str = ""
    recipient: str = ""
    approval_level: str = ""


@dataclass
class DataInput:
    """数据输入项"""
    name: str = ""
    source: str = ""
    frequency: str = ""


@dataclass
class TestCase:
    """测试用例"""
    name: str = ""
    input_data: dict = field(default_factory=dict)
    expected_output: dict = field(default_factory=dict)
    assert_rules: list[str] = field(default_factory=list)


@dataclass
class SkillStructured:
    """SKILL.md的结构化表示"""
    frontmatter: dict = field(default_factory=dict)
    purpose: str = ""
    steps: list[DecisionStep] = field(default_factory=list)
    antipatterns: list[Antipattern] = field(default_factory=list)
    output_definition: list[OutputItem] = field(default_factory=list)
    data_inputs: list[DataInput] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    raw_sections: dict = field(default_factory=dict)  # 保留未解析的章节原文（解析时填充）
    custom_sections: dict = field(default_factory=dict)  # 自定义章节（用户编辑时维护）


# ===== 正则匹配模式 =====
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_STEP_HEADER_RE = re.compile(r"^(?:Step|步骤)[_\s]*(\d+)\s*[:：]\s*(.+)$", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^[\s]*[├└│|]?[─-]*\s*(.+?)(?:\s*[→:：]\s*(.+))?$")


# 自然语言条件关键词（中英文）
# 英文关键词用 \b 词边界；中文多字词直接匹配（不会误匹配子串）；
# 单字中文词（当、或、且）要求后跟条件上下文，避免 "当前" "或者" 误匹配。
_CONDITION_MULTI_CHAR_RE = re.compile(
    r'(?:between|greater\s+than|less\s+than|equal\s+to|exceed|below|above|within|not\s+in|in\s+range|'
    r'大于|小于|等于|不等于|超过|低于|高于|不超过|介于|属于|不属于|是否)',
    re.IGNORECASE,
)
_CONDITION_CONTEXT_RE = re.compile(
    r'(?:如果\s*.+[><=≥≤]|当\s+.+[><=≥≤时]|且\s+.+[><=≥≤]|或\s+.+[><=≥≤])',
)


def _is_condition_line(line: str) -> bool:
    """判断一行文本是否为条件表达式（比较运算符 或 自然语言条件关键词）。"""
    if re.search(r'[><=≥≤×≠]', line):
        return True
    if _CONDITION_MULTI_CHAR_RE.search(line):
        return True
    if _CONDITION_CONTEXT_RE.search(line):
        return True
    return False


def _strip_leading_emoji(text: str) -> str:
    """去掉文本开头的所有emoji和空白字符（基于Unicode category，不依赖硬编码列表）。"""
    i = 0
    while i < len(text):
        ch = text[i]
        cat = unicodedata.category(ch)
        # So=Other Symbol, Sk=Modifier Symbol, Cf=Format char, Mn=Nonspacing Mark
        if cat in ("So", "Sk", "Cf", "Mn") or ch in ("\ufe0f", "\u200d") or ch.isspace():
            i += 1
        else:
            break
    return text[i:].strip()


class SkillParser:
    """SKILL.md ↔ 结构化数据 双向转换"""

    # v2.8.1 D3：进程内 LRU 缓存（content hash → SkillStructured）
    _CACHE_MAX = 128
    _cache: "OrderedDict[str, SkillStructured]" = OrderedDict()
    _cache_hits = 0
    _cache_misses = 0

    @classmethod
    def _cache_key(cls, skill_md: str) -> str:
        # SHA1 够用，碰撞概率可忽略；SKILL.md 体量通常 < 50kB
        return hashlib.sha1(skill_md.encode("utf-8", errors="replace")).hexdigest()

    @classmethod
    def clear_cache(cls) -> int:
        """清空 parse 缓存，返回清除前条目数（测试 / 显式刷新用）。"""
        count = len(cls._cache)
        cls._cache.clear()
        cls._cache_hits = 0
        cls._cache_misses = 0
        return count

    @classmethod
    def cache_stats(cls) -> dict:
        """返回 {hits, misses, size}（用于观测埋点 / dashboard）。"""
        total = cls._cache_hits + cls._cache_misses
        return {
            "hits": cls._cache_hits,
            "misses": cls._cache_misses,
            "size": len(cls._cache),
            "hit_rate": round(cls._cache_hits / total, 3) if total else 0.0,
        }

    def parse(self, skill_md: str) -> SkillStructured:
        """SKILL.md全文 → 结构化数据（带 LRU 缓存）。"""
        if not skill_md:
            return self._parse_uncached(skill_md)

        key = self._cache_key(skill_md)
        cached = SkillParser._cache.get(key)
        if cached is not None:
            # LRU：触达则挪到末尾（最近使用）
            SkillParser._cache.move_to_end(key)
            SkillParser._cache_hits += 1
            # 返回深拷贝，防止调用方 mutation 污染缓存
            return copy.deepcopy(cached)

        SkillParser._cache_misses += 1
        result = self._parse_uncached(skill_md)
        # 入缓存 + 淘汰最旧
        SkillParser._cache[key] = copy.deepcopy(result)
        if len(SkillParser._cache) > SkillParser._CACHE_MAX:
            SkillParser._cache.popitem(last=False)
        return result

    def _parse_uncached(self, skill_md: str) -> SkillStructured:
        """原始解析逻辑（不走缓存），用于显式刷新 / clear_cache 后重算。"""
        result = SkillStructured()

        # 1. 解析YAML frontmatter
        result.frontmatter = self._parse_frontmatter(skill_md)
        # 1.5 校验 + 归一化待办相关字段
        self._normalize_reviewer_fields(result.frontmatter)

        # 2. 提取各章节
        sections = self._split_sections(skill_md)
        result.raw_sections = sections

        # 3. 解析各章节
        result.purpose = self._get_section_content(sections, "目的")
        # 兜底：如果没有"目的"章节，用 frontmatter description 或第一个章节内容
        if not result.purpose:
            result.purpose = result.frontmatter.get("description", "")
        result.steps = self._parse_decision_steps(self._get_section_content(sections, "执行步骤"))
        result.antipatterns = self._parse_antipatterns(self._get_section_content(sections, "反例"))
        result.output_definition = self._parse_output_definition(self._get_section_content(sections, "输出"))
        result.data_inputs = self._parse_data_inputs(self._get_section_content(sections, "数据输入"))
        result.test_cases = self._parse_test_cases(self._get_section_content(sections, "测试用例"))
        # 自动提取自定义章节（非标准章节）
        _std_keys = {"目的", "执行步骤", "反例", "输出", "数据输入", "测试用例"}
        result.custom_sections = {
            k: v for k, v in sections.items()
            if not any(sk in k for sk in _std_keys)
            and k != result.frontmatter.get("name", "")
        }

        return result

    def render(self, data: SkillStructured) -> str:
        """结构化数据 → SKILL.md全文"""
        parts = []

        # 1. frontmatter
        if data.frontmatter:
            parts.append("---")
            parts.append(yaml.dump(data.frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False).strip())
            parts.append("---")
            parts.append("")

        # 2. 标题
        name = data.frontmatter.get("name", "Untitled Skill")
        parts.append(f"# {name}")
        parts.append("")

        # 3. 目的
        if data.purpose:
            parts.append("## 目的")
            parts.append("")
            parts.append(data.purpose.strip())
            parts.append("")

        # 4. 执行步骤
        if data.steps:
            parts.append("## 执行步骤")
            parts.append("")
            for step in data.steps:
                parts.append(f"### Step {step.id}: {step.name}")
                parts.append("")
                if step.description:
                    parts.append(step.description)
                    parts.append("")
                for i, branch in enumerate(step.branches):
                    connector = "└─" if i == len(step.branches) - 1 else "├─"
                    line = f"  {connector} {branch.condition}"
                    if branch.conclusion:
                        line += f" → {branch.conclusion}"
                    parts.append(line)
                    if branch.action:
                        parts.append(f"      动作: {branch.action}")
                    if branch.next_step:
                        parts.append(f"      → 进入 Step {branch.next_step}")
                parts.append("")

        # 5. 反例
        if data.antipatterns:
            parts.append("## 反例")
            parts.append("")
            for i, ap in enumerate(data.antipatterns, 1):
                parts.append(f"{i}. **误判场景**: {ap.scenario}")
                parts.append(f"   **正确做法**: {ap.correct_action}")
                if ap.source:
                    parts.append(f"   来源: {ap.source}")
                parts.append("")

        # 6. 输出定义
        if data.output_definition:
            parts.append("## 输出")
            parts.append("")
            parts.append("| 输出项 | 格式 | 接收人 | 审批级别 |")
            parts.append("|---|---|---|---|")
            for item in data.output_definition:
                parts.append(f"| {item.name} | {item.format} | {item.recipient} | {item.approval_level} |")
            parts.append("")

        # 7. 数据输入
        if data.data_inputs:
            parts.append("## 数据输入")
            parts.append("")
            parts.append("| 数据名称 | 来源 | 刷新频率 |")
            parts.append("|---|---|---|")
            for di in data.data_inputs:
                parts.append(f"| {di.name} | {di.source} | {di.frequency} |")
            parts.append("")

        # 8. 测试用例
        if data.test_cases:
            parts.append("## 测试用例")
            parts.append("")
            for tc in data.test_cases:
                parts.append(f"### {tc.name}")
                parts.append("")
                if tc.input_data:
                    parts.append("**输入:**")
                    parts.append("```json")
                    parts.append(json.dumps(tc.input_data, ensure_ascii=False, indent=2))
                    parts.append("```")
                    parts.append("")
                if tc.expected_output:
                    parts.append("**期望输出:**")
                    parts.append("```json")
                    parts.append(json.dumps(tc.expected_output, ensure_ascii=False, indent=2))
                    parts.append("```")
                    parts.append("")
                if tc.assert_rules:
                    parts.append("**断言:**")
                    for rule in tc.assert_rules:
                        parts.append(f"- `{rule}`")
                    parts.append("")

        # 9. 保留未识别的原始章节（避免roundtrip丢失内容）
        _known_keys = {"目的", "执行步骤", "反例", "输出", "数据输入", "测试用例"}
        # 优先使用 custom_sections（用户编辑后的数据）
        rendered_custom_keys = set()
        for section_title, section_body in data.custom_sections.items():
            if section_title == data.frontmatter.get("name", ""):
                continue
            parts.append(f"## {section_title}")
            parts.append("")
            if section_body:
                parts.append(section_body)
                parts.append("")
            rendered_custom_keys.add(section_title)
        # raw_sections 作为补充（文件解析时保留的原始内容，跳过已渲染的标准章节和 custom_sections）
        for section_title, section_body in data.raw_sections.items():
            if any(k in section_title for k in _known_keys):
                continue
            if section_title == data.frontmatter.get("name", ""):
                continue
            if section_title in rendered_custom_keys:
                continue
            parts.append(f"## {section_title}")
            parts.append("")
            if section_body:
                parts.append(section_body)
                parts.append("")

        return "\n".join(parts)

    # ===== 内部解析方法 =====

    def _parse_frontmatter(self, text: str) -> dict:
        """提取YAML frontmatter"""
        match = _FRONTMATTER_RE.match(text)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}

    # ----- 待办系统相关字段校验（docs/plans/aiclaw-workspace-and-ai-todo-plan.md §4.2.1）-----
    _ALLOWED_DECISION_MODES = {"any_of", "all_of", "independent"}

    def _normalize_reviewer_fields(self, frontmatter: dict) -> None:
        """对 reviewer / reviewer_role / decision_mode 做就地归一化与基础校验。

        - reviewer：允许字符串或字符串列表，归一化成 list[str]
        - reviewer_role：必须是字符串
        - decision_mode：必须在 ALLOWED_DECISION_MODES 内，否则抛 SKILL_FRONTMATTER_INVALID
        """
        from app.common.exceptions import AppError

        if not isinstance(frontmatter, dict) or not frontmatter:
            return

        if "reviewer" in frontmatter:
            value = frontmatter["reviewer"]
            if value is None:
                frontmatter.pop("reviewer", None)
            elif isinstance(value, str):
                cleaned = value.strip()
                frontmatter["reviewer"] = [cleaned] if cleaned else []
            elif isinstance(value, (list, tuple)):
                cleaned_list = [str(item).strip() for item in value if str(item).strip()]
                frontmatter["reviewer"] = cleaned_list
            else:
                raise AppError(
                    "SKILL_FRONTMATTER_INVALID",
                    400,
                    {"detail": "reviewer 必须是字符串或字符串列表"},
                )

        if "reviewer_role" in frontmatter:
            role_value = frontmatter["reviewer_role"]
            if role_value is None:
                frontmatter.pop("reviewer_role", None)
            elif not isinstance(role_value, str) or not role_value.strip():
                raise AppError(
                    "SKILL_FRONTMATTER_INVALID",
                    400,
                    {"detail": "reviewer_role 必须是非空字符串"},
                )
            else:
                frontmatter["reviewer_role"] = role_value.strip()

        if "decision_mode" in frontmatter:
            mode = frontmatter["decision_mode"]
            if mode is None:
                frontmatter.pop("decision_mode", None)
            elif not isinstance(mode, str) or mode.strip() not in self._ALLOWED_DECISION_MODES:
                raise AppError(
                    "SKILL_FRONTMATTER_INVALID",
                    400,
                    {
                        "detail": (
                            f"decision_mode 必须是 {sorted(self._ALLOWED_DECISION_MODES)} 之一"
                        )
                    },
                )
            else:
                frontmatter["decision_mode"] = mode.strip()

    def _split_sections(self, text: str) -> dict[str, str]:
        """
        按 ## 标题拆分章节（只按二级标题，### 保留在内容中）。
        返回 {"目的": "章节内容...", "执行步骤": "章节内容...", ...}
        """
        # 去掉frontmatter
        fm_match = _FRONTMATTER_RE.match(text)
        if fm_match:
            text = text[fm_match.end():]

        sections: dict[str, str] = {}
        # 只匹配 # 和 ## 标题（不匹配 ###）
        h2_re = re.compile(r"^(#{1,2})\s+(.+)$", re.MULTILINE)
        matches = list(h2_re.finditer(text))

        for i, match in enumerate(matches):
            title = match.group(2).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()

            # 用关键词匹配标准章节名（支持别名）
            _aliases = {
                "目的": ("目的",),
                "执行步骤": ("执行步骤", "判断逻辑", "决策步骤", "决策逻辑"),
                "反例": ("反例", "误判"),
                "输出": ("输出",),
                "数据输入": ("数据输入",),
                "测试用例": ("测试用例", "测试"),
            }
            matched = False
            for key, aliases in _aliases.items():
                if any(a in title for a in aliases):
                    sections[key] = content
                    matched = True
                    break
            if not matched:
                sections[title] = content

        return sections

    def _get_section_content(self, sections: dict, key: str) -> str:
        """获取章节内容，支持模糊匹配"""
        if key in sections:
            return sections[key]
        for k, v in sections.items():
            if key in k:
                return v
        return ""

    def _parse_decision_steps(self, text: str) -> list[DecisionStep]:
        """
        解析执行步骤章节为结构化Step列表。
        支持多种分支格式：
          1. 树形符号：├─ 条件 → 结论
          2. 条件前缀：条件: ROI > 1.5
          3. 纯文本：ROI > 盈亏线 × 1.2（含比较运算符的行）
          4. 列表格式：- 结论: 绿灯 / - 动作: 加预算
        """
        if not text:
            return []

        steps = []
        current_step: DecisionStep | None = None

        for line in text.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Step标题（### Step N: xxx）
            step_match = _STEP_HEADER_RE.match(line_stripped.lstrip("#").strip())
            if step_match:
                if current_step:
                    steps.append(current_step)
                current_step = DecisionStep(
                    id=step_match.group(1),
                    name=step_match.group(2).strip(),
                )
                continue

            if not current_step:
                continue

            # 格式1: 树形符号（├─ 或 └─）
            if any(marker in line for marker in ("├─", "└─", "├-", "└-")):
                branch_text = line_stripped
                for prefix in ("├─", "└─", "├-", "└-"):
                    if prefix in branch_text:
                        branch_text = branch_text.split(prefix, 1)[1].strip()
                        break
                # 去掉emoji前缀（使用Unicode category，不依赖硬编码emoji列表）
                branch_text = _strip_leading_emoji(branch_text)
                parts = re.split(r"\s*[→]\s*", branch_text, maxsplit=1)
                condition = parts[0].strip()
                # 如果是"结论: xxx"格式，归属到上一个branch
                conclusion_match = re.match(r'结论\s*[:：]\s*(.+)', condition)
                if conclusion_match and current_step.branches:
                    current_step.branches[-1].conclusion = conclusion_match.group(1).strip()
                else:
                    branch = Branch(condition=condition)
                    if len(parts) > 1:
                        branch.conclusion = parts[1].strip()
                    current_step.branches.append(branch)

            # 格式2: "条件:" 前缀
            elif re.match(r'^条件\s*[:：]', line_stripped):
                condition = re.split(r'^条件\s*[:：]\s*', line_stripped, maxsplit=1)[1].strip()
                branch = Branch(condition=condition)
                current_step.branches.append(branch)

            # 格式3: 纯文本条件行（含比较运算符 或 自然语言条件关键词）
            elif (_is_condition_line(line_stripped)
                  and not re.match(r'^[-*]?\s*(动作|结论|→)', line_stripped)
                  and not line_stripped.startswith(("│", "|"))):
                parts = re.split(r"\s*[→]\s*", line_stripped, maxsplit=1)
                branch = Branch(condition=parts[0].strip())
                if len(parts) > 1:
                    branch.conclusion = parts[1].strip()
                current_step.branches.append(branch)

            # 结论行（独立的 "- 结论:" 或 "结论:"）
            elif re.match(r'^[-*]?\s*结论\s*[:：]', line_stripped):
                if current_step.branches:
                    conclusion = re.split(r'结论\s*[:：]\s*', line_stripped, maxsplit=1)[1].strip()
                    current_step.branches[-1].conclusion = conclusion

            # 动作行
            elif re.match(r'^[-*│|\s]*动作\s*[:：]', line_stripped):
                if current_step.branches:
                    action_text = re.split(r'动作\s*[:：]\s*', line_stripped, maxsplit=1)[1].strip()
                    current_step.branches[-1].action = action_text

            # 下一步引用
            elif "进入" in line_stripped and re.search(r'Step\s*\w+', line_stripped, re.IGNORECASE):
                if current_step.branches:
                    next_match = re.search(r"Step\s*(\w+)", line_stripped, re.IGNORECASE)
                    if next_match:
                        current_step.branches[-1].next_step = next_match.group(1)

            # 步骤描述（只在没有分支时设置，避免把条件行误当描述）
            elif not current_step.description and not current_step.branches:
                current_step.description = line_stripped

        if current_step:
            steps.append(current_step)

        return steps

    def _parse_antipatterns(self, text: str) -> list[Antipattern]:
        """解析反例章节"""
        if not text:
            return []

        antipatterns = []
        current: Antipattern | None = None

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 新的反例项（数字开头）
            num_match = re.match(r"^\d+\.\s*\*\*(?:误判场景)\*\*\s*[:：]\s*(.+)", line)
            if num_match:
                if current:
                    antipatterns.append(current)
                current = Antipattern(scenario=num_match.group(1).strip())
                continue

            if current:
                correct_match = re.match(r"\*\*正确做法\*\*\s*[:：]\s*(.+)", line)
                if correct_match:
                    current.correct_action = correct_match.group(1).strip()
                elif re.match(r"(\*\*)?来源(\*\*)?\s*[:：]\s*(.+)", line):
                    source_match = re.match(r"(\*\*)?来源(\*\*)?\s*[:：]\s*(.+)", line)
                    current.source = source_match.group(3).strip()

        if current:
            antipatterns.append(current)

        return antipatterns

    def _parse_output_definition(self, text: str) -> list[OutputItem]:
        """解析输出定义（Markdown表格）"""
        return [
            OutputItem(name=cols[0], format=cols[1], recipient=cols[2], approval_level=cols[3])
            for cols in self._parse_md_table(text, expected_cols=4)
        ]

    def _parse_data_inputs(self, text: str) -> list[DataInput]:
        """解析数据输入（Markdown表格）"""
        return [
            DataInput(name=cols[0], source=cols[1], frequency=cols[2])
            for cols in self._parse_md_table(text, expected_cols=3)
        ]

    def _parse_test_cases(self, text: str) -> list[TestCase]:
        """解析测试用例章节"""
        if not text:
            return []

        cases = []
        # 按### 拆分子章节
        sub_sections = re.split(r"^###\s+", text, flags=re.MULTILINE)

        for section in sub_sections:
            section = section.strip()
            if not section:
                continue

            lines = section.split("\n")
            name = lines[0].strip()
            body = "\n".join(lines[1:])

            tc = TestCase(name=name)

            # 提取 JSON 代码块
            json_blocks = re.findall(r"```json\s*\n(.*?)\n```", body, re.DOTALL)
            if len(json_blocks) > 2:
                logger.warning(f"测试用例 '{name}' 含 {len(json_blocks)} 个 JSON 块，仅使用前 2 个（input/expected_output）")
            if len(json_blocks) >= 1:
                try:
                    tc.input_data = json.loads(json_blocks[0])
                except json.JSONDecodeError as e:
                    logger.warning(f"测试用例 '{name}' 第 1 个 JSON 块解析失败: {e}")
            if len(json_blocks) >= 2:
                try:
                    tc.expected_output = json.loads(json_blocks[1])
                except json.JSONDecodeError as e:
                    logger.warning(f"测试用例 '{name}' 第 2 个 JSON 块解析失败: {e}")

            # 兼容 YAML 代码块（标准格式 + waoowaoo 格式）
            # 支持未关闭的代码块（文件末尾缺 ```）
            if not tc.input_data:
                yaml_blocks = re.findall(r"```ya?ml\s*\n(.*?)(?:\n```|$)", body, re.DOTALL)
                for yb in yaml_blocks:
                    try:
                        parsed_yaml = yaml.safe_load(yb)
                        if isinstance(parsed_yaml, dict):
                            # 标准格式: input / expected_output
                            if "input" in parsed_yaml and not tc.input_data:
                                tc.input_data = parsed_yaml["input"]
                            if "expected_output" in parsed_yaml and not tc.expected_output:
                                tc.expected_output = parsed_yaml["expected_output"]
                            # waoowaoo 格式: input_data / expected_output
                            if "input_data" in parsed_yaml and not tc.input_data:
                                tc.input_data = parsed_yaml["input_data"]
                            if "expected_output" in parsed_yaml and not tc.expected_output:
                                tc.expected_output = parsed_yaml.get("expected_output")
                    except Exception as e:
                        logger.debug("parser: 测试用例 YAML 片段解析失败: {}", e)

            # 提取断言规则
            for line in body.split("\n"):
                rule_match = re.match(r"^-\s*`(.+)`", line.strip())
                if rule_match:
                    tc.assert_rules.append(rule_match.group(1))

            cases.append(tc)

        return cases

    def _parse_md_table(self, text: str, expected_cols: int) -> list[list[str]]:
        """解析Markdown表格，返回数据行（跳过表头和分隔行）。

        防御性处理：缺少分隔行的非标准表格也能正确解析（第一行视为表头跳过）。
        """
        if not text:
            return []

        rows = []
        lines = text.strip().split("\n")
        header_found = False
        pre_separator_lines: list[list[str]] = []  # 分隔行之前的行（用于 fallback）

        for line in lines:
            line = line.strip()
            if not line.startswith("|"):
                continue

            cols = [c.strip() for c in line.split("|")[1:-1]]

            # 跳过分隔行（---）
            if cols and all(re.match(r"^[-:]+$", c) for c in cols):
                header_found = True
                continue

            if not header_found:
                # 还没遇到分隔行，暂存（第一行是表头，其余可能是数据）
                pre_separator_lines.append(cols)
                continue

            if len(cols) >= expected_cols:
                rows.append(cols[:expected_cols])
            elif cols and any(c.strip() for c in cols):
                logger.warning(f"表格行列数不足: 期望 {expected_cols} 列, 实际 {len(cols)} 列, 内容: {cols}")

        # 防御：无分隔行时，第一行视为表头，其余行作为数据
        if not header_found and len(pre_separator_lines) > 1:
            logger.warning(f"表格缺少分隔行（---|---），将第一行视为表头，解析剩余 {len(pre_separator_lines) - 1} 行")
            for cols in pre_separator_lines[1:]:
                if len(cols) >= expected_cols:
                    rows.append(cols[:expected_cols])

        return rows


# 全局实例
skill_parser = SkillParser()
