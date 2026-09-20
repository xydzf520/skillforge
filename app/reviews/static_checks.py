"""Static review checks for Skill runtime code and MCP server entrypoints."""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.skills.core.git_service import git_service


CODE_SUFFIXES = {".py", ".js", ".ts", ".mjs", ".cjs"}
DIRECT_LLM_MODULES = (
    "anthropic",
    "openai",
    "google.generativeai",
    "cohere",
    "deepseek",
    "zhipuai",
    "dashscope",
    "langchain_openai",
    "langchain_anthropic",
)
DIRECT_LLM_JS_PACKAGES = (
    "openai",
    "@anthropic-ai/sdk",
    "@google/generative-ai",
    "cohere-ai",
    "deepseek",
)
LLM_API_HOST_RE = re.compile(
    r"https?://(?:api\.)?(?:deepseek|openai|anthropic|cohere)\.[A-Za-z0-9_.:-]+"
    r"|https?://api\.openai\.com"
    r"|https?://api\.anthropic\.com"
    r"|https?://api\.deepseek\.com"
    r"|https?://open\.bigmodel\.cn/api",
    re.IGNORECASE,
)
DIRECT_HTTP_CALL_RE = re.compile(
    r"\b(?:(?:requests|httpx)\s*\.\s*"
    r"(?:get|post|put|patch|request|stream|Client|AsyncClient)|"
    r"fetch|axios\s*\.\s*(?:get|post|put|patch|request))\s*\([^#\n]*"
    r"(?:https?://[^\"')\s]+|base_url\s*=\s*[\"']https?://[^\"']+)",
    re.IGNORECASE,
)
PLATFORM_LLM_GATEWAY_RE = re.compile(
    r"^\s*(?:from|import)\s+app\.(?:common\.ai|intelligence|skills\.intelligence)\b",
    re.MULTILINE,
)
IMPORT_FALLBACK_RE = re.compile(
    r"^\s*(?:from|import)\s+("
    + "|".join(re.escape(module) for module in DIRECT_LLM_MODULES)
    + r")(?:\b|\.)",
    re.MULTILINE,
)
JS_IMPORT_RE = re.compile(
    r"^\s*import(?:\s+[^;\n]+?\s+from)?\s*[\"']("
    + "|".join(re.escape(module) for module in DIRECT_LLM_JS_PACKAGES)
    + r")[\"']|require\(\s*[\"']("
    + "|".join(re.escape(module) for module in DIRECT_LLM_JS_PACKAGES)
    + r")[\"']\s*\)",
    re.MULTILINE,
)


@dataclass
class StaticCheckFinding:
    rule: str
    path: str
    line: int
    message: str
    snippet: str = ""
    severity: str = "error"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StaticCheckReport:
    passed: bool = True
    findings: list[StaticCheckFinding] = field(default_factory=list)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    def add(self, finding: StaticCheckFinding) -> None:
        self.findings.append(finding)
        self.passed = False

    def extend(self, findings: list[StaticCheckFinding]) -> None:
        for finding in findings:
            self.add(finding)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "finding_count": self.finding_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def static_check_error_detail(report: StaticCheckReport | dict) -> dict:
    payload = report if isinstance(report, dict) else report.to_dict()
    return {
        "reason": "检测到 Skill 运行代码直接调用 LLM SDK/API",
        "override_required": True,
        "override_min_length": 20,
        **payload,
    }


def _line_for(source: str, line_no: int) -> str:
    if line_no <= 0:
        return ""
    lines = source.splitlines()
    if line_no > len(lines):
        return ""
    return lines[line_no - 1].strip()[:240]


def _module_is_blocked(module: str | None) -> bool:
    if not module:
        return False
    return any(module == item or module.startswith(f"{item}.") for item in DIRECT_LLM_MODULES)


