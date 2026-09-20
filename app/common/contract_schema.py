"""contract.json 输出契约校验共享工具。

设计原则（平台级,不知道任何具体 skill 的业务字段）：
- `get_output_schema`     : 从 contract 取 output_schema (jsonschema)
- `lint_output_schema`    : 用 jsonschema.Draft7Validator.check_schema 校验 schema 自身合法
- `validate_output`       : 用 jsonschema.validate 校验实际 output 是否符合 schema
- `load_sample_input`     : 读 fixtures/sample_input.json
- `run_main_with_sample`  : subprocess 跑 scripts/main.py,stdin 塞 sample_input,stdout 解析回 JSON

被三处消费:
- P0 skill_creation_runner: 生成时真跑 main.py 验 schema 对齐,不符返工
- P1 review_context_service.finalize: 确认 contract 有 output_schema + sample_input (结构闸门)
- P2 execution_service.execute_skill: 运行时 warn-only 对比,写 contract_drifts

另外提供 2 个 SSOT 辅助能力:
- 从 output_schema.required 渲染 SKILL.md 的 `## 输出定义`
- 把持久化 files/materialize 到临时目录,复用同一套 verify_schema_phase
"""

from __future__ import annotations

import asyncio
import ast
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from loguru import logger


SAMPLE_INPUT_PATH = "fixtures/sample_input.json"
_ACQUISITION_MARKERS = (
    ".fetch_api(",
    ".extract(",
    ".capture_apis(",
    ".explore(",
    ".browser_collect(",
)
_PLACEHOLDER_MARKERS = (
    "待实现",
    "请补充业务逻辑",
    "NotImplemented",
    "NotImplementedError",
    "placeholder",
    "stub",
    "占位",
    "空白草稿",
)
_URL_RE = re.compile(r"https?://[^\s`\"')>\]}]+")
_IGNORED_URL_HOST_MARKERS = (
    "json-schema.org",
    "schema.org",
)


def _trusted_skillforge_sdk_dirs() -> list[Path]:
    """返回 Skill 运行时允许注入的受信任 SDK 目录。"""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / "app" / "skill_runtime_sdk",
        repo_root / "skills-repo" / "_shared",
    ]
    existing: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        existing.append(resolved)
    return existing

TODO_ITEM_REQUIRED_FIELDS = ("kind", "title")
TODO_ITEM_RECOMMENDED_FIELDS = (
    "summary",
    "payload",
    "tasks",
    "reviewers",
    "reviewer_role",
    "sla_hours",
    "decision_mode",
    "callback",
)
REPORT_ITEM_REQUIRED_FIELDS = ("channel", "title", "summary", "recipients")
REPORT_ITEM_RECOMMENDED_FIELDS = ("content_markdown", "payload")

DATA_PROOF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["url", "method", "fetched_at", "sample_keys", "is_sample"],
    "properties": {
        "url": {"type": "string"},
        "method": {"type": "string"},
        "status": {"type": ["integer", "null"]},
        "fetched_at": {"type": "string"},
        "sample_keys": {"type": "array", "items": {"type": "string"}},
        "row_count": {"type": ["integer", "null"]},
        "is_sample": {"type": "boolean"},
    },
}


def normalize_data_proofs(value: Any) -> list[dict[str, Any]]:
    """Normalize SDK/platform data provenance into the canonical DataProof list."""
    if not isinstance(value, list):
        return []
    proofs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "GET").upper()
        status_raw = item.get("status")
        try:
            status = int(status_raw) if status_raw is not None else None
        except (TypeError, ValueError):
            status = None
        row_count_raw = item.get("row_count")
        try:
            row_count = int(row_count_raw) if row_count_raw is not None else None
        except (TypeError, ValueError):
            row_count = None
        sample_keys_raw = item.get("sample_keys")
        sample_keys = [
            str(key)[:120]
            for key in (sample_keys_raw if isinstance(sample_keys_raw, list) else [])
            if key is not None
        ][:50]
        proofs.append({
            "url": str(item.get("url") or "")[:2000],
            "method": method[:20],
            "status": status,
            "fetched_at": str(item.get("fetched_at") or ""),
            "sample_keys": sample_keys,
            "row_count": row_count,
            "is_sample": bool(item.get("is_sample")),
        })
    return proofs


def data_proofs_sample_used(value: Any) -> bool:
    return any(bool(item.get("is_sample")) for item in normalize_data_proofs(value))

TODO_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": list(TODO_ITEM_REQUIRED_FIELDS),
    "properties": {
        "kind": {"type": "string", "enum": ["review", "dispatch"]},
        "title": {"type": "string", "maxLength": 200},
        "summary": {"type": "string"},
        "payload": {"type": "object"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "executor": {"type": ["string", "null"]},
                    "content": {"type": "string"},
                    "deadline": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        "reviewers": {"type": "array", "items": {"type": "string"}},
        "reviewer_role": {
            "type": "string",
            "enum": ["biz_owner", "operator", "ai_engineer", "admin", "director"],
        },
        "sla_hours": {"type": "number"},
        "decision_mode": {
            "type": "string",
            "enum": ["any_of", "all_of", "independent"],
        },
        "callback": {"type": "object"},
    },
}

