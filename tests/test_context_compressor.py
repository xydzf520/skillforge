"""Workbench 上下文压缩模块测试。

覆盖：
1. 消息数未达阈值时不压缩
2. 超阈值时触发 LLM 压缩
3. LLM 不可用时降级为简单截断
4. truncate_context_simple 基本行为
5. 简单摘要构建（_build_simple_summary）格式正确
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.workbench.context_compressor import (
    COMPRESS_THRESHOLD,
    MAX_FULL_MESSAGES,
    maybe_compress_context,
    truncate_context_simple,
    _build_simple_summary,
)


def _make_messages(n: int) -> list[dict]:
    """生成 n 条测试消息。"""
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"第 {i+1} 条消息内容"})
    return messages


class TestMaybeCompressContext:
    """maybe_compress_context 测试。"""

    @pytest.mark.asyncio
    async def test_no_compression_below_threshold(self):
        """消息数未超过阈值时原样返回。"""
        messages = _make_messages(COMPRESS_THRESHOLD - 1)
        result = await maybe_compress_context(messages)
        assert result == messages
        assert len(result) == COMPRESS_THRESHOLD - 1

    @pytest.mark.asyncio
    async def test_no_compression_at_threshold(self):
        """消息数等于阈值时原样返回。"""
        messages = _make_messages(COMPRESS_THRESHOLD)
        result = await maybe_compress_context(messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_compression_triggered_with_llm(self):
        """超过阈值时用 LLM 生成摘要。"""
        messages = _make_messages(COMPRESS_THRESHOLD + 10)

        with patch(
            "app.workbench.context_compressor._llm_summarize",
            new_callable=AsyncMock,
            return_value="这是一段压缩摘要",
        ) as mock_summarize:
            result = await maybe_compress_context(messages)

            # 调用了 LLM 摘要
            mock_summarize.assert_called_once()

            # 结果为 1 摘要 + MAX_FULL_MESSAGES 条完整消息
            assert len(result) == MAX_FULL_MESSAGES + 1

            # 第一条是系统摘要
            assert result[0]["role"] == "system"
            assert "压缩摘要" in result[0]["content"]
            assert "这是一段压缩摘要" in result[0]["content"]

            # 最后 MAX_FULL_MESSAGES 条是原始消息的尾部
            expected_tail = messages[-MAX_FULL_MESSAGES:]
            assert result[1:] == expected_tail

    @pytest.mark.asyncio
    async def test_compression_fallback_on_llm_failure(self):
        """LLM 不可用时降级为简单截断摘要。"""
        messages = _make_messages(COMPRESS_THRESHOLD + 5)

        with patch(
            "app.workbench.context_compressor._llm_summarize",
            new_callable=AsyncMock,
            side_effect=Exception("LLM 超时"),
        ):
            result = await maybe_compress_context(messages)

            # 仍然压缩了
            assert len(result) == MAX_FULL_MESSAGES + 1

            # 第一条是简单截断摘要
            assert result[0]["role"] == "system"
            assert "截断" in result[0]["content"]


class TestTruncateContextSimple:
    """truncate_context_simple 测试。"""

    def test_no_truncation_below_max(self):
        """消息数未超过限制时原样返回。"""
        messages = _make_messages(10)
        result = truncate_context_simple(messages, max_messages=20)
        assert result == messages

    def test_truncation_at_max(self):
        """消息数等于限制时原样返回。"""
        messages = _make_messages(20)
        result = truncate_context_simple(messages, max_messages=20)
        assert result == messages

    def test_truncation_above_max(self):
        """超过限制时保留最后 N 条。"""
        messages = _make_messages(50)
        result = truncate_context_simple(messages, max_messages=20)
        assert len(result) == 20
        assert result == messages[-20:]

    def test_custom_max(self):
        """自定义 max_messages。"""
        messages = _make_messages(10)
        result = truncate_context_simple(messages, max_messages=5)
        assert len(result) == 5
        assert result == messages[-5:]


class TestBuildSimpleSummary:
    """_build_simple_summary 内部函数测试。"""

    def test_basic_format(self):
        """基本格式正确。"""
        messages = _make_messages(5)
        result = _build_simple_summary(messages)
        assert result["role"] == "system"
        assert "5 条对话" in result["content"]
        assert "[user]" in result["content"]
        assert "[assistant]" in result["content"]

    def test_long_content_truncated(self):
        """长消息内容被截断。"""
        messages = [{"role": "user", "content": "x" * 200}]
        result = _build_simple_summary(messages)
        # 80 字符截断 + 省略号
        assert "…" in result["content"]

    def test_many_messages_collapsed(self):
        """超过 15 条时中间部分省略。"""
        messages = _make_messages(25)
        result = _build_simple_summary(messages)
        assert "省略" in result["content"]

    def test_empty_messages(self):
        """空消息列表。"""
        result = _build_simple_summary([])
        assert result["role"] == "system"
        assert "0 条" in result["content"]
