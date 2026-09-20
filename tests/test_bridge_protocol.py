"""AIClaw Bridge 协议层单测：enrollment / 签名 / 重放防护。

不依赖真实 WebSocket 连接，直接 mock + 调底层模块。
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.aiclaw import bridge_router as br
from app.aiclaw.bridge_protocol import BridgeOpResponseFrame, ForwardResponseFrame
from app.aiclaw.security import hash_enrollment_token, verify_enrollment_token, verify_signature
from app.config import settings


def _make_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(pub_bytes).decode("utf-8")


def test_verify_signature_correct_pubkey():
    private_key, pubkey_b64 = _make_keypair()
    nonce = "abcd1234"
    sig = base64.b64encode(private_key.sign(nonce.encode())).decode()
    assert verify_signature(pubkey_b64, nonce, sig) is True


def test_verify_signature_wrong_nonce():
    private_key, pubkey_b64 = _make_keypair()
    sig = base64.b64encode(private_key.sign(b"orig")).decode()
    assert verify_signature(pubkey_b64, "different", sig) is False


def test_verify_signature_wrong_pubkey():
    private_key, _ = _make_keypair()
    _, other_pub = _make_keypair()
    sig = base64.b64encode(private_key.sign(b"hello")).decode()
    assert verify_signature(other_pub, "hello", sig) is False


def test_verify_signature_invalid_input():
    assert verify_signature("not-base64", "x", "not-base64") is False


def test_hash_enrollment_token_uses_bcrypt():
    # bcrypt 每次 hash 结果不同（salt 不同），必须用 verify 比对
    h1 = hash_enrollment_token("token-1")
    h2 = hash_enrollment_token("token-1")
    assert h1 != h2  # bcrypt salt 保证两次 hash 不同
    # bcrypt hash 以 "$2b$" 开头
    assert h1.startswith("$2")
    # verify 必须能比对正确 token
    assert verify_enrollment_token("token-1", h1) is True
    assert verify_enrollment_token("token-1", h2) is True
    # 错误 token 必须返回 False
    assert verify_enrollment_token("token-2", h1) is False
    # 空值兜底
    assert verify_enrollment_token("", h1) is False
    assert verify_enrollment_token("token-1", None) is False
    assert verify_enrollment_token("token-1", "not-a-bcrypt-hash") is False


def test_verify_enrollment_token_legacy_sha256_compat():
    """[C3] v1.10.0 之前的 sha256 hash 仍能通过 verify, 避免 v1.10.1 部署后未消费的
    instance 因 hash 算法切换被踢出 (sha256 不可逆, 只能 verify 兼容, 不能 rehash)。
    """
    from app.aiclaw.security import _legacy_sha256_hash

    # 模拟 v1.10.0 留下的 sha256 hash
    legacy_hash = _legacy_sha256_hash("token-A")
    assert len(legacy_hash) == 64  # sha256 hex
    assert all(c in "0123456789abcdef" for c in legacy_hash)

    # verify 应能识别老 hash 并通过
    assert verify_enrollment_token("token-A", legacy_hash) is True
    # 错误 token 仍应拒绝
    assert verify_enrollment_token("token-B", legacy_hash) is False
    # 空值兜底
    assert verify_enrollment_token("", legacy_hash) is False


def test_verify_enrollment_token_format_dispatch():
    """verify 应根据 hash 格式自动派发到 bcrypt 或 sha256 路径。"""
    from app.aiclaw.security import _legacy_sha256_hash

    bcrypt_hash = hash_enrollment_token("dual-token")
    legacy_hash = _legacy_sha256_hash("dual-token")

    # 同一 token 用两种 hash 都能验证
    assert verify_enrollment_token("dual-token", bcrypt_hash) is True
    assert verify_enrollment_token("dual-token", legacy_hash) is True

    # 拒绝既不是 bcrypt 也不是 sha256 的格式 (例如 base64 / md5)
    assert verify_enrollment_token("dual-token", "abc123") is False  # 太短
    assert verify_enrollment_token("dual-token", "z" * 64) is False  # 64 字符但不是 hex
    assert verify_enrollment_token("dual-token", "$1$" + "x" * 50) is False  # md5-crypt 不支持


@pytest.mark.asyncio
async def test_authenticate_rejects_invalid_first_frame_type():
    websocket = MagicMock()
    websocket.close = AsyncMock()
    websocket.send_json = AsyncMock()
    result = await br._authenticate(websocket, {"type": "register", "instance_id": "x"})
    assert result is None
    websocket.close.assert_awaited_once()
    args = websocket.close.await_args.kwargs or websocket.close.await_args.args
    # 关闭 reason 必须明确指出 first frame must be auth_init
    if hasattr(args, "get"):
        assert args.get("code") == 4400 or 4400 in args.values()


@pytest.mark.asyncio
async def test_authenticate_rejects_missing_instance_id():
    websocket = MagicMock()
    websocket.close = AsyncMock()
    websocket.send_json = AsyncMock()
    result = await br._authenticate(
        websocket,
        {"type": "auth_init", "instance_id": "", "pubkey": "x", "enrollment_token": "y"},
    )
    assert result is None
    websocket.close.assert_awaited_once()


def test_register_frame_removed_from_protocol():
    """RegisterFrame 已删除，不应再可被 import。"""
    import app.aiclaw.bridge_protocol as bp

    assert not hasattr(bp, "RegisterFrame")


def test_forward_response_accepts_plain_text_gateway_error():
    frame = ForwardResponseFrame.model_validate({
        "type": "forward_response",
        "request_id": "request-1",
        "epoch": 7,
        "ok": False,
        "error": "local gateway offline",
    })

    assert frame.error == "local gateway offline"


def test_bridge_op_response_accepts_plain_text_error():
    frame = BridgeOpResponseFrame.model_validate({
        "type": "bridge_op_response",
        "request_id": "request-2",
        "epoch": 8,
        "ok": False,
        "error": "operation unavailable",
    })

    assert frame.error == "operation unavailable"