REPORT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": list(REPORT_ITEM_REQUIRED_FIELDS),
    "properties": {
        "channel": {
            "type": "string",
            "enum": ["dingtalk_card", "dingtalk_markdown", "email", "feishu"],
        },
        "title": {"type": "string", "maxLength": 80},
        "summary": {"type": "string"},
        "content_markdown": {"type": "string"},
        "recipients": {
            "type": "object",
            "properties": {
                "users": {"type": "array", "items": {"type": "string"}},
                "roles": {"type": "array", "items": {"type": "string"}},
                "departments": {"type": "array", "items": {"type": "string"}},
            },
        },
        "payload": {"type": "object"},
    },
}


def get_output_schema(contract: dict | None) -> dict | None:
    """从 contract 读 output_schema。

    兼容两处位置:
    - contract["output_schema"]           (顶层,推荐新位置)
    - contract["output"]["output_schema"] (嵌 output 内,兼容 LLM 写到哪都能读)
    返回 None 表示 skill 尚未升级到 jsonschema 规范 — 平台跳过校验 only warn。
    """
    if not isinstance(contract, dict):
        return None
    top = contract.get("output_schema")
    if isinstance(top, dict):
        return top
    inner = (contract.get("output") or {}).get("output_schema")
    if isinstance(inner, dict):
        return inner
    return None


def normalize_output_schema_platform_fields(
    contract: dict | None,
    *,
    force_todos: bool = False,
    require_reports: bool = True,
) -> dict | None:
    """Normalize platform-owned output schema fields in a contract copy.

    LLMs should not hand-roll the `todos` / `reports` item contracts. This
    helper keeps business fields untouched while injecting the canonical
    platform schemas and required entries for fields that the platform owns.
    """
    if not isinstance(contract, dict):
        return contract

    normalized = copy.deepcopy(contract)
    output_schema = get_output_schema(normalized)
    if not isinstance(output_schema, dict):
        return normalized

    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        output_schema["properties"] = properties

    required = output_schema.get("required")
    if not isinstance(required, list):
        required = []
        output_schema["required"] = required

    def _append_required(field: str) -> None:
        if field not in required:
            required.append(field)

    def _set_platform_array(field: str, item_schema: dict[str, Any]) -> None:
        properties[field] = {
            "type": "array",
            "items": copy.deepcopy(item_schema),
        }

    if require_reports:
        _set_platform_array("reports", REPORT_ITEM_SCHEMA)
        _append_required("reports")

    declares_todos = force_todos or "todos" in properties or "todos" in required
    if declares_todos:
        _set_platform_array("todos", TODO_ITEM_SCHEMA)
        _append_required("todos")

    return normalized


def lint_output_schema(schema: dict) -> list[str]:
    """校验 output_schema 自身是否合法 jsonschema (Draft-07)。

    返回错误消息列表,空列表表示合法。
    另外加几条 lint 规则防止 LLM 写得过松:
    - 顶层必须 type=object
    - 顶层 required 至少有 1 项(否则 schema 毫无约束力)
    - required 字段的 properties 不得是 空 schema `{}`:必须至少有
      type / enum / const / $ref 之一,否则 schema 会把一切当合法,
      起不到校验作用。
    """
    errors: list[str] = []
    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as e:
        errors.append(f"output_schema 不符合 Draft-07 规范: {e.message}")
        return errors

    if schema.get("type") != "object":
        errors.append("output_schema 顶层 type 必须是 'object'")

    required = schema.get("required") or []
    if not isinstance(required, list) or len(required) == 0:
        errors.append("output_schema 必须声明至少 1 个 required 字段")

    # 业务约束:required 字段的 properties schema 不得是空 schema。
    # 空 dict `{}` / None / 缺 type&enum&const&$ref 都会让 jsonschema
    # 把任何值当合法,等于绕过类型检查。
    if isinstance(required, list):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        for field in required:
            if not isinstance(field, str):
                continue
            field_schema = properties.get(field)
            if field_schema is None or (
                isinstance(field_schema, dict)
                and not any(
                    k in field_schema for k in ("type", "enum", "const", "$ref")
                )
            ):
                errors.append(
                    f"properties[{field!r}] 必须指定 type/enum/const/$ref"
                    "（禁止空 schema 逃逸类型检查）"
                )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        errors.extend(_lint_platform_array_schema(properties, "todos", TODO_ITEM_SCHEMA))
        errors.extend(_lint_platform_array_schema(properties, "reports", REPORT_ITEM_SCHEMA))

    return errors


def _lint_platform_array_schema(
    properties: dict[str, Any],
    field: str,
    item_schema: dict[str, Any],
) -> list[str]:
    """平台固定输出字段必须声明 item 结构，避免 todos/reports 退化成裸数组。"""
    field_schema = properties.get(field)
    if not isinstance(field_schema, dict):
        return []

    errors: list[str] = []
    if field_schema.get("type") != "array":
        errors.append(f"properties[{field!r}] type 必须是 'array'")
        return errors

    items = field_schema.get("items")
    if not isinstance(items, dict) or items.get("type") != "object":
        errors.append(f"properties[{field!r}].items 必须声明 object schema")
        return errors

    item_props = items.get("properties")
    if not isinstance(item_props, dict):
        item_props = {}

    expected_props = item_schema.get("properties") or {}
    missing = [name for name in expected_props if name not in item_props]
    if missing:
        errors.append(
            f"properties[{field!r}].items.properties 缺少平台契约字段: "
            + ", ".join(missing[:12])
        )

    required = items.get("required") or []
    expected_required = item_schema.get("required") or []
    if not isinstance(required, list):
        errors.append(f"properties[{field!r}].items.required 必须是数组")
    else:
        missing_required = [name for name in expected_required if name not in required]
        if missing_required:
            errors.append(
                f"properties[{field!r}].items.required 缺少必填字段: "
                + ", ".join(missing_required)
            )
    return errors


