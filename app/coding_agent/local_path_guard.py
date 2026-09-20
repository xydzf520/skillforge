"""
Local path availability guard for coding-agent tasks.

The coding agent may run on a Linux server while the user's prompt refers to a
Windows-local path from another workstation. Without a preflight, the agent can
only search the server filesystem and may produce a misleading "not found" or
"deleted" conclusion.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable


PathExists = Callable[[Path], bool]


_WINDOWS_PATH_START_RE = re.compile(r"(?<![A-Za-z0-9])(?P<drive>[A-Za-z]):[\\/]")
_WINDOWS_PATH_STOP_CHARS = {
    '"', "'", "`", "\r", "\n", "<", ">", "|",
    "，", "。", "；", "：", "、", "”", "’",
}
_WINDOWS_PATH_TRAILING_CHARS = " \t,.;:，。；：、)]}）】》"


@dataclass(frozen=True)
class LocalPathAvailabilityIssue:
    """A prompt/tool references a local path unavailable from this host."""

    original_path: str
    mapped_path: str
    platform: str
    code: str = "CODING_AGENT_LOCAL_PATH_UNAVAILABLE"

    @property
    def message(self) -> str:
        return (
            f"检测到 Windows 本地路径 {self.original_path}，但当前执行环境是 {self.platform}，"
            f"且映射路径 {self.mapped_path} 不存在。当前服务器无法访问该目录，不能确认其中的 "
            "LICENSE/BSL/COPYING/NOTICE/版权/法律文件是否存在或已删除。"
        )

    def detail(self) -> dict:
        return {
            "detail": self.message,
            "original_path": self.original_path,
            "mapped_path": self.mapped_path,
            "platform": self.platform,
            "suggestions": [
                "在拥有该路径的 Windows 机器上运行 OpenCode/SkillForge agent。",
                "把 Windows C 盘挂载到当前 Linux 主机的 /mnt/c 后重试。",
                "把 golutra-master 目录上传或同步到当前服务器上的工作目录后重试。",
            ],
            "windows_checks": build_windows_license_check_commands(self.original_path),
        }


def find_first_unavailable_local_path(
    text: str,
    *,
    platform: str | None = None,
    path_exists: PathExists | None = None,
) -> LocalPathAvailabilityIssue | None:
    """Return an issue when *text* references a Windows path unavailable here.

    On Windows hosts this guard does not block: the path may be searchable by the
    local agent even if it needs to inspect parent directories first. On non-
    Windows hosts we allow a Windows path only when the common WSL-style mapping
    `/mnt/<drive>/...` exists.
    """
    if not text:
        return None

    platform_name = (platform or _current_platform()).lower()
    if platform_name.startswith(("win", "cygwin", "msys")):
        return None

    exists = path_exists or Path.exists
    for raw_path in iter_windows_absolute_paths(text):
        mapped_path = windows_path_to_wsl_path(raw_path)
        try:
            is_available = exists(mapped_path)
        except OSError:
            is_available = False
        if not is_available:
            return LocalPathAvailabilityIssue(
                original_path=raw_path,
                mapped_path=str(mapped_path),
                platform=platform_name,
            )
    return None


def iter_windows_absolute_paths(text: str) -> Iterable[str]:
    """Yield Windows absolute paths from free text.

    The scanner keeps spaces because Windows paths often contain names such as
    "Default Project", and stops at quotes/newlines/shell separators.
    """
    for match in _WINDOWS_PATH_START_RE.finditer(text):
        start = match.start()
        end = len(text)
        for idx in range(start, len(text)):
            if text[idx] in _WINDOWS_PATH_STOP_CHARS:
                end = idx
                break
        raw_path = text[start:end].strip().rstrip(_WINDOWS_PATH_TRAILING_CHARS)
        if raw_path:
            yield raw_path


def windows_path_to_wsl_path(path: str) -> Path:
    drive = path[0].lower()
    rest = path[2:].lstrip("\\/")
    win_rest = PureWindowsPath(rest).as_posix()
    return Path("/mnt") / drive / win_rest


def build_windows_license_check_commands(path: str) -> list[str]:
    escaped_path = path.rstrip("\\/")
    parent = str(PureWindowsPath(escaped_path).parent)
    if parent == ".":
        parent = escaped_path
    return [
        f'Get-ChildItem "{parent}" -Directory -Recurse -Filter "*golutra*"',
        (
            f'Get-ChildItem "{escaped_path}" -Recurse -Force | '
            "Where-Object { $_.Name -match 'license|licence|copying|notice|copyright|legal|bsl' }"
        ),
        (
            f'Select-String -Path "{escaped_path}\\*" '
            '-Pattern "BSL|Business Source License|license|copyright|legal|notice" -Recurse'
        ),
        (
            "git -C "
            f'"{escaped_path}" log --all --name-status -- '
            "'*LICENSE*' '*LICENCE*' '*COPYING*' '*NOTICE*' '*LEGAL*' '*COPYRIGHT*' '*BSL*'"
        ),
    ]


def _current_platform() -> str:
    if os.name == "nt":
        return "windows"
    return "linux"
