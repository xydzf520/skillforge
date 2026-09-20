"""AIClaw bridge enrollment / signature helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta

import bcrypt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from loguru import logger

from app.config import settings
from app.common.time_utils import now_bjt

_ROTATION_TOKENS: dict[str, tuple[str, datetime]] = {}


def generate_enrollment_token() -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires_at = now_bjt() + timedelta(minutes=settings.ENROLLMENT_TOKEN_TTL_MINUTES)
    return raw, expires_at


def generate_rotation_token(instance_id: str) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires_at = now_bjt() + timedelta(minutes=settings.ENROLLMENT_TOKEN_TTL_MINUTES)
    _ROTATION_TOKENS[instance_id] = (raw, expires_at)
    return raw, expires_at


def get_rotation_token(instance_id: str) -> str | None:
    token_info = _ROTATION_TOKENS.get(instance_id)
    if not token_info:
        return None
    raw, expires_at = token_info
    if expires_at <= now_bjt():
        _ROTATION_TOKENS.pop(instance_id, None)
        return None
    return raw


def clear_rotation_token(instance_id: str) -> None:
    _ROTATION_TOKENS.pop(instance_id, None)


def hash_enrollment_token(token: str) -> str:
    """用 bcrypt 哈希 enrollment token（带 pepper 防数据库泄露后离线爆破）。

    返回 60 字节左右的 bcrypt hash 字符串。每次调用结果不同（因 salt），
    必须用 verify_enrollment_token 比对，不能直接 ==。
    """
    payload = f"{token}{settings.ENROLLMENT_PEPPER}".encode("utf-8")
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("utf-8")


def _legacy_sha256_hash(token: str) -> str:
    """[兼容] v1.10.0 之前的 sha256 哈希算法 — 仅供 verify 路径使用。

    sha256 不可逆, 无法 rehash 已存的 hash; 但 enrollment_token_hash 是一次性的
    (验证通过后会被清空), 所以 verify 时双层兼容即可让老 hash 自然消亡。
    """
    return hashlib.sha256(f"{token}{settings.ENROLLMENT_PEPPER}".encode("utf-8")).hexdigest()


def _is_bcrypt_hash(hashed: str) -> bool:
    """识别 bcrypt hash 格式 ($2a$ / $2b$ / $2y$ 前缀, 长度 ≥ 50)。"""
    return hashed.startswith(("$2a$", "$2b$", "$2y$")) and len(hashed) >= 50


def _is_legacy_sha256_hash(hashed: str) -> bool:
    """识别 64 字符 hex 字符串 (sha256 输出格式)。"""
    if len(hashed) != 64:
        return False
    try:
        int(hashed, 16)
        return True
    except ValueError:
        return False


def verify_enrollment_token(token: str, hashed: str | None) -> bool:
    """常时间比对 enrollment token 与存储的 hash。

    [C3] 双层兼容: v1.10.0 之前的 sha256 hash 与 v1.10.1+ 的 bcrypt hash 均可校验。
    enrollment_token_hash 是一次性 (验证后会被 bridge_router 清空), 所以无需主动
    rehash 写回 — 老 hash 在 v1.10.1 部署后自然在第一次成功 enroll 时消失,
    后续新签发的 token 一律走 bcrypt。
    """
    if not token or not hashed:
        return False

    hashed = hashed.strip()
    payload = f"{token}{settings.ENROLLMENT_PEPPER}".encode("utf-8")

    # 1. bcrypt 路径 (v1.10.1+)
    if _is_bcrypt_hash(hashed):
        try:
            return bcrypt.checkpw(payload, hashed.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    # 2. legacy sha256 路径 (v1.10.0 及以前) — 部署 v1.10.1 后未消费的 instance 用
    if _is_legacy_sha256_hash(hashed):
        legacy = _legacy_sha256_hash(token)
        if secrets.compare_digest(legacy, hashed.lower()):
            logger.warning(
                "[security] enrollment token 验证使用了 legacy sha256 hash, "
                "建议尽快完成 enroll 让 hash 自然清空 (下一次签发将是 bcrypt)"
            )
            return True
        return False

    # 3. 未知格式 — 拒绝
    return False


def build_fingerprint(hostname: str | None = None, platform: str | None = None) -> str:
    left = hostname or "unknown-host"
    right = platform or "unknown-platform"
    return f"{left}:{right}"[:200]


def verify_signature(device_pubkey: str, nonce: str, signature: str) -> bool:
    try:
        pubkey_bytes = base64.b64decode(device_pubkey)
        sig_bytes = base64.b64decode(signature)
        Ed25519PublicKey.from_public_bytes(pubkey_bytes).verify(sig_bytes, nonce.encode("utf-8"))
        return True
    except Exception:
        return False
