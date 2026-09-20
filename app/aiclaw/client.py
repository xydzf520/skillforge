"""SkillForge 侧 AIClaw 转发客户端。"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator
from uuid import uuid4

from app.common.exceptions import AppError
from app.config import settings

from .bridge_registry import bridge_registry


class AIClawClient:
    def __init__(self, instance_id: str, gateway_kind: str | None = None):
        self.instance_id = instance_id
        # gateway_kind 来自 bridge capabilities 上报 —— `aiclaw` / `openclaw` / None。
        # openclaw 的 `agents.list` 只接受空 params，给它传 `includeIdentity` 会被拒
        # （`INVALID_REQUEST: unexpected property 'includeIdentity'`）。必须按 gateway
        # 分流。未知时按最严格 openclaw 行为走，保证不误传多余参数。
        self.gateway_kind = (gateway_kind or "").lower()

    def _conn(self):
        conn = bridge_registry.get(self.instance_id)
        if not conn:
            raise AppError(
                "BRIDGE_OFFLINE",
                503,
                {"detail": f"实例 {self.instance_id} 的 bridge 未连接"},
            )
        return conn

    async def list_agents(self) -> list[dict]:
        params: dict = {}
        if self.gateway_kind == "aiclaw":
            # AIClaw 支持 includeIdentity=true → 返回值带 identity.name
            params["includeIdentity"] = True
        return await self._call_with_context("agents.list", params, "agents")

    async def list_skills(self, agent_id: str) -> list[dict]:
        return await self._call_with_context(
            "skills.status", {"agentId": agent_id}, "skills"
        )

    async def _call_with_context(self, method: str, params: dict, result_key: str) -> list[dict]:
        """统一的 bridge 转发调用 —— 失败时把 bridge 原始 error / method / gateway_kind
        一起放进 AppError detail，便于 UI 显示具体原因。

        之前 UI 只能看到笼统一句"AIClaw 调用失败"，真正的错误（方法不认识 / 参数不对 /
        后端返回结构异常）都被吞了，没法排查。
        """
        try:
            result = await self._conn().call(method, params)
        except AppError as exc:
            raise AppError(
                exc.code,
                exc.status,
                self._enrich_detail(exc.detail, method=method),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                "AICLAW_ERROR",
                502,
                self._enrich_detail({"raw": str(exc)}, method=method),
            ) from exc
        return result.get(result_key, [])

    def _enrich_detail(self, detail: dict | None, method: str) -> dict:
        """把 method / bridge 原始 error 上浮到 AppError detail，便于 UI 展示具体原因。

        bridge_registry.handle_forward_response 把 bridge 回传的 error 放在 detail['detail']，
        这里把它重命名成 `bridge_error`，前端好取。gateway_kind 的展示由 router 侧加，
        因为 client 层没有 DB session。
        """
        enriched: dict = {"method": method, "instance_id": self.instance_id}
        if detail:
            inner = detail.get("detail") if isinstance(detail, dict) else None
            if inner is not None:
                enriched["bridge_error"] = inner
            enriched.update({k: v for k, v in detail.items() if k != "detail"})
        return enriched

    async def chat_send(
        self,
        agent_id: str,
        message: str,
        attachments: list[dict] | None = None,
        model_context: dict | None = None,
        thinking: str = "medium",
    ) -> AsyncIterator[dict]:
        attachments = attachments or []
        self._validate_attachments(message, attachments)
        normalized_attachments = self._to_aiclaw_attachments(attachments)
        run_id = f"sf-{uuid4().hex}"
        conn = self._conn()
        queue = conn.open_chat_stream(run_id)

        try:
            ack = await conn.call(
                "chat.send",
                {
                    "sessionKey": f"agent:{agent_id}:main",
                    "message": message,
                    "idempotencyKey": run_id,
                    "thinking": thinking,
                    "attachments": normalized_attachments,
                    "modelContext": dict(model_context or {}),
                },
                timeout=10,
            )
        except Exception:
            conn.close_chat_stream(run_id)
            raise

        # AIClaw 可能返回与 idempotencyKey 不同的 runId，注册 alias 让事件能定位到 queue
        aiclaw_run_id = ack.get("runId") if isinstance(ack, dict) else None
        if aiclaw_run_id:
            conn.register_run_alias(run_id, aiclaw_run_id)

        yield {"state": "started", "runId": run_id, "aiclawRunId": aiclaw_run_id}

        try:
            while True:
                raw = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.AICLAW_CHAT_STREAM_TIMEOUT_SECONDS,
                )
                normalized = self._normalize_chat_event(run_id, raw)
                if normalized is None:
                    continue
                yield normalized
                if normalized.get("state") in {"final", "aborted", "error"}:
                    break
        except asyncio.TimeoutError:
            yield {
                "state": "error",
                "runId": run_id,
                "error": "chat stream timeout",
            }
        finally:
            conn.close_chat_stream(run_id)

    async def chat_abort(self, agent_id: str, run_id: str) -> None:
        await self._conn().call(
            "chat.abort",
            {
                "sessionKey": f"agent:{agent_id}:main",
                "runId": run_id,
            },
        )

    async def install_skill(
        self,
        skill_id: str,
        files: list[dict],
        target_dir: str | None = None,
        shared_files: list[dict] | None = None,
    ) -> dict:
        """通过 bridge 把 skill 文件写到设备 ~/.aiclaw/skills/<skill_id>/。

        files: [{"path": "SKILL.md", "content_b64": "..."}, ...]
        """
        return await self._conn().bridge_op(
            "install_skill",
            {"skill_id": skill_id, "files": files, "target_dir": target_dir, "shared_files": shared_files or []},
            timeout=60,
        )

    async def reload_skills(self) -> dict:
        """让本地 AIClaw/OpenClaw gateway 重新加载 skills。

        文件通过 bridge 写到磁盘后，部分 gateway 不会立刻重新扫描；同步完成后补一次
        `skills.reload`，让设备端列表和实际可执行 skill 尽快一致。
        """
        try:
            return await self._conn().call("skills.reload", {}, timeout=15)
        except AppError as exc:
            raise AppError(
                exc.code,
                exc.status,
                self._enrich_detail(exc.detail, method="skills.reload"),
            ) from exc

    async def register_agent_skill(
        self,
        skill_id: str,
        *,
        runtime: dict | None = None,
        target_dir: str | None = None,
        timeout: int = 30,
    ) -> dict:
        """Ask bridge to register a synced Skill with the local OpenClaw Agent runtime."""
        return await self._conn().bridge_op(
            "register_skill",
            {
                "skill_id": skill_id,
                "runtime": runtime or {"backend": "openclaw_agent"},
                "target_dir": target_dir,
                "timeout": timeout,
            },
            timeout=max(1, min(int(timeout), 120)) + 5,
        )

    async def sync_schedules(
        self,
        schedules: list[dict],
        submit_token: str,
        submit_url: str = "",
        config_version: int = 0,
    ) -> dict:
        """推送定时执行配置到节点 bridge。"""
        return await self._conn().bridge_op(
            "sync_schedules",
            {
                "schedules": schedules,
                "submit_token": submit_token,
                "submit_url": submit_url,
                "config_version": config_version,
            },
            timeout=30,
        )

    async def submit_training_job(self, payload: dict, *, timeout: int = 30) -> dict:
        """Submit a frozen training manifest to the local Bridge training gateway."""
        return await self._conn().bridge_op(
            "training.submit_job",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def cancel_training_job(self, payload: dict, *, timeout: int = 15) -> dict:
        return await self._conn().bridge_op(
            "training.cancel_job",
            payload,
            timeout=max(5, min(int(timeout), 60)),
        )

    async def stream_training_logs(self, payload: dict, *, timeout: int = 15) -> dict:
        return await self._conn().bridge_op(
            "training.stream_logs",
            payload,
            timeout=max(5, min(int(timeout), 60)),
        )

    async def collect_training_result(self, payload: dict, *, timeout: int = 15) -> dict:
        return await self._conn().bridge_op(
            "training.collect_result",
            payload,
            timeout=max(5, min(int(timeout), 60)),
        )

    async def download_training_artifact(self, payload: dict, *, timeout: int = 60) -> dict:
        return await self._conn().bridge_op(
            "training.download_artifact",
            payload,
            timeout=max(10, min(int(timeout), 300)),
        )

    async def import_training_artifact(self, payload: dict, *, timeout: int = 300) -> dict:
        return await self._conn().bridge_op(
            "training.import_artifact",
            payload,
            timeout=max(30, min(int(timeout), 1800)),
        )

    async def get_training_artifact_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.artifact_status",
            payload,
            timeout=max(5, min(int(timeout), 60)),
        )

    async def write_training_dataset_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_write_chunk",
            payload,
            timeout=max(10, min(int(timeout), 600)),
        )

    async def commit_training_dataset(self, payload: dict, *, timeout: int = 300) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_commit",
            payload,
            timeout=max(30, min(int(timeout), 1800)),
        )

    async def get_training_dataset_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_status",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def start_training_dataset_export(self, payload: dict, *, timeout: int = 3600) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_export_start",
            payload,
            timeout=max(60, min(int(timeout), 21600)),
        )

    async def export_training_dataset_manifest(self, payload: dict, *, timeout: int = 3600) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_export_manifest",
            payload,
            timeout=max(60, min(int(timeout), 21600)),
        )

    async def export_training_dataset_file_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_export_file_chunk",
            payload,
            timeout=max(10, min(int(timeout), 600)),
        )

    async def import_training_dataset_from_export(self, payload: dict, *, timeout: int = 21600) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_import_from_export",
            payload,
            timeout=max(60, min(int(timeout), 86400)),
        )

    async def get_training_dataset_relay_file_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_import_relay_file_status",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def import_training_dataset_relay_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_import_relay_chunk",
            payload,
            timeout=max(10, min(int(timeout), 600)),
        )

    async def commit_training_dataset_relay_import(self, payload: dict, *, timeout: int = 21600) -> dict:
        return await self._conn().bridge_op(
            "training.dataset_import_relay_commit",
            payload,
            timeout=max(60, min(int(timeout), 86400)),
        )

    async def bootstrap_training_env(self, payload: dict, *, timeout: int = 1800) -> dict:
        return await self._conn().bridge_op(
            "training.bootstrap_env",
            payload,
            timeout=max(60, min(int(timeout), 7200)),
        )

    async def get_training_env_status(self, payload: dict | None = None, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.bootstrap_status",
            payload or {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def prepare_training_model(self, payload: dict, *, timeout: int = 7200) -> dict:
        return await self._conn().bridge_op(
            "training.prepare_model",
            payload,
            timeout=max(60, min(int(timeout), 21600)),
        )

    async def get_training_model_status(self, payload: dict | None = None, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_status",
            payload or {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def discover_training_models(self, payload: dict | None = None, *, timeout: int = 60) -> dict:
        return await self._conn().bridge_op(
            "training.discover_models",
            payload or {},
            timeout=max(10, min(int(timeout), 300)),
        )

    async def explore_training_model_paths(self, payload: dict | None = None, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.explore_model_paths",
            payload or {},
            timeout=max(10, min(int(timeout), 600)),
        )

    async def start_training_model_export(self, payload: dict, *, timeout: int = 3600) -> dict:
        return await self._conn().bridge_op(
            "training.model_export_start",
            payload,
            timeout=max(60, min(int(timeout), 21600)),
        )

    async def get_training_model_export_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_export_status",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def cancel_training_model_export(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_export_cancel",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def export_training_model_manifest(self, payload: dict, *, timeout: int = 3600) -> dict:
        return await self._conn().bridge_op(
            "training.model_export_manifest",
            payload,
            timeout=max(60, min(int(timeout), 21600)),
        )

    async def export_training_model_file_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.model_export_file_chunk",
            payload,
            timeout=max(10, min(int(timeout), 600)),
        )

    async def import_training_model_from_export(self, payload: dict, *, timeout: int = 21600) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_from_export",
            payload,
            timeout=max(60, min(int(timeout), 86400)),
        )

    async def get_training_model_import_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_status",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def cancel_training_model_import(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_cancel",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def get_training_model_relay_file_status(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_relay_file_status",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def import_training_model_relay_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_relay_chunk",
            payload,
            timeout=max(10, min(int(timeout), 600)),
        )

    async def commit_training_model_relay_import(self, payload: dict, *, timeout: int = 21600) -> dict:
        return await self._conn().bridge_op(
            "training.model_import_relay_commit",
            payload,
            timeout=max(60, min(int(timeout), 86400)),
        )

    async def configure_training_runtime(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.configure_runtime",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def discover_training_python_envs(self, payload: dict | None = None, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "training.discover_python_envs",
            payload or {},
            timeout=max(15, min(int(timeout), 600)),
        )

    async def register_openwebui_model(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.register_openwebui_model",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def get_openwebui_status(self, payload: dict | None = None, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "training.openwebui_status",
            payload or {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def test_openwebui_chat(self, payload: dict, *, timeout: int = 600) -> dict:
        return await self._conn().bridge_op(
            "training.openwebui_chat_test",
            payload,
            timeout=max(30, min(int(timeout), 3600)),
        )

    async def run_intelligence_analyze(self, payload: dict, *, timeout: int = 120) -> dict:
        """Delegate a governed sf.analyze call to a department Agent."""
        return await self._conn().bridge_op(
            "intelligence.analyze",
            payload,
            timeout=max(10, min(int(timeout), 300)),
        )

    async def run_training_inference(self, payload: dict, *, timeout: int = 300) -> dict:
        """Run a deployed training adapter on the target Bridge node."""
        return await self._conn().bridge_op(
            "training.inference",
            payload,
            timeout=max(30, min(int(timeout), 1800)),
        )

    async def get_media_capabilities(self, payload: dict | None = None, *, timeout: int = 30) -> dict:
        """Read the Bridge-owned ComfyUI/H3 capability snapshot."""
        return await self._conn().bridge_op(
            "media.capabilities",
            payload or {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def submit_media_job(self, payload: dict, *, timeout: int = 120) -> dict:
        """Submit an allow-listed media template; arbitrary workflows are never accepted."""
        return await self._conn().bridge_op(
            "media.submit_job",
            payload,
            timeout=max(15, min(int(timeout), 600)),
        )

    async def get_media_job(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "media.get_job",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def cancel_media_job(self, payload: dict, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "media.cancel_job",
            payload,
            timeout=max(5, min(int(timeout), 120)),
        )

    async def collect_media_result(self, payload: dict, *, timeout: int = 60) -> dict:
        return await self._conn().bridge_op(
            "media.collect_result",
            payload,
            timeout=max(10, min(int(timeout), 300)),
        )

    async def read_media_result_chunk(self, payload: dict, *, timeout: int = 120) -> dict:
        return await self._conn().bridge_op(
            "media.read_result_chunk",
            payload,
            timeout=max(15, min(int(timeout), 300)),
        )

    async def bootstrap_h3_media(self, payload: dict, *, timeout: int = 60) -> dict:
        """Start the fixed, resumable h3_all_modes_v1 bootstrap profile."""
        return await self._conn().bridge_op(
            "media.bootstrap_h3",
            payload,
            timeout=max(15, min(int(timeout), 300)),
        )

    async def get_h3_media_bootstrap_status(self, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "media.bootstrap_status",
            {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def cancel_h3_media_bootstrap(self, *, timeout: int = 30) -> dict:
        return await self._conn().bridge_op(
            "media.bootstrap_cancel",
            {},
            timeout=max(5, min(int(timeout), 120)),
        )

    async def remove_skill(self, skill_id: str, target_dir: str | None = None) -> dict:
        return await self._conn().bridge_op(
            "remove_skill",
            {"skill_id": skill_id, "target_dir": target_dir},
        )

    async def list_local_skills(self, target_dir: str | None = None) -> dict:
        return await self._conn().bridge_op(
            "list_local_skills",
            {"target_dir": target_dir},
        )

    async def read_skill_from_device(self, skill_id: str, target_dir: str | None = None) -> dict:
        """从设备 ~/.aiclaw/skills/<skill_id>/ 反向读取文件，用于导入到 SkillForge。"""
        return await self._conn().bridge_op(
            "read_skill",
            {"skill_id": skill_id, "target_dir": target_dir},
            timeout=60,
        )

    async def run_skill_script(
        self,
        skill_id: str,
        payload: dict | None = None,
        *,
        run_id: str | None = None,
        run_token: str | None = None,
        run_mode: str | None = None,
        skill_git_commit_full: str | None = None,
        script_path: str = "scripts/main.py",
        timeout: int = 120,
        target_dir: str | None = None,
    ) -> dict:
        """在 AIClaw/OpenClaw 节点本机执行已同步 skill 的 Python 入口。

        这条路径不走 chat.send，不让 LLM 代写执行结果；bridge 直接拉起
        `<skills_dir>/<skill_id>/scripts/main.py`，把 stdout JSON 原样返回。
        """
        bridge_timeout = max(1, min(int(timeout), 14400)) + 30
        return await self._conn().bridge_op(
            "run_skill_script",
            {
                "skill_id": skill_id,
                "run_id": run_id,
                "run_token": run_token,
                "run_mode": run_mode,
                "skill_git_commit_full": skill_git_commit_full,
                "payload": payload or {},
                "script_path": script_path,
                "timeout": timeout,
                "target_dir": target_dir,
            },
            timeout=bridge_timeout,
        )

    async def run_agent_skill(
        self,
        skill_id: str,
        payload: dict | None = None,
        *,
        run_id: str | None = None,
        runtime: dict | None = None,
        run_token: str | None = None,
        run_mode: str | None = None,
        skill_git_commit_full: str | None = None,
        timeout: int = 300,
        target_dir: str | None = None,
    ) -> dict:
        """Ask bridge to run a synced Skill through the local OpenClaw Agent runtime."""
        bridge_timeout = max(1, min(int(timeout), 14400)) + 30
        runtime_payload = dict(runtime or {"backend": "openclaw_agent"})
        if run_mode and not runtime_payload.get("run_mode"):
            runtime_payload["run_mode"] = run_mode
        return await self._conn().bridge_op(
            "run_agent_skill",
            {
                "skill_id": skill_id,
                "run_id": run_id,
                "run_token": run_token,
                "run_mode": run_mode,
                "skill_git_commit_full": skill_git_commit_full,
                "payload": payload or {},
                "runtime": runtime_payload,
                "timeout": timeout,
                "target_dir": target_dir,
            },
            timeout=bridge_timeout,
        )

    @staticmethod
    def _normalize_chat_event(run_id: str, raw: dict) -> dict | None:
        event = raw.get("event")
        payload = raw.get("payload", {}) or {}

        if event == "agent":
            data = payload.get("data", {}) or {}
            text = data.get("text") or payload.get("text")
            if not text:
                return None
            return {
                "state": "streaming",
                "runId": payload.get("runId") or run_id,
                "delta": text,
                "event": event,
            }

        if event == "chat":
            message = payload.get("message", {}) or {}
            text = AIClawClient._extract_text(message) or payload.get("text", "")
            return {
                "state": payload.get("state", "streaming"),
                "runId": payload.get("runId") or run_id,
                "text": text,
                "message": message,
                "event": event,
            }

        return {
            "state": payload.get("state", "streaming"),
            "runId": payload.get("runId") or run_id,
            "payload": payload,
            "event": event,
        }

    @staticmethod
    def _extract_text(message: dict | str) -> str:
        if isinstance(message, str):
            return message
        if not isinstance(message, dict):
            return ""
        content = message.get("content", [])
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(part for part in parts if part)
        return json.dumps(message, ensure_ascii=False)

    @staticmethod
    def _to_aiclaw_attachments(attachments: list[dict]) -> list[dict]:
        """把 SkillForge 内部 {name, mimeType, data} 转成 AIClaw 协议
        {type, fileName, mimeType, content}。
        """
        normalized = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or item.get("data") or ""
            if not content:
                continue
            mime = item.get("mimeType") or item.get("mime_type") or "application/octet-stream"
            kind = item.get("type")
            if not kind:
                kind = "image" if str(mime).startswith("image/") else "file"
            normalized.append(
                {
                    "type": kind,
                    "fileName": item.get("fileName") or item.get("name") or "attachment",
                    "mimeType": mime,
                    "content": content,
                }
            )
        return normalized

    @staticmethod
    def _validate_attachments(message: str, attachments: list[dict]) -> None:
        if len(message.encode("utf-8")) > settings.BRIDGE_MAX_FRAME_BYTES:
            raise AppError("PARAM_INVALID", 400, {"detail": "message exceeds max frame bytes"})

        total_bytes = 0
        for item in attachments:
            raw = ""
            if isinstance(item, dict):
                raw = str(item.get("data") or item.get("content") or "")
            # base64 → 实际字节数 ≈ len * 3/4
            size = (len(raw) * 3) // 4
            total_bytes += size
            if size > settings.BRIDGE_MAX_ATTACHMENT_BYTES:
                raise AppError("PARAM_INVALID", 400, {"detail": "attachment exceeds max bytes"})
        if total_bytes > settings.BRIDGE_MAX_ATTACHMENTS_TOTAL_BYTES:
            raise AppError("PARAM_INVALID", 400, {"detail": "attachments exceed total max bytes"})
