"""call_llm_stream 单元测试。

用 mock httpx 流验证 SSE 解析的正确性，覆盖：
- 正常 delta + done
- usage 字段提取
- 错误状态码
- 异常 chunk 跳过
- timeout / connect error
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.common.ai import call_llm, call_llm_multimodal, call_llm_stream


class FakeStreamResponse:
    """模拟 httpx.Response 的流式响应"""

    def __init__(self, status_code: int, lines: list[str], body: bytes = b""):
        self.status_code = status_code
        self._lines = lines
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeClientStream:
    """模拟 httpx.AsyncClient.stream() 的 async context manager 链"""

    def __init__(self, response: FakeStreamResponse):
        self._resp = response

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return None


class FakeAsyncClient:
    """模拟 httpx.AsyncClient"""

    def __init__(self, response: FakeStreamResponse, raise_exc: Exception | None = None):
        self._resp = response
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url, **kwargs):
        if self._raise:
            raise self._raise
        return FakeClientStream(self._resp)


class FakePostResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class FakePostClient:
    def __init__(self, captured: dict):
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.captured["url"] = url
        self.captured["json"] = kwargs.get("json")
        return FakePostResponse()


def _make_sse_chunk(content: str | None = None, finish_reason: str | None = None, usage: dict | None = None) -> str:
    """构造一条 OpenAI 兼容的 SSE chunk"""
    data = {
        "id": "test",
        "object": "chat.completion.chunk",
        "choices": [{
            "index": 0,
            "delta": {"content": content} if content is not None else {},
            "finish_reason": finish_reason,
        }],
    }
    if usage:
        data["usage"] = usage
    return f"data: {json.dumps(data, ensure_ascii=False)}"


@pytest.mark.asyncio
async def test_multimodal_qwen3_request_disables_thinking(monkeypatch):
    """Qwen3 vision calls must disable thinking so content is not left empty."""
    captured: dict = {}

    def fake_client(*args, **kwargs):
        return FakePostClient(captured)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_profile_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test",
            "ai.api_key": "k",
            "ai.model": "Qwen/Qwen3.6-35B-A3B",
            "ai.max_tokens": 1000,
            "ai.temperature": 0.3,
            "ai.timeout": 30,
        }),
    )
    monkeypatch.setattr("app.common.ai._record_usage", AsyncMock())

    result = await call_llm_multimodal(
        "sys",
        [{"type": "text", "text": "看图"}],
        json_mode=False,
        model_profile="vision",
        require_system_config=True,
    )

    assert result == "ok"
    assert captured["json"]["model"] == "Qwen/Qwen3.6-35B-A3B"
    assert captured["json"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_deepseek_v4_request_uses_official_non_thinking_mode(monkeypatch):
    """V4 defaults to thinking; structured platform calls need final JSON content."""
    captured: dict = {}

    def fake_client(*args, **kwargs):
        return FakePostClient(captured)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_profile_config",
        AsyncMock(return_value={
            "ai.api_base": "https://api.deepseek.com",
            "ai.api_key": "k",
            "ai.model": "deepseek-chat",
            "ai.max_tokens": 1000,
            "ai.temperature": 0.3,
            "ai.timeout": 30,
        }),
    )
    monkeypatch.setattr("app.common.ai._record_usage", AsyncMock())

    result = await call_llm(
        "return JSON",
        "json",
        json_mode=False,
        model_override="deepseek-v4-flash",
        require_system_config=True,
    )

    assert result == "ok"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["thinking"] == {"type": "disabled"}


# ===== 1. 正常流式 =====

@pytest.mark.asyncio
async def test_stream_normal_delta_done(monkeypatch):
    """正常流：3 个 delta + 一个 finish_reason + [DONE]"""
    lines = [
        _make_sse_chunk("你好"),
        _make_sse_chunk("，我"),
        _make_sse_chunk("是 AI"),
        _make_sse_chunk(finish_reason="stop"),
        "data: [DONE]",
    ]
    fake_resp = FakeStreamResponse(200, lines)

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    delta_events = [e for e in events if e["type"] == "delta"]
    done_events = [e for e in events if e["type"] == "done"]

    assert len(delta_events) == 3
    assert delta_events[0]["content"] == "你好"
    assert delta_events[1]["content"] == "，我"
    assert delta_events[2]["content"] == "是 AI"
    assert len(done_events) >= 1
    assert done_events[-1]["finish_reason"] == "stop"


# ===== 2. usage 提取 =====

@pytest.mark.asyncio
async def test_stream_extracts_usage(monkeypatch):
    """最后一条 chunk 带 usage，应 yield usage 事件"""
    lines = [
        _make_sse_chunk("结果"),
        _make_sse_chunk(finish_reason="stop"),
        # 最后一条带 usage（OpenAI 规范）
        f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}})}",
        "data: [DONE]",
    ]
    fake_resp = FakeStreamResponse(200, lines)

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    usage_events = [e for e in events if e["type"] == "usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["input_tokens"] == 100
    assert usage_events[0]["output_tokens"] == 50
    assert usage_events[0]["total_tokens"] == 150


# ===== 3. 错误状态码 =====

@pytest.mark.asyncio
async def test_stream_http_error(monkeypatch):
    """非 200 状态码应 yield 一条 error 事件"""
    fake_resp = FakeStreamResponse(429, [], body=b"rate limited")

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "429" in error_events[0]["error"]


@pytest.mark.asyncio
async def test_stream_http_error_redacts_secret(monkeypatch):
    """供应商错误正文回显 key/token 时，流式 error 事件必须脱敏。"""
    body = b'{"api_key":"sk-stream-secret-123","error":"Authorization: Bearer raw-stream-token"}'
    fake_resp = FakeStreamResponse(401, [], body=body)

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    error_text = next(e["error"] for e in events if e["type"] == "error")
    assert "sk-stream-secret-123" not in error_text
    assert "raw-stream-token" not in error_text
    assert "[REDACTED]" in error_text


# ===== 4. 跳过格式异常的 chunk =====

@pytest.mark.asyncio
async def test_stream_skips_invalid_json(monkeypatch):
    """解析失败的 chunk 不应中断流"""
    lines = [
        _make_sse_chunk("正常"),
        "data: {malformed json",  # 损坏的 chunk
        _make_sse_chunk("继续"),
        _make_sse_chunk(finish_reason="stop"),
        "data: [DONE]",
    ]
    fake_resp = FakeStreamResponse(200, lines)

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    delta_events = [e for e in events if e["type"] == "delta"]
    assert len(delta_events) == 2
    assert delta_events[0]["content"] == "正常"
    assert delta_events[1]["content"] == "继续"


# ===== 5. timeout 异常 =====

@pytest.mark.asyncio
async def test_stream_timeout(monkeypatch):
    """httpx.TimeoutException 应转为 error 事件"""

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            FakeStreamResponse(200, []),
            raise_exc=httpx.TimeoutException("timeout"),
        )

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    events = []
    async for ev in call_llm_stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "timeout" in error_events[0]["error"].lower()


# ===== 6. messages 优先级高于 system+user =====

@pytest.mark.asyncio
async def test_stream_messages_overrides_system_user(monkeypatch):
    """传 messages 时应忽略 system/user 参数"""
    captured_payload = {}

    class CapturingClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return FakeClientStream(self._resp)

    fake_resp = FakeStreamResponse(200, [_make_sse_chunk("hi"), "data: [DONE]"])

    def fake_client(*args, **kwargs):
        return CapturingClient(fake_resp)

    monkeypatch.setattr("app.common.ai.httpx.AsyncClient", fake_client)
    monkeypatch.setattr(
        "app.common.ai.get_ai_config",
        AsyncMock(return_value={
            "ai.api_base": "http://test", "ai.api_key": "k",
            "ai.model": "test-model", "ai.max_tokens": 1000,
            "ai.temperature": 0.3, "ai.timeout": 30,
        }),
    )

    custom_msgs = [
        {"role": "system", "content": "自定义系统"},
        {"role": "user", "content": "提问 1"},
        {"role": "assistant", "content": "回答 1"},
        {"role": "user", "content": "提问 2"},
    ]
    async for _ in call_llm_stream(
        system="不应该被使用",
        user="也不应该被使用",
        messages=custom_msgs,
    ):
        pass

    assert captured_payload.get("messages") == custom_msgs
    assert captured_payload.get("stream") is True
