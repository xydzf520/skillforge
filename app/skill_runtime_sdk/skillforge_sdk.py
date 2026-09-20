# -*- coding: utf-8 -*-
"""
SkillForge SDK — Skill 脚本标准库。

所有 Skill 脚本导入此文件即可获得：
  - 浏览器数据采集（自动适配本地/远程）
  - 执行结果回推（创建平台报告/待办）
  - 平台审批通过后的 dispatch 任务由 SkillForge 再推送钉钉
  - 零配置：地址和 token 自动从 Bridge 配置读取

用法：
    from skillforge_sdk import SkillForge

    sf = SkillForge("my-skill-id")

    # 采集
    data = sf.fetch_api("https://sycm.taobao.com/cc/item/live/view/top.json?...")

    # 分析 + 提交（自动创建平台待办；不会在提交时直接推钉钉）
    sf.submit(
        output={"items": data, "analysis": "..."},
        todos=[
            sf.todo_dispatch(
                title="天猫链接整改",
                summary="3个商品下滑",
                tasks=[
                    sf.task("示例品牌001 下滑35%，建议检查关键词", deadline="2026-04-12"),
                    sf.task("黑金001 下滑27%，建议检查ROI", deadline="2026-04-12"),
                ],
            ),
        ],
    )
"""

import hashlib
import json
import os
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

try:
    import requests
except ModuleNotFoundError:
    requests = None

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

BJT = ZoneInfo("Asia/Shanghai")
MCP_RUNTIME_DRIFT = "MCP_RUNTIME_DRIFT"
PRODUCTION_ENVS = {"prod", "production", "staging", "stage"}
LOCAL_ENVS = {"local", "dev", "development", "test", "testing"}


def _safe_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


class _UrllibResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if 200 <= self.status_code < 300:
            return None
        raise RuntimeError(f"HTTP {self.status_code}: {self.text[:500]}")

    def json(self):
        return json.loads(self.text) if self.text else {}


