from __future__ import annotations

import uuid
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from time import sleep
from urllib.parse import urlparse

import httpx


JsonDict = dict[str, Any]
TRANSIENT_STATUSES = {502, 503, 504}


def _request_id() -> str:
    return f"sf_{uuid.uuid4().hex}"


def _is_local_http_host(hostname: str | None) -> bool:
    value = (hostname or "").strip().lower().strip("[]")
    return value in {"localhost", "127.0.0.1", "::1"} or value.endswith(".localhost")


def _validate_base_url(base_url: str, *, allow_insecure_http: bool) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("base_url must be an absolute URL")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and (allow_insecure_http or _is_local_http_host(parsed.hostname))):
        raise ValueError("base_url must use HTTPS for external Project SDK access")
    return base_url.rstrip("/")


def _error_category(status_code: int | None, body: Any) -> str:
    detail = body.get("error", {}).get("detail") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
    explicit = detail.get("error_category") if isinstance(detail, dict) else None
    if explicit in {"auth_error", "validation_error", "quota_error", "transient_error", "server_error"}:
        return explicit
    if status_code in {401, 403}:
        return "auth_error"
    if status_code == 429:
        return "quota_error"
    if status_code in {400, 404, 413, 422}:
        return "validation_error"
    if status_code in TRANSIENT_STATUSES:
        return "transient_error"
    return "server_error"


class SkillForgeProjectSdkError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "SKILLFORGE_SDK_ERROR",
        category: str = "server_error",
        retry_after_seconds: float | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.category = category
        self.retry_after_seconds = retry_after_seconds
        self.response_body = response_body


