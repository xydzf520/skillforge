"""Hermes Agent HTTP 客户端。

通过 OpenAI 兼容 API 与运行 Hermes Agent 的设备通信。
支持对话、Skill 下发、健康检查。

Hermes API 文档: https://github.com/nousresearch/hermes-agent
默认端口: 8642
认证: Bearer <API_SERVER_KEY>
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import AsyncIterator

import httpx
from loguru import logger

from app.common.exceptions import AppError


class HermesClient:
    """Hermes Agent HTTP API 客户端"""

    def __init__(self, base_url: str, api_key: str = ""):
        # 规范化 URL
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("http"):
            self.base_url = f"http://{self.base_url}"
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # ── 健康检查 ──

    async def health(self) -> dict:
        """检查 Hermes Agent 是否在线"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/health", headers=self._headers())
                if r.status_code == 200:
                    return {"online": True, **r.json()}
                return {"online": False, "status_code": r.status_code}
        except Exception as e:
            return {"online": False, "error": str(e)}

    # ── 对话（非流式） ──

    async def chat(self, message: str, *, session_id: str = "", system: str = "") -> dict:
        """发送一条消息并等待完整回复（OpenAI chat/completions 格式）"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})

        payload = {
            "messages": messages,
            "model": "hermes-agent",
            "stream": False,
        }

        headers = self._headers()
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if r.status_code != 200:
                    raise AppError("HERMES_CHAT_FAILED", r.status_code, {"detail": r.text[:300]})
                data = r.json()
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage", {}),
                    "model": data.get("model", "hermes-agent"),
                }
        except AppError:
            raise
        except Exception as e:
            raise AppError("HERMES_UNREACHABLE", 503, {"detail": str(e)})

    # ── 对话（流式） ──

    async def chat_stream(self, message: str, *, session_id: str = "", system: str = "") -> AsyncIterator[dict]:
        """流式对话 — yield 事件（兼容 SkillForge WS 前端协议）"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})

        payload = {
            "messages": messages,
            "model": "hermes-agent",
            "stream": True,
        }

        headers = self._headers()
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        yield {"state": "started", "agent_type": "hermes"}

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield {"state": "error", "error": body.decode()[:300]}
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield {"state": "streaming", "text": content}
                        except json.JSONDecodeError:
                            continue

            yield {"state": "final", "text": ""}
        except Exception as e:
            yield {"state": "error", "error": str(e)}

    # ── Skill 下发 ──

    async def install_skill(self, skill_id: str, files: list[dict]) -> dict:
        """通过 Hermes 的 chat API 让 Agent 自行写入 Skill 文件。

        Hermes 没有专用的 install_skill RPC，但 Agent 有 file 和 terminal 工具。
        我们用一条 system 指令让它把文件写到 ~/.hermes/skills/{skill_id}/。

        files: [{"path": "SKILL.md", "content": "..."}, ...]
        """
        # 构建写入指令
        commands = []
        for f in files:
            path = f["path"]
            content = f.get("content", "")
            # base64 编码避免内容中的特殊字符干扰
            content_b64 = base64.b64encode(content.encode()).decode()
            commands.append(
                f"mkdir -p ~/.hermes/skills/{skill_id}/$(dirname {path}) && "
                f"echo '{content_b64}' | base64 -d > ~/.hermes/skills/{skill_id}/{path}"
            )

        install_prompt = (
            f"请执行以下命令，将 Skill '{skill_id}' 安装到本地：\n\n"
            + "\n".join(f"```bash\n{cmd}\n```" for cmd in commands)
            + "\n\n安装完成后回复 'installed'。"
        )

        result = await self.chat(
            install_prompt,
            system="你是运维助手。请直接执行用户给出的 bash 命令，不要询问确认。",
        )
        return {
            "skill_id": skill_id,
            "installed": "install" in result.get("content", "").lower() or "成功" in result.get("content", ""),
            "response": result.get("content", "")[:500],
        }

    async def remove_skill(self, skill_id: str) -> dict:
        """删除设备上的 Skill"""
        result = await self.chat(
            f"请执行: rm -rf ~/.hermes/skills/{skill_id} && echo 'removed'",
            system="你是运维助手。请直接执行用户给出的 bash 命令。",
        )
        return {"skill_id": skill_id, "removed": True}

    async def list_skills(self) -> list[dict]:
        """列出设备上已安装的 Skill"""
        result = await self.chat(
            "请执行: ls -1 ~/.hermes/skills/ 2>/dev/null && echo '---END---' || echo 'no skills'",
            system="你是运维助手。只输出命令结果，不加解释。",
        )
        content = result.get("content", "")
        skills = []
        for line in content.split("\n"):
            line = line.strip()
            if line and line != "---END---" and not line.startswith("no "):
                skills.append({"id": line, "source": "hermes"})
        return skills
