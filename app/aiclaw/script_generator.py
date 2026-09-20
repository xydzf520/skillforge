"""生成可部署到内网机器的 bridge 脚本。

源码真源：`bridge/skillforgebridge.py`，里面用 `# =BEGIN-LINUX=` / `# =END-LINUX=`
和 `# =BEGIN-MACOS=` / `# =END-MACOS=` 注释块标记了平台专属代码。本模块在用户
下载时：
1. 按 `platform`（linux / darwin）裁剪掉不相关平台的块
2. 读裁剪后的源码
3. prepend 一段 `_BRIDGE_CONFIG` 常量（instance_id / enrollment_token / urls
   / BRIDGE_PLATFORM_TARGET）
4. 返回完整脚本

自动更新端点 `/api/aiclaw/bridge/script` 只返回裁剪后的主体（不含配置区），
bridge 客户端启动时用 STATE_DIR/env.json 里的 BRIDGE_PLATFORM_TARGET 决定走哪
一份自动更新。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.common.exceptions import AppError
from app.config import settings


BRIDGE_SOURCE_PATH = Path(__file__).resolve().parents[2] / "bridge" / "skillforgebridge.py"
_BRIDGE_VERSION_RE = re.compile(r'^BRIDGE_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _read_bridge_version() -> str:
    """Keep the platform updater version tied to the single-file Bridge source."""
    source = BRIDGE_SOURCE_PATH.read_text(encoding="utf-8")
    match = _BRIDGE_VERSION_RE.search(source)
    if not match:
        raise RuntimeError("bridge source is missing BRIDGE_VERSION")
    return match.group(1)


BRIDGE_VERSION = _read_bridge_version()

SUPPORTED_PLATFORMS = ("linux", "darwin")

# cache 按 (mtime_ns, platform) 维度，避免同一份源被重复裁剪
_SOURCE_CACHE: dict[tuple[str, str], str] = {}
_HASH_CACHE: dict[tuple[str, str], str] = {}


def _normalize_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p not in SUPPORTED_PLATFORMS:
        raise AppError(
            "INVALID_PLATFORM",
            400,
            {"detail": f"platform 必须是 {SUPPORTED_PLATFORMS} 之一，收到 {platform!r}"},
        )
    return p


def _strip_other_platform_blocks(source: str, platform: str) -> str:
    """从源码里删除"非目标平台"的注释块。

    规则：
    - `# =BEGIN-LINUX=` 到 `# =END-LINUX=` 之间的行属于 linux 块
    - `# =BEGIN-MACOS=` 到 `# =END-MACOS=` 之间的行属于 macos 块
    - 目标 platform=linux 时删 MACOS 块；darwin 时删 LINUX 块
    - BEGIN / END 标记本身也一并删掉，保持产物干净

    块不允许嵌套；未闭合会导致裁剪结果不正确 → 抛异常。
    """
    keep = "LINUX" if platform == "linux" else "MACOS"
    drop = "MACOS" if keep == "LINUX" else "LINUX"
    begin_drop = f"# =BEGIN-{drop}="
    end_drop = f"# =END-{drop}="
    begin_keep = f"# =BEGIN-{keep}="
    end_keep = f"# =END-{keep}="

    out: list[str] = []
    state = "normal"  # normal | dropping
    for idx, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if state == "normal":
            if stripped == begin_drop:
                state = "dropping"
                continue
            if stripped == end_drop:
                raise RuntimeError(f"bridge source:{idx} 出现未配对的 {end_drop}")
            if stripped in (begin_keep, end_keep):
                # 保留块的 BEGIN/END 标记也扔掉，让产物干净
                continue
            out.append(line)
        else:  # dropping
            if stripped == end_drop:
                state = "normal"
                continue
            if stripped == begin_drop:
                raise RuntimeError(f"bridge source:{idx} 出现嵌套的 {begin_drop}")
            # 块内其他行：丢掉
    if state != "normal":
        raise RuntimeError(f"bridge source: {begin_drop} 未配对 {end_drop}")
    return "\n".join(out)


def read_bridge_source(platform: str) -> str:
    """读取 + 按 platform 裁剪 bridge 真源。mtime 不变时走缓存。"""
    plat = _normalize_platform(platform)
    key = (str(BRIDGE_SOURCE_PATH.stat().st_mtime_ns), plat)
    cached = _SOURCE_CACHE.get(key)
    if cached is not None:
        return cached
    # 源码变更时，把老 mtime 对应的所有 platform 缓存都清掉
    current_mtime = key[0]
    _SOURCE_CACHE_keys = [k for k in _SOURCE_CACHE if k[0] != current_mtime]
    for k in _SOURCE_CACHE_keys:
        _SOURCE_CACHE.pop(k, None)
    _HASH_CACHE_keys = [k for k in _HASH_CACHE if k[0] != current_mtime]
    for k in _HASH_CACHE_keys:
        _HASH_CACHE.pop(k, None)
    raw = BRIDGE_SOURCE_PATH.read_text(encoding="utf-8")
    source = _strip_other_platform_blocks(raw, plat)
    _SOURCE_CACHE[key] = source
    return source


def compute_bridge_script_hash(platform: str) -> str:
    """bridge 源码的 sha256 前 16 位 hex（按 platform 分别计算）。"""
    plat = _normalize_platform(platform)
    key = (str(BRIDGE_SOURCE_PATH.stat().st_mtime_ns), plat)
    cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    source = read_bridge_source(plat)
    h = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    _HASH_CACHE[key] = h
    return h


def _format_py_literal(v) -> str:
    """把 None / str 格式化为合法 Python 字面量。"""
    if v is None:
        return "None"
    return repr(str(v))


def render(
    instance_id: str,
    enrollment_token: str,
    skillforge_ws_url: str,
    platform: str,
    local_aiclaw_url: str | None = None,
    local_aiclaw_token: str | None = None,
) -> str:
    """生成带 instance 配置 + 平台裁剪的 bridge 脚本供用户下载。"""
    plat = _normalize_platform(platform)
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    browser_url = f"{base}/api/browser/collect-remote" if base else ""
    browser_token = settings.BROWSER_REMOTE_TOKEN or ""

    # 从 ws url 派生 HTTP(S) 基址，供 bridge 自动更新使用
    if skillforge_ws_url.startswith("wss://"):
        http_base = "https://" + skillforge_ws_url[len("wss://"):]
    elif skillforge_ws_url.startswith("ws://"):
        http_base = "http://" + skillforge_ws_url[len("ws://"):]
    else:
        http_base = skillforge_ws_url
    # 去掉 /api/aiclaw/bridge/ws 后缀，得到纯 SkillForge server 根地址
    for suffix in ("/api/aiclaw/bridge/ws",):
        if http_base.endswith(suffix):
            http_base = http_base[: -len(suffix)]
            break

    config_lines = [
        f"# SkillForge bridge — instance={instance_id} platform={plat} version={BRIDGE_VERSION}",
        "# 本文件由 SkillForge server 在下载时生成。",
        "# - 配置区（下面）由下载流程填充，每次下载都会是最新 token。",
        "# - 主程序区（配置区之后）是 bridge 的真实源码，会在启动时自动从 server 拉取最新版本覆盖。",
        "",
        "# 兼容 macOS 自带 python3（3.9.x）— PEP 604 的 `X | None` 语法只当字符串注解。",
        "# `from __future__` 必须在文件所有 executable 语句之前，所以放在配置区最顶。",
        "from __future__ import annotations",
        "",
        "import os as _os",
        "_BRIDGE_CONFIG = {",
        f"    'INSTANCE_ID': {_format_py_literal(instance_id)},",
        f"    'ENROLLMENT_TOKEN': {_format_py_literal(enrollment_token)},",
        f"    'SKILLFORGE_WS_URL': {_format_py_literal(skillforge_ws_url)},",
        f"    'SKILLFORGE_HTTP_BASE': {_format_py_literal(http_base)},",
        f"    'LOCAL_AICLAW_URL': {_format_py_literal(local_aiclaw_url or settings.AICLAW_LOCAL_DEFAULT)},",
        f"    'LOCAL_AICLAW_TOKEN_FALLBACK': {_format_py_literal(local_aiclaw_token or '')},",
        f"    'SKILLFORGE_BROWSER_URL': {_format_py_literal(browser_url)},",
        f"    'SKILLFORGE_BROWSER_TOKEN': {_format_py_literal(browser_token)},",
        f"    'BRIDGE_VERSION_SEEN': {_format_py_literal(BRIDGE_VERSION)},",
        f"    'BRIDGE_PLATFORM_TARGET': {_format_py_literal(plat)},",
        "}",
        "for _k, _v in _BRIDGE_CONFIG.items():",
        "    if _v and not _os.environ.get(_k):",
        "        _os.environ[_k] = _v",
        "",
        "# === bridge 主程序源码（自动更新时会被 server 最新版覆盖）===",
        "",
    ]
    config_block = "\n".join(config_lines)

    source = read_bridge_source(plat)
    # 去掉源码的 shebang（我们要在最外层只留一个）
    lines = source.splitlines()
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
        body_lines = lines[1:]
    else:
        shebang = "#!/usr/bin/env python3"
        body_lines = lines

    # 去掉 body 里的 `from __future__ import annotations` —— 已在配置区最顶放过，
    # Python 规定 future import 整个文件只能出现在最开头一次，此处留着会 SyntaxError。
    body_lines = [
        ln for ln in body_lines
        if ln.strip() != "from __future__ import annotations"
    ]
    body = "\n".join(body_lines)

    return f"{shebang}\n{config_block}\n{body}\n"


def build_skillforge_bridge_ws_url() -> str:
    """根据 PUBLIC_BASE_URL 构造 bridge ws URL。

    - 用 rstrip + 拼接而非 urljoin，避免 urljoin 在子路径下丢前缀
    - http(s) 自动转 ws(s)
    """
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = base
    return f"{ws_base}/api/aiclaw/bridge/ws"
