"""把内部节点事件翻译成业务可读的中文进度。

策略：
- 优先用本地 EVENT_TEXT 字典（覆盖 6 节点 + interrupt 事件）。
- 字典 miss 时调 LLM（progress_translator.txt prompt）兜底，限速 + 缓存避免烧钱。
- LLM 也失败 → 通用兜底字符串。
"""

from __future__ import annotations

import json

from loguru import logger

from app.agent_core.prompt_loader import load_prompt
from app.common.ai import call_llm_cached


EVENT_TEXT = {
    "intent_extract": "正在理解你的目标，并整理成可确认的任务合同。",
    "risk_gate": "正在检查权限和风险边界，确认哪些动作需要授权。",
    "skill_generate": "正在生成 Skill 骨架和关键规则。",
    "adapter_bind": "正在绑定输出适配器，确保格式符合目标系统要求。",
    "sandbox_run": "正在用样例数据执行预演，生成真实输出。",
    "publish_gate": "正在检查 60 分门禁，确认是否满足发布条件。",
    "xmodel_check": "正在用第二个模型复核任务理解的一致性。",
    "interrupt_target": "请确认平台对你目标的理解。",
    "interrupt_permission": "请授权平台执行不可逆动作（推送/写入）。",
    "interrupt_preview": "请检查预演输出是否符合预期。",
    "interrupt_responsibility": "请确认失败时的告警和回滚策略。",
}


def translate_event(event_name: str, detail: str = "") -> str:
    """同步快路径：仅查字典，miss 返回通用兜底。"""
    base = EVENT_TEXT.get(event_name, "正在处理当前任务。")
    if detail:
        return f"{base} {detail}".strip()
    return base


async def translate_event_async(event_name: str, detail: str = "") -> str:
    """异步路径：字典 miss 时调 LLM 兜底翻译。"""
    if event_name in EVENT_TEXT:
        base = EVENT_TEXT[event_name]
        return f"{base} {detail}".strip() if detail else base

    try:
        prompt = load_prompt("progress_translator")
    except FileNotFoundError:
        return translate_event(event_name, detail)

    user = json.dumps({"event": event_name, "detail": detail}, ensure_ascii=False)
    try:
        result = await call_llm_cached(
            cache_key=f"progress_translate:{event_name}:{detail[:100]}",
            system=prompt,
            user=user,
            json_mode=False,
            cache_ttl=3600,
            call_source="agent_core.progress_translator",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("progress translator LLM 失败: {}", exc)
        return translate_event(event_name, detail)

    if isinstance(result, str) and result.strip():
        return result.strip()[:80]
    return translate_event(event_name, detail)
