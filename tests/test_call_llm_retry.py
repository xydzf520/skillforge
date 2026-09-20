"""app/common/ai.call_llm_with_retry 单元测试（v2.8.1 C5）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.common.ai import LLMAuthError, LLMQuotaExceededError, call_llm_with_retry
from app.common.retry import RetryableError


@pytest.mark.asyncio
async def test_success_no_retry():
    """第一次就成功，返回 dict，不重试。"""
    with patch(
        "app.common.ai.call_llm",
        new=AsyncMock(return_value={"meta": {"name": "ok"}}),
    ) as mock:
        result = await call_llm_with_retry("sys", "user")
    assert result == {"meta": {"name": "ok"}}
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_retryable_error_then_success():
    """5xx / 超时抛 RetryableError，第二次成功。"""
    mock = AsyncMock(
        side_effect=[
            RetryableError("500 server error"),
            {"meta": {"name": "ok"}},
        ]
    )
    with patch("app.common.ai.call_llm", new=mock):
        result = await call_llm_with_retry(
            "sys", "user", max_retries=2, description="test"
        )
    assert result == {"meta": {"name": "ok"}}
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted_returns_none():
    """所有重试都失败，返回 None（而不是崩溃）。"""
    mock = AsyncMock(side_effect=RetryableError("persistent 500"))
    with patch("app.common.ai.call_llm", new=mock):
        result = await call_llm_with_retry(
            "sys", "user", max_retries=2, description="test"
        )
    assert result is None
    assert mock.call_count == 3  # 首次 + 2 次重试


@pytest.mark.asyncio
async def test_auth_error_propagates():
    """认证错误不重试，直接冒泡让调用方感知。"""
    mock = AsyncMock(side_effect=LLMAuthError("bad key"))
    with patch("app.common.ai.call_llm", new=mock):
        with pytest.raises(LLMAuthError):
            await call_llm_with_retry("sys", "user")
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_quota_error_propagates():
    """配额耗尽不重试，直接冒泡。"""
    mock = AsyncMock(side_effect=LLMQuotaExceededError("out of quota"))
    with patch("app.common.ai.call_llm", new=mock):
        with pytest.raises(LLMQuotaExceededError):
            await call_llm_with_retry("sys", "user")
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_json_parse_failure_retry_with_hint():
    """JSON 解析失败（call_llm 返回 None）→ 带提示再试一次成功。"""
    mock = AsyncMock(side_effect=[None, {"meta": {"name": "ok"}}])
    with patch("app.common.ai.call_llm", new=mock):
        result = await call_llm_with_retry(
            "sys", "user", json_mode=True, retry_on_parse_error=True
        )
    assert result == {"meta": {"name": "ok"}}
    assert mock.call_count == 2
    # 第二次调用的 user 应该追加了提示
    second_user = mock.call_args_list[1].args[1]
    assert "valid JSON" in second_user


@pytest.mark.asyncio
async def test_no_retry_on_parse_when_disabled():
    """retry_on_parse_error=False 时，None 直接返回。"""
    mock = AsyncMock(return_value=None)
    with patch("app.common.ai.call_llm", new=mock):
        result = await call_llm_with_retry(
            "sys", "user", json_mode=True, retry_on_parse_error=False
        )
    assert result is None
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_json_mode_false_no_parse_retry():
    """json_mode=False 时，None 不触发二次重试（没 JSON 可解析）。"""
    mock = AsyncMock(return_value=None)
    with patch("app.common.ai.call_llm", new=mock):
        result = await call_llm_with_retry(
            "sys", "user", json_mode=False, retry_on_parse_error=True
        )
    assert result is None
    assert mock.call_count == 1
