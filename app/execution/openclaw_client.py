"""
通过WebSocket与OpenClaw/AIClaw Gateway通信。
SkillForge不执行Skill脚本，只负责调度和收结果。

支持两种认证方式：
- OpenClaw: HTTP Header Bearer token
- AIClaw: WebSocket 协议内 challenge-response (connect 消息)
"""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Awaitable

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from loguru import logger

from app.common.exceptions import AppError
from app.config import settings


def _local_config_dir_order() -> list[str]:
    order: list[str] = []

    def add(item: str) -> None:
        clean = (item or "").strip()
        if clean and clean not in order:
            order.append(clean)

    add(".openclaw")
    add(".aiclaw")
    return order


def _discover_local_device_identities() -> list[dict]:
    identities: list[dict] = []
    seen: set[str] = set()
    for cfg_dir in _local_config_dir_order():
        identity_path = Path.home() / cfg_dir / "identity" / "device.json"
        if not identity_path.exists():
            continue
        try:
            data = json.loads(identity_path.read_text(encoding="utf-8"))
            device_id = str(data.get("deviceId") or data.get("device_id") or data.get("id") or "").strip()
            public_key_pem = str(data.get("publicKeyPem") or "").strip()
            private_key_pem = str(data.get("privateKeyPem") or "").strip()
            if not (device_id and public_key_pem and private_key_pem):
                continue
            key = f"{device_id}:{public_key_pem}"
            if key in seen:
                continue
            seen.add(key)
            identities.append(
                {
                    "device_id": device_id,
                    "public_key_pem": public_key_pem,
                    "private_key_pem": private_key_pem,
                    "source_dir": cfg_dir,
                }
            )
        except Exception:
            logger.debug("忽略不可用的本机 OpenClaw 设备身份: {}", identity_path)
    return identities


def _discover_local_device_identity() -> dict:
    identities = _discover_local_device_identities()
    return identities[0] if identities else {}


