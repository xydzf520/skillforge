"""Node 3: TaskContract -> Skill draft + SKILL.md."""

from __future__ import annotations

import json
import re

import yaml
from loguru import logger

from app.agent_core.prompt_loader import load_prompt
from app.common.ai import call_llm
from app.skills.core.parser import Branch, DecisionStep, OutputItem, SkillStructured, TestCase, skill_parser
from app.workbench.task_contract import build_skill_draft_from_contract


# P3-1: scripts 文件名白名单 — 防止 LLM 生成的 scripts dict key 含路径穿越
_SAFE_SCRIPT_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-./]+$")
_MAX_SCRIPTS_COUNT = 30
_MAX_SCRIPT_BYTES = 200 * 1024


def _validate_scripts(raw) -> dict | None:
    """校验 LLM 输出的 scripts 字段。

    返回 None 表示无效，应该放弃。否则返回 sanitized dict。
    """
    if not isinstance(raw, dict):
        return None
    if len(raw) > _MAX_SCRIPTS_COUNT:
        logger.warning("[skill_generate] LLM scripts 数量 {} 超过上限 {}", len(raw), _MAX_SCRIPTS_COUNT)
        return None
    safe: dict[str, str] = {}
    for path, content in raw.items():
        if not isinstance(path, str) or not _SAFE_SCRIPT_NAME_RE.match(path):
            logger.warning("[skill_generate] LLM script 路径非法 path={!r}", path)
            return None
        if ".." in path.split("/"):
            logger.warning("[skill_generate] LLM script 路径含 .. path={}", path)
            return None
        if not isinstance(content, str):
            logger.warning("[skill_generate] LLM script 内容非字符串 path={} type={}", path, type(content))
            return None
        if len(content.encode("utf-8")) > _MAX_SCRIPT_BYTES:
            logger.warning("[skill_generate] LLM script 内容过大 path={} bytes={}", path, len(content))
            return None
        safe[path] = content
    return safe


# [M4] YAML 嵌套深度上限 — PyYAML safe_load 不直接支持深度限制,
# 用启发式: 缩进深度 / 2 ≈ 嵌套层数 (假设 2 空格缩进), 实际只是个粗略上界
_MAX_YAML_NESTING = 50