def _find_blocked_imports(path: str, source: str) -> list[StaticCheckFinding]:
    findings: list[StaticCheckFinding] = []
    suffix = Path(path).suffix
    if suffix in {".js", ".ts", ".mjs", ".cjs"}:
        for match in JS_IMPORT_RE.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            findings.append(StaticCheckFinding(
                rule="llm_sdk_import",
                path=path,
                line=line_no,
                message="禁止在 Skill 运行代码中直接导入 LLM SDK",
                snippet=_line_for(source, line_no),
            ))
        return findings

    try:
        tree = ast.parse(source)
    except SyntaxError:
        for match in IMPORT_FALLBACK_RE.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            findings.append(StaticCheckFinding(
                rule="llm_sdk_import",
                path=path,
                line=line_no,
                message="禁止在 Skill 运行代码或 MCP server 中直接导入 LLM SDK",
                snippet=_line_for(source, line_no),
            ))
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                if _module_is_blocked(module) or module == "google.generativeai":
                    findings.append(StaticCheckFinding(
                        rule="llm_sdk_import",
                        path=path,
                        line=node.lineno,
                        message="禁止在 Skill 运行代码或 MCP server 中直接导入 LLM SDK",
                        snippet=_line_for(source, node.lineno),
                    ))
                elif module == "google" and alias.asname == "generativeai":
                    findings.append(StaticCheckFinding(
                        rule="llm_sdk_import",
                        path=path,
                        line=node.lineno,
                        message="禁止在 Skill 运行代码或 MCP server 中直接导入 LLM SDK",
                        snippet=_line_for(source, node.lineno),
                    ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            google_generativeai = module == "google" and any(
                alias.name == "generativeai" for alias in node.names
            )
            if _module_is_blocked(module) or google_generativeai:
                findings.append(StaticCheckFinding(
                    rule="llm_sdk_import",
                    path=path,
                    line=node.lineno,
                    message="禁止在 Skill 运行代码或 MCP server 中直接导入 LLM SDK",
                    snippet=_line_for(source, node.lineno),
                ))
    return findings


def _find_direct_llm_http_calls(path: str, source: str) -> list[StaticCheckFinding]:
    findings: list[StaticCheckFinding] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not DIRECT_HTTP_CALL_RE.search(line):
            continue
        if not LLM_API_HOST_RE.search(line):
            continue
        findings.append(StaticCheckFinding(
            rule="llm_api_http_call",
            path=path,
            line=line_no,
            message="禁止在 Skill 运行代码或 MCP server 中直连外部 LLM API",
            snippet=stripped[:240],
        ))
    return findings


def _find_platform_llm_gateway_imports(path: str, source: str) -> list[StaticCheckFinding]:
    findings: list[StaticCheckFinding] = []
    for match in PLATFORM_LLM_GATEWAY_RE.finditer(source):
        line_no = source.count("\n", 0, match.start()) + 1
        findings.append(StaticCheckFinding(
            rule="platform_llm_gateway_import",
            path=path,
            line=line_no,
            message="Skill 运行代码和 MCP server 不能绕过 SDK 直接调用平台 LLM 网关",
            snippet=_line_for(source, line_no),
        ))
    return findings


def scan_static_text(path: str, source: str) -> StaticCheckReport:
    """Scan a single code file for direct LLM SDK/API usage."""
    report = StaticCheckReport()
    report.extend(_find_blocked_imports(path, source))
    report.extend(_find_direct_llm_http_calls(path, source))
    report.extend(_find_platform_llm_gateway_imports(path, source))
    return report


def _is_code_path(path: str) -> bool:
    return Path(path).suffix in CODE_SUFFIXES


def scan_platform_mcp_static_checks(
    scripts_dir: Path | None = None,
) -> StaticCheckReport:
    """Scan repository-level scripts/*_mcp_server.py entrypoints."""
    report = StaticCheckReport()
    base = scripts_dir or Path(__file__).resolve().parents[2] / "scripts"
    if not base.is_dir():
        return report
    for path in sorted(base.glob("*_mcp_server.py")):
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.add(StaticCheckFinding(
                rule="static_check_read_error",
                path=str(path),
                line=0,
                message=f"静态检测读取文件失败: {exc}",
            ))
            continue
        report.extend(scan_static_text(f"scripts/{path.name}", source).findings)
    return report


def scan_skill_static_checks(
    skill_id: str,
    *,
    include_platform_mcp: bool = True,
    platform_scripts_dir: Path | None = None,
) -> StaticCheckReport:
    """Scan Skill repository code plus optional platform MCP server entrypoints."""
    report = StaticCheckReport()
    try:
        files = git_service.list_skill_files(skill_id)
    except Exception as exc:  # noqa: BLE001
        report.add(StaticCheckFinding(
            rule="static_check_inventory_error",
            path=skill_id,
            line=0,
            message=f"静态检测读取 Skill 文件清单失败: {exc}",
        ))
        files = []

    for rel_path in files:
        if not _is_code_path(rel_path):
            continue
        try:
            source = git_service.read_file(skill_id, rel_path, errors="replace") or ""
        except Exception as exc:  # noqa: BLE001
            report.add(StaticCheckFinding(
                rule="static_check_read_error",
                path=f"{skill_id}/{rel_path}",
                line=0,
                message=f"静态检测读取文件失败: {exc}",
            ))
            continue
        report.extend(scan_static_text(f"{skill_id}/{rel_path}", source).findings)

    if include_platform_mcp:
        report.extend(scan_platform_mcp_static_checks(platform_scripts_dir).findings)
    return report