def _http_post(url: str, *, json: dict | None = None, headers: dict | None = None, timeout: int | float | None = None):
    if requests is not None:
        if headers is None:
            return requests.post(url, json=json, timeout=timeout)
        return requests.post(url, json=json, headers=headers, timeout=timeout)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    data = None if json is None else __import__("json").dumps(json, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout or 60) as resp:
            return _UrllibResponse(resp.status, resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        return _UrllibResponse(exc.code, exc.read().decode("utf-8", "ignore"))


def _raise_http_for_status(resp):
    try:
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - SDK should surface platform error body
        body = getattr(resp, "text", "") or ""
        detail = ""
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
                code = error.get("code") if isinstance(error, dict) else None
                message = error.get("message") if isinstance(error, dict) else None
                error_detail = error.get("detail") if isinstance(error, dict) else None
                parts = [str(part) for part in (code, message) if part]
                if error_detail:
                    parts.append(json.dumps(error_detail, ensure_ascii=False)[:500])
                detail = " | ".join(parts)
        except Exception:
            detail = body[:500]
        if detail:
            raise RuntimeError(f"{exc}: {detail}") from exc
        raise


def _find_bridge_config(filename):
    """从 Bridge 配置目录读取 JSON 配置文件。"""
    if filename == "browser.json" and os.environ.get("BROWSER_API_URL"):
        return {"url": os.environ["BROWSER_API_URL"], "token": os.environ.get("BROWSER_API_TOKEN", "")}
    if filename == "skillforge.json" and (
        os.environ.get("SKILLFORGE_PLATFORM_URL") or os.environ.get("SKILLFORGE_BASE_URL")
    ):
        return {
            "base_url": os.environ.get("SKILLFORGE_PLATFORM_URL") or os.environ["SKILLFORGE_BASE_URL"],
            "token": os.environ.get("BROWSER_API_TOKEN", ""),
        }

    bridge_base = Path.home() / ".skillforge_bridge"
    if bridge_base.exists():
        for cfg_path in sorted(bridge_base.glob(f"*/{filename}"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                cfg = json.loads(cfg_path.read_text())
                if cfg.get("url") or cfg.get("base_url"):
                    return cfg
            except (json.JSONDecodeError, OSError):
                continue

    if filename == "browser.json":
        return {"url": "http://127.0.0.1:8000/api/browser/collect-local", "token": ""}
    return {"base_url": "http://127.0.0.1:8000", "token": ""}


def _find_skillforge_config() -> dict:
    if yaml is None:
        return {}
    candidates: list[Path] = []
    env_root = os.environ.get("SKILLFORGE_SKILL_ROOT") or os.environ.get("SKILLFORGE_PROJECT_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])
    seen: set[str] = set()
    for root in candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        path = root / "skillforge.yaml"
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


class SkillForge:
    """Skill 脚本的统一入口。"""

    def __init__(self, skill_id: str):
        self.skill_id = skill_id
        self._browser_cfg = _find_bridge_config("browser.json")
        self._sf_cfg = _find_bridge_config("skillforge.json")
        self._skill_cfg = _find_skillforge_config()
        self._data_proofs = []
        self._collection_proofs = []
        self._runtime_model_context = self._load_runtime_model_context()
        self._decision_model_context = {}

    def browser_collect(self, task_type: str, params: dict, timeout: int = 90) -> dict:
        """调用浏览器 API（explore / extract / capture_apis）。"""
        url = self._browser_cfg["url"]
        token = self._browser_cfg.get("token", "")
        if token:
            url += ("&" if "?" in url else "?") + f"token={token}"
        resp = _http_post(url, json={
            "platform": "generic",
            "task_type": task_type,
            "params": params,
        }, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"浏览器采集失败: {data.get('error', data)}")
        return data["result"]

    @staticmethod
    def _sample_keys(data) -> list:
        if isinstance(data, dict):
            return [str(k) for k in list(data.keys())[:20]]
        if isinstance(data, list):
            keys = []
            for row in data[:5]:
                if isinstance(row, dict):
                    for key in row.keys():
                        if key not in keys:
                            keys.append(key)
                if len(keys) >= 20:
                    break
            return [str(k) for k in keys[:20]]
        return []

    @staticmethod
    def _row_count(data):
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("rows", "items", "data", "list", "records", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return len(value)
            return 1 if data else 0
        return None

    def _record_data_proof(
        self,
        api_url: str,
        method: str,
        status,
        data,
        is_sample: bool,
        extra: dict | None = None,
    ) -> None:
        proof = {
            "url": api_url,
            "method": method.upper(),
            "status": status,
            "fetched_at": datetime.now(BJT).isoformat(),
            "sample_keys": self._sample_keys(data),
            "row_count": self._row_count(data),
            "is_sample": bool(is_sample),
        }
        if extra:
            proof.update(extra)
        collection_proofs = self._collection_proofs_from_data(data)
        proof_ids = [
            str(item.get("proof_id"))
            for item in collection_proofs
            if isinstance(item, dict) and item.get("proof_id")
        ]
        if proof_ids:
            proof["proof_ids"] = proof_ids
        self._data_proofs.append(proof)
        self._merge_collection_proofs(collection_proofs)

    @staticmethod
    def _collection_proofs_from_data(data) -> list[dict]:
        if not isinstance(data, dict):
            return []
        meta = data.get("_skillforge_meta") if isinstance(data.get("_skillforge_meta"), dict) else {}
        raw = meta.get("collection_proofs")
        if not isinstance(raw, list):
            return []
        proofs = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("proof_id"):
                continue
            proofs.append(dict(item))
        return proofs

    def _merge_collection_proofs(self, proofs: list[dict]) -> None:
        seen = {
            str(item.get("proof_id"))
            for item in self._collection_proofs
            if isinstance(item, dict) and item.get("proof_id")
        }
        for proof in proofs or []:
            proof_id = str(proof.get("proof_id") or "")
            if not proof_id or proof_id in seen:
                continue
            self._collection_proofs.append(dict(proof))
            seen.add(proof_id)

    @staticmethod
    def _load_runtime_model_context() -> dict:
        raw = os.environ.get("SKILLFORGE_MODEL_CONTEXT_JSON") or ""
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def model_context(self) -> dict:
        """Return the governed model context injected for this Skill run."""
        return dict(self._decision_model_context or self._runtime_model_context or {})

    @staticmethod
    def _deployment_context_from_analyze_result(result: dict) -> dict:
        deployment = _safe_dict(result.get("active_model_deployment"))
        route = _safe_dict(result.get("analysis_delegate_route"))
        if not deployment:
            deployment = _safe_dict(route.get("active_model_deployment"))
        deployment_id = str(
            deployment.get("id")
            or deployment.get("model_deployment_id")
            or deployment.get("deployment_id")
            or ""
        ).strip()
        if not deployment_id:
            return {}
        inference = _safe_dict(result.get("deployed_model_inference"))
        if not inference:
            inference = _safe_dict(route.get("deployed_model_inference"))
        context = {
            "model_deployment_id": deployment_id[:80],
            "model_family": str(deployment.get("model_family") or "")[:100] or None,
            "deployment_status": str(deployment.get("status") or deployment.get("deployment_status") or "")[:30] or None,
            "rollout_percent": deployment.get("rollout_percent"),
            "rollout_selected": deployment.get("rollout_selected"),
            "rollout_bucket": deployment.get("rollout_bucket"),
            "rollout_reason": str(deployment.get("rollout_reason") or "")[:50] or None,
            "artifact_id": str(deployment.get("artifact_id") or "")[:120] or None,
            "artifact_sha256": str(deployment.get("artifact_sha256") or "")[:64] or None,
            "target_skill_ids": [
                str(item)[:100]
                for item in (deployment.get("target_skill_ids") if isinstance(deployment.get("target_skill_ids"), list) else [])
                if str(item or "").strip()
            ][:20],
            "model_runtime_status": (
                deployment.get("runtime_status")
                if isinstance(deployment.get("runtime_status"), dict)
                else None
            ),
        }
        if inference:
            context.update({
                "inference_status": str(inference.get("status") or "")[:30] or None,
                "inference_backend": str(inference.get("backend") or "")[:50] or None,
                "inference_gateway_id": str(inference.get("gateway_id") or "")[:100] or None,
                "inference_profile": str(inference.get("profile") or "")[:50] or None,
                "inference_text_sha256": str(inference.get("text_sha256") or "")[:64] or None,
                "inference_metrics": (
                    inference.get("metrics")
                    if isinstance(inference.get("metrics"), dict)
                    else None
                ),
            })
        return {key: value for key, value in context.items() if value not in (None, "", [], {})}

    @staticmethod
    def _merge_skillforge_context(context_pack: dict, runtime_model_context: dict) -> dict:
        if not runtime_model_context:
            return context_pack
        merged = dict(context_pack or {})
        skillforge = dict(merged.get("skillforge") or {}) if isinstance(merged.get("skillforge"), dict) else {}
        if "runtime_model_context" not in skillforge:
            skillforge["runtime_model_context"] = runtime_model_context
        if "active_model_deployment" not in skillforge:
            active_model = runtime_model_context.get("active_model_deployment")
            if isinstance(active_model, dict):
                skillforge["active_model_deployment"] = active_model
        merged["skillforge"] = skillforge
        return merged

    @staticmethod
    def _env_name() -> str:
        return str(
            os.environ.get("SKILLFORGE_ENV")
            or os.environ.get("APP_ENV")
            or os.environ.get("ENV")
            or os.environ.get("RUNTIME_ENV")
            or "local"
        ).lower()

    @classmethod
    def _is_production_env(cls) -> bool:
        return cls._env_name() in PRODUCTION_ENVS

    @staticmethod
    def _truthy_env(*names: str) -> bool:
        for name in names:
            value = os.environ.get(name)
            if value is None:
                continue
            return str(value).strip().lower() in {"1", "true", "yes", "on", "allow", "allowed"}
        return False

    @classmethod
    def _allow_skill_bundled_mcp(cls) -> bool:
        if cls._is_production_env():
            return cls._truthy_env("SKILLFORGE_ALLOW_SKILL_BUNDLED_MCP", "SKILLFORGE_MCP_ALLOW_SKILL_BUNDLED")
        if cls._env_name() in LOCAL_ENVS:
            return cls._truthy_env("SKILLFORGE_ALLOW_SKILL_BUNDLED_MCP", "SKILLFORGE_MCP_ALLOW_SKILL_BUNDLED")
        return False

    @classmethod
    def _force_local_mcp(cls) -> bool:
        if cls._is_production_env() or cls._env_name() not in LOCAL_ENVS:
            return False
        return cls._truthy_env("SKILLFORGE_MCP_FORCE_LOCAL", "SKILLFORGE_FORCE_LOCAL_MCP")

    def _fetch_json_local_url(self) -> str:
        configured = self._browser_cfg.get("url") or "http://127.0.0.1:8000/api/browser/collect-local"
        if "/api/browser/collect-local" in configured:
            return configured.replace("/api/browser/collect-local", "/api/browser/fetch-json-local")
        if "/api/browser/fetch-json-local" in configured:
            return configured
        parsed = urlparse(configured)
        if parsed.scheme and parsed.netloc:
            return urlunparse((parsed.scheme, parsed.netloc, "/api/browser/fetch-json-local", "", "", ""))
        return "http://127.0.0.1:8000/api/browser/fetch-json-local"

    def _project_root_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        env_root = os.environ.get("SKILLFORGE_PROJECT_ROOT")
        if env_root:
            candidates.append(Path(env_root))
        cwd = Path.cwd()
        candidates.extend([cwd, *cwd.parents])
        here = Path(__file__).resolve()
        candidates.extend([here.parent, *here.parents])
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    @staticmethod
    def _file_hash(path: Path) -> str:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return f"sha256:{digest}"
        except OSError:
            return ""

    def _platform_mcp_roots(self) -> list[Path]:
        roots: list[Path] = []
        for name in (
            "SKILLFORGE_MCP_CANONICAL_DIR",
            "SKILLFORGE_MCP_DIR",
            "SKILLFORGE_TRUSTED_MCP_DIR",
        ):
            value = os.environ.get(name)
            if value:
                roots.append(Path(value))
        env_root = os.environ.get("SKILLFORGE_PROJECT_ROOT")
        if env_root:
            roots.append(Path(env_root) / "scripts")
        roots.append(Path(__file__).resolve().parents[2] / "scripts")
        return self._unique_paths(roots)

    @staticmethod
    def _unique_paths(paths: list[Path]) -> list[Path]:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _bundled_mcp_roots(self) -> list[Path]:
        roots: list[Path] = []
        cwd = Path.cwd()
        roots.extend([cwd / "scripts", *[parent / "scripts" for parent in cwd.parents]])
        env_root = os.environ.get("SKILLFORGE_PROJECT_ROOT")
        if env_root:
            roots.append(Path(env_root) / "scripts")
        return self._unique_paths(roots)

    def _local_mcp_script(self, tool_name: str) -> tuple[Path, dict]:
        if tool_name.startswith("tmall_"):
            script_name = "tmall_mcp_server.py"
        elif tool_name.startswith("yuyidata_"):
            script_name = "yuyidata_mcp_server.py"
        else:
            raise RuntimeError(f"未配置本地 MCP adapter: {tool_name}")

        for root in self._platform_mcp_roots():
            script = root / script_name
            if script.exists():
                return script, {
                    "mcp_runtime_source": "platform_shared",
                    "mcp_runtime_path": str(script),
                    "mcp_runtime_hash": self._file_hash(script),
                }

        for root in self._bundled_mcp_roots():
            script = root / script_name
            if script.exists():
                meta = {
                    "mcp_runtime_source": "skill_bundled",
                    "mcp_runtime_path": str(script),
                    "mcp_runtime_hash": self._file_hash(script),
                    "mcp_runtime_warning": MCP_RUNTIME_DRIFT,
                }
                if not self._allow_skill_bundled_mcp():
                    meta["mcp_runtime_blocked"] = True
                    self._last_mcp_runtime_meta = meta
                    raise RuntimeError(f"{MCP_RUNTIME_DRIFT}: skill bundled MCP runtime is not allowed")
                return script, meta
        raise RuntimeError(f"未找到 MCP server 脚本: {script_name}")

    def _call_local_mcp_tool(
        self,
        tool_name: str,
        arguments: dict | None,
        timeout: int | None = None,
        mcp_env: dict | None = None,
    ) -> tuple[dict, dict]:
        script, runtime_meta = self._local_mcp_script(tool_name)
        self._last_mcp_runtime_meta = runtime_meta
        timeout_seconds = max(
            3,
            min(
                int(timeout or os.environ.get("SKILLFORGE_MCP_TOOL_TIMEOUT_SECONDS") or 25),
                1800,
            ),
        )
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        env = os.environ.copy()
        env.setdefault("SKILLFORGE_PLATFORM_URL", self._sf_cfg.get("base_url", "http://127.0.0.1:8000"))
        env.setdefault("SKILLFORGE_SKILL_ID", self.skill_id)
        for preferred, fallback in (
            ("SKILLFORGE_RUN_TOKEN", "RUN_TOKEN"),
            ("SKILLFORGE_RUN_ID", "RUN_ID"),
            ("SKILLFORGE_INSTANCE_ID", "INSTANCE_ID"),
        ):
            value = env.get(preferred) or env.get(fallback)
            if value:
                env[preferred] = value
                env[fallback] = value
        for key, value in (mcp_env or {}).items():
            if value is not None:
                env[str(key)] = str(value)
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(req, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(script.parent.parent),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"MCP tool {tool_name} timeout after {timeout_seconds}s") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"MCP tool {tool_name} failed: {detail[:500]}")
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError(f"MCP tool {tool_name} 无响应")
        try:
            envelope = json.loads(lines[-1])
        except json.JSONDecodeError:
            raise RuntimeError(f"MCP tool {tool_name} 返回非 JSON-RPC: {lines[-1][:200]}") from None
        result = envelope.get("result") if isinstance(envelope, dict) else {}
        if isinstance(result, dict) and result.get("isError"):
            content = result.get("content") or []
            text = content[0].get("text") if content and isinstance(content[0], dict) else str(result)
            raise RuntimeError(f"MCP tool {tool_name} error: {text[:500]}")
        content = result.get("content") if isinstance(result, dict) else None
        text = content[0].get("text") if content and isinstance(content[0], dict) else ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"MCP tool 返回非 JSON: {text[:200]}") from None
        return data if isinstance(data, dict) else {"items": data}, runtime_meta

    def _platform_mcp_gateway_url(self) -> str:
        explicit = os.environ.get("SKILLFORGE_MCP_GATEWAY_URL")
        if explicit:
            return explicit
        base = (
            os.environ.get("SKILLFORGE_PLATFORM_URL")
            or os.environ.get("SKILLFORGE_BASE_URL")
            or self._sf_cfg.get("base_url")
            or "http://127.0.0.1:8000"
        ).rstrip("/")
        return f"{base}/api/codex/mcp/call"

    @staticmethod
    def _run_token() -> str:
        return (
            os.environ.get("SKILLFORGE_RUN_TOKEN")
            or os.environ.get("OPENCLAW_RUN_TOKEN")
            or os.environ.get("RUN_TOKEN")
            or ""
        )

    def _call_platform_mcp_tool(
        self,
        tool_name: str,
        arguments: dict | None,
        timeout: int | None = None,
        dry_run: bool | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict, dict]:
        token = self._run_token()
        if not token:
            raise RuntimeError("MCP_ENV_MISSING: missing SKILLFORGE_RUN_TOKEN")
        timeout_seconds = max(
            3,
            min(
                int(timeout or os.environ.get("SKILLFORGE_MCP_TOOL_TIMEOUT_SECONDS") or 120),
                1800,
            ),
        )
        payload = {
            "server": "skillforge",
            "tool": tool_name,
            "arguments": arguments or {},
            "skill_id": self.skill_id,
            "run_id": os.environ.get("SKILLFORGE_RUN_ID") or os.environ.get("OPENCLAW_RUN_ID") or os.environ.get("RUN_ID") or "",
            "run_mode": os.environ.get("SKILLFORGE_RUN_MODE") or self._env_name(),
            "dry_run": True if dry_run is None else bool(dry_run),
            "idempotency_key": idempotency_key,
        }
        resp = _http_post(
            self._platform_mcp_gateway_url(),
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )
        _raise_http_for_status(resp)
        result = resp.json()
        if not result.get("ok", False):
            raise RuntimeError(f"MCP tool {tool_name} failed: {result}")
        proof = result.get("proof") if isinstance(result.get("proof"), dict) else {}
        meta = {
            "mcp_runtime_source": "skillforge_gateway",
            "credential_location": "platform_only",
            "proof_id": proof.get("id"),
            "run_mode": proof.get("run_mode") or payload["run_mode"],
            "dry_run": proof.get("dry_run"),
        }
        data = result.get("data")
        return data if isinstance(data, dict) else {"items": data if isinstance(data, list) else [data]}, meta

    def fetch_api(
        self,
        api_url: str,
        method: str = "GET",
        is_sample: bool = False,
        page_url: str = None,
        headers: dict = None,
        body=None,
        timeout: int = 120,
        platform: str = None,
        shop_id: str = None,
        source_id: str = None,
        data_scope: str = None,
        endpoint_family: str = None,
        warning_group: str = None,
        credential_scope: str = None,
        credential_plan_id: str = None,
        dry_run: bool | None = None,
    ) -> dict:
        """在浏览器页面上下文中 fetch API，返回 JSON。"""
        if str(api_url).startswith("mcp://"):
            tool_name = str(api_url).removeprefix("mcp://").strip("/")
            if not tool_name:
                raise RuntimeError("mcp:// 缺少 tool name")
            arguments = dict(body) if isinstance(body, dict) else {}
            for key, value in {
                "platform": platform,
                "shop_id": shop_id,
                "source_id": source_id,
                "data_scope": data_scope,
                "endpoint_family": endpoint_family,
                "warning_group": warning_group,
                "credential_scope": credential_scope,
                "credential_plan_id": credential_plan_id,
            }.items():
                if value is not None and key not in arguments:
                    arguments[key] = value
            mcp_env = {}
            if shop_id:
                mcp_env["SKILLFORGE_SHOP_ID"] = shop_id
                mcp_env["SHOP_ID"] = shop_id
            try:
                force_local_mcp = self._force_local_mcp()
                if self._run_token() and not force_local_mcp:
                    data, runtime_meta = self._call_platform_mcp_tool(tool_name, arguments, timeout=timeout, dry_run=dry_run)
                else:
                    if self._is_production_env() and not force_local_mcp:
                        raise RuntimeError("MCP_ENV_MISSING: production Skill must call SkillForge MCP Gateway")
                    data, runtime_meta = self._call_local_mcp_tool(tool_name, arguments, timeout=timeout, mcp_env=mcp_env)
            except Exception as exc:
                runtime_meta = dict(getattr(self, "_last_mcp_runtime_meta", {}) or {})
                if MCP_RUNTIME_DRIFT in str(exc) or runtime_meta.get("mcp_runtime_warning") == MCP_RUNTIME_DRIFT:
                    self._record_data_proof(
                        api_url,
                        method,
                        "mcp_rejected",
                        {"error": MCP_RUNTIME_DRIFT},
                        is_sample,
                        runtime_meta,
                    )
                raise
            if runtime_meta.get("mcp_runtime_warning") == MCP_RUNTIME_DRIFT:
                data = dict(data)
                meta = dict(data.get("_skillforge_meta") or {})
                meta.update(runtime_meta)
                data["_skillforge_meta"] = meta
            self._record_data_proof(api_url, method, "mcp", data, is_sample, runtime_meta)
            return data

        if platform or shop_id or data_scope or endpoint_family or warning_group:
            base = self._sf_cfg.get("base_url", "http://127.0.0.1:8000").rstrip("/")
            token = os.environ.get("SKILLFORGE_RUN_TOKEN") or os.environ.get("RUN_TOKEN") or ""
            resp = _http_post(
                f"{base}/api/collection/fetch",
                json={
                    "skill_id": self.skill_id,
                    "run_id": os.environ.get("SKILLFORGE_RUN_ID") or os.environ.get("RUN_ID") or "",
                    "instance_id": os.environ.get("SKILLFORGE_INSTANCE_ID") or os.environ.get("INSTANCE_ID") or "",
                    "source_id": source_id,
                    "platform": platform or "",
                    "shop_id": shop_id or "",
                    "data_scope": data_scope or "",
                    "endpoint_family": endpoint_family or "read_metrics",
                    "warning_group": warning_group or f"{platform or 'platform'}.read",
                    "credential_scope": credential_scope,
                    "credential_plan_id": credential_plan_id,
                    "params": {
                        "url": api_url,
                        "method": method.upper(),
                        "headers": headers or {},
                        "body": body,
                        "page_url": page_url,
                    },
                },
                headers={"X-Run-Token": token} if token else {},
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success"):
                raise RuntimeError(f"采集 fetch 失败: {payload.get('error', payload)}")
            fetch_payload = payload.get("data")
            status = fetch_payload.get("status") if isinstance(fetch_payload, dict) else None
            data = fetch_payload.get("data") if isinstance(fetch_payload, dict) and "data" in fetch_payload else fetch_payload
            if not isinstance(data, (dict, list)):
                data = {}
            self._record_data_proof(api_url, method, status, data, is_sample)
            return data if isinstance(data, dict) else {"items": data}

        if page_url or headers or body is not None:
            url = self._fetch_json_local_url()
            token = self._browser_cfg.get("token", "")
            if token:
                url += ("&" if "?" in url else "?") + f"token={token}"
            resp = _http_post(url, json={
                "url": api_url,
                "method": method.upper(),
                "headers": headers or {},
                "body": body,
                "page_url": page_url,
            }, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success"):
                raise RuntimeError(f"浏览器 fetch_json 失败: {payload.get('error', payload)}")
            fetch_payload = payload.get("data")
            status = fetch_payload.get("status") if isinstance(fetch_payload, dict) else None
            data = fetch_payload.get("data") if isinstance(fetch_payload, dict) and "data" in fetch_payload else fetch_payload
            if not isinstance(data, (dict, list)):
                data = {}
            self._record_data_proof(api_url, method, status, data, is_sample)
            return data if isinstance(data, dict) else {"items": data}

        js = (
            "fetch("
            f"{json.dumps(api_url)},"
            f"{{credentials:'include',cache:'no-store',method:{json.dumps(method.upper())}}}"
            ").then(async r=>{"
            "let d; try { d = await r.json(); } catch(e) { d = await r.text(); }"
            "return JSON.stringify({status:r.status,data:d});"
            "})"
        )
        result = self.browser_collect("extract", {"js": js})
        raw = result.get("data")
        if isinstance(raw, str):
            raw = json.loads(raw)
        status = raw.get("status") if isinstance(raw, dict) and "data" in raw else None
        data = raw.get("data") if isinstance(raw, dict) and "data" in raw else raw
        if not isinstance(data, (dict, list)):
            data = {}
        self._record_data_proof(api_url, method, status, data, is_sample)
        return data if isinstance(data, dict) else {"items": data}

    def fetch_many(self, requests_: list[dict], **common_kwargs) -> list[dict]:
        """Fetch several APIs with the same governance kwargs."""
        results = []
        for item in requests_ or []:
            if not isinstance(item, dict):
                continue
            kwargs = {**common_kwargs, **{k: v for k, v in item.items() if k != "api_url"}}
            results.append(self.fetch_api(item.get("api_url") or item.get("url"), **kwargs))
        return results

    def explore(self, url: str, wait_ms: int = 8000) -> dict:
        return self.browser_collect("explore", {"url": url, "wait_ms": wait_ms})

    def extract(self, js: str = None, selector: str = None, url: str = None, wait_ms: int = 5000):
        params = {}
        if url:
            params["url"] = url
            params["wait_ms"] = wait_ms
        if js:
            params["js"] = js
        elif selector:
            params["selector"] = selector
        return self.browser_collect("extract", params)

    def capture_apis(self, url: str, wait_ms: int = 15000) -> dict:
        return self.browser_collect("capture_apis", {"url": url, "wait_ms": wait_ms})

    def analyze(
        self,
        *,
        context_pack: dict,
        prompt_version: str = "analysis_v1",
        cache_key: str = None,
        timeout: int = 300,
        **deprecated_kwargs,
    ) -> dict:
        known_deprecated = {"model", "json", "json_mode", "max_tokens", "system", "user"}
        for key in deprecated_kwargs:
            if key not in known_deprecated:
                raise TypeError(f"sf.analyze() got an unexpected keyword argument '{key}'")
            warnings.warn(
                f"sf.analyze({key}=...) is deprecated and ignored by SDK; "
                "model and prompt policy are decided by SkillForge.",
                DeprecationWarning,
                stacklevel=2,
            )

        base = self._sf_cfg.get("base_url", "http://127.0.0.1:8000").rstrip("/")
        skill_git_commit_full = (
            os.environ.get("SKILLFORGE_SKILL_GIT_COMMIT_FULL")
            or os.environ.get("SKILL_GIT_COMMIT_FULL")
            or os.environ.get("OPENCLAW_SKILL_GIT_COMMIT_FULL")
            or self._sf_cfg.get("base_commit")
            or self._skill_cfg.get("base_commit")
            or self._sf_cfg.get("git_commit")
            or self._skill_cfg.get("git_commit")
            or ""
        )
        instance_id = (
            os.environ.get("SKILLFORGE_INSTANCE_ID")
            or os.environ.get("OPENCLAW_INSTANCE_ID")
            or os.environ.get("INSTANCE_ID")
            or ""
        )
        if instance_id in {"platform", "local", "localhost", "127.0.0.1"}:
            instance_id = ""
        effective_context_pack = self._merge_skillforge_context(
            context_pack if isinstance(context_pack, dict) else {},
            self._runtime_model_context,
        )
        payload = {
            "skill_id": self.skill_id,
            "skill_git_commit_full": skill_git_commit_full,
            "run_id": os.environ.get("SKILLFORGE_RUN_ID") or os.environ.get("OPENCLAW_RUN_ID") or "",
            "instance_id": instance_id or None,
            "prompt_version": prompt_version,
            "context_pack": effective_context_pack,
        }
        if cache_key:
            payload["cache_key"] = cache_key
        headers = {}
        run_token = (
            os.environ.get("SKILLFORGE_INTELLIGENCE_RUN_TOKEN")
            or os.environ.get("SKILLFORGE_RUN_TOKEN")
            or os.environ.get("OPENCLAW_RUN_TOKEN")
        )
        if run_token:
            headers["X-Run-Token"] = run_token
        resp = _http_post(
            f"{base}/api/intelligence/analyze",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        _raise_http_for_status(resp)
        result = resp.json()
        if isinstance(result, dict):
            deployment_context = self._deployment_context_from_analyze_result(result)
            if deployment_context:
                self._decision_model_context = deployment_context
        return result

    def submit(self, output: dict, todos: list = None, params: dict = None,
               reports: list = None, idempotency_key: str = None,
               run_id: str = None, data_provenance: list = None,
               run_mode: str = None, parent_run_id: str = None,
               batch_id: str = None, sample_used: bool = None,
               instance_id: str = None, remote_run_id: str = None,
               skill_version: str = None, agent_type: str = None):
        output = dict(output or {})
        self._merge_output_list(output, "reports", reports)
        self._merge_output_list(output, "todos", todos)
        proofs = list(data_provenance) if data_provenance is not None else list(self._data_proofs)
        output_meta = dict(output.get("_skillforge_meta") or {})
        output_meta["data_proofs"] = proofs
        output_meta["data_provenance"] = proofs
        existing_collection = (
            output_meta.get("collection_proofs")
            if isinstance(output_meta.get("collection_proofs"), list)
            else []
        )
        self._merge_collection_proofs([item for item in existing_collection if isinstance(item, dict)])
        if self._collection_proofs:
            output_meta["collection_proofs"] = list(self._collection_proofs)
        model_context = self._decision_model_context or self._runtime_model_context
        if model_context:
            output_meta["model_context"] = model_context
            output_meta["active_model_deployment"] = {
                key: model_context.get(key)
                for key in (
                    "model_deployment_id",
                    "model_family",
                    "deployment_status",
                    "rollout_percent",
                    "rollout_selected",
                    "rollout_bucket",
                    "rollout_reason",
                    "artifact_id",
                    "artifact_sha256",
                    "target_skill_ids",
                )
                if model_context.get(key) is not None
            }
        if run_mode:
            output_meta["run_mode"] = run_mode
        if sample_used is not None:
            output_meta["sample_used"] = bool(sample_used)
        output["_skillforge_meta"] = output_meta

        base = self._sf_cfg.get("base_url", "http://127.0.0.1:8000")
        token = self._sf_cfg.get("token", "")
        url = f"{base}/api/executions/submit-result?token={token}"
        payload = {
            "skill_id": self.skill_id,
            "output": output,
            "params": {
                **(params or {}),
                **({"model_context": model_context} if model_context else {}),
            },
            "triggered_by": "skill_sdk",
            "data_provenance": proofs,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if run_id:
            payload["run_id"] = run_id
        if run_mode:
            payload["run_mode"] = run_mode
        if parent_run_id:
            payload["parent_run_id"] = parent_run_id
        if batch_id:
            payload["batch_id"] = batch_id
        if sample_used is not None:
            payload["sample_used"] = bool(sample_used)
        source_fields = {
            "instance_id": instance_id or os.environ.get("SKILLFORGE_INSTANCE_ID") or os.environ.get("OPENCLAW_INSTANCE_ID") or os.environ.get("INSTANCE_ID"),
            "remote_run_id": remote_run_id or os.environ.get("OPENCLAW_REMOTE_RUN_ID") or os.environ.get("SKILLFORGE_REMOTE_RUN_ID"),
            "skill_version": skill_version or os.environ.get("OPENCLAW_SKILL_VERSION") or os.environ.get("SKILL_VERSION"),
            "agent_type": agent_type or os.environ.get("OPENCLAW_AGENT_TYPE") or os.environ.get("AGENT_TYPE"),
        }
        for key, value in source_fields.items():
            if value:
                payload[key] = value
        headers = {}
        run_token = os.environ.get("SKILLFORGE_RUN_TOKEN") or os.environ.get("OPENCLAW_RUN_TOKEN")
        if run_token:
            headers["X-Run-Token"] = run_token
        resp = _http_post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("error"):
            raise RuntimeError(f"回推失败: {result}")
        return result

    @staticmethod
    def _merge_output_list(output: dict, key: str, items: list = None):
        if not items:
            return
        existing = output.get(key)
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(items)
        output[key] = merged

    @staticmethod
    def report_card(title: str, summary: str, detail: str = None,
                    sections: list = None, recipients=None,
                    channel: str = "dingtalk_card") -> dict:
        card = {
            "channel": channel,
            "title": title,
            "summary": summary,
        }
        if recipients:
            if isinstance(recipients, dict):
                card["recipients"] = recipients
            elif isinstance(recipients, list):
                card["recipients"] = {"users": recipients}
            else:
                card["recipients"] = {"users": [str(recipients)]}
        content_parts = []
        if detail:
            content_parts.append(str(detail))
        if sections:
            for section in sections:
                if isinstance(section, dict):
                    section_title = section.get("title")
                    section_content = section.get("content") or section.get("detail")
                    if section_title:
                        content_parts.append(f"## {section_title}")
                    if section_content:
                        content_parts.append(str(section_content))
                else:
                    content_parts.append(str(section))
        if content_parts:
            card["content_markdown"] = "\n\n".join(content_parts)
        payload = {}
        if detail:
            payload["detail"] = detail
        if sections:
            payload["sections"] = sections
        if payload:
            card["payload"] = payload
        return card

    @staticmethod
    def todo_dispatch(title: str, tasks: list, summary: str = "",
                      reviewers: list = None, reviewer_role: str = None,
                      sla_hours: int = None) -> dict:
        todo = {
            "kind": "dispatch",
            "title": title,
            "summary": summary,
            "tasks": tasks,
        }
        if reviewers:
            todo["reviewers"] = reviewers
        if reviewer_role:
            todo["reviewer_role"] = reviewer_role
        if sla_hours:
            todo["sla_hours"] = sla_hours
        return todo

    @staticmethod
    def todo_review(title: str, summary: str = "",
                    reviewers: list = None, reviewer_role: str = None) -> dict:
        todo = {"kind": "review", "title": title, "summary": summary}
        if reviewers:
            todo["reviewers"] = reviewers
        if reviewer_role:
            todo["reviewer_role"] = reviewer_role
        return todo

    @staticmethod
    def task(content: str, deadline: str = None, executor: str = None) -> dict:
        t = {"content": content}
        if deadline:
            t["deadline"] = deadline if "T" in deadline else f"{deadline}T18:00:00"
        if executor:
            t["executor"] = executor
        return t

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{self.skill_id}] {msg}")