def validate_output(output: Any, schema: dict) -> list[str]:
    """用 schema 校验 output,返回错误消息列表。空列表代表通过。

    每条错误是 path + message 组合,例于 LLM 读后返工。
    """
    if not isinstance(schema, dict):
        return ["schema 缺失或非 dict"]
    validator = Draft7Validator(schema)
    errors = []
    for err in validator.iter_errors(output):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors


def load_sample_input(work_dir: Path | str) -> dict | None:
    """读取 work_dir/fixtures/sample_input.json。不存在或解析失败返回 None。"""
    p = Path(work_dir) / SAMPLE_INPUT_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("sample_input.json 解析失败 path={} err={}", p, e)
        return None


#: 允许透传给 LLM 生成的 main.py 的环境变量白名单。
#:
#: 禁止传 DB/LLM key/钉钉 secret 等敏感环境给 LLM 生成的 main.py —
#: 这里只保留 main.py 端到端跑 HTTP/curl/证书/时区 所必需的基础环境,
#: 其它一律丢弃(含 DATABASE_URL / DINGTALK_APP_SECRET / LITELLM_API_KEY 等)。
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "PYTHONIOENCODING",
    }
)


async def run_main_with_sample(
    work_dir: Path | str,
    sample_input: dict,
    *,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """subprocess 跑 `python3 scripts/main.py`,stdin 塞 sample_input JSON。

    返回 (returncode, stdout, stderr)。timeout 超时会 terminate 并抛 TimeoutError。
    cwd 固定为 work_dir,PYTHONPATH 固定为 scripts/ + 受信任的
    SkillForge SDK 目录 (不拼接外部 PYTHONPATH,防止越权 import 平台代码)。
    env 只透传 _SAFE_ENV_KEYS 白名单,绝不把 DB/LLM key/钉钉 secret
    等敏感环境暴露给 LLM 生成的 main.py。
    """
    main_py = Path(work_dir) / "scripts" / "main.py"
    if not main_py.exists():
        return (127, "", f"main.py not found at {main_py}")

    # 显式白名单:仅保留 main.py 端到端跑 HTTP/curl/证书/时区 所必需的基础环境。
    # 禁止传 DB/LLM/钉钉等敏感环境给 LLM 生成的 main.py。
    env: dict[str, str] = {
        key: value
        for key, value in os.environ.items()
        if key in _SAFE_ENV_KEYS
    }
    scripts_dir = str(Path(work_dir) / "scripts")
    sdk_dirs = _trusted_skillforge_sdk_dirs()
    python_paths = [scripts_dir, *(str(path) for path in sdk_dirs)]
    # PYTHONPATH 直接写死 scripts_dir + 受信任 SDK 目录,不拼接外部值 —
    # 防止 LLM 用 PYTHONPATH=../app 之类越权 import 平台代码。
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    if sdk_dirs:
        env["SKILLFORGE_TRUSTED_SDK_DIR"] = str(sdk_dirs[0])

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(main_py),
        cwd=str(work_dir),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin_bytes = json.dumps(sample_input, ensure_ascii=False).encode("utf-8")
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return (-9, "", f"main.py timed out after {timeout}s")
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _datasource_input_names(contract: dict | None) -> list[str]:
    """返回 contract.input 中声明为 datasource 的数据源名称。"""
    if not isinstance(contract, dict):
        return []
    inputs = contract.get("input") or []
    if not isinstance(inputs, list):
        return []

    names: list[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip().lower()
        if source == "datasource":
            name = str(item.get("name") or "").strip() or "<未命名数据源>"
            names.append(name)
    return names


def _external_urls_in_text(text: str | None) -> list[str]:
    """提取业务外部 URL，过滤 JSON Schema 这类规范地址。"""
    urls: list[str] = []
    for url in _URL_RE.findall(text or ""):
        normalized = url.rstrip(".,;")
        if any(marker in normalized for marker in _IGNORED_URL_HOST_MARKERS):
            continue
        urls.append(normalized)
    return urls


def _external_api_permission_targets(contract: dict | None) -> list[str]:
    if not isinstance(contract, dict):
        return []
    permissions = contract.get("permissions") or []
    if not isinstance(permissions, list):
        return []

    targets: list[str] = []
    for item in permissions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action != "external_api":
            continue
        target = str(item.get("target") or "").strip() or "<未指定外部接口>"
        targets.append(target)
    return targets


def _runtime_acquisition_reason(
    contract: dict | None,
    *,
    skill_md_text: str | None = None,
) -> str | None:
    datasource_names = _datasource_input_names(contract)
    if datasource_names:
        return f"contract.input 声明了 datasource 数据源 {datasource_names}"

    external_targets = _external_api_permission_targets(contract)
    if external_targets:
        return f"contract.permissions 声明了 external_api {external_targets}"

    contract_text = ""
    if isinstance(contract, dict):
        try:
            contract_text = json.dumps(contract, ensure_ascii=False)
        except TypeError:
            contract_text = str(contract)
    urls = _external_urls_in_text(skill_md_text) + _external_urls_in_text(contract_text)
    if urls:
        return f"SKILL.md/contract 提到了外部接口 {urls[:3]}"

    return None


def _iter_strings(value: Any, path: str = "$", depth: int = 0) -> list[tuple[str, str]]:
    if depth > 12:
        return []
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        out: list[tuple[str, str]] = []
        for idx, item in enumerate(value):
            out.extend(_iter_strings(item, f"{path}[{idx}]", depth + 1))
        return out
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            out.extend(_iter_strings(item, f"{path}.{key}", depth + 1))
        return out
    return []


def _find_placeholder_marker(text: str) -> str | None:
    lowered = text.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def lint_placeholder_implementation(main_py_text: str | None) -> list[str]:
    """阻断骨架 main.py 进入发布预览。

    schema 对齐只能证明字段形状正确，不能证明代码已经实现业务逻辑。
    这里专门拦截常见骨架：占位文案、pass、NotImplementedError。
    """
    text = main_py_text or ""
    if not text.strip():
        return ["scripts/main.py 为空，必须实现可运行的业务逻辑。"]

    errors: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return errors

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            marker = _find_placeholder_marker(node.value)
            if marker:
                errors.append(
                    f"scripts/main.py 仍包含占位实现文案 {marker!r}，必须改成真实业务逻辑。"
                )
                break
        elif isinstance(node, ast.Pass):
            errors.append(
                f"scripts/main.py 第 {getattr(node, 'lineno', '?')} 行仍有 pass，占位分支必须实现或删除。"
            )
            break
        elif isinstance(node, ast.Raise):
            exc = node.exc
            exc_name = ""
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                exc_name = exc.id
            elif isinstance(exc, ast.Attribute):
                exc_name = exc.attr
            if exc_name == "NotImplementedError":
                errors.append(
                    f"scripts/main.py 第 {getattr(node, 'lineno', '?')} 行仍抛 NotImplementedError，必须实现业务逻辑。"
                )
                break

    return errors


def lint_placeholder_output(output: Any) -> list[str]:
    """阻断 main.py 运行结果返回骨架/占位内容。"""
    for path, value in _iter_strings(output):
        marker = _find_placeholder_marker(value)
        if marker:
            return [
                f"main.py 输出 {path} 含占位文案 {marker!r}，不能用骨架结果通过发布校验。"
            ]
    return []


def lint_runtime_data_acquisition(
    contract: dict | None,
    main_py_text: str | None,
    *,
    skill_md_text: str | None = None,
) -> list[str]:
    """静态校验需要外部/平台数据的 Skill 是否具备运行时真实采集路径。

    生成阶段的 `fixtures/sample_input.json` 只能用于 schema/回归校验，
    不能成为运行时数据来源。只要 contract/SKILL.md 声明了 datasource、
    external_api 或外部 URL，`scripts/main.py` 就必须包含 SkillForge SDK
    的采集方法；否则这类 Skill 会退化成"拿样例 payload 做分析"。
    """
    text = main_py_text or ""
    errors: list[str] = []

    reads_fixture = any(
        "sample_input.json" in line
        and not line.lstrip().startswith("#")
        and any(marker in line for marker in ("open(", "Path(", "read_text(", "json.load("))
        for line in text.splitlines()
    )
    if reads_fixture:
        errors.append(
            "scripts/main.py 不允许读取 fixtures/sample_input.json；该文件只给平台做离线契约校验，"
            "真实运行必须来自 payload 或 SkillForge SDK 数据采集。"
        )

    errors.extend(_lint_forbidden_runtime_side_effects(text))
    errors.extend(_lint_silent_empty_acquisition(text))

    reason = _runtime_acquisition_reason(contract, skill_md_text=skill_md_text)
    if not reason:
        return errors

    uses_sdk = "skillforge_sdk" in text or "SkillForge(" in text
    has_collect_inputs = bool(re.search(r"^\s*def\s+collect_inputs\s*\(", text, re.M))
    uses_acquisition = any(marker in text for marker in _ACQUISITION_MARKERS)
    if not (uses_sdk and uses_acquisition and has_collect_inputs):
        errors.append(
            f"{reason}，但 scripts/main.py 没有 SkillForge SDK 真实采集逻辑。"
            "必须实现 collect_inputs(payload)：payload 已带真实数据时直接分析；缺失时用 "
            "skillforge_sdk.SkillForge.fetch_api/extract/capture_apis/explore 从浏览器登录态采集。"
            "fixtures/sample_input.json 只能做契约校验，不能当运行数据。"
        )

    return errors


_FORBIDDEN_RUNTIME_IMPORTS = {
    "requests",
    "httpx",
    "aiohttp",
    "smtplib",
    "dingtalk",
}


def _lint_forbidden_runtime_side_effects(text: str) -> list[str]:
    """Generated main.py must return intent, not send messages directly."""
    if not text.strip():
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    errors: list[str] = []
    def _is_forbidden_module(name: str) -> bool:
        root = (name or "").split(".", 1)[0]
        return (
            root in _FORBIDDEN_RUNTIME_IMPORTS
            or root.startswith("dingtalk")
            or ".dingtalk" in (name or "")
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name or ""):
                    errors.append(
                        f"scripts/main.py 禁止直接 import {alias.name!r}。"
                        "业务采集必须走 SkillForge SDK；待办/报告只能 return 给平台，不能直接推钉钉/邮件。"
                    )
        elif isinstance(node, ast.ImportFrom):
            if _is_forbidden_module(node.module or ""):
                errors.append(
                    f"scripts/main.py 禁止直接 from {node.module!r} import。"
                    "业务采集必须走 SkillForge SDK；待办/报告只能 return 给平台，不能直接推钉钉/邮件。"
                )
    return errors


def _is_empty_literal(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return len(getattr(node, "elts", getattr(node, "keys", [])) or []) == 0
    return isinstance(node, ast.Constant) and node.value is None


def _lint_silent_empty_acquisition(text: str) -> list[str]:
    """Reject acquisition helpers that hide failures by returning empty data.

    A generated skill that catches API errors and returns `{}`/`[]` will pass
    sample schema checks while real executions silently report "0 issues".
    Acquisition code must either raise or expose a `data_errors` style signal.
    """
    if not text.strip():
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    errors: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = ast.get_source_segment(text, fn) or ""
        if not any(marker in segment for marker in _ACQUISITION_MARKERS):
            continue
        for handler in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
            for stmt in handler.body:
                if isinstance(stmt, ast.Return) and _is_empty_literal(stmt.value):
                    errors.append(
                        "scripts/main.py 不允许在采集失败时静默返回空 dict/list/None；"
                        f"第 {getattr(stmt, 'lineno', '?')} 行必须改为抛出异常或记录 data_errors。"
                    )
                    break
                if isinstance(stmt, ast.Pass):
                    errors.append(
                        "scripts/main.py 不允许在采集失败时用 pass 吞掉异常；"
                        f"第 {getattr(stmt, 'lineno', '?')} 行必须改为抛出异常或记录 data_errors。"
                    )
                    break
    return errors


def parse_stdout_json(stdout: str) -> tuple[dict | list | None, str | None]:
    """解析 main.py stdout。

    优先整体 JSON 解析,失败则尝试提取最后一个 JSON 对象(容忍 skill 在 stdout 前打了 log)。
    用 json.JSONDecoder().raw_decode 扫描每个 `{` / `[` 起点,正确处理字符串里的
    大括号和转义(不会像反向括号匹配那样把 `{"note":"a { b"}` 里的 `{` 当成结构)。
    返回 (parsed, None) 或 (None, err_msg)。
    """
    text = (stdout or "").strip()
    if not text:
        return None, "stdout 为空"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as whole_err:
        last_err: json.JSONDecodeError | None = whole_err

    # 容错: 从头扫描每个 { 或 [ 起点,记录 **最后一个** 能成功 raw_decode 的结果。
    # raw_decode 能正确识别字符串转义和内嵌括号,比手写栈匹配靠谱。
    decoder = json.JSONDecoder()
    result: dict | list | None = None
    found_any = False
    idx = 0
    n = len(text)
    while idx < n:
        ch = text[idx]
        if ch == "{" or ch == "[":
            try:
                parsed, _end = decoder.raw_decode(text, idx)
            except json.JSONDecodeError as e:
                last_err = e
                idx += 1
                continue
            if isinstance(parsed, (dict, list)):
                result = parsed
                found_any = True
                # 继续往后扫,保留最后一个合法对象(而非首个)
                idx = _end
                continue
            idx += 1
        else:
            idx += 1

    if found_any:
        return result, None
    if last_err is None:
        return None, "stdout 无 JSON 对象"
    return None, f"JSON 解析失败: {last_err}"


def summarize_errors(errors: list[str], max_items: int = 8) -> str:
    """拼成一行返工提示给 LLM,最多 max_items 条避免污染上下文。"""
    if not errors:
        return "(no errors)"
    if len(errors) <= max_items:
        return "; ".join(errors)
    shown = errors[:max_items]
    return "; ".join(shown) + f"; (+{len(errors) - max_items} more)"


# ─── Triple alignment:SKILL.md §输出定义 ↔ output_schema.required ↔ main.py return ───
import re as _re

_PLATFORM_FIELDS = {"todos", "reports", "诊断报告"}
_SKILL_MD_OUTPUT_RE = _re.compile(r"##\s*输出定义\s*\n([\s\S]*?)(?=\n##|\Z)")
_SKILL_MD_OUTPUT_SECTION_RE = _re.compile(r"(?ms)^##\s*输出定义\s*$\n.*?(?=^##\s|\Z)")
_SKILL_MD_TODO_SECTION_RE = _re.compile(r"(?ms)^##\s*(?:待办|待办输出|Todo|Todos)\s*$\n.*?(?=^##\s|\Z)")
_FIELD_LINE_RE = _re.compile(r"^\s*-\s*\*{0,2}([^*:：\n]+?)\*{0,2}\s*[:：]", _re.MULTILINE)


def parse_skill_md_output_fields(skill_md: str) -> list[str]:
    """从 SKILL.md ## 输出定义 章节提取字段名列表。

    兼容 `- **字段名**: 说明` / `- 字段名: 说明` 两种写法,
    字段名允许含空格、斜杠、中英混排,直到首个冒号或 `**` 结束。
    """
    if not skill_md:
        return []
    m = _SKILL_MD_OUTPUT_RE.search(skill_md)
    if not m:
        return []
    return [s.strip() for s in _FIELD_LINE_RE.findall(m.group(1))]


def _schema_type_label(field_schema: dict | None) -> str:
    if not isinstance(field_schema, dict):
        return "unknown"
    typ = field_schema.get("type")
    if isinstance(typ, list):
        return " / ".join(str(t) for t in typ)
    if isinstance(typ, str):
        return typ
    if "enum" in field_schema:
        return "enum"
    return "unknown"


def render_skill_md_output_section(
    output_schema: dict | None,
    output_descriptions: dict | None = None,
) -> str:
    """从 output_schema.required 渲染 `## 输出定义` 章节。

    设计原则:
    - SSOT 只认 required,不把非 required 的 properties 写进 SKILL.md
    - 保持 required 原顺序,避免无意义 diff
    - 跳过平台固定字段(todos/reports/诊断报告)
    """
    if not isinstance(output_schema, dict):
        return ""

    required = output_schema.get("required") or []
    properties = output_schema.get("properties") or {}
    if not isinstance(required, list):
        return ""

    lines = ["## 输出定义", ""]
    for field in required:
        if not isinstance(field, str) or field in _PLATFORM_FIELDS:
            continue
        desc = None
        if isinstance(output_descriptions, dict):
            raw_desc = output_descriptions.get(field)
            if isinstance(raw_desc, str) and raw_desc.strip():
                desc = raw_desc.strip()
        if not desc:
            desc = f"平台按 output_schema 校验的必需输出字段（类型：{_schema_type_label(properties.get(field))}）"
        lines.append(f"- {field}: {desc}")
    return "\n".join(lines).rstrip() + "\n\n"


def sync_skill_md_output_section(
    skill_md: str,
    output_schema: dict | None,
    output_descriptions: dict | None = None,
) -> str:
    """把 SKILL.md 的 `## 输出定义` 收口到 output_schema.required。"""
    if not skill_md or not isinstance(output_schema, dict):
        return skill_md

    rendered = render_skill_md_output_section(output_schema, output_descriptions)
    if not rendered:
        return skill_md

    if _SKILL_MD_OUTPUT_SECTION_RE.search(skill_md):
        return _SKILL_MD_OUTPUT_SECTION_RE.sub(rendered, skill_md, count=1)

    test_case_match = _re.search(r"(?m)^##\s*测试用例\s*$", skill_md)
    if test_case_match:
        pos = test_case_match.start()
        prefix = skill_md[:pos].rstrip() + "\n\n"
        suffix = skill_md[pos:].lstrip()
        return prefix + rendered + suffix

    base = skill_md.rstrip()
    if not base:
        return rendered
    return base + "\n\n" + rendered


def _schema_declares_platform_field(output_schema: dict | None, field: str) -> bool:
    if not isinstance(output_schema, dict):
        return False
    required = output_schema.get("required") or []
    properties = output_schema.get("properties") or {}
    return (
        isinstance(required, list)
        and field in required
    ) or (
        isinstance(properties, dict)
        and field in properties
    )


def render_skill_md_todo_section(output_schema: dict | None) -> str:
    """从 output_schema 渲染一个可被前端 `待办` 模块识别的章节。

    这个章节不是运行时真源；真源仍是 `scripts/main.py` 返回的
    `output.todos`。它的作用是把能力声明落到 SKILL.md，让编辑器模块
    不再显示“未配置待办”，用户能直接确认这个 Skill 会输出待办。
    """
    if not _schema_declares_platform_field(output_schema, "todos"):
        return ""

    rows = [
        {
            "kind": "dispatch",
            "title": "运行时 output.todos 待办输出",
            "summary": "contract.json 已声明 todos；scripts/main.py 返回 todos 后会进入收件待办。",
            "reviewer_role": "operator",
            "sla_hours": 24,
            "decision_mode": "any_of",
            "payload_fields": ["output"],
            "tasks": [
                {
                    "content": "以运行结果 todos[].tasks 为准",
                }
            ],
        }
    ]
    return "\n".join([
        "## 待办",
        "",
        "```json",
        json.dumps(rows, ensure_ascii=False, indent=2),
        "```",
        "",
    ])


def sync_skill_md_todo_section(skill_md: str, output_schema: dict | None) -> str:
    """确保声明了 output.todos 的 Skill 在 SKILL.md 中有 `## 待办` 章节。"""
    rendered = render_skill_md_todo_section(output_schema)
    if not rendered:
        return skill_md
    if _SKILL_MD_TODO_SECTION_RE.search(skill_md or ""):
        return skill_md

    test_case_match = _re.search(r"(?m)^##\s*测试用例\s*$", skill_md or "")
    if test_case_match:
        pos = test_case_match.start()
        prefix = skill_md[:pos].rstrip() + "\n\n"
        suffix = skill_md[pos:].lstrip()
        return prefix + rendered + suffix

    base = (skill_md or "").rstrip()
    if not base:
        return rendered
    return base + "\n\n" + rendered


def normalize_skill_bundle_files(files: dict[str, str], contract: dict | None) -> dict[str, str]:
    """对生成/发布用的 files 做平台侧规范化。

    当前做两件事：
    - 用 output_schema.required 覆盖 SKILL.md §输出定义，让 SKILL.md
      不再成为业务字段的第二份手写真源；
    - 当 output_schema 声明 todos 时，补齐 SKILL.md §待办，保证
      Studio 模块能明确展示“这个 Skill 会输出待办”。
    """
    normalized = dict(files or {})
    skill_md = normalized.get("SKILL.md")
    if not skill_md or not isinstance(contract, dict):
        return normalized

    output_schema = get_output_schema(contract)
    output_descriptions = (contract.get("output") or {}).get("schema")
    normalized_skill_md = sync_skill_md_output_section(
        skill_md,
        output_schema,
        output_descriptions if isinstance(output_descriptions, dict) else None,
    )
    normalized["SKILL.md"] = sync_skill_md_todo_section(normalized_skill_md, output_schema)
    return normalized


def load_sample_input_from_files(files: dict[str, str]) -> dict | None:
    """从 files dict 读取 fixtures/sample_input.json。"""
    if not isinstance(files, dict):
        return None
    raw = files.get(SAMPLE_INPUT_PATH)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("sample_input.json 解析失败(files dict) err={}", e)
        return None


def get_preview_input(
    contract: dict | None = None,
    files: dict[str, str] | None = None,
) -> dict:
    """统一取预演使用的样例输入。

    优先级:
    1. files/fixtures/sample_input.json
    2. contract.sample_input (内部过渡字段)
    3. legacy contract.fixtures[0].input
    4. legacy contract.fixtures[0]
    """
    sample_input = load_sample_input_from_files(files or {})
    if isinstance(sample_input, dict):
        return sample_input

    contract = contract or {}
    inline = contract.get("sample_input")
    if isinstance(inline, dict):
        return inline

    fixtures = contract.get("fixtures") or []
    if fixtures and isinstance(fixtures[0], dict):
        fixture = fixtures[0]
        if isinstance(fixture.get("input"), dict):
            return fixture["input"]
        return fixture
    return {}


def materialize_skill_bundle(work_dir: Path | str, files: dict[str, str]) -> None:
    """把一组 skill files 写到临时目录,供 verify_schema_phase / preview 复用。"""
    base = Path(work_dir)
    base.mkdir(parents=True, exist_ok=True)
    for rel_path, content in (files or {}).items():
        path = base / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


async def build_verified_preview(
    contract: dict,
    files: dict[str, str],
    *,
    cache_key: str,
    timeout: float = 10.0,
) -> dict:
    """构造预演并复用真实 main.py + sample_input 做一次发布前级别校验。

    用途:
    - 生成成功后的 ready 预览
    - 页面恢复/刷新后的预览重建
    - legacy stream_skill_creation 的增强帧
    """
    from collections import Counter
    from tempfile import TemporaryDirectory

    from app.workbench.task_contract import build_preview

    preview = build_preview(contract, cache_key=cache_key, cached=False, files=files)
    sample_input = load_sample_input_from_files(files)
    output_schema = get_output_schema(contract)
    if sample_input is None or not files.get("scripts/main.py"):
        return preview

    implementation_errors = lint_placeholder_implementation(files.get("scripts/main.py"))
    acquisition_errors = lint_runtime_data_acquisition(
        contract,
        files.get("scripts/main.py"),
        skill_md_text=files.get("SKILL.md"),
    )
    preview_errors = implementation_errors + acquisition_errors
    if preview_errors:
        preview["success"] = False
        preview["schema_errors"] = preview_errors[:10]
        preview["sandbox_summary"] = "⚠️ Skill 实现不完整：" + "; ".join(preview_errors[:2])
        return preview

    preview["fixture_used"] = sample_input
    preview["run_mode"] = "sample_preview"
    preview["sample_used"] = True
    normalized_files = normalize_skill_bundle_files(files, contract)
    with TemporaryDirectory(prefix="sf_preview_") as td:
        materialize_skill_bundle(td, normalized_files)
        rc, stdout, stderr = await run_main_with_sample(td, sample_input, timeout=timeout)

    if rc != 0:
        preview["success"] = False
        preview["schema_errors"] = [(stderr or "").strip()[-200:] or f"exit={rc}"]
        preview["sandbox_summary"] = f"⚠️ 样例沙箱运行失败：{preview['schema_errors'][0]}"
        return preview

    parsed, parse_err = parse_stdout_json(stdout)
    actual_output: dict[str, Any] = {}
    if parse_err:
        preview["success"] = False
        preview["schema_errors"] = [f"stdout 非法 JSON: {parse_err}"]
        preview["sandbox_summary"] = f"⚠️ 样例沙箱输出不是合法 JSON：{parse_err}"
        return preview

    if isinstance(parsed, dict):
        actual_output = parsed
    placeholder_errors = lint_placeholder_output(parsed)
    if placeholder_errors:
        preview["success"] = False
        preview["schema_errors"] = placeholder_errors[:10]
        preview["sandbox_summary"] = "⚠️ 样例沙箱输出仍是占位结果：" + "; ".join(placeholder_errors[:2])
        return preview

    schema_errors = validate_output(parsed, output_schema) if output_schema else []
    if schema_errors:
        preview["success"] = False
        preview["schema_errors"] = schema_errors[:10]
        preview["sandbox_summary"] = "⚠️ 样例沙箱输出与 output_schema 不一致：" + "; ".join(schema_errors[:3])

    todos = actual_output.get("todos") if isinstance(actual_output.get("todos"), list) else []
    reports = actual_output.get("reports") if isinstance(actual_output.get("reports"), list) else []
    summary_lines: list[str] = []
    if todos:
        by_role = Counter((t.get("reviewer_role") or "未指定") for t in todos if isinstance(t, dict))
        breakdown = " / ".join(f"{role}:{count}" for role, count in by_role.most_common())
        summary_lines.append(f"📝 将创建 **{len(todos)} 个待办**（{breakdown}）")
    if reports:
        channels = Counter((r.get("channel") or "unknown") for r in reports if isinstance(r, dict))
        ch_breakdown = " / ".join(f"{channel}:{count}" for channel, count in channels.most_common())
        summary_lines.append(f"📢 将推送 **{len(reports)} 份报告**（{ch_breakdown}）")
    if preview.get("success") is False and preview.get("sandbox_summary"):
        summary_lines.append(preview["sandbox_summary"])
    if not todos and not reports:
        summary_lines.append("⚠️ 样例沙箱未产出 todos 或 reports；这个 skill 只输出业务数据")
    preview["sandbox_summary"] = "\n".join(summary_lines)
    preview["sandbox_counts"] = {"todos": len(todos), "reports": len(reports)}
    # 给创建页/审批页直接渲染“运行后会进入哪里”的结构化预览。
    # 这里只回传平台流向字段，避免把完整业务输出塞进 WebSocket 帧。
    preview["sandbox_output"] = {"todos": todos, "reports": reports}
    preview["rendered_output"] = "\n\n---\n\n".join(
        s for s in [preview.get("rendered_output") or "", "\n".join(summary_lines)] if s
    )
    return preview


def check_triple_alignment(
    skill_md: str,
    output_schema: dict | None,
    main_output: dict | None,
) -> list[str]:
    """校验 SKILL.md §输出定义 ↔ output_schema.required ↔ main.py return 三处字段一致。

    排除平台固定字段(todos/reports/诊断报告)。违反任何一条都返错误文本,
    用于 verify_schema_phase 聚合 + 注入返工 prompt 让 aiclaw 修。
    """
    if not isinstance(output_schema, dict):
        return []
    skill_md_fields = set(parse_skill_md_output_fields(skill_md)) - _PLATFORM_FIELDS
    required = set(output_schema.get("required") or []) - _PLATFORM_FIELDS
    main_keys = set((main_output or {}).keys()) - _PLATFORM_FIELDS

    errors: list[str] = []
    extra_in_skill_md = skill_md_fields - required
    missing_in_skill_md = required - skill_md_fields
    missing_in_main = required - main_keys
    if extra_in_skill_md:
        errors.append(
            f"SKILL.md §输出定义 声明 {sorted(extra_in_skill_md)} 但 output_schema.required 没有(请补 required 或删 SKILL.md 字段)"
        )
    if missing_in_skill_md:
        errors.append(
            f"output_schema.required 声明 {sorted(missing_in_skill_md)} 但 SKILL.md §输出定义 没列(请补 SKILL.md 字段)"
        )
    if missing_in_main:
        errors.append(
            f"main.py return 缺字段 {sorted(missing_in_main)}(禁止把它们折叠进 markdown 字符串)"
        )
    return errors


def _infer_type(value: Any) -> str:
    """从 Python 值推断 jsonschema type 字符串。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) or isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def infer_schema_from_samples(samples: list[dict]) -> dict:
    """从 N 条真实 output 样本反推一个保守 jsonschema Draft-07。

    规则(保守 = 尽量宽,不对真实数据过严):
    - required = 所有样本都出现的 key 取交集
    - properties[k].type = N 个样本该 key 类型的集合(union),单元素时写 string,多元素时写 ["string","null"]
    - 顶层 type=object
    - 不递归 items 和 nested object 的 schema(降低意外过严风险,后续人工补)
    """
    if not samples:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": [],
            "properties": {},
        }

    dict_samples = [s for s in samples if isinstance(s, dict)]
    if not dict_samples:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": [],
            "properties": {},
        }

    # required: 所有样本都出现的 key 的交集
    key_sets = [set(s.keys()) for s in dict_samples]
    required = sorted(set.intersection(*key_sets)) if key_sets else []

    # properties: 每个 key 在各样本里出现的 type 集合
    all_keys: set[str] = set()
    for ks in key_sets:
        all_keys.update(ks)

    properties: dict[str, Any] = {}
    for key in sorted(all_keys):
        types: set[str] = set()
        for s in dict_samples:
            if key in s:
                types.add(_infer_type(s[key]))
        types.discard("null")  # null 不单独当类型,后续在 nullable 里表达
        if len(types) == 1:
            properties[key] = {"type": next(iter(types))}
        elif len(types) > 1:
            properties[key] = {"type": sorted(types)}
        else:
            properties[key] = {}  # 全 null,不指定 type

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": required,
        "properties": properties,
    }
