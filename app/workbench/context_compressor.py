"""Workbench 长会话上下文压缩。

当对话消息数超过阈值时，将早期消息压缩为摘要，
保留最近 N 条完整消息 + 1 条压缩摘要。

适用于 workbench 的 legacy chat 流（handle_chat_stream），
coding_agent 子进程链路有自己的 context_budget 机制。
"""

from __future__ import annotations

from loguru import logger


# 保留完整消息数（最近的 N 条不压缩）
MAX_FULL_MESSAGES = 20
# 超过此数触发压缩
COMPRESS_THRESHOLD = 30


async def maybe_compress_context(
    messages: list[dict],
    skill_context: str = "",
) -> list[dict]:
    """如果消息数超过阈值，压缩早期消息为摘要。

    策略：
    1. messages[:-(MAX_FULL_MESSAGES)] → 用 LLM 生成简短摘要
    2. 返回 [system_summary] + messages[-(MAX_FULL_MESSAGES):]
    3. 如果 LLM 不可用，用简单截断策略（保留 role+要点）

    Args:
        messages: 完整的消息列表（role/content dict 格式）
        skill_context: 当前 Skill 上下文摘要（辅助 LLM 理解对话背景）

    Returns:
        压缩后的消息列表，长度 <= MAX_FULL_MESSAGES + 1
    """
    if len(messages) <= COMPRESS_THRESHOLD:
        return messages

    # 分割：早期消息 vs 保留的最近消息
    cutoff = len(messages) - MAX_FULL_MESSAGES
    early_messages = messages[:cutoff]
    recent_messages = messages[cutoff:]

    # 尝试用 LLM 生成摘要
    try:
        summary_text = await _llm_summarize(early_messages, skill_context)
    except Exception as exc:
        logger.warning(f"上下文压缩 LLM 摘要失败，降级为简单截断: {exc}")
        summary_text = None

    if summary_text:
        summary_message = {
            "role": "system",
            "content": (
                f"[上下文压缩摘要] 以下是此前 {len(early_messages)} 条对话的要点：\n\n"
                f"{summary_text}"
            ),
        }
    else:
        # LLM 不可用时的简单截断策略
        summary_message = _build_simple_summary(early_messages)

    logger.info(
        f"上下文压缩: {len(messages)} 条消息 → 1 摘要 + {len(recent_messages)} 条完整消息"
    )
    return [summary_message] + recent_messages


def truncate_context_simple(
    messages: list[dict],
    max_messages: int = MAX_FULL_MESSAGES,
) -> list[dict]:
    """简单截断：保留最后 N 条，丢弃早期消息。不依赖 LLM。

    用于 LLM 完全不可用或不想等 LLM 时的快速降级方案。

    Args:
        messages: 完整的消息列表
        max_messages: 保留的最大消息数

    Returns:
        截断后的消息列表
    """
    if len(messages) <= max_messages:
        return messages
    return messages[-max_messages:]


async def _llm_summarize(
    early_messages: list[dict],
    skill_context: str = "",
) -> str | None:
    """用 LLM 将早期消息列表压缩为简短摘要。

    Returns:
        摘要文本，或 None（调用失败时由上层降级处理）
    """
    from app.common.ai import call_llm

    # 构建待压缩的对话文本
    conversation_lines = []
    for msg in early_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        # 截断单条过长的消息，避免摘要请求本身就超限
        if isinstance(content, str) and len(content) > 500:
            content = content[:500] + "…(截断)"
        conversation_lines.append(f"[{role}] {content}")

    conversation_text = "\n".join(conversation_lines)

    system = (
        "你是一个对话摘要助手。将以下对话记录压缩为简洁的要点摘要，"
        "保留关键决策、用户请求和 AI 回答的核心内容。"
        "摘要应控制在 200 字以内。只输出摘要，不要加前缀或解释。"
    )
    user_prompt = (
        f"请压缩以下对话记录为要点摘要：\n\n{conversation_text}"
    )
    if skill_context:
        user_prompt += f"\n\n（当前 Skill 上下文：{skill_context[:200]}）"

    result = await call_llm(
        system=system,
        user=user_prompt,
        max_tokens=300,
        temperature=0.2,
        json_mode=False,
        call_source="workbench_context_compress",
    )

    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        text = result.get("content") or result.get("text") or ""
        if text.strip():
            return text.strip()
    return None


def _build_simple_summary(early_messages: list[dict]) -> dict:
    """不依赖 LLM 的简单摘要：提取每条消息的前 80 字符做列表。

    Args:
        early_messages: 要摘要的早期消息列表

    Returns:
        一条 system 角色的摘要消息
    """
    lines = []
    for msg in early_messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            preview = content[:80].replace("\n", " ")
            if len(content) > 80:
                preview += "…"
        else:
            preview = "(非文本内容)"
        lines.append(f"- [{role}] {preview}")

    # 限制行数，太多也没意义
    if len(lines) > 15:
        lines = lines[:5] + [f"  …(中间省略 {len(lines) - 10} 条)…"] + lines[-5:]

    return {
        "role": "system",
        "content": (
            f"[上下文截断] 此前 {len(early_messages)} 条对话已被截断，"
            f"以下是简要记录：\n" + "\n".join(lines)
        ),
    }