def _discover_local_device_id() -> str:
    for env_key in ("LOCAL_AICLAW_DEVICE_ID", "AICLAW_DEVICE_ID", "OPENCLAW_DEVICE_ID"):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value

    identity = _discover_local_device_identity()
    if identity.get("device_id"):
        return str(identity["device_id"])

    for cfg_dir in _local_config_dir_order():
        paired_path = Path.home() / cfg_dir / "devices" / "paired.json"
        if not paired_path.exists():
            continue
        try:
            data = json.loads(paired_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if isinstance(value, dict):
                    device_id = str(value.get("deviceId") or value.get("device_id") or value.get("id") or "").strip()
                    if device_id:
                        return device_id
                if isinstance(key, str) and key.strip():
                    return key.strip()
        except Exception:
            logger.debug("忽略不可用的本机 OpenClaw paired 设备: {}", paired_path)
    return ""


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _public_key_raw_base64url(public_key_pem: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _base64url(raw)


def _local_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return sys.platform


def _build_local_device_auth(
    *,
    identity: dict | None = None,
    nonce: str,
    token: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    client_platform: str,
) -> dict | None:
    identity = identity or _discover_local_device_identity()
    if not identity:
        return None

    try:
        signed_at_ms = int(time.time() * 1000)
        payload = "|".join(
            [
                "v3",
                str(identity["device_id"]),
                client_id,
                client_mode,
                role,
                ",".join(scopes),
                str(signed_at_ms),
                token or "",
                nonce,
                (client_platform or "").strip().lower(),
                "",
            ]
        )
        private_key = load_pem_private_key(
            str(identity["private_key_pem"]).encode("utf-8"),
            password=None,
        )
        signature = _base64url(private_key.sign(payload.encode("utf-8")))
        return {
            "id": identity["device_id"],
            "publicKey": _public_key_raw_base64url(str(identity["public_key_pem"])),
            "signature": signature,
            "signedAt": signed_at_ms,
            "nonce": nonce,
        }
    except Exception:
        logger.warning("本机 OpenClaw 设备身份签名失败，将退化为 device.id 握手")
        return None


class OpenClawClient:
    """WebSocket客户端，兼容 OpenClaw 和 AIClaw Gateway"""

    def __init__(self, gateway_url: str, auth_token: str = ""):
        self.gateway_url = gateway_url
        self.auth_token = auth_token

    @staticmethod
    def _mock_allowed(*, sandbox: bool) -> bool:
        return settings.ALLOW_MOCK_EXECUTION and sandbox

    @staticmethod
    def _log_mock_warning() -> None:
        logger.warning("[WARN] Mock execution enabled — do NOT use in production")

    @staticmethod
    def _parse_final_output(text: str, role: str = "assistant") -> dict:
        """把 agent final 文本尽量还原成 Skill 输出 JSON。

        OpenClaw/AIClaw 的 chat.final 通常是自然语言文本；如果 Skill 按约定返回
        JSON（含 reports/todos），这里直接还原成 dict，后续 execution_service 才能
        写入收件-报告和待办。解析失败则保留旧行为。
        """
        raw = (text or "").strip()
        if not raw:
            return {"output": "", "role": role}

        candidates = [raw]
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            candidates.append("\n".join(lines).strip())

        # 兼容“说明文字 + ```json ... ```”格式。
        marker = "```json"
        if marker in raw:
            tail = raw.split(marker, 1)[1]
            block = tail.split("```", 1)[0].strip()
            candidates.append(block)

        for item in candidates:
            try:
                parsed = json.loads(item)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed

        return {"output": raw, "role": role}

    async def _connect_and_auth(self):
        """建立 WebSocket 连接并完成认证（兼容 AIClaw challenge-response）"""
        ws = await websockets.connect(
            self.gateway_url,
            open_timeout=10,
            close_timeout=5,
        )

        try:
            # 尝试接收第一条消息（AIClaw 会发 challenge）
            first_msg = None
            try:
                first_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except asyncio.TimeoutError:
                # OpenClaw 不发 challenge，直接可用
                return ws

            # AIClaw challenge-response 认证
            if first_msg and first_msg.get("event") == "connect.challenge":
                nonce = first_msg.get("payload", {}).get("nonce", "")
                client_id = "gateway-client"
                client_mode = "backend"
                client_platform = _local_platform()
                role = "operator"
                scopes = ["operator.read", "operator.write", "operator.admin"]
                params = {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": client_id,
                        "displayName": "SkillForge",
                        "version": "1.0",
                        "platform": client_platform,
                        "mode": client_mode,
                    },
                    "caps": [],
                    "role": role,
                    "scopes": scopes,
                }
                if self.auth_token:
                    params["auth"] = {"token": self.auth_token}

                device = _build_local_device_auth(
                    nonce=nonce,
                    token=self.auth_token,
                    client_id=client_id,
                    client_mode=client_mode,
                    role=role,
                    scopes=scopes,
                    client_platform=client_platform,
                )
                if device:
                    params["device"] = device
                else:
                    bare_id = _discover_local_device_id()
                    if bare_id:
                        params["device"] = {"id": bare_id}

                connect_id = f"sf-{nonce[:8]}"
                connect_msg = {
                    "type": "req",
                    "method": "connect",
                    "id": connect_id,
                    "params": params,
                }
                await ws.send(json.dumps(connect_msg))

                # 等待认证结果
                deadline = time.time() + 10
                while True:
                    if time.time() >= deadline:
                        raise asyncio.TimeoutError("AIClaw connect auth response timed out")
                    timeout = max(0.1, deadline - time.time())
                    auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    if auth_resp.get("type") != "res" or auth_resp.get("id") != connect_id:
                        logger.debug(f"AIClaw 认证中间消息: {auth_resp.get('type')}")
                        continue
                    break
                if auth_resp.get("ok"):
                    logger.info("AIClaw Gateway 认证成功")
                else:
                    error_msg = auth_resp.get("error", {}).get("message", "认证失败")
                    logger.error(f"AIClaw 认证失败: {error_msg}")
                    await ws.close()
                    raise AppError("GATEWAY_AUTH_FAILED", 401, {"detail": error_msg})

            return ws

        except AppError:
            await ws.close()
            raise
        except Exception:
            await ws.close()
            raise

    async def run_skill(
        self,
        skill_id: str,
        params: dict,
        sandbox: bool = False,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> dict:
        """让Gateway执行一个Skill，返回结果。

        生产执行 (sandbox=False) 对连接 / 瞬态网络错误自动重试 1 次（指数退避），
        沙箱执行不重试（错了也能在 UI 明显看到）。
        EXECUTION_SCRIPT_ERROR（Skill 逻辑错误）不重试 — 那是业务 bug，不是瞬态问题。
        """
        if not sandbox:
            from app.common.retry import retry_with_backoff, RetryableError

            async def _attempt() -> dict:
                try:
                    return await self._run_skill_once(skill_id, params, sandbox, on_progress)
                except AppError as e:
                    # Gateway 连接 / 通信失败视为可重试；业务逻辑错误 (EXECUTION_SCRIPT_ERROR) 不重试
                    if e.code == "GATEWAY_EXECUTION_FAILED":
                        raise RetryableError(f"Gateway 瞬态失败: {e}") from e
                    raise

            try:
                return await retry_with_backoff(
                    _attempt,
                    max_retries=1,  # 重试 1 次（共尝试 2 次），避免首发延迟过大
                    base_delay=1.0,
                    description=f"run_skill {skill_id}",
                    retryable_exceptions=(RetryableError,),
                )
            except RetryableError as e:
                raise AppError(
                    "GATEWAY_EXECUTION_FAILED", 502,
                    {"detail": f"重试后仍失败: {e}"},
                ) from e

        return await self._run_skill_once(skill_id, params, sandbox, on_progress)

    async def _run_skill_once(
        self,
        skill_id: str,
        params: dict,
        sandbox: bool,
        on_progress: Callable[[dict], Awaitable[None]] | None,
    ) -> dict:
        """单次执行 Skill，无重试。由 run_skill / 重试逻辑包装调用。"""
        try:
            ws = await self._connect_and_auth()

            try:
                # 用 chat.send 让 agent 执行 skill
                prompt = (
                    f"请执行 Skill '{skill_id}'，参数: {json.dumps(params, ensure_ascii=False)}。"
                    "最终回复必须是一个 JSON 对象；报告放顶层 reports 数组，待办放顶层 todos 数组，"
                    "没有则返回空数组，不要只返回 Markdown。"
                )
                if sandbox:
                    prompt += "（run_mode=sandbox_test，沙箱模式，仅测试不影响生产；如使用样例/fixture 数据，必须在结果 metadata 中标记 sample_used=true。）"

                import uuid as _uuid
                await ws.send(json.dumps({
                    "type": "req",
                    "method": "chat.send",
                    "id": f"sf-run-{skill_id[:20]}",
                    "params": {
                        "sessionKey": f"agent:main:skillforge-{skill_id}",
                        "message": prompt,
                        "idempotencyKey": str(_uuid.uuid4()),
                    },
                }))

                result = {}
                got_ok = False

                async for message in ws:
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    event = data.get("event", "")
                    payload = data.get("payload", {})

                    if msg_type == "res":
                        if data.get("ok") is False:
                            error = data.get("error", {})
                            logger.warning(f"AIClaw 方法调用失败: {error}")
                            raise AppError("EXECUTION_SCRIPT_ERROR", 500, detail=error)
                        got_ok = True
                        continue

                    if not got_ok:
                        continue

                    # agent stream=assistant → 实时文本
                    if event == "agent":
                        stream = payload.get("stream", "")
                        ad = payload.get("data", {})
                        if stream == "assistant" and isinstance(ad, dict):
                            text = ad.get("text", "")
                            if text and on_progress:
                                await on_progress({"text": text})

                    # chat state=final → 最终回复
                    elif event == "chat":
                        state = payload.get("state", "")
                        msg = payload.get("message", {})
                        if state == "final" and isinstance(msg, dict):
                            content = msg.get("content", [])
                            if isinstance(content, list):
                                texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                                result = self._parse_final_output("\n".join(texts), msg.get("role", "assistant"))
                            elif isinstance(content, str):
                                result = self._parse_final_output(content, msg.get("role", "assistant"))
                            break

                # 超时兜底：如果 60s 内没收到 final，返回已有结果
                if not result and got_ok:
                    try:
                        while True:
                            msg2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                            if msg2.get("event") == "chat" and msg2.get("payload", {}).get("state") == "final":
                                msg_body = msg2["payload"].get("message", {})
                                content = msg_body.get("content", [])
                                if isinstance(content, list):
                                    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                                    result = self._parse_final_output("\n".join(texts), "assistant")
                                break
                    except asyncio.TimeoutError:
                        pass

                return result

            finally:
                await ws.close()

        except AppError:
            raise
        except Exception as e:
            logger.error(f"Gateway执行失败: {skill_id} error={e}")
            if self._mock_allowed(sandbox=sandbox):
                self._log_mock_warning()
                return self._mock_result(skill_id, sandbox)
            raise AppError("GATEWAY_EXECUTION_FAILED", 502, {"detail": str(e)}) from e

    async def run_playbook(
        self,
        playbook_id: str,
        params: dict,
        on_step_complete: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> dict:
        """执行Playbook"""
        try:
            ws = await self._connect_and_auth()

            try:
                await ws.send(json.dumps({
                    "type": "req",
                    "method": "playbook.run",
                    "id": f"sf-pb-{playbook_id[:20]}",
                    "params": {
                        "playbook": playbook_id,
                        "params": params,
                        "metadata": {"source": "skillforge"},
                    },
                }))

                steps_result = {}
                final_status = "completed"

                async for message in ws:
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    event = data.get("event", "")

                    if event == "playbook.step_complete":
                        payload = data.get("payload", {})
                        step_id = payload.get("step_id", "")
                        steps_result[step_id] = payload.get("output", {})
                        if on_step_complete:
                            await on_step_complete(step_id, payload)
                    elif event == "playbook.result":
                        final_status = data.get("payload", {}).get("status", "completed")
                        break
                    elif event == "playbook.error":
                        raise AppError("EXECUTION_SCRIPT_ERROR", 500, detail=data.get("payload"))
                    elif msg_type == "response":
                        if data.get("error"):
                            raise AppError("EXECUTION_SCRIPT_ERROR", 500, detail=data.get("error"))
                        break

                return {"playbook_id": playbook_id, "status": final_status, "steps": steps_result}

            finally:
                await ws.close()

        except AppError:
            raise
        except Exception as e:
            logger.error(f"Playbook执行失败: {playbook_id} error={e}")
            raise AppError("PLAYBOOK_EXECUTION_FAILED", 502, {"detail": str(e)}) from e

    async def get_status(self) -> dict:
        """心跳检测"""
        try:
            ws = await self._connect_and_auth()
            try:
                await ws.send(json.dumps({"type": "req", "method": "status", "id": "sf-hb"}))
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                return {"online": True, "skills_count": data.get("result", {}).get("skills_count", 0)}
            finally:
                await ws.close()
        except Exception:
            return {"online": False}

    async def reload_skills(self) -> dict:
        """通知Gateway重新加载Skill文件"""
        try:
            ws = await self._connect_and_auth()
            try:
                await ws.send(json.dumps({"type": "req", "method": "skills.reload", "id": "sf-reload"}))
                data = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                return data.get("result", {"ok": True})
            finally:
                await ws.close()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _mock_result(self, skill_id: str, sandbox: bool) -> dict:
        """Mock结果"""
        return {
            "skill_id": skill_id,
            "status": "success",
            "sandbox": sandbox,
            "run_mode": "sandbox_test" if sandbox else "manual_real",
            "mock": True,
            "output": {"message": f"Mock执行结果: {skill_id}"},
        }


# 默认客户端实例
default_client = OpenClawClient(
    gateway_url=settings.OPENCLAW_DEFAULT_URL,
    auth_token=settings.OPENCLAW_DEFAULT_AUTH,
)
