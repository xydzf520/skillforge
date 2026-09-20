from __future__ import annotations

import asyncio

import pytest

from app.aiclaw.bridge_registry import BridgeConnection, _bridge_transport_chunks as server_transport_chunks
from app.common.exceptions import AppError
from bridge.skillforgebridge import _bridge_transport_chunks as bridge_transport_chunks
from bridge.skillforgebridge import _bridge_transport_decode as bridge_transport_decode


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_bridge_op_large_payload_uses_chunk_frames_and_reassembles_large_response() -> None:
    ws = _FakeWebSocket()
    conn = BridgeConnection("demo-prod", ws, epoch=7)
    large_payload = {
        "skill_id": "tmall-link-decline-analysis-20260423",
        "blob": "A" * 600_000,
    }
    expected = {
        "success": True,
        "result_blob": "B" * 700_000,
    }

    task = asyncio.create_task(conn.bridge_op("run_skill_script", large_payload, timeout=5))
    await asyncio.sleep(0)

    assert ws.sent, "bridge_op should send frames"
    assert all(frame["type"] == "bridge_op_chunk" for frame in ws.sent)
    assert ws.sent[0]["op"] == "run_skill_script"
    assert ws.sent[0]["encoding"] in {"json", "gzip+base64"}
    assert ws.sent[0]["chunk_count"] >= 1

    encoding, chunks = server_transport_chunks(expected)
    for chunk_index, chunk in enumerate(chunks):
        await conn.handle_bridge_op_response_chunk(
            {
                "type": "bridge_op_response_chunk",
                "request_id": ws.sent[0]["request_id"],
                "epoch": 7,
                "encoding": encoding,
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
                "data": chunk,
            }
        )

    assert await task == expected


@pytest.mark.asyncio
async def test_bridge_op_error_preserves_result_detail() -> None:
    ws = _FakeWebSocket()
    conn = BridgeConnection("demo-prod", ws, epoch=7)

    task = asyncio.create_task(conn.bridge_op("run_skill_script", {"skill_id": "demo"}, timeout=5))
    await asyncio.sleep(0)

    await conn.handle_bridge_op_response(
        {
            "type": "bridge_op_response",
            "request_id": ws.sent[0]["request_id"],
            "epoch": 7,
            "ok": False,
            "error": {"message": "script stdout is not a JSON object"},
            "result": {
                "returncode": 1,
                "stderr": "missing dependency",
                "raw": "not-json",
            },
        }
    )

    with pytest.raises(AppError) as excinfo:
        await task

    assert excinfo.value.code == "BRIDGE_OP_ERROR"
    assert excinfo.value.detail["detail"]["result"]["stderr"] == "missing dependency"


def test_bridge_chunk_helpers_roundtrip_large_payload() -> None:
    value = {
        "title": "天猫店铺链接下滑分析",
        "items": [
            {
                "item_id": f"8{i:03d}",
                "summary": "搜索访客下降；转化下降；市场Top300掉榜；需要复核流量来源和价格策略"
            }
            for i in range(120)
        ],
    }

    encoding, chunks = bridge_transport_chunks(value)

    assert encoding in {"json", "gzip+base64"}
    assert len(chunks) >= 1
    restored = bridge_transport_decode(
        {index: chunk for index, chunk in enumerate(chunks)},
        chunk_count=len(chunks),
        encoding=encoding,
    )

    assert restored == value
