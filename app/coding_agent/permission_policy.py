"""
权限策略：决定 aiclawcode 子进程发起的工具调用是否放行。

三档策略：
- strict   - 所有写操作 / Bash / 网络调用都问用户
- balanced - skill/session 目录内 Read/Edit/Write/Bash 自动放行并通知，
             危险 Bash 问用户，越界一律拒绝（默认）
- loose    - skill 目录内全部自动放行，Bash 也默认放行，越界拒绝

越界判断使用 os.path.commonpath，严格防御 path traversal（../../etc/passwd 等）。
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterable, Literal

from app.coding_agent.local_path_guard import find_first_unavailable_local_path
from app.coding_agent.schemas import PermissionEvaluation


Strategy = Literal["strict", "balanced", "loose"]


# 工具分类
_READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "TodoRead"}
_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_EXEC_TOOLS = {"Bash"}
_NETWORK_TOOLS = {"WebFetch", "WebSearch"}
_INTROSPECTION_TOOLS = {"Task", "TaskOutput", "TaskList", "TaskGet", "TaskUpdate", "TaskStop", "TodoWrite"}

# ─────────────────────────────────────────────────────
# Bash 命令分级（balanced 策略下按危险等级细分权限）
# ─────────────────────────────────────────────────────

BASH_SAFE_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "wc", "echo", "pwd", "date",
    "grep", "find", "which", "env", "printenv",
}

BASH_MODERATE_COMMANDS: set[str] = {
    "cp", "mv", "mkdir", "touch", "chmod", "sed", "awk",
    "curl", "wget", "pip", "npm", "node", "python",
}

# 多词命令（如 "git push"）也在此集合中，_classify_bash_command 会做前缀匹配
BASH_DANGEROUS_COMMANDS: set[str] = {
    "rm", "rmdir", "kill", "pkill", "dd", "mkfs",
    "git push", "git reset", "npm publish", "twine upload",
    "gh release", "gh pr merge", "docker", "sudo",
    "systemctl", "service",
}


def _classify_bash_command(command: str) -> str:
    """将 bash 命令分类为 safe/moderate/dangerous。

    分类逻辑：
    1. 提取命令第一个词（去掉环境变量赋值前缀）
    2. 先检查多词命令（如 "git push"）的前缀匹配
    3. 再按单词匹配 safe → dangerous → moderate
    4. 未知命令默认 moderate（保守但不过度阻止）

    Args:
        command: 完整的 bash 命令字符串

    Returns:
        "safe" / "moderate" / "dangerous"
    """
    stripped = command.strip()
    if not stripped:
        return "moderate"

    # 检查多词危险命令的前缀匹配（如 "git push --force"）
    for dangerous_cmd in BASH_DANGEROUS_COMMANDS:
        if " " in dangerous_cmd and stripped.startswith(dangerous_cmd):
            return "dangerous"

    # 提取第一个词
    first_word = stripped.split()[0]

    # 跳过环境变量赋值前缀（如 "FOO=bar python script.py"）
    if "=" in first_word and not first_word.startswith("="):
        parts = stripped.split()
        for part in parts:
            if "=" not in part or part.startswith("="):
                first_word = part
                break

    if first_word in BASH_SAFE_COMMANDS:
        return "safe"
    if first_word in BASH_DANGEROUS_COMMANDS:
        return "dangerous"
    if first_word in BASH_MODERATE_COMMANDS:
        return "moderate"
    return "moderate"  # 默认中等


_SHELL_OPERATORS = {"|", "||", "&&", ";", "&", "(", ")", "{", "}"}


def _iter_bash_path_tokens(command: str) -> list[str]:
    """提取 Bash 命令中需要做边界检查的路径 token。

    只检查明确路径：绝对路径、~、./、../。URL / shell 操作符 / 重定向
    不按文件路径处理，避免误伤 curl/API/内联 Python 代码。
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return ["<parse-error>"]

    path_tokens: list[str] = []
    for part in parts:
        if not part or part.startswith("-"):
            continue
        if part in _SHELL_OPERATORS:
            continue
        if "://" in part:
            continue
        if part.startswith((">", "<")) or ">&" in part or part.endswith(">&1"):
            continue
        if part.startswith(("~", "/", "./", "../")) or part == "..":
            path_tokens.append(part)
    return path_tokens


def _bash_has_external_side_effect(command: str) -> bool:
    """识别会对真实外部系统产生写入/发布/删除的常见命令。"""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False

    idx = 0
    while idx < len(parts) and "=" in parts[idx] and not parts[idx].startswith("="):
        idx += 1
    argv = parts[idx:]
    if not argv:
        return False

    cmd = argv[0]
    joined = " ".join(argv[:3])
    if joined.startswith(("git push", "npm publish", "twine upload", "gh release", "gh pr merge")):
        return True
    if cmd in {"curl", "wget"}:
        lowered = [p.lower() for p in argv[1:]]
        write_methods = {"post", "put", "patch", "delete"}
        for i, part in enumerate(lowered):
            if part in {"-x", "--request"} and i + 1 < len(lowered) and lowered[i + 1] in write_methods:
                return True
            if part.startswith("-x") and part[2:].lower() in write_methods:
                return True
            if part.startswith("--request=") and part.split("=", 1)[1] in write_methods:
                return True
            raw_part = argv[i + 1]
            if part in {"-d", "--data", "--data-raw", "--data-binary", "--form"} or raw_part == "-F":
                return True
    return False