@dataclass
class SkillForgeProjectSdk:
    base_url: str
    project_id: str
    token: str
    timeout: float = 180.0
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.token:
            raise ValueError("token is required")
        self.base_url = _validate_base_url(self.base_url, allow_insecure_http=self.allow_insecure_http)
        self.timeout = max(1.0, float(self.timeout))
        self.max_retries = max(0, int(self.max_retries))
        self.retry_base_seconds = max(0.05, float(self.retry_base_seconds))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def start_run(self, payload: JsonDict | None = None, *, request_id: str | None = None) -> JsonDict:
        payload = dict(payload or {})
        payload.setdefault("project_id", self.project_id)
        payload.setdefault("request_id", request_id or _request_id())
        return self._post("/api/projects/sdk/runs", payload)

    def input(self, project_run_id: str, payload: JsonDict, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload)
        body.setdefault("request_id", request_id or _request_id())
        return self._post(f"/api/projects/sdk/runs/{project_run_id}/input", body)

    def ingest(self, project_run_id: str, payload: JsonDict, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload)
        body.setdefault("request_id", request_id or _request_id())
        return self._post(f"/api/projects/sdk/runs/{project_run_id}/ingest", body)

    def capability(self, project_run_id: str, payload: JsonDict, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload)
        body.setdefault("request_id", request_id or _request_id())
        return self._post(f"/api/projects/sdk/runs/{project_run_id}/capability", body)

    def generate(
        self,
        project_run_id: str,
        prompt: str,
        input: JsonDict | None = None,
        *,
        request_id: str | None = None,
        json_mode: bool = False,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> JsonDict:
        return self.capability(
            project_run_id,
            {
                "capability": "ai.generate",
                "prompt": prompt,
                "input": input or {},
                "json_mode": json_mode,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            },
            request_id=request_id,
        )

    def chat(
        self,
        project_run_id: str,
        messages: list[dict[str, Any]],
        input: JsonDict | None = None,
        *,
        request_id: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> JsonDict:
        merged_input = dict(input or {})
        merged_input["messages"] = messages
        return self.capability(
            project_run_id,
            {
                "capability": "ai.chat",
                "input": merged_input,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
            },
            request_id=request_id,
        )

    def list_236_models(self) -> JsonDict:
        return self._get("/api/projects/openai/236/v1/models")

    def chat_236(
        self,
        project_run_id: str,
        model: str,
        messages: list[dict[str, Any]],
        input: JsonDict | None = None,
        *,
        request_id: str | None = None,
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_input_tokens: int | None = None,
        context_window: int | None = None,
        truncation: str | None = None,
        temperature: float | None = None,
        timeout_seconds: int | None = None,
    ) -> JsonDict:
        merged_input = dict(input or {})
        merged_input["messages"] = messages
        return self.capability(
            project_run_id,
            {
                "capability": "ai.chat",
                "target_gateway_id": "inference-primary",
                "model": model,
                "messages": messages,
                "input": merged_input,
                "max_tokens": max_tokens,
                "max_output_tokens": max_output_tokens,
                "max_input_tokens": max_input_tokens,
                "context_window": context_window,
                "truncation": truncation,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
            },
            request_id=request_id,
        )

    def openai_236_chat(self, payload: JsonDict, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload)
        body.setdefault("request_id", request_id or _request_id())
        return self._post("/api/projects/openai/236/v1/chat/completions", body)

    def analyze(
        self,
        project_run_id: str,
        input: JsonDict | None = None,
        *,
        prompt: str | None = None,
        request_id: str | None = None,
    ) -> JsonDict:
        return self.capability(
            project_run_id,
            {"capability": "ai.analyze", "prompt": prompt, "input": input or {}},
            request_id=request_id,
        )

    def trace(self, project_run_id: str) -> JsonDict:
        return self._get(f"/api/projects/sdk/runs/{project_run_id}/trace")

    def training_sync(self, project_run_id: str, payload: JsonDict | None = None, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload or {})
        body.setdefault("request_id", request_id or _request_id())
        return self._post(f"/api/projects/sdk/runs/{project_run_id}/training-sync", body)

    def upload_asset(
        self,
        project_run_id: str,
        file: bytes | bytearray | str | Path,
        *,
        file_name: str | None = None,
        mime_type: str | None = None,
        metadata: JsonDict | None = None,
    ) -> JsonDict:
        if isinstance(file, (str, Path)):
            path = Path(file)
            content = path.read_bytes()
            upload_name = file_name or path.name
        else:
            content = bytes(file)
            upload_name = file_name or "asset"
        files = {"file": (upload_name, content, mime_type or "application/octet-stream")}
        data = {}
        if metadata:
            data["metadata_json"] = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        return self._multipart(f"/api/projects/sdk/runs/{project_run_id}/assets", files=files, data=data)

    def record_training_sample(
        self,
        project_run_id: str,
        content: JsonDict,
        *,
        dataset_profile: str = "text_sft_v1",
        asset_id: str | None = None,
        media_refs: list[dict[str, Any]] | None = None,
        labels: list[Any] | None = None,
        quality_score: float | None = None,
        modality: str | None = None,
        metadata: JsonDict | None = None,
        request_id: str | None = None,
    ) -> JsonDict:
        return self._post(
            f"/api/projects/sdk/runs/{project_run_id}/training-samples",
            {
                "request_id": request_id or _request_id(),
                "asset_id": asset_id,
                "dataset_profile": dataset_profile,
                "modality": modality,
                "content": content,
                "media_refs": media_refs or [],
                "labels": labels or [],
                "quality_score": quality_score,
                "metadata": metadata or {},
            },
        )

    def list_trainable_assets(self, *, limit: int = 200) -> JsonDict:
        return self._get(f"/api/projects/sdk/training/assets?limit={int(limit)}")

    def create_dataset_version(self, payload: JsonDict, *, request_id: str | None = None) -> JsonDict:
        body = dict(payload)
        if request_id:
            body.setdefault("request_id", request_id)
        return self._post("/api/projects/sdk/training/datasets", body)

    def sync_training_dataset(self, dataset_version_id: str, payload: JsonDict | None = None) -> JsonDict:
        return self._post(f"/api/projects/sdk/training/datasets/{dataset_version_id}/sync", dict(payload or {}))

    def _post(self, path: str, payload: JsonDict) -> JsonDict:
        return self._request("POST", path, payload=payload)

    def _get(self, path: str) -> JsonDict:
        return self._request("GET", path)

    def _multipart(self, path: str, *, files: dict[str, Any], data: dict[str, Any]) -> JsonDict:
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        "POST",
                        f"{self.base_url}{path}",
                        headers={"Authorization": f"Bearer {self.token}"},
                        files=files,
                        data=data,
                    )
                return self._parse(response)
            except SkillForgeProjectSdkError as exc:
                if exc.status_code in TRANSIENT_STATUSES and attempt < self.max_retries:
                    sleep(self._retry_delay_seconds(attempt))
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.max_retries:
                    sleep(self._retry_delay_seconds(attempt))
                    continue
                raise SkillForgeProjectSdkError(
                    str(exc) or "SkillForge network request failed",
                    code="NETWORK_ERROR",
                    category="transient_error",
                ) from exc
        raise SkillForgeProjectSdkError("SkillForge request failed", category="server_error")

    def _request(self, method: str, path: str, *, payload: JsonDict | None = None) -> JsonDict:
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=self._headers if payload is not None else {"Authorization": f"Bearer {self.token}"},
                        json=payload,
                    )
                return self._parse(response)
            except SkillForgeProjectSdkError as exc:
                if exc.status_code in TRANSIENT_STATUSES and attempt < self.max_retries:
                    sleep(self._retry_delay_seconds(attempt))
                    continue
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.max_retries:
                    sleep(self._retry_delay_seconds(attempt))
                    continue
                is_timeout = isinstance(exc, httpx.TimeoutException)
                raise SkillForgeProjectSdkError(
                    "SkillForge request timed out" if is_timeout else str(exc) or "SkillForge network request failed",
                    code="REQUEST_TIMEOUT" if is_timeout else "NETWORK_ERROR",
                    category="transient_error",
                ) from exc

        raise SkillForgeProjectSdkError("SkillForge request failed", category="server_error")

    @staticmethod
    def _parse(response: httpx.Response) -> JsonDict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error = body.get("error", {}) if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
            detail = error.get("detail")
            message = detail if isinstance(detail, str) else error.get("message") or body.get("detail") or f"SkillForge request failed: {response.status_code}"
            retry_after = None
            if isinstance(detail, dict):
                try:
                    retry_after = float(detail.get("retry_after_seconds") or 0) or None
                except Exception:
                    retry_after = None
            if retry_after is None:
                try:
                    retry_after = float(response.headers.get("retry-after") or 0) or None
                except Exception:
                    retry_after = None
            raise SkillForgeProjectSdkError(
                str(message),
                status_code=response.status_code,
                code=str(error.get("code") or f"HTTP_{response.status_code}"),
                category=_error_category(response.status_code, body),
                retry_after_seconds=retry_after,
                response_body=body,
            )
        return body

    def _retry_delay_seconds(self, attempt: int) -> float:
        return self.retry_base_seconds * (2 ** attempt)