def _yaml_too_deep(raw: str) -> bool:
    """[M4] 启发式检测 YAML 嵌套过深 — 防止恶意输入触发 PyYAML 栈溢出。"""
    max_indent = 0
    for line in raw.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent > max_indent:
            max_indent = indent
    # 假设 2 空格缩进 — max_indent / 2 是层数
    return (max_indent // 2) > _MAX_YAML_NESTING


def _validate_policy_yaml(raw) -> str | None:
    """校验 LLM 输出的 policy_yaml 字段必须是合法 YAML。

    [M4] yaml.safe_load 已经默认使用 SafeLoader (拒绝任意 Python 对象实例化),
    本函数额外加: 大小限制 + 嵌套深度启发式 + 顶层类型校验。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    if len(raw) > 50 * 1024:
        logger.warning("[skill_generate] LLM policy_yaml 过长 len={}", len(raw))
        return None
    if _yaml_too_deep(raw):
        logger.warning("[skill_generate] LLM policy_yaml 嵌套过深, 拒绝")
        return None
    try:
        parsed = yaml.safe_load(raw)  # SafeLoader: 不实例化任意 Python 对象
    except yaml.YAMLError as e:
        logger.warning("[skill_generate] LLM policy_yaml 解析失败: {}", e)
        return None
    except RecursionError:
        logger.warning("[skill_generate] LLM policy_yaml 解析时栈溢出 (嵌套太深)")
        return None
    if not isinstance(parsed, (dict, list)):
        logger.warning("[skill_generate] LLM policy_yaml 顶层不是 dict/list: {}", type(parsed))
        return None
    return raw


def _validate_skill_md(raw) -> str | None:
    """校验 LLM 输出的 skill_md 字段是合法 SKILL.md。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    if len(raw) > 100 * 1024:
        logger.warning("[skill_generate] LLM skill_md 过长 len={}", len(raw))
        return None
    # 必须含 frontmatter
    if not raw.lstrip().startswith("---"):
        logger.warning("[skill_generate] LLM skill_md 缺少 frontmatter")
        return None
    try:
        parts = raw.split("---", 2)
        if len(parts) < 3:
            return None
        # [M4] frontmatter 也做嵌套防御 — frontmatter 通常 < 4KB, 同样限制
        fm_text = parts[1]
        if len(fm_text) > 8 * 1024:
            logger.warning("[skill_generate] LLM skill_md frontmatter 过大 len={}", len(fm_text))
            return None
        if _yaml_too_deep(fm_text):
            logger.warning("[skill_generate] LLM skill_md frontmatter 嵌套过深, 拒绝")
            return None
        fm = yaml.safe_load(fm_text)  # SafeLoader: 不实例化任意 Python 对象
        if not isinstance(fm, dict):
            return None
    except yaml.YAMLError as e:
        logger.warning("[skill_generate] LLM skill_md frontmatter 解析失败: {}", e)
        return None
    except RecursionError:
        logger.warning("[skill_generate] LLM skill_md frontmatter 解析时栈溢出")
        return None
    return raw


def _render_skill_md(skill: dict) -> str:
    structured = SkillStructured()
    meta = skill.get("meta") or {}
    structured.frontmatter = {
        "name": meta.get("name", ""),
        "department": meta.get("department", ""),
        "trigger_type": meta.get("trigger_type", "manual"),
        "risk_level": meta.get("risk_level", "R2"),
        "description": meta.get("description", ""),
    }
    structured.purpose = skill.get("goal", "")
    structured.steps = [
        DecisionStep(
            id=step.get("id", ""),
            name=step.get("name", ""),
            description=step.get("description", ""),
            branches=[
                Branch(
                    condition=branch.get("condition", ""),
                    conclusion=branch.get("conclusion", ""),
                    action=branch.get("action", ""),
                    next_step=branch.get("next_step"),
                )
                for branch in (step.get("branches") or [])
            ],
        )
        for step in (skill.get("rules") or [])
    ]
    structured.output_definition = [
        OutputItem(
            name=row.get("name", ""),
            format=row.get("format", "text"),
            recipient=row.get("recipient", ""),
            approval_level=row.get("approval_level", ""),
        )
        for row in (skill.get("output_table") or [])
    ]
    structured.test_cases = [
        TestCase(
            name=case.get("name", ""),
            input_data=case.get("input_data", {}),
            expected_output=case.get("expected_output", {}),
            assert_rules=case.get("assert_rules", []),
        )
        for case in (skill.get("test_cases") or [])
    ]
    return skill_parser.render(structured)


async def _llm_enhance_skill(contract: dict) -> dict | None:
    """LLM 增强：让 deepseek 基于 contract 直接产出 skill_md/policy_yaml/scripts。

    返回 None 时上层走模板路径。当前主要用于把 LLM 返回的脚本写到 skill 字典里。
    """
    try:
        prompt = load_prompt("skill_generate")
    except FileNotFoundError:
        return None
    user = json.dumps(contract, ensure_ascii=False)
    result = await call_llm(
        system=prompt,
        user=user,
        json_mode=True,
        temperature=0.3,
        max_tokens=6000,  # SKILL.md + scripts/main.py + policy.yaml 合起来不小
        call_source="agent_core.skill_generate",
    )
    if not isinstance(result, dict):
        return None
    # 必须含至少一个字段才认为有效
    if not any(k in result for k in ("intent_md", "skill_md", "policy_yaml", "scripts")):
        return None
    return result


async def skill_generate_node(state: dict) -> dict:
    contract = state.get("contract") or {}
    draft = build_skill_draft_from_contract(contract).model_dump()
    next_state = dict(state)
    next_state["skill"] = draft
    next_state["skill_md"] = _render_skill_md(draft)

    # 用 LLM 补充 scripts/main.py 等可执行片段（失败不影响主流程）
    enhancement = await _llm_enhance_skill(contract)
    if enhancement:
        # P3-1: 严格校验 LLM 输出的每个字段，非法的整段丢弃，不让脏数据进入 state
        safe_scripts = _validate_scripts(enhancement.get("scripts"))
        if safe_scripts:
            draft["scripts"] = safe_scripts
            next_state["skill"] = draft

        safe_policy = _validate_policy_yaml(enhancement.get("policy_yaml"))
        if safe_policy:
            next_state["policy_yaml"] = safe_policy

        safe_md = _validate_skill_md(enhancement.get("skill_md"))
        if safe_md:
            # 保留模板版作为 fallback，LLM 版本放到 skill_md_llm 供前端按需展示
            next_state["skill_md_llm"] = safe_md

        logger.info(
            "[skill_generate] LLM 增强 raw_keys={} valid_scripts={} valid_policy={} valid_md={}",
            list(enhancement.keys()),
            bool(safe_scripts),
            bool(safe_policy),
            bool(safe_md),
        )

    return next_state