class PermissionPolicy:
    """单个 session 的权限决策器。skill_dir 是该会话允许操作的根目录。"""

    def __init__(
        self,
        skill_dir: Path | str,
        strategy: Strategy = "balanced",
        *,
        extra_allowed_roots: Iterable[Path | str] | None = None,
    ):
        self.skill_dir = Path(skill_dir).resolve()
        self.strategy = strategy
        roots = [self.skill_dir]
        for root in extra_allowed_roots or []:
            try:
                roots.append(Path(root).resolve())
            except (OSError, ValueError):
                continue
        self.allowed_roots = tuple(dict.fromkeys(roots))

    def evaluate(self, tool: str, tool_input: dict) -> PermissionEvaluation:
        """
        判断一次工具调用应当如何处置。

        返回 PermissionEvaluation:
            decision = "auto_allow" | "notify_allow" | "ask_user" | "deny"
            reason   = 简短说明
        """
        # 1. 越界检测（任何带 file_path / path 的工具）
        path_arg = self._extract_path(tool, tool_input)
        if path_arg is not None and not self._is_within_skill_dir(path_arg):
            return PermissionEvaluation(
                decision="deny",
                reason=f"path '{path_arg}' is outside skill directory",
            )

        # 2. 按工具类型 + strategy 决定
        if tool in _READ_ONLY_TOOLS or tool in _INTROSPECTION_TOOLS:
            return PermissionEvaluation(decision="auto_allow", reason=f"{tool} is read-only")

        if tool in _WRITE_TOOLS:
            if self.strategy == "strict":
                return PermissionEvaluation(decision="ask_user", reason=f"{tool} requires confirmation in strict mode")
            if self.strategy == "balanced":
                return PermissionEvaluation(decision="notify_allow", reason=f"{tool} auto-allowed (within skill dir, notify UI)")
            # loose
            return PermissionEvaluation(decision="auto_allow", reason=f"{tool} auto-allowed (loose mode)")

        if tool in _EXEC_TOOLS:
            cmd = tool_input.get("command", "")
            path_issue = find_first_unavailable_local_path(cmd)
            if path_issue is not None:
                return PermissionEvaluation(
                    decision="deny",
                    reason=path_issue.message,
                )
            level = _classify_bash_command(cmd)
            bad_path = self._first_bash_path_outside_allowed_roots(cmd)
            if bad_path is not None:
                return PermissionEvaluation(
                    decision="deny",
                    reason=f"Bash path '{bad_path}' is outside allowed skill/session directories",
                )
            if _bash_has_external_side_effect(cmd):
                return PermissionEvaluation(
                    decision="ask_user",
                    reason="Bash command may perform a real external side effect",
                )
            if level == "dangerous":
                return PermissionEvaluation(
                    decision="ask_user",
                    reason="Bash dangerous command requires user approval",
                )
            if self.strategy == "strict":
                return PermissionEvaluation(decision="ask_user", reason="Bash requires user approval in strict mode")
            if self.strategy == "balanced":
                return PermissionEvaluation(
                    decision="notify_allow",
                    reason=f"Bash {level} command auto-allowed inside skill/session boundary",
                )
            return PermissionEvaluation(
                decision="auto_allow",
                reason=f"Bash {level} command auto-allowed inside skill/session boundary",
            )

        if tool in _NETWORK_TOOLS:
            if self.strategy == "loose":
                return PermissionEvaluation(decision="notify_allow", reason=f"{tool} auto-allowed (loose mode)")
            return PermissionEvaluation(decision="ask_user", reason=f"{tool} requires user approval")

        # 3. MCP 工具（mcp__开头）
        if tool.startswith("mcp__"):
            if self.strategy == "loose":
                return PermissionEvaluation(decision="auto_allow", reason=f"MCP tool auto-allowed (loose mode)")
            if self.strategy == "balanced":
                return PermissionEvaluation(decision="notify_allow", reason=f"MCP tool notify-allowed (balanced mode)")
            return PermissionEvaluation(decision="ask_user", reason=f"MCP tool requires approval (strict mode)")

        # 4. 未知工具：保守 ask_user
        return PermissionEvaluation(decision="ask_user", reason=f"unknown tool '{tool}'")

    # ─────────────────────────────────────────────────────
    # 内部辅助
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_path(tool: str, tool_input: dict) -> str | None:
        """从工具 input 中提取文件路径参数（如有）。"""
        for key in ("file_path", "path", "notebook_path"):
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    def _is_within_allowed_roots(self, path_str: str, *, base_dir: Path | None = None) -> bool:
        """严格判断 path_str 是否在允许根目录之内（防御 ../ 攻击）。"""
        try:
            target = Path(path_str)
            if not target.is_absolute():
                target = ((base_dir or self.skill_dir) / target)
            target = target.resolve()
        except (OSError, ValueError):
            return False

        for root in self.allowed_roots:
            try:
                common = os.path.commonpath([str(root), str(target)])
            except ValueError:
                # commonpath raises ValueError on different drives (Windows) or empty
                continue
            if common == str(root):
                return True
        return False

    def _is_within_skill_dir(self, path_str: str) -> bool:
        return self._is_within_allowed_roots(path_str)

    def _first_bash_path_outside_allowed_roots(self, command: str) -> str | None:
        for raw_path in _iter_bash_path_tokens(command):
            if raw_path == "<parse-error>":
                return raw_path
            if not self._is_within_allowed_roots(raw_path):
                return raw_path
        return None
