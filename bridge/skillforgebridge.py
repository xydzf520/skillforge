#!/usr/bin/env python3
"""SkillForge Bridge — 跨平台 daemon + 系统托盘 + 自动更新。

一个脚本解决三件事：
1. **WebSocket 桥接**：内网 AIClaw/OpenClaw Gateway ↔ SkillForge server（双向请求/事件转发）
2. **自启动**：Linux (systemd) / macOS (launchd user agent) 一键注册，开机即用 + 崩溃自动重启
3. **自动更新**：启动时 + 每 1 小时查 server 端 bridge 源码 hash，不一致就替换自身并重启

运行模式：
    python3 skillforgebridge_<instance>.py            # 默认：一键 install —— 装依赖 + 注册 launchd/systemd + 后台拉起
    python3 skillforgebridge_<instance>.py --install  # 同上（显式）
    python3 skillforgebridge_<instance>.py --daemon   # 无头模式：只跑 bridge（systemd/launchd 启动时用）
    python3 skillforgebridge_<instance>.py --tray     # 前台托盘模式（调试用）
    python3 skillforgebridge_<instance>.py --update   # 立即检查并应用更新
    python3 skillforgebridge_<instance>.py --uninstall# 停止服务 + 删除自启动配置（保留 state）
    python3 skillforgebridge_<instance>.py --status   # 打印当前状态

配置来源（优先级从高到低）：
    1. env 变量（run-time）
    2. ~/.skillforge_bridge/<instance>/env.json（安装时持久化）
    3. 脚本顶部 _BRIDGE_CONFIG 常量（下载时 server 填入）
"""

# 让 `X | None` 这类 PEP 604 注解只做字符串，不在 runtime 求值 —
# 兼容 macOS 自带 python3（可能是 3.9.x，不支持 type | type）。
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import glob
import hashlib
import gzip
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import platform
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# ═════════════════════════════════════════════════════════════════════════════
#  配置区（env 注入，见文件顶部 _BRIDGE_CONFIG，由 server 下载时填入）
# ═════════════════════════════════════════════════════════════════════════════

BRIDGE_VERSION = "2.4.96"
UPDATE_HEADER_MARKER = "# === bridge 主程序源码"
TRAINING_ARTIFACT_DOWNLOAD_MAX_BYTES = int(
    os.getenv("SKILLFORGE_TRAINING_ARTIFACT_DOWNLOAD_MAX_BYTES", str(512 * 1024 * 1024))
)
TRAINING_MODEL_EXPORT_SESSION_TTL_SECONDS = int(
    os.getenv("SKILLFORGE_TRAINING_MODEL_EXPORT_SESSION_TTL_SECONDS", "3600")
)
TRAINING_MODEL_TRANSFER_CHUNK_BYTES = int(
    os.getenv("SKILLFORGE_TRAINING_MODEL_TRANSFER_CHUNK_BYTES", str(8 * 1024 * 1024))
)
OPENWEBUI_DEFAULT_MODEL_ID = "skillforge-ft-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
OPENWEBUI_DEFAULT_RUNTIME_PROFILE = "mlx-qwen3.6-35b-a3b-lora"
OPENWEBUI_DEFAULT_PORT = 18080
OPENWEBUI_MLX_PACKAGE = os.getenv("SKILLFORGE_OPENWEBUI_MLX_PACKAGE", "mlx-lm")
OPENWEBUI_MLX_QWEN35_MOE_PACKAGE = os.getenv("SKILLFORGE_OPENWEBUI_MLX_QWEN35_MOE_PACKAGE", "mlx-lm==0.30.7")
OPENWEBUI_MLX_INSTALL_TIMEOUT_SECONDS = int(os.getenv("SKILLFORGE_OPENWEBUI_MLX_INSTALL_TIMEOUT_SECONDS", "1800"))
OPENWEBUI_MLX_CONVERT_TIMEOUT_SECONDS = int(os.getenv("SKILLFORGE_OPENWEBUI_MLX_CONVERT_TIMEOUT_SECONDS", "21600"))
# 本地调试时未经 server render，没有 _BRIDGE_CONFIG，直接从 env 读（或默认）
INSTANCE_ID = os.getenv("INSTANCE_ID", "")
ENROLLMENT_TOKEN = os.getenv("ENROLLMENT_TOKEN", "")
SKILLFORGE_WS_URL = os.getenv("SKILLFORGE_WS_URL", "ws://127.0.0.1:8000/api/aiclaw/bridge/ws")
SKILLFORGE_HTTP_BASE = os.getenv("SKILLFORGE_HTTP_BASE", "http://127.0.0.1:8000")
LOCAL_AICLAW_URL = os.getenv("LOCAL_AICLAW_URL", "ws://127.0.0.1:18789")
LOCAL_AICLAW_TOKEN_FALLBACK = os.getenv("LOCAL_AICLAW_TOKEN_FALLBACK", "")
LOCAL_TRAINING_GATEWAY_URL = os.getenv("LOCAL_TRAINING_GATEWAY_URL", os.getenv("TRAINING_GATEWAY_URL", ""))
LOCAL_TRAINING_GATEWAY_TOKEN = os.getenv("LOCAL_TRAINING_GATEWAY_TOKEN", os.getenv("TRAINING_GATEWAY_TOKEN", ""))
LOCAL_INTELLIGENCE_GATEWAY_URL = os.getenv(
    "LOCAL_INTELLIGENCE_GATEWAY_URL",
    os.getenv("INTELLIGENCE_GATEWAY_URL", os.getenv("AGENT_ANALYSIS_GATEWAY_URL", "")),
)
LOCAL_INTELLIGENCE_GATEWAY_TOKEN = os.getenv(
    "LOCAL_INTELLIGENCE_GATEWAY_TOKEN",
    os.getenv("INTELLIGENCE_GATEWAY_TOKEN", os.getenv("AGENT_ANALYSIS_GATEWAY_TOKEN", "")),
)
MEDIA_COMFYUI_URL = os.getenv("SKILLFORGE_MEDIA_COMFYUI_URL", "http://127.0.0.1:8188").strip().rstrip("/")
MEDIA_TEMPLATE_DIR = Path(
    os.getenv("SKILLFORGE_MEDIA_TEMPLATE_DIR", str(Path.home() / "minimax-h3" / "workflows"))
).expanduser()
MEDIA_INPUT_DIR = Path(
    os.getenv("SKILLFORGE_MEDIA_INPUT_DIR", str(Path.home() / "minimax-h3" / "ComfyUI-app" / "input"))
).expanduser()
MEDIA_OUTPUT_DIR = Path(
    os.getenv("SKILLFORGE_MEDIA_OUTPUT_DIR", str(Path.home() / "minimax-h3" / "ComfyUI-app" / "output"))
).expanduser()
MEDIA_LOCAL_EDIT_RUNNER = Path(
    os.getenv("SKILLFORGE_MEDIA_LOCAL_EDIT_RUNNER", "")
).expanduser() if os.getenv("SKILLFORGE_MEDIA_LOCAL_EDIT_RUNNER", "").strip() else None
MEDIA_SAM2_ROOT = Path(os.getenv("SKILLFORGE_MEDIA_SAM2_ROOT", "")).expanduser() if os.getenv("SKILLFORGE_MEDIA_SAM2_ROOT", "").strip() else None
MEDIA_PROPAINTER_ROOT = Path(os.getenv("SKILLFORGE_MEDIA_PROPAINTER_ROOT", "")).expanduser() if os.getenv("SKILLFORGE_MEDIA_PROPAINTER_ROOT", "").strip() else None
MEDIA_MODEL_ROOTS = [
    Path(item).expanduser()
    for item in os.getenv(
        "SKILLFORGE_MEDIA_MODEL_ROOTS",
        str(Path.home() / "minimax-h3" / "ComfyUI" / "models"),
    ).split(os.pathsep)
    if item.strip()
]
# BRIDGE_PLATFORM_TARGET 由 server render 时注入，决定自动更新调用哪个平台的接口。
# 下载该脚本时用户选了 linux 就是 linux，选了 macOS 就是 darwin —— bridge 绝不会跨平台升级。
BRIDGE_PLATFORM_TARGET = os.getenv("BRIDGE_PLATFORM_TARGET", "")

# 每个 instance 独立 STATE_DIR，同一台主机多 bridge 不冲突
# 路径只含 ASCII：中文/特殊 instance_id 用 md5 前8位 slug 替代，避免 systemd / venv 路径问题
_INSTANCE_SLUG = hashlib.md5((INSTANCE_ID or "default").encode()).hexdigest()[:8]
STATE_DIR = Path.home() / ".skillforge_bridge" / _INSTANCE_SLUG
LEGACY_STATE_DIR = Path.home() / ".skillforge_bridge" / (INSTANCE_ID or "default")
KEY_PATH = STATE_DIR / "device.key"
KEY_OLD_PATH = STATE_DIR / "device.key.old"
ENV_PATH = STATE_DIR / "env.json"       # 安装时持久化所有运行参数
LOG_PATH = STATE_DIR / "bridge.log"     # 运行日志
PID_PATH = STATE_DIR / "bridge.pid"     # 当前进程 pid（tray/daemon 共用）
LOCK_PATH = STATE_DIR / "bridge.lock"   # 单实例进程锁，避免多个自启动入口互抢同一 instance
SCRIPT_CACHE_PATH = STATE_DIR / "bridge.py"       # 已安装的主脚本（自动更新覆盖这里）
ICON_CACHE_PATH = STATE_DIR / "icon.png"          # 解码后的 PNG，pystray 用
AUTOSTART_LAUNCHER_PATH = STATE_DIR / "bridge_autostart.sh"  # crontab fallback 时调用


def _migrate_legacy_state_dir() -> None:
    """兼容 2.3.0 之前按 instance_id 命名的 state 目录。

    新版本用 md5 slug 避免中文/特殊字符进入 systemd 路径；老节点已有的
    device.key 必须迁移，否则服务重启后会生成新 key，服务端看到 pubkey
    mismatch，bridge 永远连不上。
    """
    if not INSTANCE_ID or LEGACY_STATE_DIR == STATE_DIR or not LEGACY_STATE_DIR.exists():
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("device.key", "device.key.old", "env.json", "schedules.json", "icon.png"):
            src = LEGACY_STATE_DIR / name
            dst = STATE_DIR / name
            if not src.exists() or src.is_symlink():
                continue
            # device.key 是身份根，迁移时以 legacy 为准；其他文件不覆盖新状态。
            if name != "device.key" and dst.exists():
                continue
            shutil.copy2(src, dst)
            if name.startswith("device.key"):
                os.chmod(dst, 0o600)
    except Exception:
        pass


_migrate_legacy_state_dir()

MEDIA_BOOTSTRAP_PROFILE = "h3_all_modes_v1"
MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS = 3
MEDIA_BOOTSTRAP_ROOT = STATE_DIR / "media"
MEDIA_BOOTSTRAP_STATUS_PATH = MEDIA_BOOTSTRAP_ROOT / "bootstrap.json"
MEDIA_BOOTSTRAP_COMFY_DIR = MEDIA_BOOTSTRAP_ROOT / "ComfyUI"
MEDIA_BOOTSTRAP_VENV_DIR = MEDIA_BOOTSTRAP_ROOT / "venv"
MEDIA_BOOTSTRAP_MODEL_DIR = MEDIA_BOOTSTRAP_ROOT / "models"
MEDIA_BOOTSTRAP_INPUT_DIR = MEDIA_BOOTSTRAP_COMFY_DIR / "input"
MEDIA_BOOTSTRAP_OUTPUT_DIR = MEDIA_BOOTSTRAP_COMFY_DIR / "output"
MEDIA_BOOTSTRAP_COMFY_TAG = "v0.31.0"
MEDIA_BOOTSTRAP_COMFY_REPO = "https://github.com/Comfy-Org/ComfyUI.git"
MEDIA_BOOTSTRAP_MODEL_REPO_REVISION = "014cd40f7e177756c6b2473c0d93b1c89a790dd2"
MEDIA_BOOTSTRAP_LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
MEDIA_BOOTSTRAP_RUNTIME_PACKAGES = ("imageio-ffmpeg==0.6.0",)
MEDIA_BOOTSTRAP_MODELS = (
    {
        "path": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "size": 20970379616,
        "sha256": "e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a",
    },
    {
        "path": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "size": 20970379616,
        "sha256": "9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779",
    },
    {
        "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "size": 15687142551,
        "sha256": "35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6",
    },
    {
        "path": "vae/minimax_h3_audio_vae_fp32.safetensors",
        "size": 605254808,
        "sha256": "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48",
    },
    {
        "path": "vae/minimax_h3_video_vae_fp16.safetensors",
        "size": 5207808496,
        "sha256": "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522",
    },
)
if MEDIA_BOOTSTRAP_MODEL_DIR not in MEDIA_MODEL_ROOTS:
    MEDIA_MODEL_ROOTS.append(MEDIA_BOOTSTRAP_MODEL_DIR)
MEDIA_RUNTIME_MODEL_DIR = MEDIA_INPUT_DIR.parent / "models"
if MEDIA_RUNTIME_MODEL_DIR not in MEDIA_MODEL_ROOTS:
    MEDIA_MODEL_ROOTS.append(MEDIA_RUNTIME_MODEL_DIR)

_MEDIA_BOOTSTRAP_LOCK = threading.Lock()
_MEDIA_BOOTSTRAP_CANCEL = threading.Event()
_MEDIA_BOOTSTRAP_THREAD = None
_MEDIA_BOOTSTRAP_PROCESS = None
MEDIA_BOOTSTRAP_PARALLEL_THRESHOLD_BYTES = 256 * 1024 * 1024
MEDIA_BOOTSTRAP_RANGE_CHUNK_BYTES = 64 * 1024 * 1024
MEDIA_BOOTSTRAP_PARALLELISM = 8
MEDIA_BOOTSTRAP_RESUMABLE_STATUSES = {
    "preflighting",
    "preparing_comfyui",
    "installing",
    "downloading",
    "verifying",
    "configuring",
    "starting",
    "self_testing",
}
MEDIA_IDLE_RELEASE_MIN_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("SKILLFORGE_MEDIA_IDLE_RELEASE_MIN_INTERVAL_SECONDS", "60")),
)
MEDIA_IDLE_RELEASE_VRAM_THRESHOLD_MB = max(
    512,
    int(os.getenv("SKILLFORGE_MEDIA_IDLE_RELEASE_VRAM_THRESHOLD_MB", "2048")),
)

_MEDIA_IDLE_RELEASE_LOCK = threading.Lock()
_MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC = 0.0

UPDATE_INTERVAL_SECONDS = 3600          # 运行中每 1h 检查一次更新
UPDATE_TIMEOUT_SECONDS = 10             # HTTP 请求超时
BRIDGE_WS_CHUNK_THRESHOLD_BYTES = 256 * 1024
BRIDGE_WS_CHUNK_TARGET_BYTES = 192 * 1024

# ═════════════════════════════════════════════════════════════════════════════
#  嵌入资源：128x128 SkillForge 模块化 SK 图标，由 render_brand_assets.mjs 生成
# ═════════════════════════════════════════════════════════════════════════════

ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAACXBIWXMAADsOAAA7DgHMtqGDAAAI"
    "7UlEQVR4nO2da2xdxRHHj6PSTyGXMyYBCSNoaL+gSoDsu3N9vetLKAiK1JaQ8IhUQksj8oWHQAGE"
    "yItX25AmLf1Apao0gkAAIdQiCBIqr6S0CAgNYMIb8zBIJMFxEg6QBOGt1nYNlevG9+5jzp4zK81X"
    "3znz/3lnd2f2nCTxPI7o7p4FUswHKVaAEvekUrwAEvtBiV2g8AAo1CW3AyOxkNg/EhuFG0CK5aBw"
    "noldEuNo7+2qpgrXgsQ+UDicgyDHasOjMRRr2uvVriTPA4SYkSqxJJXi1RwErpCWKtxmYtxerx+a"
    "5GXMqNUgVXj92JROHqRymNiVKlxZkTKl1L4NesRCUGIHfUDKamIQevHyJEmmBVV+ZqP2XZDiH/QB"
    "YINREJ6eUa8fF0R86KnOBYlDHHzMFYCpEntTief71H5aKvG31A/KhgdLC2vcp4Tjj//2yP6UBdBR"
    "xEDi3Uln5yHuxJdiI/lDsekmIXjYBQRtqcI7OPgYK4AbrNIB53yM3ySubkl8kOIccufZtIsYpFIs"
    "aEp8s6cEibtZACwGhBJ3V2TX7Knq38aHPFhECDYbbQ+qfqpqi8idZdN+YlC98P9P/bUagMSdLAAW"
    "EsJUie2VxgmHTf7fL8UN1E6yod8YSLF80no+l3SxBACKwf/ZT2AaDeidY4MAMUglXjlxBhht32IR"
    "VDk6iyb08FE7xYZBY1Dp7T7pG9M/rmUBsLxHxKDwFXKH2HTgGGz9um+fW7d1CQEcnt7oPNwUfebn"
    "wBk2RRCDXjzb5P+VLACWFcBlibmulQNH2BRBDKS4y8wAW1gALCWAqcTnzAzwHrUjbEgTA4n9BoBB"
    "FgDLCaHEneYMYD+5I2yaBgCxL/H1x2fNqetfvf+UvmbgMTL79euPNe139uW+psxFrBbftKLp3/2m"
    "/eGB+1r+bW8AXL3xTlLxlw08oauLFuQegNoF5+sdnw61LP5TW7foI0+R+QLABN4IQAmAAbAV37OA"
    "AHScdrLe+vbrLYv/xofv6+/9+HQrH7wAYKZeSvFN6jEpKO8ArHv4ry2L/0m2R5+86EJrrZwDcNEf"
    "V5OKf+3A4/rMpVe07H8WCACbvP/pgS/0z1de50QvpwDM/skZ+oaBTaQA3Pjsg1bPkAUAwDbvr7rj"
    "T840cwqACT6l+AY+A2GeAeiwzPsb/7lZz2x05w8AM+2a6ZcSAJN+bJ8j8wyATd5/uf9NfewPT3Um"
    "vjMAYt3zQ2AAbPL+9j27NP70PKfiOwMg1j0/BATAJu/v3f+5Pvfq1he2XgGIec8PgQCwzftLb7vV"
    "i/hOAIh5zw+BALDJ+/c//qhu763lF4CY9/wQAACbvP/sa336qNMa3sSPHgDbPT94BsAm7w8M7tAn"
    "nnOWV/GjBsDFnh88AmCT94e+yPSZl1zsXfyoAXCx5wePANjk/ctW3RxEfCcAuFws5cEyBwDY5H2b"
    "2j4DkAMAahZ537a2zwAQA9Bhkfdd1PYZAGIA1rWY913V9hkAQgAWt5j3Xdb2GQAiAGoWed9lbZ8B"
    "IACgwyLvu67tMwAEAKxrMe+b2v4xZ/yAVHwn5wAPPPm3lgJgawuuXeIlIFkA333V9kkA+P68H408"
    "UGgA3vzoA3306XOiA2Cvx9o+CQDGrvrdb0hmgVvvXR8dAEs91vbJADALmWe2vRQcgD37P9eNXyyM"
    "BoD7Pdf2yQAwZoQwgoSGwIDnciWdefIzRG2fFABjZkoODYAxk4LyDMBAoNo+OQBmUWYWZ6EBMItQ"
    "sxjNIwBDAWv75AAYM9szilnAbEfzCMBlAWv7uQAg9rOBzLFPl9/yy/IBEPPZQOYjBVy6uFwAxHw2"
    "kHnwqTSLwCKcDWQet4GmcFQaAGI9G8g8+lXog6CinA1knv1adtvvywVAbGcDmWe/ClkMKtLZQBYI"
    "TtNFVBoAfNhUg53XhpC+d99y/rIHBoAAgA6LlrBHnvl7/C1hZZ8BwLYp9M7bGYDYAYAyt4XzDIDj"
    "MWh1PTD42V49Z9HPGICYZwAo69UwngHQ3eXQF1+I73IoA4BOr4f/+aG/FA8AqoOg0CkAHL0gImQP"
    "QWGPgqkB6LB9RUygHoLCFoOoAYCyvCQqj+XgvAAADl4T57uHoHANIaGLQVD2F0XmrSUsdDkYArwq"
    "1mcPQaGaQikaQqDsL4vOU1s4RUsYBHxdvI8egsJcDKFqCoUm/77NesBHD0EhroZRtoVDk3/fdj3g"
    "uoegEJdDKS+GAMVHoxz2EER/PZz6ahi0+DuF+mxcrHt+IATAdj3gqocg6lfE5OF6OJT907Ex7/mB"
    "GAAnH4+27CGI9jVxeXlFDOTg8/E2PQQGgP2ugseGccVAin0JKDFI7gibpgEAdxoA3mMBsJwQSuxP"
    "UoVbyB1h0xQxSCU+Z9YAG1gALCmEYn0CUqygd4QNaGKw1MwA81gALCeEPdW5yRHd3bNA4TC5M2w6"
    "cAy+mt7oPDwxAyT2sQBYLgil+Ffyn5EqXEvuEJsOCwDeMg5Ae73axQJgqSCs1KsnjgPAaQBLZanC"
    "bf8l/mgaEEuoHWPDMABIvHICAO31+qGgxC4WAQsOohg0Wk8AYGwxeD29g2zgNwbLkslGRcoUlNjB"
    "ImAhQUyl+Dg9tbMyKQAji8EecRG1o2zoJQbtUlyQTGG0gRJPswhYLBAlbjLaTgWAZEa9fhxI3E3u"
    "NJt2JP5QRXbNnpL446lAivksABYDwl48uynxxyFQYg2582zaMgarEovRlkqxjkXAOEGUeHeSJNNs"
    "AEiSzs5DQIqN5A/DppuMwUNGu8TJaDS+lSq8nUXASEAU692J//VoA4mr6R+ODSaPwfBYzp/adq+V"
    "0S7FWVwzwNyBmCrck/bWzk1CDLOnBImbqR+aDUdjIHFTRYnvJIFHG/SIhakS21kIJIJRDKYSL/Y6"
    "5R9sVBonHAZSLOcrZhhOeImfmKreQQs7IcfMRmO6aTQAha/wjIC+hO8DKa4wsU7yPCq93SeN7Rhe"
    "NO3HDAS2KrqJ3VbTwDmhhy+WYXrPzVm0uYViTqZShc+DwnfGUgZfTVcmBiOxeGckNlLcNRKrnurc"
    "8b59j+PfJvnEBxRS9OYAAAAASUVORK5CYII="
)


def _bridge_transport_blob(value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) >= BRIDGE_WS_CHUNK_THRESHOLD_BYTES and "content_base64" not in value:
        compressed = gzip.compress(raw, compresslevel=6)
        if len(compressed) < len(raw):
            return "gzip+base64", base64.b64encode(compressed).decode("ascii")
    return "json", raw.decode("utf-8")


def _bridge_transport_chunks(value):
    encoding, blob = _bridge_transport_blob(value)
    if len(blob.encode("utf-8")) <= BRIDGE_WS_CHUNK_THRESHOLD_BYTES:
        return encoding, [blob]
    return encoding, [
        blob[index:index + BRIDGE_WS_CHUNK_TARGET_BYTES]
        for index in range(0, len(blob), BRIDGE_WS_CHUNK_TARGET_BYTES)
    ]


def _bridge_transport_decode(parts, chunk_count, encoding):
    if chunk_count <= 0:
        raise ValueError("chunk_count must be positive")
    missing = [idx for idx in range(chunk_count) if idx not in parts]
    if missing:
        raise ValueError(f"missing chunk indexes: {missing[:5]}")
    blob = "".join(parts[idx] for idx in range(chunk_count))
    if encoding == "gzip+base64":
        raw = gzip.decompress(base64.b64decode(blob))
    elif encoding == "json":
        raw = blob.encode("utf-8")
    else:
        raise ValueError(f"unsupported encoding: {encoding}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("decoded payload must be object")
    return value


def _write_icon_to_cache() -> Path:
    """写入当前品牌图标；升级后替换旧缓存，无变化时不重复写入。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        icon_bytes = base64.b64decode(ICON_PNG_B64)
        if not ICON_CACHE_PATH.exists() or ICON_CACHE_PATH.read_bytes() != icon_bytes:
            ICON_CACHE_PATH.write_bytes(icon_bytes)
    except Exception:  # noqa: BLE001
        pass
    return ICON_CACHE_PATH


# ═════════════════════════════════════════════════════════════════════════════
#  systemd / launchd 自启动单元模板
# ═════════════════════════════════════════════════════════════════════════════

# =BEGIN-LINUX=
SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=SkillForge Bridge - {instance_id}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Type=simple
ExecStart={python_quoted} {script_quoted} --daemon
Restart=always
RestartSec=5
StandardOutput=append:{log}
StandardError=append:{log}
Environment="SKILLFORGE_BRIDGE_DAEMON=1"

[Install]
WantedBy=default.target
"""
# =END-LINUX=

# =BEGIN-MACOS=
LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.skillforge.bridge.{instance_id}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <!-- SSH/headless 下 user/UID 往往是 Background session；显式允许两种 session。 -->
    <key>LimitLoadToSessionType</key>
    <array>
        <string>Background</string>
        <string>Aqua</string>
    </array>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>SKILLFORGE_BRIDGE_DAEMON</key>
        <string>1</string>
    </dict>
</dict>
</plist>
"""
# =END-MACOS=

# ═════════════════════════════════════════════════════════════════════════════
#  延迟导入第三方依赖（bootstrap 之后才安全调用）
# ═════════════════════════════════════════════════════════════════════════════

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    import websockets
    _CORE_DEPS_OK = True
except ImportError:
    # 这个分支只在 bootstrap 前命中；bootstrap 后会 re-exec 重新进来
    serialization = None  # type: ignore[assignment]
    Ed25519PrivateKey = None  # type: ignore[assignment]
    load_pem_private_key = None  # type: ignore[assignment]
    websockets = None  # type: ignore[assignment]
    _CORE_DEPS_OK = False


def _local_config_dir_order() -> list[str]:
    """按当前本地 gateway 进程推断优先读取 .aiclaw 还是 .openclaw。"""
    order: list[str] = []

    def add(value: str) -> None:
        if value and value not in order:
            order.append(value)

    try:
        import glob
        for proc_dir in glob.glob("/proc/[0-9]*"):
            try:
                cmd = (Path(proc_dir) / "cmdline").read_bytes().replace(b"\0", b" ").lower()
                environ = (Path(proc_dir) / "environ").read_bytes()
                cwd = os.readlink(Path(proc_dir) / "cwd")
                blob = cmd + b" " + environ
                if b"OPENCLAW_GATEWAY_TOKEN=" in blob or "/.openclaw/" in cwd or cwd.endswith("/.openclaw"):
                    add(".openclaw")
                if b"AICLAW_GATEWAY_TOKEN=" in blob or "/.aiclaw/" in cwd or cwd.endswith("/.aiclaw"):
                    add(".aiclaw")
            except Exception:
                continue
    except Exception:
        pass

    add(".openclaw")
    add(".aiclaw")
    return order


def discover_local_aiclaw_token() -> str:
    """兼容 AIClaw 与 openclaw-cn 两种 gateway。

    两边的 WebSocket 协议（PROTOCOL_VERSION=3 / client.id=gateway-client / role=operator）
    完全一致，差异仅在 token 来源（env 名 / config 路径 / 进程名）。每次连接都重新发现，
    AIClaw/openclaw 重启换 token 也自动跟上。
    """
    for env_key in ("LOCAL_AICLAW_TOKEN", "AICLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_TOKEN"):
        v = os.environ.get(env_key)
        if v:
            return v
    try:
        import glob
        for environ_path in glob.glob("/proc/*/environ"):
            try:
                data = open(environ_path, "rb").read()
                if b"AICLAW_GATEWAY_TOKEN=" not in data and b"OPENCLAW_GATEWAY_TOKEN=" not in data:
                    continue
                for kv in data.split(b"\0"):
                    if kv.startswith(b"AICLAW_GATEWAY_TOKEN=") or kv.startswith(b"OPENCLAW_GATEWAY_TOKEN="):
                        return kv.split(b"=", 1)[1].decode()
            except Exception:
                continue
    except Exception:
        pass
    for cfg_dir in _local_config_dir_order():
        for cfg_name in ("aiclaw.json", "openclaw.json"):
            cfg_path = Path.home() / cfg_dir / cfg_name
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    t = (cfg.get("gateway") or {}).get("auth", {}).get("token") or (cfg.get("auth") or {}).get("token")
                    if t:
                        return t
                except Exception:
                    pass
    return LOCAL_AICLAW_TOKEN_FALLBACK


def discover_local_device_identities() -> list[dict]:
    """读取本机 OpenClaw/AIClaw 设备身份候选。

    OpenClaw 在 gateway token 握手时仍会按 device.id 绑定设备身份；缺少该字段时
    会把会话当作未绑定 operator 并清空 scopes，导致 agents.list 缺 operator.read。
    """
    identities: list[dict] = []
    seen: set[str] = set()
    for cfg_dir in _local_config_dir_order():
        identity_path = Path.home() / cfg_dir / "identity" / "device.json"
        if identity_path.exists():
            try:
                data = json.loads(identity_path.read_text(encoding="utf-8"))
                device_id = str(data.get("deviceId") or data.get("device_id") or data.get("id") or "").strip()
                public_key_pem = str(data.get("publicKeyPem") or "").strip()
                private_key_pem = str(data.get("privateKeyPem") or "").strip()
                if device_id and public_key_pem and private_key_pem:
                    key = f"{device_id}:{public_key_pem}"
                    if key in seen:
                        continue
                    seen.add(key)
                    identities.append({
                        "device_id": device_id,
                        "public_key_pem": public_key_pem,
                        "private_key_pem": private_key_pem,
                        "source_dir": cfg_dir,
                    })
            except Exception:
                pass
    return identities


def discover_local_device_identity() -> dict:
    identities = discover_local_device_identities()
    return identities[0] if identities else {}


def discover_local_device_id() -> str:
    """返回设备 id；没有完整身份时退化读取 paired.json，仅用于诊断显示。"""
    for env_key in ("LOCAL_AICLAW_DEVICE_ID", "AICLAW_DEVICE_ID", "OPENCLAW_DEVICE_ID"):
        v = os.environ.get(env_key, "").strip()
        if v:
            return v

    identity = discover_local_device_identity()
    if identity.get("device_id"):
        return str(identity["device_id"])

    for cfg_dir in (".openclaw", ".aiclaw"):
        paired_path = Path.home() / cfg_dir / "devices" / "paired.json"
        if paired_path.exists():
            try:
                data = json.loads(paired_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, dict):
                            v = str(value.get("deviceId") or value.get("device_id") or value.get("id") or "").strip()
                            if v:
                                return v
                        if isinstance(key, str) and key.strip():
                            return key.strip()
            except Exception:
                pass

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


def build_local_device_auth(
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
    identity = identity or discover_local_device_identity()
    if not identity:
        return None

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


def load_or_create_key():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        return load_pem_private_key(KEY_PATH.read_bytes(), password=None)

    private_key = Ed25519PrivateKey.generate()
    KEY_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(KEY_PATH, 0o600)
    return private_key


def export_pubkey(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("utf-8")


def rotate_key_material():
    if KEY_PATH.exists():
        KEY_OLD_PATH.write_bytes(KEY_PATH.read_bytes())
    private_key = Ed25519PrivateKey.generate()
    KEY_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(KEY_PATH, 0o600)
    return private_key


async def _connect_local_with_identity(identity: dict | None):
    local_token = discover_local_aiclaw_token()
    ws = await websockets.connect(
        LOCAL_AICLAW_URL,
        open_timeout=10,
        close_timeout=5,
        max_size=64 * 1024 * 1024,
        ping_interval=None,
    )
    try:
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        except asyncio.TimeoutError:
            return ws
        if first.get("event") == "connect.challenge":
            nonce = first.get("payload", {}).get("nonce", "")
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
                    "displayName": "SkillForge Bridge",
                    "version": "1.7.0",
                    "platform": client_platform,
                    "mode": client_mode,
                },
                "caps": [],
                "role": role,
                "scopes": scopes,
            }
            if local_token:
                params["auth"] = {"token": local_token}
            device = build_local_device_auth(
                identity=identity,
                nonce=nonce,
                token=local_token,
                client_id=client_id,
                client_mode=client_mode,
                role=role,
                scopes=scopes,
                client_platform=client_platform,
            )
            if device:
                params["device"] = device
            else:
                # 没有完整加密身份时仍补上 device.id，避免 OpenClaw 把会话当作
                # 未绑定 operator 并清空 scopes（导致 agents.list 缺 operator.read）
                bare_id = discover_local_device_id()
                if bare_id:
                    params["device"] = {"id": bare_id}
            connect_id = f"bridge-{nonce[:8]}"
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "method": "connect",
                        "id": connect_id,
                        "params": params,
                    }
                )
            )
            deadline = time.time() + 10
            while True:
                timeout = max(0.1, deadline - time.time())
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if ack.get("type") != "res" or ack.get("id") != connect_id:
                    continue
                if not ack.get("ok", False):
                    raise RuntimeError(f"local connect failed: {ack.get('error')}")
                break
        return ws
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
        raise


async def connect_local():
    identities = discover_local_device_identities()
    if not identities:
        return await _connect_local_with_identity(None)

    last_error: Exception | None = None
    for identity in identities:
        try:
            return await _connect_local_with_identity(identity)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return await _connect_local_with_identity(None)


async def _call_local_gateway(method: str, params: dict, timeout: int = 60):
    from uuid import uuid4

    ws = await connect_local()
    request_id = f"bridge-local-{uuid4().hex[:12]}"
    try:
        await ws.send(json.dumps({
            "type": "req",
            "method": method,
            "id": request_id,
            "params": params,
        }, ensure_ascii=False))
        deadline = time.time() + max(1, int(timeout))
        while True:
            remaining = max(0.1, deadline - time.time())
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if msg.get("type") != "res" or msg.get("id") != request_id:
                continue
            ok = msg.get("ok", msg.get("error") is None)
            result = msg.get("result", msg.get("payload", {}))
            error = msg.get("error")
            return bool(ok), result if isinstance(result, dict) else {"output": result}, error
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  节点调度器（node scheduler）— cron 匹配 + 定时执行 + 结果提交
# ═════════════════════════════════════════════════════════════════════════════

SCHEDULES_PATH = STATE_DIR / "schedules.json"
_SCHEDULE_CHECK_INTERVAL = 30
_SCHEDULE_MISSED_GRACE_SECONDS = 300
_schedule_reload_event = None
SUPPORTED_RUNTIMES = ("bridge_script", "openclaw_agent", "hybrid")
_scheduler_state: dict = {
    "started_at": None,
    "last_tick_at": None,
    "last_error": None,
    "last_config_version": None,
    "last_fired": {},
}
_PROCESS_LOCK_FH = None


def _get_schedule_reload_event():
    global _schedule_reload_event
    current_loop = asyncio.get_running_loop()
    event_loop = getattr(_schedule_reload_event, "_loop", None)
    if _schedule_reload_event is None or (
        event_loop is not None and event_loop is not current_loop
    ):
        _schedule_reload_event = asyncio.Event()
    return _schedule_reload_event


def _field_match(field: str, value: int, min_val: int, max_val: int) -> bool:
    """匹配 cron 单字段。支持 *, */N, N-M, N-M/N, N,M,O, 字面值。"""
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        # */N 或 *
        if part.startswith("*"):
            if part == "*":
                return True
            if part.startswith("*/"):
                try:
                    step = int(part[2:])
                except ValueError:
                    continue
                if step <= 0:
                    continue
                if (value - min_val) % step == 0:
                    return True
            continue
        # 范围: N-M 或 N-M/N
        if "-" in part:
            range_part, _, step_part = part.partition("/")
            bounds = range_part.split("-", 1)
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except (ValueError, IndexError):
                continue
            lo = max(lo, min_val)
            hi = min(hi, max_val)
            if lo > hi:
                continue
            step = 1
            if step_part:
                try:
                    step = int(step_part)
                except ValueError:
                    continue
                if step <= 0:
                    continue
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
            continue
        # 字面值
        try:
            if int(part) == value:
                return True
        except ValueError:
            continue
    return False


def _cron_match(expr: str, dt: datetime) -> bool:
    """5 字段标准 cron 匹配 (minute hour dom month dow)。dow: 0=Sun..6=Sat, 7 也视为 Sun。"""
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    if not _field_match(minute, dt.minute, 0, 59):
        return False
    if not _field_match(hour, dt.hour, 0, 23):
        return False
    if not _field_match(dom, dt.day, 1, 31):
        return False
    if not _field_match(month, dt.month, 1, 12):
        return False
    # dow: 0=Sun, 1=Mon, ..., 6=Sat；Python isoweekday(): Mon=1..Sun=7
    py_dow = dt.isoweekday() % 7  # Mon=1..Sat=6, Sun=0
    # 接受 7 也代表 Sun: 替换 7→0 再匹配
    dow_normalized = dow.replace("7", "0")
    if not _field_match(dow_normalized, py_dow, 0, 6):
        return False
    return True


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _cron_expected_minutes(
    expr: str,
    now: datetime,
    lookback_seconds: int,
    *,
    after: datetime | None = None,
) -> list[datetime]:
    """Return due cron minutes from a bounded window through now.

    ``after`` is exclusive. It prevents a freshly pushed schedule from
    backfilling cron minutes that were already in the past when the node
    accepted the config.
    """
    if lookback_seconds <= 0:
        current = _floor_minute(now)
        if after is not None and current <= _floor_minute(after):
            return []
        return [current] if _cron_match(expr, now) else []
    window_start = _floor_minute(now - timedelta(seconds=lookback_seconds))
    window_end = _floor_minute(now)
    due = []
    if after is not None:
        cursor = _floor_minute(after) + timedelta(minutes=1)
        if cursor < window_start:
            cursor = window_start
    else:
        cursor = window_start
    while cursor <= window_end:
        if _cron_match(expr, cursor):
            due.append(cursor)
        cursor = cursor + timedelta(minutes=1)
    return due


def _schedule_timezone(entry: dict):
    tz_name = str((entry or {}).get("timezone") or "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return None
    return None


def _schedule_now(entry: dict) -> datetime:
    tz = _schedule_timezone(entry)
    if tz is not None:
        return datetime.now(tz)
    return datetime.now()


def _schedule_config_updated_at(config: dict, entry: dict) -> datetime | None:
    try:
        updated_at = float((config or {}).get("updated_at") or 0)
    except (TypeError, ValueError):
        return None
    if updated_at <= 0:
        return None
    tz = _schedule_timezone(entry)
    try:
        if tz is not None:
            return datetime.fromtimestamp(updated_at, tz=tz)
        return datetime.fromtimestamp(updated_at)
    except Exception:
        return None


def _normalize_runtime_config(runtime) -> dict:
    if isinstance(runtime, str):
        runtime = {"backend": runtime}
    runtime = dict(runtime or {}) if isinstance(runtime, dict) else {}
    backend = str(runtime.get("backend") or "bridge_script").strip().lower()
    if backend not in SUPPORTED_RUNTIMES:
        backend = "bridge_script"
    fallback = runtime.get("fallback")
    if fallback is not None:
        fallback = str(fallback).strip().lower() or None
        if fallback not in SUPPORTED_RUNTIMES:
            fallback = None
    timeout = runtime.get("timeout") or 300
    try:
        timeout = int(timeout)
    except Exception:
        timeout = 300
    timeout = max(1, min(timeout, 3600))
    tools = runtime.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    return {
        "backend": backend,
        "fallback": fallback,
        "entry": str(runtime.get("entry") or "SKILL.md"),
        "script_entry": str(runtime.get("script_entry") or runtime.get("script_path") or "scripts/main.py"),
        "timeout": timeout,
        "tools": [str(item) for item in tools if item],
        "output_schema": str(runtime.get("output_schema") or "contract.json"),
        "policy_pack": str(runtime.get("policy_pack") or "policy_pack.yaml"),
    }


def _schedule_payload(entry: dict) -> dict:
    raw = (entry or {}).get("payload")
    if raw is None:
        raw = (entry or {}).get("params")
    payload = dict(raw) if isinstance(raw, dict) else {}
    model_context = (entry or {}).get("model_context")
    if isinstance(model_context, dict):
        payload["model_context"] = model_context
    return payload


def _payload_model_context(run_payload: dict | None) -> dict:
    if not isinstance(run_payload, dict):
        return {}
    script_input = run_payload.get("payload") if isinstance(run_payload.get("payload"), dict) else {}
    model_context = script_input.get("model_context")
    return model_context if isinstance(model_context, dict) else {}


def _active_model_deployment_from_context(model_context: dict) -> dict:
    value = model_context.get("active_model_deployment") if isinstance(model_context, dict) else {}
    return value if isinstance(value, dict) else {}


def _strip_frontmatter_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _read_skill_frontmatter(skill_dir: Path) -> dict:
    """读取 SKILL.md 顶部简单 YAML frontmatter（无 PyYAML 依赖）。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists() or skill_md.is_symlink():
        return {}
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")[:65536]
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description", "summary", "display_name", "emoji", "homepage", "always", "disabled"}:
            meta[key] = _strip_frontmatter_quotes(value)
    return meta


def _load_agent_skill_registry() -> dict:
    reg_path = STATE_DIR / "agent_skills_registry.json"
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_local_skill_item(skill_id: str, root: Path, registry_entry: dict | None = None) -> dict:
    registry_entry = registry_entry if isinstance(registry_entry, dict) else {}
    meta = _read_skill_frontmatter(root)
    display_name = meta.get("name") or meta.get("display_name") or skill_id
    description = meta.get("description") or meta.get("summary") or ""
    disabled_raw = str(meta.get("disabled") or "").strip().lower()
    disabled = disabled_raw in {"1", "true", "yes", "on"} or registry_entry.get("status") == "disabled"
    runtime = registry_entry.get("runtime") if isinstance(registry_entry.get("runtime"), dict) else {}
    return {
        "name": display_name,
        "description": description,
        "source": "openclaw-managed",
        "bundled": False,
        "filePath": str(root / "SKILL.md"),
        "baseDir": str(root),
        # 用目录 ID 作为稳定 key，避免两个 SKILL.md name 相同被上游/前端合并。
        "skillKey": skill_id,
        "emoji": meta.get("emoji") or "",
        "homepage": meta.get("homepage") or "",
        "always": str(meta.get("always") or "").strip().lower() in {"1", "true", "yes", "on"},
        "disabled": disabled,
        "blockedByAllowlist": False,
        "eligible": not disabled,
        "requirements": {"bins": [], "anyBins": [], "env": [], "config": [], "os": []},
        "missing": {"bins": [], "anyBins": [], "env": [], "config": [], "os": []},
        "configChecks": [],
        "install": [],
        "runtime": runtime,
        "updated_at": registry_entry.get("updated_at"),
        "registered_at": registry_entry.get("registered_at"),
    }


def _list_bridge_managed_skill_items(target_dir: str | None = None) -> tuple[str | None, list[dict]]:
    cap = discover_local_capabilities()
    raw_dirs = [target_dir] if target_dir else cap.get("skills_dirs", [])
    registry = _load_agent_skill_registry()
    items = []
    seen_roots = set()
    selected_dir = target_dir or cap.get("skills_dir_default")
    for base_raw in raw_dirs:
        if not base_raw:
            continue
        base = Path(str(base_raw)).expanduser()
        try:
            base_resolved = base.resolve(strict=False)
        except Exception:
            continue
        if not base.exists() or not base.is_dir() or base.is_symlink():
            continue
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name)
        except Exception:
            continue
        for root in children:
            if not root.is_dir() or root.is_symlink() or not _valid_skill_id(root.name):
                continue
            try:
                root_resolved = root.resolve(strict=True)
                root_resolved.relative_to(base_resolved)
            except Exception:
                continue
            if root_resolved in seen_roots or not (root / "SKILL.md").exists():
                continue
            seen_roots.add(root_resolved)
            items.append(_build_local_skill_item(root.name, root_resolved, registry.get(root.name)))
    return selected_dir, items


def _merge_skills_status_payload(payload: dict | None) -> dict:
    payload = dict(payload or {}) if isinstance(payload, dict) else {}
    gateway_items = payload.get("skills") if isinstance(payload.get("skills"), list) else []
    _dir, local_items = _list_bridge_managed_skill_items()
    merged = []
    seen = set()

    def _normalize_item(item: dict) -> dict:
        item = dict(item)
        base_dir = str(item.get("baseDir") or "")
        is_managed = item.get("source") == "openclaw-managed" or item.get("bundled") is False
        if is_managed and base_dir:
            skill_id = Path(base_dir).name
            if _valid_skill_id(skill_id):
                item["skillKey"] = skill_id
                item.setdefault("source", "openclaw-managed")
                item["bundled"] = False
        return item

    def _identity(item: dict) -> str:
        base_dir = item.get("baseDir") or ""
        file_path = item.get("filePath") or ""
        if base_dir or file_path:
            return f"path:{base_dir or file_path}"
        return f"key:{item.get('skillKey') or item.get('name') or ''}"

    for item in list(gateway_items) + local_items:
        if not isinstance(item, dict):
            continue
        item = _normalize_item(item)
        ident = _identity(item)
        if ident in seen:
            continue
        seen.add(ident)
        merged.append(item)
    payload["skills"] = merged
    payload["bridge_managed_count"] = len(local_items)
    payload["bridge_merged"] = True
    return payload


def _load_schedules() -> dict:
    """读取 schedules.json，文件缺失或损坏返回空 dict。"""
    if not SCHEDULES_PATH.exists():
        return {}
    try:
        data = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_schedules(data: dict) -> None:
    """原子写 schedules.json，权限 0600。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = SCHEDULES_PATH.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(str(tmp_path), 0o600)
        tmp_path.replace(SCHEDULES_PATH)
    except Exception:
        # 清理临时文件
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _schedule_snapshot_equivalent(left: dict | None, right: dict | None) -> bool:
    """Return True when two schedule snapshots carry the same runnable content."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    comparable_keys = ("schedules", "submit_token", "submit_url")
    left_view = {key: left.get(key) for key in comparable_keys}
    right_view = {key: right.get(key) for key in comparable_keys}
    return json.dumps(left_view, sort_keys=True, ensure_ascii=False) == json.dumps(
        right_view,
        sort_keys=True,
        ensure_ascii=False,
    )


async def _execute_scheduled_skill(skill_id: str, submit_url: str, submit_token: str, schedule: dict | None = None) -> None:
    """按 schedule runtime 执行节点 Skill，并把结果提交到 SkillForge。"""
    from uuid import uuid4
    logger = setup_logging()
    run_id = f"node-{uuid4().hex[:12]}"
    schedule = schedule or {}
    scheduled_at = schedule.get("scheduled_at")
    started_at = scheduled_at or _schedule_now(schedule).isoformat()
    runtime = _normalize_runtime_config(schedule.get("runtime"))
    payload = _schedule_payload(schedule)
    skill_git_commit_full = schedule.get("skill_git_commit_full")
    requested_backend = runtime["backend"]
    actual_backend = requested_backend
    fallback_from = None
    try:
        running_output = {
            "status": "running",
            "_skillforge_meta": {
                "execution_backend": requested_backend,
                "requested_backend": requested_backend,
                "instance_id": INSTANCE_ID,
                "remote_run_id": run_id,
                "script": runtime["script_entry"],
                "status": "running",
                "scheduled_at": scheduled_at,
            },
        }
        running_submit = await asyncio.to_thread(
            _post_submit_result,
            submit_url,
            submit_token,
            skill_id,
            running_output,
            run_id,
            status="running",
            started_at=started_at,
        )
        if isinstance(running_submit, dict):
            run_token = str(running_submit.get("run_token") or "")
            platform_run_id = str(running_submit.get("run_id") or "")
        else:
            run_token = str(running_submit or "")
            platform_run_id = ""
        run_token = run_token or schedule.get("run_token") or payload.get("run_token")
        skillforge_run_id = (
            platform_run_id
            or str(schedule.get("run_id") or "")
            or str(payload.get("run_id") or "")
            or run_id
        )

        if requested_backend in {"openclaw_agent", "hybrid"}:
            logger.info(
                "[scheduler] runtime=%s skill=%s run_id=%s remote_run_id=%s",
                requested_backend,
                skill_id,
                skillforge_run_id,
                run_id,
            )
            ok, result, err = await handle_bridge_op(
                "run_agent_skill",
                {
                    "skill_id": skill_id,
                    "run_id": skillforge_run_id,
                    "remote_run_id": run_id,
                    "payload": payload,
                    "runtime": runtime,
                    "timeout": runtime["timeout"],
                    "run_token": run_token,
                    "skill_git_commit_full": skill_git_commit_full,
                    "scheduled_at": schedule.get("scheduled_at"),
                },
            )
            if not ok and runtime.get("fallback") == "bridge_script":
                fallback_from = requested_backend
                actual_backend = "bridge_script"
                logger.warning("[scheduler] runtime=%s 失败，fallback 到 bridge_script skill=%s run_id=%s",
                               requested_backend, skill_id, run_id)
                ok, result, err = await handle_bridge_op(
                    "run_skill_script",
                    {
                        "skill_id": skill_id,
                        "run_id": skillforge_run_id,
                        "remote_run_id": run_id,
                        "run_token": run_token,
                        "skill_git_commit_full": skill_git_commit_full,
                        "payload": payload,
                        "script_path": runtime["script_entry"],
                        "timeout": runtime["timeout"],
                    },
                )
        else:
            ok, result, err = await handle_bridge_op(
                "run_skill_script",
                {
                    "skill_id": skill_id,
                    "run_id": skillforge_run_id,
                    "remote_run_id": run_id,
                    "run_token": run_token,
                    "skill_git_commit_full": skill_git_commit_full,
                    "payload": payload,
                    "script_path": runtime["script_entry"],
                    "timeout": runtime["timeout"],
                },
            )

        result = result if isinstance(result, dict) else {}
        error_message = None
        execution_status = "completed" if ok else "failed"
        if ok:
            output = result.get("output")
            if not isinstance(output, dict):
                output = {"output": output}
        else:
            message = (err or {}).get("message") if isinstance(err, dict) else str(err)
            error_message = message or f"{actual_backend} execution failed"
            output = {
                "error": "runtime_execution_failed",
                "message": error_message,
            }

        meta = output.get("_skillforge_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update({
            "execution_backend": actual_backend,
            "requested_backend": requested_backend,
            "instance_id": INSTANCE_ID,
            "skillforge_run_id": skillforge_run_id,
            "remote_run_id": run_id,
            "script": result.get("script") or runtime["script_entry"],
            "duration_ms": result.get("duration_ms"),
            "returncode": result.get("returncode"),
            "status": execution_status,
        })
        if error_message:
            meta["error_message"] = error_message
        if fallback_from:
            meta["fallback_from"] = fallback_from
        model_context = payload.get("model_context") if isinstance(payload.get("model_context"), dict) else None
        if model_context:
            meta["model_context"] = model_context
            active_model = _active_model_deployment_from_context(model_context)
            if active_model:
                meta["active_model_deployment"] = {
                    key: active_model.get(key)
                    for key in (
                        "model_deployment_id",
                        "model_family",
                        "deployment_status",
                        "rollout_percent",
                        "artifact_id",
                        "artifact_sha256",
                        "target_skill_ids",
                    )
                    if active_model.get(key) is not None
                }
        output["_skillforge_meta"] = meta

        completed_at = _schedule_now(schedule).isoformat()
        final_submit = await asyncio.to_thread(
            _post_submit_result,
            submit_url,
            submit_token,
            skill_id,
            output,
            run_id,
            status=execution_status,
            started_at=started_at,
            completed_at=completed_at,
            params=payload,
        )
        submit_failure_error = _submit_result_error(final_submit)
        if submit_failure_error:
            fallback_meta = {
                "execution_backend": actual_backend,
                "requested_backend": requested_backend,
                "instance_id": INSTANCE_ID,
                "skillforge_run_id": skillforge_run_id,
                "remote_run_id": run_id,
                "script": result.get("script") or runtime["script_entry"],
                "duration_ms": result.get("duration_ms"),
                "returncode": result.get("returncode"),
                "status": "failed",
                "original_status": execution_status,
                "result_submit_failed": True,
                "submit_error": submit_failure_error,
                "original_output_omitted": True,
            }
            if error_message:
                fallback_meta["runtime_error_message"] = error_message
            if fallback_from:
                fallback_meta["fallback_from"] = fallback_from
            failure_output = {
                "error": "result_submit_failed",
                "message": "节点执行已结束，但完整结果提交平台失败；平台未收到原始输出。",
                "submit_error": submit_failure_error,
                "reports": [{
                    "title": "节点结果提交失败",
                    "summary": "节点执行结束后，完整结果提交平台失败，已记录轻量失败状态避免页面长时间显示运行中。",
                }],
                "_skillforge_meta": fallback_meta,
            }
            fallback_submit = await asyncio.to_thread(
                _post_submit_result,
                submit_url,
                submit_token,
                skill_id,
                failure_output,
                run_id,
                status="failed",
                started_at=started_at,
                completed_at=completed_at,
                params=payload,
            )
            fallback_error = _submit_result_error(fallback_submit)
            if fallback_error:
                logger.error(
                    "[scheduler] skill %s 轻量失败状态提交仍失败 run_id=%s: %s",
                    skill_id,
                    run_id,
                    fallback_error,
                )
            else:
                logger.warning(
                    "[scheduler] skill %s 本地执行结束，但完整结果提交失败，已提交轻量失败状态 run_id=%s: %s",
                    skill_id,
                    run_id,
                    submit_failure_error,
                )

        if submit_failure_error:
            logger.warning(
                "[scheduler] skill %s 本地执行结束但结果提交失败 backend=%s run_id=%s error=%s",
                skill_id,
                actual_backend,
                run_id,
                submit_failure_error,
            )
        elif ok:
            logger.info("[scheduler] skill %s 执行完成 backend=%s run_id=%s", skill_id, actual_backend, run_id)
        else:
            logger.warning(
                "[scheduler] skill %s 执行失败 backend=%s run_id=%s error=%s",
                skill_id,
                actual_backend,
                run_id,
                error_message,
            )

    except Exception as exc:
        logger.error("[scheduler] skill %s 执行异常: %s", skill_id, exc)


def _submit_result_error(result: dict | str | None) -> str:
    if isinstance(result, dict) and result.get("submitted") is False:
        return str(result.get("error") or "submit result failed")
    return ""


def _post_submit_result(submit_url: str, submit_token: str, skill_id: str,
                        output: dict, run_id: str, *,
                        status: str | None = None,
                        started_at: str | None = None,
                        completed_at: str | None = None,
                        params: dict | None = None) -> dict | str | None:
    """同步 HTTP POST 将执行结果提交到 SkillForge。失败重试 3 次（退避 5s/15s/45s）。"""
    logger = setup_logging()
    url = f"{submit_url}?token={urllib.parse.quote(submit_token, safe='')}"
    status_value = status or (
        output.get("status") if isinstance(output, dict) else None
    ) or (
        output.get("_skillforge_meta", {}).get("status")
        if isinstance(output, dict) and isinstance(output.get("_skillforge_meta"), dict)
        else None
    )
    body = json.dumps({
        "skill_id": skill_id,
        "output": output,
        "params": params if isinstance(params, dict) else {},
        "triggered_by": "node_scheduler",
        "instance_id": INSTANCE_ID,
        "remote_run_id": run_id,
        "run_mode": "scheduled_real",
        "idempotency_key": f"{INSTANCE_ID}:{run_id}",
        "status": status_value,
        "started_at": started_at,
        "completed_at": completed_at,
    }, ensure_ascii=False).encode("utf-8")

    backoff_delays = [5, 15, 45]
    last_error = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            logger.info("[scheduler] 结果提交成功 skill=%s run_id=%s", skill_id, run_id)
            if status_value == "running" and raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                run_token = payload.get("run_token") if isinstance(payload, dict) else None
                platform_run_id = payload.get("run_id") if isinstance(payload, dict) else None
                if run_token or platform_run_id:
                    return {
                        "run_token": str(run_token) if run_token else "",
                        "run_id": str(platform_run_id) if platform_run_id else "",
                    }
            return None
        except Exception as exc:
            last_error = str(exc)
            if attempt < 2:
                delay = backoff_delays[attempt]
                logger.warning("[scheduler] 结果提交失败 (尝试 %d/3), %ds 后重试: %s",
                               attempt + 1, delay, exc)
                time.sleep(delay)
            else:
                logger.error("[scheduler] 结果提交最终失败 skill=%s run_id=%s: %s",
                             skill_id, run_id, exc)
    if status_value != "running":
        return {"submitted": False, "error": last_error or "submit result failed"}
    return None


async def _node_scheduler_loop() -> None:
    """节点调度器主循环：定期检查 cron 表达式，触发 skill 执行。"""
    logger = setup_logging()
    logger.info("[scheduler] 节点调度器启动")
    # 去重集合: key → 写入时间戳
    fired: dict[str, float] = {}
    last_checked: dict[str, datetime] = {}
    last_config_versions: dict[str, str] = {}
    _scheduler_state["started_at"] = datetime.utcnow().isoformat()

    while True:
        try:
            # 等待 reload 事件或超时（正常 tick）
            try:
                event = _get_schedule_reload_event()
                await asyncio.wait_for(event.wait(),
                                       timeout=_SCHEDULE_CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass
            _get_schedule_reload_event().clear()

            config = _load_schedules()
            schedules = config.get("schedules") or []
            submit_token = config.get("submit_token", "")
            submit_url = config.get("submit_url") or \
                f"{SKILLFORGE_HTTP_BASE}/api/executions/submit-result"
            _scheduler_state["last_tick_at"] = datetime.utcnow().isoformat()
            _scheduler_state["schedule_count"] = len(schedules)
            config_version = str(config.get("config_version") or "")
            _scheduler_state["last_config_version"] = config.get("config_version")
            active_keys: set[str] = set()

            for entry in schedules:
                if not entry.get("enabled", True):
                    continue
                cron_expr = entry.get("cron", "")
                skill_id = entry.get("skill_id", "")
                if not cron_expr or not skill_id:
                    continue
                schedule_key = f"{skill_id}:{cron_expr}:{entry.get('timezone') or ''}"
                active_keys.add(schedule_key)
                now = _schedule_now(entry)
                config_updated_at = _schedule_config_updated_at(config, entry)
                if last_config_versions.get(schedule_key) != config_version:
                    last_config_versions[schedule_key] = config_version
                    last_checked[schedule_key] = config_updated_at or (
                        now - timedelta(seconds=_SCHEDULE_CHECK_INTERVAL)
                    )
                previous = last_checked.get(schedule_key)
                if previous is None:
                    previous = now - timedelta(seconds=_SCHEDULE_CHECK_INTERVAL)
                if config_updated_at is not None and previous < config_updated_at:
                    previous = config_updated_at
                minimum_previous = now - timedelta(seconds=_SCHEDULE_MISSED_GRACE_SECONDS)
                if previous < minimum_previous:
                    previous = minimum_previous
                due_minutes = _cron_expected_minutes(
                    cron_expr,
                    now,
                    _SCHEDULE_MISSED_GRACE_SECONDS,
                    after=previous,
                )
                for due_at in due_minutes:
                    # 分钟级去重：同一 skill 同一分钟只触发一次
                    dedup_key = f"{skill_id}:{due_at:%Y%m%d%H%M}"
                    if dedup_key in fired:
                        continue
                    fired[dedup_key] = time.time()
                    delay_seconds = max(0, int((now - due_at).total_seconds()))
                    if delay_seconds > 60:
                        logger.warning(
                            "[scheduler] 补触发滞后 tick skill=%s cron=%s scheduled_at=%s delay=%ss",
                            skill_id,
                            cron_expr,
                            due_at.isoformat(),
                            delay_seconds,
                        )
                    else:
                        logger.info("[scheduler] 触发 skill %s (cron=%s)", skill_id, cron_expr)
                    run_entry = dict(entry)
                    run_entry["scheduled_at"] = due_at.isoformat()
                    _scheduler_state.setdefault("last_fired", {})[skill_id] = {
                        "scheduled_at": due_at.isoformat(),
                        "cron": cron_expr,
                        "delay_seconds": delay_seconds,
                    }
                    asyncio.create_task(
                        _execute_scheduled_skill(skill_id, submit_url, submit_token, run_entry)
                    )
                last_checked[schedule_key] = now

            stale_schedule_keys = [key for key in last_checked if key not in active_keys]
            for key in stale_schedule_keys:
                last_checked.pop(key, None)
                last_config_versions.pop(key, None)

            # 淘汰过期去重 key（>120s）
            cutoff = time.time() - max(600, _SCHEDULE_MISSED_GRACE_SECONDS * 2)
            expired = [k for k, ts in fired.items() if ts < cutoff]
            for k in expired:
                del fired[k]

        except Exception as exc:
            _scheduler_state["last_error"] = str(exc)
            logger.error("[scheduler] tick 异常（不影响下一轮）: %s", exc)


def discover_local_capabilities():
    """探测本机可写的 skill 目录 + gateway 类型 + 平台。

    优先级：
    1. 环境变量 SKILLS_DIR
    2. Gateway 配置文件中的 skills.load.dir / skills.load.extraDirs
    3. 全盘扫描 /home/*/.openclaw 和 /home/*/.aiclaw（bridge 以 root 运行时尤其需要）
    4. 当前用户 HOME 下的默认候选按 gateway 类型优先排列
    """
    cand = []
    seen = set()
    home = str(Path.home())

    def _add(p):
        rp = p.replace("~", home) if p else ""
        if rp and rp not in seen:
            seen.add(rp)
            cand.append(rp)

    # 1. 环境变量优先
    env_dir = os.environ.get("SKILLS_DIR", "")
    if env_dir:
        _add(env_dir)

    # 2. 从 Gateway 配置文件读取（当前 HOME + 全盘扫描其他用户）
    _cfg_homes = [home]
    try:
        for entry in Path("/home").iterdir():
            if entry.is_dir() and str(entry) != home:
                _cfg_homes.append(str(entry))
    except Exception:
        pass

    gateway_kind = "unknown"
    config_dir_order = [
        (name, "openclaw" if name == ".openclaw" else "aiclaw")
        for name in _local_config_dir_order()
        if name in {".openclaw", ".aiclaw"}
    ]
    for h in _cfg_homes:
        for name, kind in config_dir_order:
            cfg_path = Path(h) / name / f"{kind}.json"
            if cfg_path.exists():
                # 找到配置文件即确定 gateway 类型（取第一个命中的）
                if gateway_kind == "unknown":
                    gateway_kind = kind
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    skills_cfg = cfg.get("skills") or {}
                    load_cfg = skills_cfg.get("load") or {}
                    main_dir = load_cfg.get("dir") or ""
                    if isinstance(main_dir, str) and main_dir:
                        _add(main_dir)
                    extra = load_cfg.get("extraDirs") or []
                    for d in extra:
                        if isinstance(d, str):
                            _add(d)
                except Exception:
                    pass

    # 3. 全盘扫描已存在的 skills 目录（跨所有 /home/* 用户）
    for h in _cfg_homes:
        for subdir in [".openclaw/skills", ".aiclaw/skills", ".agents/skills"]:
            p = Path(h) / subdir
            if p.exists():
                _add(str(p))

    # 4. 按 gateway 类型优先排列候选（含 /home/* 用户的 HOME 目录）
    #    即使目录不存在也添加——后面会尝试 mkdir
    _home_dirs = [home]
    try:
        for entry in Path("/home").iterdir():
            if entry.is_dir() and str(entry) != home:
                _home_dirs.append(str(entry))
    except Exception:
        pass

    if gateway_kind == "openclaw":
        for h in _home_dirs:
            _add(str(Path(h) / ".openclaw" / "skills"))
        for h in _home_dirs:
            _add(str(Path(h) / ".aiclaw" / "skills"))
    else:
        for h in _home_dirs:
            _add(str(Path(h) / ".aiclaw" / "skills"))
        for h in _home_dirs:
            _add(str(Path(h) / ".openclaw" / "skills"))
    for h in _home_dirs:
        _add(str(Path(h) / ".agents" / "skills"))

    # 按 gateway 类型排序：匹配 gateway 的路径优先（如 openclaw 时 .openclaw 优先）
    _gw_marker = f".{gateway_kind}" if gateway_kind != "unknown" else ""
    if _gw_marker:
        cand.sort(key=lambda p: 0 if _gw_marker in p else 1)

    skills_dir_default = None
    for p in cand:
        # 优先选已存在且可写的目录；其次尝试创建
        if Path(p).exists():
            try:
                # 验证可写（touch 测试）
                (Path(p) / ".bridge_write_test").touch()
                (Path(p) / ".bridge_write_test").unlink(missing_ok=True)
                skills_dir_default = p
                break
            except Exception:
                continue
        else:
            # 目录不存在但属于已识别的 gateway 类型路径，尝试创建
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
                skills_dir_default = p
                break
            except Exception:
                continue

    # 磁盘信息
    disk_info = {}
    try:
        import shutil as _shutil
        for _p in [home, "/"] + [str(Path(h)) for h in _home_dirs if h != home]:
            usage = _shutil.disk_usage(_p)
            disk_info[_p] = {
                "total_gb": round(usage.total / (1 << 30), 1),
                "used_gb": round(usage.used / (1 << 30), 1),
                "avail_gb": round(usage.free / (1 << 30), 1),
                "use_pct": round(usage.used / usage.total * 100, 1) if usage.total else 0,
            }
    except Exception:
        pass

    # 内存信息。Linux 读取 /proc；macOS 使用 sysctl + vm_stat。
    mem_info = {}
    if platform.system() == "Darwin":
        try:
            total_result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            total_bytes = int((total_result.stdout or "0").strip() or 0)
            vm_result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            page_size = 4096
            free_pages = 0
            for _line in (vm_result.stdout or "").splitlines():
                if "page size of" in _line:
                    match = re.search(r"page size of\s+(\d+)", _line)
                    if match:
                        page_size = int(match.group(1))
                elif _line.startswith(("Pages free:", "Pages inactive:", "Pages speculative:")):
                    free_pages += int(re.sub(r"\D", "", _line.split(":", 1)[1]) or 0)
            avail_bytes = min(total_bytes, free_pages * page_size)
            used_bytes = max(0, total_bytes - avail_bytes)
            mem_info = {
                "total_gb": round(total_bytes / (1 << 30), 1),
                "avail_gb": round(avail_bytes / (1 << 30), 1),
                "used_pct": round(used_bytes * 100 / total_bytes, 1) if total_bytes else 0,
                "unified": True,
            }
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo", "r") as _f:
                _mi = {}
                for _line in _f:
                    _parts = _line.split()
                    if len(_parts) >= 2:
                        _mi[_parts[0].rstrip(":")] = int(_parts[1])  # kB
            mem_info["total_gb"] = round(_mi.get("MemTotal", 0) / 1048576, 1)
            mem_info["avail_gb"] = round(_mi.get("MemAvailable", 0) / 1048576, 1)
            mem_info["used_pct"] = round((1 - _mi.get("MemAvailable", 0) / max(_mi.get("MemTotal", 1), 1)) * 100, 1)
        except Exception:
            pass

    def _metric_number(value, default=0):
        try:
            return int(float(str(value or "").strip()))
        except (TypeError, ValueError):
            return default

    # NVIDIA GB10 把显存字段报告为 N/A，因为它使用统一内存。仍需把 GPU 保留下来，
    # 并用系统可用内存提供保守的统一显存近似值。
    gpu_info = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 5 or not parts[0]:
                    continue
                total_mb = _metric_number(parts[1])
                used_mb = _metric_number(parts[2])
                free_mb = _metric_number(parts[3])
                unified = total_mb <= 0 and bool(mem_info.get("total_gb"))
                if unified:
                    total_mb = int(float(mem_info.get("total_gb") or 0) * 1024)
                    free_mb = int(float(mem_info.get("avail_gb") or 0) * 1024)
                    used_mb = max(0, total_mb - free_mb)
                gpu_info.append({
                    "name": parts[0],
                    "backend": "cuda",
                    "unified_memory": unified,
                    "vram_total_mb": total_mb,
                    "vram_used_mb": used_mb,
                    "vram_free_mb": free_mb,
                    "gpu_util_pct": _metric_number(parts[4]),
                })
    except Exception:
        pass

    # Apple Silicon 是可调用的统一内存推理节点，但当前 CUDA QLoRA profile
    # 不支持 Metal，因此不能仅因存在 GPU 就把它标记为训练网关。
    if platform.system() == "Darwin" and not gpu_info:
        try:
            chip_result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5,
            )
            chip = (chip_result.stdout or "Apple Silicon").strip() or "Apple Silicon"
        except Exception:
            chip = "Apple Silicon"
        total_mb = int(float(mem_info.get("total_gb") or 0) * 1024)
        free_mb = int(float(mem_info.get("avail_gb") or 0) * 1024)
        gpu_info.append({
            "name": chip,
            "backend": "metal",
            "unified_memory": True,
            "vram_total_mb": total_mb,
            "vram_used_mb": max(0, total_mb - free_mb),
            "vram_free_mb": free_mb,
            "gpu_util_pct": 0,
        })

    training_gateway_configured = bool(_training_gateway_base_url())
    cuda_gpu_info = [item for item in gpu_info if item.get("backend") == "cuda"]
    training_gateway_available = bool(cuda_gpu_info or training_gateway_configured)
    training_task_env = os.environ.get("LOCAL_TRAINING_GATEWAY_TASKS") if training_gateway_configured else ""
    training_supported_tasks = [
        item.strip().lower()
        for item in (training_task_env or "").split(",")
        if item.strip().lower() in TRAINING_ALLOWED_TASKS
    ]
    if not training_supported_tasks and training_gateway_available:
        training_supported_tasks = ["lora", "qlora", "eval", "merge", "inference"]
    elif not training_supported_tasks:
        training_supported_tasks = ["eval"]

    ops = [
        "install_skill",
        "remove_skill",
        "list_local_skills",
        "read_skill",
        "sync_schedules",
        "run_skill_script",
        "run_agent_skill",
        "training.gateway_capabilities",
        "training.submit_job",
        "training.get_job",
        "training.cancel_job",
        "training.stream_logs",
        "training.collect_result",
        "training.download_artifact",
        "training.import_artifact",
        "training.artifact_status",
        "training.dataset_write_chunk",
        "training.dataset_commit",
        "training.dataset_status",
        "training.dataset_export_start",
        "training.dataset_export_manifest",
        "training.dataset_export_file_chunk",
        "training.dataset_import_from_export",
        "training.dataset_import_status",
        "training.dataset_import_relay_file_status",
        "training.dataset_import_relay_chunk",
        "training.dataset_import_relay_commit",
        "training.model_export_start",
        "training.model_export_status",
        "training.model_export_cancel",
        "training.model_export_manifest",
        "training.model_export_file_chunk",
        "training.model_stream_manifest",
        "training.model_stream_chunk",
        "training.model_import_from_export",
        "training.model_import_status",
        "training.model_import_cancel",
        "training.model_import_relay_file_status",
        "training.model_import_relay_chunk",
        "training.model_import_relay_commit",
        "training.model_stream_write",
        "training.model_stream_commit",
        "training.bootstrap_env",
        "training.bootstrap_status",
        "training.configure_runtime",
        "training.discover_python_envs",
        "training.discover_models",
        "training.explore_model_paths",
        "training.prepare_model",
        "training.model_status",
        "training.inference",
        "training.openwebui_status",
        "training.register_openwebui_model",
        "training.openwebui_chat_test",
        "media.capabilities",
        "media.submit_job",
        "media.get_job",
        "media.cancel_job",
        "media.collect_result",
        "media.read_result_chunk",
        "media.bootstrap_h3",
        "media.bootstrap_status",
        "media.bootstrap_cancel",
        "register_skill",
    ]
    ops.append("intelligence.analyze")

    media_capability = _media_capability_snapshot(gpu_info=gpu_info, include_hashes=False)
    workload_roles = []
    if media_capability.get("configured"):
        workload_roles.append("video_generation")

    return {
        "type": "bridge_capabilities",
        "gateway_kind": gateway_kind,
        "platform": os.uname().sysname,
        "home": home,
        "skills_dirs": cand,
        "skills_dir_default": skills_dir_default,
        "bridge_version": BRIDGE_VERSION,
        "runtimes": ["bridge_script", "openclaw_agent", "hybrid"],
        "ops": ops,
        "disk": disk_info,
        "memory": mem_info,
        "gpu": gpu_info,
        "workload_roles": workload_roles,
        "media": media_capability,
        "resident_models": _openwebui_resident_models(),
        "training": {
            "gateway": training_gateway_available,
            "supported_tasks": training_supported_tasks,
            "gpu_count": len(cuda_gpu_info),
            "worker_count": max(len(cuda_gpu_info), 1 if training_gateway_configured else 0),
            "resident_models": _openwebui_resident_models(),
        },
        "openwebui": {
            "enabled": _openwebui_enabled(),
            "base_url": _openwebui_base_url(),
            "model_count": len(_openwebui_model_records()),
            "model_ids": [item.get("id") for item in _openwebui_model_records()[:20]],
        },
    }


def _valid_skill_id(skill_id):
    return bool(skill_id) and isinstance(skill_id, str) and "/" not in skill_id and ".." not in skill_id and not skill_id.startswith(".")


TRAINING_ALLOWED_TASKS = {"lora", "qlora", "eval", "merge", "inference"}
TRAINING_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
TRAINING_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
TRAINING_JOB_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
TRAINING_GATEWAY_RESPONSE_MAX_BYTES = 256 * 1024
TRAINING_GATEWAY_DOWNLOAD_RESPONSE_MAX_BYTES = int(
    os.getenv("SKILLFORGE_TRAINING_GATEWAY_DOWNLOAD_RESPONSE_MAX_BYTES", str(768 * 1024 * 1024))
)
TRAINING_MODEL_EXPORT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_MODEL_EXPORT_SESSIONS = {}
_MODEL_EXPORT_LOCK = threading.Lock()
_MODEL_EXPORT_SERVER = None
_MODEL_EXPORT_SERVER_LOCK = threading.Lock()
_MODEL_IMPORT_CANCEL_FLAGS = {}
_MODEL_IMPORT_CANCEL_LOCK = threading.Lock()
LOCAL_TRAINING_RUNNER_DEFAULT_MAX_STEPS = int(os.getenv("LOCAL_TRAINING_RUNNER_DEFAULT_MAX_STEPS", "3") or "3")
LOCAL_TRAINING_RUNNER_TIMEOUT_SECONDS = int(os.getenv("LOCAL_TRAINING_RUNNER_TIMEOUT_SECONDS", "7200") or "7200")
LOCAL_TRAINING_RUNNER_MAX_SEQ_LEN = int(os.getenv("LOCAL_TRAINING_RUNNER_MAX_SEQ_LEN", "512") or "512")
LOCAL_TRAINING_RUNNER_EVAL_MAX_SAMPLES = int(os.getenv("LOCAL_TRAINING_RUNNER_EVAL_MAX_SAMPLES", "4") or "4")
LOCAL_TRAINING_RUNNER_EVAL_MAX_NEW_TOKENS = int(os.getenv("LOCAL_TRAINING_RUNNER_EVAL_MAX_NEW_TOKENS", "48") or "48")
TRAINING_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|ssh[_-]?key|signing[_-]?key|"
    r"secret[_-]?key|service[_-]?account[_-]?key|session[_-]?id|"
    r"token|secret|password|credential|cookie|authorization)"
)
TRAINING_SAFE_TOKEN_METRIC_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "generated_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "requested_max_new_tokens",
    "effective_max_new_tokens",
    "max_new_tokens",
    "tokens_per_second",
}
TRAINING_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/\-=]+|"
    r"((?:api[_-]?key|access[_-]?token|token|secret|password)=)[^\s&]+"
)


def _valid_training_job_id(job_id):
    return bool(job_id) and isinstance(job_id, str) and bool(TRAINING_JOB_ID_RE.match(job_id)) and ".." not in job_id


def _valid_training_artifact_id(artifact_id):
    return bool(artifact_id) and isinstance(artifact_id, str) and bool(TRAINING_ARTIFACT_ID_RE.match(artifact_id)) and ".." not in artifact_id


def _training_gateway_base_url():
    return (
        os.environ.get("LOCAL_TRAINING_GATEWAY_URL")
        or LOCAL_TRAINING_GATEWAY_URL
        or os.environ.get("TRAINING_GATEWAY_URL")
        or ""
    ).strip().rstrip("/")


def _training_gateway_token():
    return (
        os.environ.get("LOCAL_TRAINING_GATEWAY_TOKEN")
        or LOCAL_TRAINING_GATEWAY_TOKEN
        or os.environ.get("TRAINING_GATEWAY_TOKEN")
        or ""
    ).strip()


def _redact_training_text(value, limit=4000):
    text = str(value or "")
    text = TRAINING_SECRET_TEXT_RE.sub(lambda m: f"{m.group(1) or m.group(2)}[REDACTED]", text)
    return text[:limit]


def _redact_training_value(value, depth=0):
    if depth > 6:
        return _redact_training_text(value, limit=1000)
    if isinstance(value, dict):
        safe = {}
        for key, item in list(value.items())[:200]:
            safe_key = _redact_training_text(key, limit=120)
            key_text = str(key or "")
            if key_text.lower() in TRAINING_SAFE_TOKEN_METRIC_KEYS:
                safe[safe_key] = _redact_training_value(item, depth + 1)
            elif TRAINING_SECRET_KEY_RE.search(key_text):
                safe[safe_key] = "[REDACTED]"
            else:
                safe[safe_key] = _redact_training_value(item, depth + 1)
        return safe
    if isinstance(value, list):
        return [_redact_training_value(item, depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return _redact_training_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_training_text(value, limit=1000)


def _training_subprocess_env(extra=None):
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    if isinstance(extra, dict):
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _training_gateway_request(method, path, payload=None, timeout=30, response_max_bytes=TRAINING_GATEWAY_RESPONSE_MAX_BYTES):
    base_url = _training_gateway_base_url()
    if not base_url:
        raise RuntimeError("local training gateway is not configured")
    url = f"{base_url}{path if str(path).startswith('/') else '/' + str(path)}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    gateway_token = _training_gateway_token()
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=max(1, int(timeout))) as resp:
            raw = resp.read(response_max_bytes + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"training gateway HTTP {exc.code}: {_redact_training_text(body, limit=1000)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"training gateway request failed: {_redact_training_text(exc, limit=1000)}") from exc
    if len(raw) > response_max_bytes:
        raise RuntimeError("training gateway response too large")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("training gateway response is not JSON") from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


async def _call_training_gateway(method, path, payload=None, timeout=30):
    return await asyncio.to_thread(_training_gateway_request, method, path, payload, timeout)


async def _call_training_gateway_download(method, path, payload=None, timeout=60):
    return await asyncio.to_thread(
        _training_gateway_request,
        method,
        path,
        payload,
        timeout,
        TRAINING_GATEWAY_DOWNLOAD_RESPONSE_MAX_BYTES,
    )


TRAINING_BOOTSTRAP_PROFILES = {
    "qlora-1b": {
        "description": "CUDA QLoRA/LoRA runtime for 1B-class models",
        "cuda": "cu130",
        "supported_tasks": ["lora", "qlora", "eval", "merge", "inference"],
        "packages": [
            "accelerate==1.14.0",
            "bitsandbytes==0.49.2",
            "datasets==5.0.0",
            "einops==0.8.0",
            "kernels==0.14.1",
            "peft==0.19.1",
            "protobuf==5.27.4",
            "safetensors==0.4.5",
            "sentencepiece==0.2.0",
            "transformers==5.12.1",
            "trl==0.29.1",
        ],
    }
}
TRAINING_BOOTSTRAP_DEFAULT_PROFILE = "qlora-1b"
TRAINING_BOOTSTRAP_CUDA_VARIANTS = {
    "cu121": {
        "torch_index_url": "https://download.pytorch.org/whl/cu121",
        "torch_packages": ["torch==2.4.1", "torchvision==0.19.1", "torchaudio==2.4.1"],
    },
    "cu130": {
        "torch_index_url": "https://download.pytorch.org/whl/cu130",
        "torch_packages": ["torch==2.13.0+cu130"],
    },
}
TRAINING_BOOTSTRAP_ALLOWED_CUDA = set(TRAINING_BOOTSTRAP_CUDA_VARIANTS)
TRAINING_MODEL_PROFILES = {
    "qwen3.5-4b": {
        "model_id": "Qwen/Qwen3.5-4B",
        "modelscope_model_id": "Qwen/Qwen3.5-4B",
        "revision": "main",
        "modelscope_revision": "master",
        "bootstrap_profile": "qlora-1b",
        "description": "Qwen3.5 4B base model for text decision finetuning validation",
        "allow_4bit_load": True,
        "min_free_disk_gb": 16,
        "prepare_packages": [
            "transformers==5.12.1",
            "accelerate==1.14.0",
            "peft==0.19.1",
            "bitsandbytes==0.49.2",
            "modelscope==1.37.1",
            "qwen-vl-utils==0.0.14",
        ],
    },
    "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive": {
        "model_id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "modelscope_model_id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "revision": "main",
        "modelscope_revision": "master",
        "bootstrap_profile": "qlora-1b",
        "description": "Qwen3.6 35B A3B uncensored aggressive model from internal network storage",
        "allow_4bit_load": True,
        "min_free_disk_gb": 1,
        "default_source": "internal",
        "internal_model_ref": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "internal_model_env": "SKILLFORGE_QWEN36_35B_MODEL_DIR",
        "prepare_packages": [
            "transformers==5.12.1",
            "accelerate==1.14.0",
            "peft==0.19.1",
            "bitsandbytes==0.49.2",
            "kernels==0.14.1",
            "qwen-vl-utils==0.0.14",
        ],
    }
}
TRAINING_MODEL_DEFAULT_PROFILE = "qwen3.5-4b"
TRAINING_INTERNAL_MODEL_SOURCES = {"internal", "local_path", "artifact"}
TRAINING_RUNTIME_ENV_ALLOWLIST = {
    "SKILLFORGE_QWEN36_35B_MODEL_DIR": {"kind": "absolute_dir_path", "dynamic": True},
    "SKILLFORGE_OPENWEBUI_ENABLED": {"kind": "bool", "dynamic": False},
    "SKILLFORGE_OPENWEBUI_HOST": {"kind": "host", "dynamic": False},
    "SKILLFORGE_OPENWEBUI_PORT": {"kind": "port", "dynamic": False},
}
TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS = (
    "/opt/models",
    "/opt",
    "/models",
    "/mnt/models",
    "/mnt/data/models",
    "/mnt",
    "/data/models",
    "/data",
    "/home",
    "/workspace",
    "/media",
    "/srv/ai/models/hf/hub",
    "/srv/ai/models",
    "/srv/ai",
    "/srv/models",
    "~/models",
    "~",
    "~/.cache/huggingface/hub",
    "~/.cache/modelscope/hub",
)
TRAINING_MODEL_EXPLORE_MAX_ROOTS = 32
TRAINING_MODEL_EXPLORE_MAX_DIRS = 50000
TRAINING_MODEL_EXPLORE_MAX_DEPTH = 12
TRAINING_MODEL_EXPLORE_MAX_LIMIT = 50
TRAINING_MODEL_EXPLORE_SAFE_CONFIG_KEYS = {
    "architectures",
    "auto_map",
    "hidden_size",
    "intermediate_size",
    "max_position_embeddings",
    "model_type",
    "num_attention_heads",
    "num_hidden_layers",
    "num_key_value_heads",
    "quantization_config",
    "torch_dtype",
    "transformers_version",
    "vocab_size",
}
TRAINING_MODEL_EXPLORE_SKIP_DIR_NAMES = {
    ".aws",
    ".azure",
    ".config",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
    "boot",
    "cookies",
    "credentials",
    "dev",
    "etc",
    "lost+found",
    "proc",
    "run",
    "secrets",
    "sys",
}
TRAINING_INFERENCE_ABSOLUTE_MAX_NEW_TOKENS = 4096


def _runtime_gpu_headroom_mb():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return 0
        values = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                total = int(float(parts[0] or 0))
                free = int(float(parts[1] or 0))
                values.append(max(free, total if free <= 0 else 0))
        return max(values or [0])
    except Exception:
        return 0


def _training_inference_effective_max_new_tokens(profile, requested):
    requested = max(1, min(int(requested or 96), TRAINING_INFERENCE_ABSOLUTE_MAX_NEW_TOKENS))
    profile_text = str(profile or "").lower()
    if "35b" in profile_text:
        profile_default = 768
    elif "4b" in profile_text:
        profile_default = 2048
    elif "7b" in profile_text or "8b" in profile_text:
        profile_default = 1536
    elif "14b" in profile_text:
        profile_default = 1024
    else:
        profile_default = 1536

    headroom_mb = _runtime_gpu_headroom_mb()
    if "35b" in profile_text and headroom_mb >= 24000:
        cap = 1536
    elif "35b" in profile_text and headroom_mb >= 16000:
        cap = 1024
    elif "35b" in profile_text:
        cap = 768
    elif headroom_mb >= 24000:
        cap = TRAINING_INFERENCE_ABSOLUTE_MAX_NEW_TOKENS
    elif headroom_mb >= 16000:
        cap = 3072
    elif headroom_mb >= 10000:
        cap = 2048
    elif headroom_mb >= 7000:
        cap = 2048 if "4b" in profile_text else 1536
    elif headroom_mb > 0:
        cap = 1024
    else:
        cap = profile_default
    return max(1, min(requested, cap, TRAINING_INFERENCE_ABSOLUTE_MAX_NEW_TOKENS))


def _training_bootstrap_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _training_bootstrap_profile_dir(profile):
    safe_profile = str(profile or TRAINING_BOOTSTRAP_DEFAULT_PROFILE).replace("-", "_")
    return STATE_DIR / "training_env" / safe_profile


def _training_bootstrap_python_path(profile):
    venv_dir = _training_bootstrap_profile_dir(profile) / "venv"
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _training_bootstrap_status_path(profile):
    return _training_bootstrap_profile_dir(profile) / "env.json"


def _load_training_bootstrap_status(profile):
    path = _training_bootstrap_status_path(profile)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write_training_bootstrap_status(profile, status):
    base = _training_bootstrap_profile_dir(profile)
    base.mkdir(parents=True, exist_ok=True)
    path = _training_bootstrap_status_path(profile)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(status), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _training_model_profile_dir(profile):
    safe_profile = re.sub(r"[^a-z0-9_.-]+", "_", str(profile or TRAINING_MODEL_DEFAULT_PROFILE).lower())
    return STATE_DIR / "training_models" / safe_profile


def _training_model_status_path(profile):
    return _training_model_profile_dir(profile) / "status.json"


def _load_training_model_status(profile):
    path = _training_model_status_path(profile)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write_training_model_status(profile, status):
    base = _training_model_profile_dir(profile)
    base.mkdir(parents=True, exist_ok=True)
    path = _training_model_status_path(profile)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(status), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _normalize_training_bootstrap_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    profile = str(payload.get("profile") or TRAINING_BOOTSTRAP_DEFAULT_PROFILE).strip().lower()
    if profile not in TRAINING_BOOTSTRAP_PROFILES:
        return None, "unsupported training bootstrap profile"
    profile_cfg = TRAINING_BOOTSTRAP_PROFILES[profile]
    cuda = str(payload.get("cuda") or profile_cfg.get("cuda") or "cu121").strip().lower()
    if cuda not in TRAINING_BOOTSTRAP_ALLOWED_CUDA:
        return None, "unsupported training bootstrap cuda target"
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 3600)
    except Exception:
        timeout_seconds = 3600
    timeout_seconds = max(60, min(timeout_seconds, 7200))
    return {
        "profile": profile,
        "cuda": cuda,
        "force": _training_bootstrap_bool(payload.get("force")),
        "dry_run": _training_bootstrap_bool(payload.get("dry_run")),
        "timeout_seconds": timeout_seconds,
    }, None


def _normalize_training_model_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    profile = str(payload.get("profile") or TRAINING_MODEL_DEFAULT_PROFILE).strip().lower()
    if profile not in TRAINING_MODEL_PROFILES:
        return None, "unsupported training model profile"
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    bootstrap_profile = str(payload.get("bootstrap_profile") or profile_cfg.get("bootstrap_profile") or TRAINING_BOOTSTRAP_DEFAULT_PROFILE).strip().lower()
    if bootstrap_profile not in TRAINING_BOOTSTRAP_PROFILES:
        return None, "unsupported training bootstrap profile"
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 7200)
    except Exception:
        timeout_seconds = 7200
    timeout_seconds = max(60, min(timeout_seconds, 21600))
    validate_mode = str(payload.get("validate_mode") or "metadata").strip().lower()
    if validate_mode not in {"none", "metadata", "load_4bit"}:
        return None, "unsupported training model validate_mode"
    if validate_mode == "load_4bit" and not bool(profile_cfg.get("allow_4bit_load")):
        return None, "4bit load validation is not allowed for this profile"
    source = str(payload.get("source") or profile_cfg.get("default_source") or "auto").strip().lower()
    allowed_sources = {"auto", "huggingface", "modelscope"} | TRAINING_INTERNAL_MODEL_SOURCES
    if source not in allowed_sources:
        return None, "unsupported training model source"
    if profile == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive" and source not in TRAINING_INTERNAL_MODEL_SOURCES:
        return None, "qwen3.6 35b aggressive profile must use internal, local_path or artifact source"
    internal_model_ref = str(
        payload.get("internal_model_ref")
        or payload.get("internalModelRef")
        or payload.get("model_path")
        or payload.get("modelPath")
        or payload.get("artifact_uri")
        or payload.get("artifactUri")
        or profile_cfg.get("internal_model_ref")
        or ""
    ).strip()
    revision = str(payload.get("revision") or profile_cfg.get("revision") or "main").strip() or "main"
    if not re.match(r"^[A-Za-z0-9._/-]{1,120}$", revision):
        return None, "invalid model revision"
    modelscope_revision = str(payload.get("modelscope_revision") or profile_cfg.get("modelscope_revision") or "master").strip() or "master"
    if not re.match(r"^[A-Za-z0-9._/-]{1,120}$", modelscope_revision):
        return None, "invalid modelscope model revision"
    return {
        "profile": profile,
        "model_id": profile_cfg["model_id"],
        "modelscope_model_id": profile_cfg.get("modelscope_model_id") or profile_cfg["model_id"],
        "revision": revision,
        "modelscope_revision": modelscope_revision,
        "bootstrap_profile": bootstrap_profile,
        "force": _training_bootstrap_bool(payload.get("force")),
        "dry_run": _training_bootstrap_bool(payload.get("dry_run")),
        "auto_discover": _training_bootstrap_bool(payload.get("auto_discover") or payload.get("autoDiscover")),
        "timeout_seconds": timeout_seconds,
        "validate_mode": validate_mode,
        "source": source,
        "internal_model_ref": internal_model_ref,
    }, None


def _training_bootstrap_command_plan(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_BOOTSTRAP_PROFILES[profile]
    cuda_cfg = TRAINING_BOOTSTRAP_CUDA_VARIANTS[normalized["cuda"]]
    venv_dir = _training_bootstrap_profile_dir(profile) / "venv"
    python_path = _training_bootstrap_python_path(profile)
    timeout_seconds = normalized["timeout_seconds"]
    smoke_code = (
        "import json\n"
        "import torch\n"
        "import transformers\n"
        "import peft\n"
        "import bitsandbytes as bnb\n"
        "print(json.dumps({"
        "'torch': torch.__version__, "
        "'transformers': transformers.__version__, "
        "'peft': peft.__version__, "
        "'bitsandbytes': getattr(bnb, '__version__', ''), "
        "'cuda_available': bool(torch.cuda.is_available()), "
        "'cuda_device_count': int(torch.cuda.device_count())"
        "}, ensure_ascii=False))"
    )
    torch_install_args = [str(python_path), "-m", "pip", "install"]
    torch_index_url = str(cuda_cfg.get("torch_index_url") or "").strip()
    if torch_index_url:
        torch_install_args.extend(["--index-url", torch_index_url])
    torch_install_args.extend(cuda_cfg["torch_packages"])
    return [
        {
            "step": "create_venv",
            "args": [sys.executable, "-m", "venv", str(venv_dir)],
            "timeout": min(300, timeout_seconds),
        },
        {
            "step": "upgrade_packaging",
            "args": [str(python_path), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            "timeout": min(600, timeout_seconds),
        },
        {
            "step": "install_torch",
            "args": torch_install_args,
            "timeout": timeout_seconds,
        },
        {
            "step": "install_training_packages",
            "args": [str(python_path), "-m", "pip", "install", *profile_cfg["packages"]],
            "timeout": timeout_seconds,
        },
        {
            "step": "smoke_test",
            "args": [str(python_path), "-c", smoke_code],
            "timeout": min(180, timeout_seconds),
        },
    ]


def _training_bootstrap_plan_for_status(normalized):
    return [
        {
            "step": item["step"],
            "args": [str(part) for part in item["args"]],
            "timeout": item["timeout"],
        }
        for item in _training_bootstrap_command_plan(normalized)
    ]


def _run_training_bootstrap_command(args, timeout=300):
    proc = subprocess.run(
        [str(item) for item in args],
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout or 300)),
        env=_training_subprocess_env(),
    )
    return {
        "returncode": int(proc.returncode),
        "stdout": _redact_training_text(proc.stdout, limit=12000),
        "stderr": _redact_training_text(proc.stderr, limit=12000),
    }


def _current_training_bootstrap_status(profile=TRAINING_BOOTSTRAP_DEFAULT_PROFILE):
    normalized, error = _normalize_training_bootstrap_payload({"profile": profile})
    if error:
        return {"status": "unsupported", "profile": profile, "error": error}
    stored = _load_training_bootstrap_status(normalized["profile"])
    python_path = _training_bootstrap_python_path(normalized["profile"])
    venv_dir = _training_bootstrap_profile_dir(normalized["profile"]) / "venv"
    result = {
        "status": "not_installed",
        "profile": normalized["profile"],
        "cuda": normalized["cuda"],
        "configured": False,
        "exists": python_path.exists(),
        "venv_dir": str(venv_dir),
        "python": str(python_path),
        "bridge_instance_id": INSTANCE_ID,
        "supported_profiles": list(TRAINING_BOOTSTRAP_PROFILES.keys()),
    }
    if stored:
        result.update(stored)
        result["exists"] = python_path.exists()
        result["configured"] = bool(stored.get("status") == "succeeded" and python_path.exists())
    return _redact_training_value(result)


def _bootstrap_training_env_sync(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_BOOTSTRAP_PROFILES[profile]
    python_path = _training_bootstrap_python_path(profile)
    venv_dir = _training_bootstrap_profile_dir(profile) / "venv"
    started_at = datetime.utcnow().isoformat() + "Z"
    plan = _training_bootstrap_plan_for_status(normalized)
    existing = _load_training_bootstrap_status(profile)
    if (
        not normalized["force"]
        and existing.get("status") == "succeeded"
        and python_path.exists()
    ):
        skipped = dict(existing)
        skipped.update({
            "status": "succeeded",
            "skipped": True,
            "reason": "training environment already installed",
            "exists": True,
            "python": str(python_path),
            "venv_dir": str(venv_dir),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        return _redact_training_value(skipped)
    status = {
        "status": "planned" if normalized["dry_run"] else "running",
        "profile": profile,
        "description": profile_cfg.get("description"),
        "cuda": normalized["cuda"],
        "dry_run": normalized["dry_run"],
        "force": normalized["force"],
        "bridge_instance_id": INSTANCE_ID,
        "started_at": started_at,
        "updated_at": started_at,
        "venv_dir": str(venv_dir),
        "python": str(python_path),
        "supported_tasks": profile_cfg.get("supported_tasks") or [],
        "commands": plan,
        "events": [],
    }
    if normalized["dry_run"]:
        return _redact_training_value(status)

    _write_training_bootstrap_status(profile, status)
    for item in _training_bootstrap_command_plan(normalized):
        event = {
            "step": item["step"],
            "started_at": datetime.utcnow().isoformat() + "Z",
        }
        try:
            command_result = _run_training_bootstrap_command(item["args"], timeout=item["timeout"])
        except subprocess.TimeoutExpired as exc:
            command_result = {
                "returncode": 124,
                "stdout": _redact_training_text(getattr(exc, "stdout", "") or "", limit=12000),
                "stderr": _redact_training_text(getattr(exc, "stderr", "") or "command timed out", limit=12000),
            }
        except Exception as exc:
            command_result = {
                "returncode": 1,
                "stdout": "",
                "stderr": _redact_training_text(exc, limit=12000),
            }
        event.update({
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "returncode": command_result["returncode"],
            "stdout_tail": command_result.get("stdout", "")[-4000:],
            "stderr_tail": command_result.get("stderr", "")[-4000:],
        })
        if item["step"] == "smoke_test" and command_result["returncode"] == 0:
            smoke_line = (command_result.get("stdout") or "").strip().splitlines()[-1:]
            if smoke_line:
                try:
                    event["smoke"] = json.loads(smoke_line[0])
                except Exception:
                    event["smoke"] = {"raw": smoke_line[0][:1000]}
        status["events"].append(event)
        status["updated_at"] = event["completed_at"]
        if command_result["returncode"] != 0:
            status["status"] = "failed"
            status["error"] = f"{item['step']} failed with exit code {command_result['returncode']}"
            _write_training_bootstrap_status(profile, status)
            return _redact_training_value(status)
        if item["step"] == "smoke_test":
            smoke = event.get("smoke") if isinstance(event.get("smoke"), dict) else {}
            if normalized["cuda"] != "cpu" and smoke.get("cuda_available") is False:
                status["status"] = "failed"
                status["error"] = "torch CUDA smoke test failed"
                _write_training_bootstrap_status(profile, status)
                return _redact_training_value(status)
        _write_training_bootstrap_status(profile, status)

    status["status"] = "succeeded"
    status["configured"] = True
    status["exists"] = python_path.exists()
    status["completed_at"] = datetime.utcnow().isoformat() + "Z"
    status["updated_at"] = status["completed_at"]
    status["env"] = {
        "SKILLFORGE_TRAINING_PROFILE": profile,
        "SKILLFORGE_TRAINING_PYTHON": str(python_path),
        "LOCAL_TRAINING_GATEWAY_TASKS": ",".join(profile_cfg.get("supported_tasks") or []),
    }
    _write_training_bootstrap_status(profile, status)
    return _redact_training_value(status)


def _training_model_script(validate_mode):
    return (
        "import json, os, shutil, sys, traceback\n"
        "from pathlib import Path\n"
        "os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')\n"
        "os.environ.setdefault('TQDM_DISABLE', '1')\n"
        "os.environ.setdefault('DISABLE_TQDM', '1')\n"
        "os.environ.setdefault('MODELSCOPE_DISABLE_TQDM', '1')\n"
        "model_id = os.environ['SKILLFORGE_MODEL_ID']\n"
        "modelscope_model_id = os.environ.get('SKILLFORGE_MODELSCOPE_MODEL_ID') or model_id\n"
        "revision = os.environ.get('SKILLFORGE_MODEL_REVISION') or 'main'\n"
        "modelscope_revision = os.environ.get('SKILLFORGE_MODELSCOPE_REVISION') or 'master'\n"
        "target_dir = Path(os.environ['SKILLFORGE_MODEL_DIR'])\n"
        "validate_mode = os.environ.get('SKILLFORGE_VALIDATE_MODE') or 'metadata'\n"
        "source = os.environ.get('SKILLFORGE_MODEL_SOURCE') or 'auto'\n"
        "target_dir.mkdir(parents=True, exist_ok=True)\n"
        "free_gb = shutil.disk_usage(str(target_dir)).free / (1 << 30)\n"
        "result = {'model_id': model_id, 'modelscope_model_id': modelscope_model_id, 'revision': revision, 'modelscope_revision': modelscope_revision, 'source': source, 'target_dir': str(target_dir), 'free_disk_gb': round(free_gb, 2), 'validate_mode': validate_mode, 'download_attempts': []}\n"
        "def emit_result():\n"
        "    print('SKILLFORGE_MODEL_RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)\n"
        "try:\n"
        "    snapshot_path = None\n"
        "    errors = []\n"
        "    if source in {'auto', 'huggingface'}:\n"
        "        try:\n"
        "            from huggingface_hub import snapshot_download as hf_snapshot_download\n"
        "            snapshot_path = hf_snapshot_download(repo_id=model_id, revision=revision, local_dir=str(target_dir), local_dir_use_symlinks=False, resume_download=True)\n"
        "            result['download_attempts'].append({'source': 'huggingface', 'ok': True})\n"
        "        except Exception as exc:\n"
        "            errors.append({'source': 'huggingface', 'error': repr(exc)[-4000:]})\n"
        "            result['download_attempts'].append({'source': 'huggingface', 'ok': False, 'error': repr(exc)[-2000:]})\n"
        "            if source == 'huggingface':\n"
        "                raise\n"
        "    if snapshot_path is None and source in {'auto', 'modelscope'}:\n"
        "        try:\n"
        "            from modelscope import snapshot_download as ms_snapshot_download\n"
        "            snapshot_path = ms_snapshot_download(modelscope_model_id, revision=modelscope_revision, local_dir=str(target_dir))\n"
        "            result['download_attempts'].append({'source': 'modelscope', 'ok': True})\n"
        "        except Exception as exc:\n"
        "            errors.append({'source': 'modelscope', 'error': repr(exc)[-4000:]})\n"
        "            result['download_attempts'].append({'source': 'modelscope', 'ok': False, 'error': repr(exc)[-2000:]})\n"
        "            raise\n"
        "    if snapshot_path is None:\n"
        "        raise RuntimeError('model download failed: ' + json.dumps(errors, ensure_ascii=False))\n"
        "    result['snapshot_path'] = snapshot_path\n"
        "    files = []\n"
        "    total_size = 0\n"
        "    for item in Path(snapshot_path).rglob('*'):\n"
        "        if item.is_file():\n"
        "            size = item.stat().st_size\n"
        "            total_size += size\n"
        "            if len(files) < 200:\n"
        "                files.append({'path': str(item.relative_to(snapshot_path)), 'size': size})\n"
        "    result['file_count'] = len(files)\n"
        "    result['sample_files'] = files[:30]\n"
        "    result['size_gb'] = round(total_size / (1 << 30), 3)\n"
        "    if validate_mode != 'none':\n"
        "        from transformers import AutoConfig, AutoTokenizer\n"
        "        cfg = AutoConfig.from_pretrained(snapshot_path, trust_remote_code=True)\n"
        "        tok = AutoTokenizer.from_pretrained(snapshot_path, trust_remote_code=True)\n"
        "        result['config'] = {'model_type': getattr(cfg, 'model_type', ''), 'architectures': getattr(cfg, 'architectures', None), 'hidden_size': getattr(cfg, 'hidden_size', None), 'num_hidden_layers': getattr(cfg, 'num_hidden_layers', None), 'vocab_size': getattr(cfg, 'vocab_size', None)}\n"
        "        result['tokenizer'] = {'class': tok.__class__.__name__, 'vocab_size': getattr(tok, 'vocab_size', None), 'model_max_length': getattr(tok, 'model_max_length', None)}\n"
        "    if validate_mode == 'load_4bit':\n"
        "        import torch\n"
        "        if not hasattr(torch.nn.Module, 'set_submodule'):\n"
        "            def _sf_set_submodule(self, target, module):\n"
        "                parts = str(target).split('.')\n"
        "                parent = self.get_submodule('.'.join(parts[:-1])) if len(parts) > 1 else self\n"
        "                setattr(parent, parts[-1], module)\n"
        "            torch.nn.Module.set_submodule = _sf_set_submodule\n"
        "        from transformers import AutoModelForCausalLM, BitsAndBytesConfig\n"
        "        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True, bnb_4bit_quant_type='nf4')\n"
        "        model = AutoModelForCausalLM.from_pretrained(snapshot_path, quantization_config=quant, device_map='auto', trust_remote_code=True)\n"
        "        inputs = tok('输出一个JSON: {\"status\":\"ok\"}', return_tensors='pt')\n"
        "        device = next(model.parameters()).device\n"
        "        inputs = {k: v.to(device) for k, v in inputs.items()}\n"
        "        with torch.no_grad():\n"
        "            out = model.generate(**inputs, max_new_tokens=8, do_sample=False)\n"
        "        result['load_4bit'] = {'ok': True, 'device': str(device), 'cuda_available': bool(torch.cuda.is_available()), 'generated_tokens': int(out.shape[-1])}\n"
        "    result['ok'] = True\n"
        "    emit_result()\n"
        "except Exception as exc:\n"
        "    result['ok'] = False\n"
        "    result['error'] = repr(exc)[-4000:]\n"
        "    result['traceback_tail'] = traceback.format_exc()[-8000:]\n"
        "    emit_result()\n"
        "    raise\n"
    )


def _training_model_validation_from_stdout(stdout):
    for line in reversed((stdout or "").strip().splitlines()):
        candidate = line.strip()
        if candidate.startswith("SKILLFORGE_MODEL_RESULT="):
            candidate = candidate.split("=", 1)[1].strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_training_runtime_env_value(key, value, rule):
    text = str(value or "").strip()
    if not text:
        return None, "runtime env value is required"
    kind = rule.get("kind")
    if kind == "absolute_dir_path":
        if text.startswith("file://"):
            text = text[7:]
        path = Path(text).expanduser()
        if not path.is_absolute():
            return None, f"{key} must be an absolute path"
        try:
            resolved = path.resolve(strict=True)
        except Exception:
            return None, f"{key} path does not exist"
        if not resolved.is_dir():
            return None, f"{key} path must be a directory"
        return str(resolved), None
    if kind == "bool":
        lowered = text.lower()
        if lowered not in {"0", "1", "true", "false", "yes", "no", "on", "off", "enabled", "disabled"}:
            return None, f"{key} must be a boolean"
        return "1" if lowered in {"1", "true", "yes", "on", "enabled"} else "0", None
    if kind == "host":
        if not re.match(r"^[A-Za-z0-9_.:-]{1,120}$", text):
            return None, f"{key} has invalid host value"
        return text, None
    if kind == "port":
        try:
            port = int(text)
        except Exception:
            return None, f"{key} must be a port"
        if port < 1 or port > 65535:
            return None, f"{key} must be a port"
        return str(port), None
    return None, f"{key} is not configurable"


def _configure_training_runtime_sync(payload):
    if not isinstance(payload, dict):
        return {"configured": False, "error": "payload must be object"}
    raw_env = payload.get("env")
    if not isinstance(raw_env, dict):
        raw_env = {
            key: payload.get(key)
            for key in TRAINING_RUNTIME_ENV_ALLOWLIST
            if payload.get(key) not in (None, "")
        }
    updates = {}
    errors = {}
    requires_restart = []
    for key, value in raw_env.items():
        clean_key = str(key or "").strip()
        rule = TRAINING_RUNTIME_ENV_ALLOWLIST.get(clean_key)
        if not rule:
            errors[clean_key or ""] = "runtime env key is not allowed"
            continue
        normalized, error = _normalize_training_runtime_env_value(clean_key, value, rule)
        if error:
            errors[clean_key] = error
            continue
        updates[clean_key] = normalized
        if not rule.get("dynamic"):
            requires_restart.append(clean_key)
    if errors:
        return {"configured": False, "errors": errors}
    if not updates:
        return {"configured": False, "error": "no runtime env updates"}
    save_installed_config(extra=updates)
    for key, value in updates.items():
        os.environ[key] = value
    return {
        "configured": True,
        "updated_keys": sorted(updates),
        "requires_restart": sorted(requires_restart),
        "env": {key: updates[key] for key in sorted(updates)},
        "config_path": str(ENV_PATH),
    }


def _internal_training_model_path(normalized, profile_cfg):
    raw = (
        normalized.get("internal_model_ref")
        or os.environ.get(str(profile_cfg.get("internal_model_env") or ""))
        or ""
    )
    raw = str(raw or "").strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    if not raw or raw == normalized.get("profile"):
        env_key = str(profile_cfg.get("internal_model_env") or "")
        if env_key and os.environ.get(env_key):
            raw = os.environ[env_key]
        else:
            raw = f"/opt/models/{normalized.get('profile')}"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None, "internal model path must be absolute"
    try:
        resolved = path.resolve(strict=True)
    except Exception:
        return None, "internal model path does not exist"
    if not resolved.is_dir():
        return None, "internal model path must be a directory"
    return resolved, None


def _link_internal_training_model(model_dir, source_path, *, force=False):
    model_dir = Path(model_dir)
    source_path = Path(source_path)
    if model_dir.exists() or model_dir.is_symlink():
        try:
            if model_dir.resolve(strict=True) == source_path.resolve(strict=True):
                return "already_linked"
        except Exception:
            pass
        if model_dir.is_symlink():
            if force:
                model_dir.unlink()
            else:
                return "model_dir_exists_with_different_target"
        elif model_dir.is_dir():
            try:
                is_empty = not any(model_dir.iterdir())
            except Exception:
                is_empty = False
            if is_empty:
                model_dir.rmdir()
            else:
                return "model_dir_exists_not_empty"
        else:
            return "model_dir_exists_not_directory"
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(source_path), str(model_dir), target_is_directory=True)
    return "linked"


def _internal_training_model_probe(source_path):
    source_path = Path(source_path)
    files = []
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors.index.json"):
        item = source_path / name
        if item.exists():
            files.append({"path": name, "size": item.stat().st_size if item.is_file() else 0})
    return {
        "snapshot_path": str(source_path),
        "file_count": sum(1 for item in source_path.rglob("*") if item.is_file()),
        "sample_files": files[:30],
        "config": (source_path / "config.json").exists(),
        "tokenizer": (source_path / "tokenizer.json").exists() or (source_path / "tokenizer_config.json").exists(),
    }


def _compact_training_model_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _internal_training_model_candidate_terms(profile, profile_cfg):
    values = [
        profile,
        profile_cfg.get("model_id"),
        profile_cfg.get("modelscope_model_id"),
        profile_cfg.get("internal_model_ref"),
    ]
    terms = set()
    for value in values:
        text = str(value or "").strip().lower()
        compact = _compact_training_model_text(text)
        if text:
            terms.add(text)
            terms.add(text.replace("/", "--"))
            terms.add(text.replace("/", "-"))
        if compact:
            terms.add(compact)
    terms.update({"qwen3.6", "qwen36", "35b", "a3b"})
    return {term for term in terms if term}


def _training_model_path_matches_terms(path, terms):
    text = str(path or "").lower()
    compact = _compact_training_model_text(text)
    return any(term in text or _compact_training_model_text(term) in compact for term in terms)


def _training_model_path_matches_profile(path, profile, profile_cfg):
    text = str(path or "").lower()
    compact = _compact_training_model_text(text)
    strict_values = [
        profile,
        profile_cfg.get("model_id"),
        profile_cfg.get("modelscope_model_id"),
        profile_cfg.get("internal_model_ref"),
    ]
    for value in strict_values:
        strict = _compact_training_model_text(value)
        if strict and strict in compact:
            return True
    return (
        "qwen" in compact
        and ("36" in compact or "3.6" in text)
        and "35b" in compact
        and "a3b" in compact
    )


def _training_model_dir_markers(path):
    path = Path(path)
    try:
        has_config = (path / "config.json").is_file()
    except Exception:
        has_config = False
    has_tokenizer = False
    for name in ("tokenizer.json", "tokenizer_config.json", "vocab.json"):
        try:
            if (path / name).is_file():
                has_tokenizer = True
                break
        except Exception:
            continue
    try:
        has_index = (path / "model.safetensors.index.json").is_file()
    except Exception:
        has_index = False
    weight_count = 1 if has_index else 0
    sample_files = []
    total_bytes = 0
    file_count = 0
    try:
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            file_count += 1
            try:
                size = item.stat().st_size
            except Exception:
                size = 0
            total_bytes += size
            rel = str(item.relative_to(path))
            if len(sample_files) < 30:
                sample_files.append({"path": rel, "size": size})
            if item.suffix in {".safetensors", ".bin", ".gguf", ".pt"}:
                weight_count += 1
            if file_count >= 5000:
                break
    except Exception:
        pass
    has_weights = weight_count > 0
    return {
        "has_config": has_config,
        "has_vocab": has_tokenizer,
        "has_tokenizer": has_tokenizer,
        "has_weights": has_weights,
        "has_weight_index": has_index,
        "weight_count": weight_count,
        "file_count": file_count,
        "size_gb": round(total_bytes / (1 << 30), 3),
        "sample_files": sample_files,
    }


def _training_explore_safe_metadata(path):
    config_path = Path(path) / "config.json"
    try:
        if not config_path.is_file():
            return {}
        if config_path.stat().st_size > 2 * 1024 * 1024:
            return {"config_error": "config_too_large"}
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"config_error": exc.__class__.__name__}
    if not isinstance(value, dict):
        return {}
    metadata = {}
    for key in TRAINING_MODEL_EXPLORE_SAFE_CONFIG_KEYS:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool)) or item is None:
            metadata[key] = item
        elif isinstance(item, list):
            metadata[key] = [
                entry
                for entry in item[:20]
                if isinstance(entry, (str, int, float, bool))
            ]
        elif isinstance(item, dict):
            metadata[key] = {
                str(k)[:80]: v
                for k, v in list(item.items())[:20]
                if isinstance(v, (str, int, float, bool)) or v is None
            }
    return metadata


def _normalize_training_model_explore_payload(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return None, error
    raw = payload if isinstance(payload, dict) else {}
    roots = []
    raw_roots = raw.get("roots") if isinstance(raw.get("roots"), list) else []
    for item in raw_roots:
        text = str(item or "").strip()
        if not text:
            continue
        if text.startswith("file://"):
            text = text[7:]
        if not (text.startswith("/") or text.startswith("~")):
            continue
        roots.append(text[:500])
        if len(roots) >= TRAINING_MODEL_EXPLORE_MAX_ROOTS:
            break
    terms = []
    raw_terms = raw.get("terms") if isinstance(raw.get("terms"), list) else []
    for item in raw_terms:
        text = str(item or "").strip().lower()
        if not text:
            continue
        text = re.sub(r"[^a-z0-9._:/ -]+", "", text)[:160]
        if text and text not in terms:
            terms.append(text)
        if len(terms) >= 20:
            break
    try:
        max_dirs = int(raw.get("max_dirs") or raw.get("maxDirs") or 12000)
    except Exception:
        max_dirs = 12000
    try:
        max_depth = int(raw.get("max_depth") or raw.get("maxDepth") or 9)
    except Exception:
        max_depth = 9
    try:
        limit = int(raw.get("limit") or 12)
    except Exception:
        limit = 12
    normalized.update({
        "roots": roots,
        "terms": terms,
        "max_dirs": max(1, min(max_dirs, TRAINING_MODEL_EXPLORE_MAX_DIRS)),
        "max_depth": max(1, min(max_depth, TRAINING_MODEL_EXPLORE_MAX_DEPTH)),
        "limit": max(1, min(limit, TRAINING_MODEL_EXPLORE_MAX_LIMIT)),
        "include_metadata": _training_bootstrap_bool(raw.get("include_metadata") or raw.get("includeMetadata")),
    })
    return normalized, None


def _training_model_explore_raw_roots(profile_cfg, extra_roots=None):
    roots = []
    if isinstance(extra_roots, list):
        roots.extend(str(item or "").strip() for item in extra_roots if str(item or "").strip())
    roots.extend(_internal_training_model_candidate_raw_roots(profile_cfg))
    seen = set()
    result = []
    for item in roots:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result[: TRAINING_MODEL_EXPLORE_MAX_ROOTS + len(TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS) + 4]


def _candidate_scan_root_summary_for_raw_roots(raw_roots):
    rows = []
    seen = set()
    for raw in raw_roots:
        item = {"raw": raw, "exists": False, "is_dir": False, "path": None, "error": None}
        try:
            path = Path(raw).expanduser()
            resolved = path.resolve(strict=True)
            item.update({"exists": True, "is_dir": resolved.is_dir(), "path": str(resolved)})
        except Exception as exc:
            item["error"] = exc.__class__.__name__
        key = item.get("path") or f"missing:{raw}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def _training_model_resolved_roots(raw_roots):
    resolved_roots = []
    seen = set()
    for raw in raw_roots:
        try:
            path = Path(str(raw or "")).expanduser()
            resolved = path.resolve(strict=True)
        except Exception:
            continue
        if not resolved.is_dir():
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_roots.append(resolved)
    return resolved_roots


def _should_skip_training_model_explore_dir(path):
    name = Path(path).name.lower()
    if name in TRAINING_MODEL_EXPLORE_SKIP_DIR_NAMES:
        return True
    return name.startswith(".") and name != ".cache"


def _scan_training_model_candidates(profile, profile_cfg, *, raw_roots, extra_terms=None, limit=8, max_dirs=12000, max_depth=9, include_metadata=False):
    terms = set(_internal_training_model_candidate_terms(profile, profile_cfg))
    for term in extra_terms or []:
        if term:
            terms.add(str(term).lower())
    candidates = []
    seen = set()
    visited = set()
    scanned = 0
    skipped_dirs = 0
    roots = _candidate_scan_root_summary_for_raw_roots(raw_roots)
    common_container_names = {
        ".cache",
        "ai",
        "cache",
        "checkpoints",
        "checkpoint",
        "data",
        "hf",
        "huggingface",
        "hub",
        "llm",
        "llms",
        "ml",
        "modelscope",
        "model",
        "models",
        "nas",
        "qwen",
        "shared",
        "snapshots",
        "storage",
        "transformers",
        "weights",
    }

    def add_candidate(path, reason, ancestor_match=False):
        try:
            resolved = Path(path).resolve(strict=True)
        except Exception:
            return
        key = str(resolved)
        if key in seen or not resolved.is_dir():
            return
        markers = _training_model_dir_markers(resolved)
        if not (markers["has_config"] and (markers["has_vocab"] or markers["has_weights"])):
            return
        profile_match = _training_model_path_matches_profile(resolved, profile, profile_cfg)
        if not (profile_match or ancestor_match):
            return
        seen.add(key)
        candidate = _training_model_candidate_score({
            "path": key,
            "reason": reason,
            "profile_match": profile_match,
            **markers,
        })
        if include_metadata:
            metadata = _training_explore_safe_metadata(resolved)
            if metadata:
                candidate["metadata"] = metadata
        candidates.append(candidate)

    for root in _training_model_resolved_roots(raw_roots):
        stack = [(root, 0, _training_model_path_matches_profile(root, profile, profile_cfg))]
        while stack and scanned < max_dirs and len(candidates) < limit:
            current, depth, ancestor_profile_match = stack.pop()
            if _should_skip_training_model_explore_dir(current):
                skipped_dirs += 1
                continue
            try:
                current_key = str(Path(current).resolve(strict=True))
            except Exception:
                continue
            if current_key in visited:
                continue
            visited.add(current_key)
            scanned += 1
            add_candidate(current, "root" if depth == 0 else "descendant", ancestor_match=ancestor_profile_match)
            if depth >= max_depth:
                continue
            try:
                children = [item for item in current.iterdir() if item.is_dir()]
            except Exception:
                continue
            children.sort(key=lambda item: (not _training_model_path_matches_terms(item, terms), item.name.lower()))
            for child in reversed(children[:300]):
                if _should_skip_training_model_explore_dir(child):
                    skipped_dirs += 1
                    continue
                child_name = child.name.lower()
                child_terms_match = _training_model_path_matches_terms(child, terms)
                child_profile_match = ancestor_profile_match or _training_model_path_matches_profile(child, profile, profile_cfg)
                should_descend = child_terms_match or child_profile_match or depth < 2 or child_name in common_container_names
                if should_descend:
                    stack.append((child, depth + 1, child_profile_match))
    candidates.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("path") or "")))
    return {
        "profile": profile,
        "model_id": profile_cfg.get("model_id"),
        "roots": roots,
        "scan_summary": {
            "scanned_dirs": scanned,
            "visited_dirs": len(visited),
            "skipped_dirs": skipped_dirs,
            "max_dirs": max_dirs,
            "max_depth": max_depth,
            "candidate_count": len(candidates),
            "existing_roots": sum(1 for item in roots if item.get("is_dir")),
        },
        "candidates": candidates[:limit],
    }


def _internal_training_model_candidate_raw_roots(profile_cfg):
    roots = []
    env_key = str(profile_cfg.get("internal_model_env") or "")
    env_value = os.environ.get(env_key) if env_key else ""
    if env_value:
        roots.extend([env_value, str(Path(env_value).expanduser().parent)])
    roots.extend(TRAINING_INTERNAL_MODEL_CANDIDATE_ROOTS)
    roots.extend([str(STATE_DIR / "models"), str(STATE_DIR / "training_models")])
    return [str(item or "").strip() for item in roots if str(item or "").strip()]


def _internal_training_model_candidate_roots(profile_cfg):
    resolved_roots = []
    seen = set()
    for raw in _internal_training_model_candidate_raw_roots(profile_cfg):
        try:
            path = Path(str(raw or "")).expanduser()
            resolved = path.resolve(strict=True)
        except Exception:
            continue
        if not resolved.is_dir():
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_roots.append(resolved)
    return resolved_roots


def _candidate_scan_root_summary(profile_cfg):
    rows = []
    seen = set()
    for raw in _internal_training_model_candidate_raw_roots(profile_cfg):
        item = {"raw": raw, "exists": False, "is_dir": False, "path": None, "error": None}
        try:
            path = Path(raw).expanduser()
            resolved = path.resolve(strict=True)
            item.update({"exists": True, "is_dir": resolved.is_dir(), "path": str(resolved)})
        except Exception as exc:
            item["error"] = exc.__class__.__name__
        key = item.get("path") or f"missing:{raw}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def _training_model_candidate_score(candidate):
    score = 0
    if candidate.get("profile_match"):
        score += 50
    if candidate.get("has_config"):
        score += 20
    if candidate.get("has_tokenizer") or candidate.get("has_vocab"):
        score += 10
    if candidate.get("has_weights"):
        score += 20
    try:
        score += min(int(candidate.get("weight_count") or 0), 20)
    except Exception:
        pass
    reason = str(candidate.get("reason") or "")
    if reason == "root":
        score += 5
    candidate["score"] = score
    return candidate


def _discover_internal_training_model_candidates(profile, profile_cfg, *, limit=8, max_dirs=12000, max_depth=9):
    return _scan_training_model_candidates(
        profile,
        profile_cfg,
        raw_roots=_internal_training_model_candidate_raw_roots(profile_cfg),
        limit=limit,
        max_dirs=max_dirs,
        max_depth=max_depth,
    )


def _find_internal_training_model_candidates(profile, profile_cfg, *, limit=8, max_dirs=12000, max_depth=9):
    return _discover_internal_training_model_candidates(
        profile,
        profile_cfg,
        limit=limit,
        max_dirs=max_dirs,
        max_depth=max_depth,
    )["candidates"]


def _discover_training_models_sync(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    discovery = _discover_internal_training_model_candidates(profile, profile_cfg)
    discovery["source"] = normalized.get("source")
    discovery["internal_model_ref"] = normalized.get("internal_model_ref") or None
    return _redact_training_value(discovery)


def _explore_training_model_paths_sync(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    raw_roots = _training_model_explore_raw_roots(profile_cfg, normalized.get("roots") or [])
    discovery = _scan_training_model_candidates(
        profile,
        profile_cfg,
        raw_roots=raw_roots,
        extra_terms=normalized.get("terms") or [],
        limit=normalized.get("limit") or 12,
        max_dirs=normalized.get("max_dirs") or 12000,
        max_depth=normalized.get("max_depth") or 9,
        include_metadata=bool(normalized.get("include_metadata")),
    )
    discovery["source"] = normalized.get("source")
    discovery["internal_model_ref"] = normalized.get("internal_model_ref") or None
    discovery["requested_roots"] = normalized.get("roots") or []
    discovery["requested_terms"] = normalized.get("terms") or []
    discovery["read_policy"] = {
        "mode": "controlled_read_only",
        "reads_file_contents": bool(normalized.get("include_metadata")),
        "metadata_files": ["config.json"] if normalized.get("include_metadata") else [],
        "remote_shell": False,
    }
    return _redact_training_value(discovery)


def _current_training_model_status(profile=TRAINING_MODEL_DEFAULT_PROFILE, normalized=None):
    normalized, error = (
        (normalized, None)
        if isinstance(normalized, dict)
        else _normalize_training_model_payload({"profile": profile})
    )
    if error:
        return {"status": "unsupported", "profile": profile, "error": error}
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    model_dir = _training_model_profile_dir(profile) / "model"
    stored = _load_training_model_status(profile)
    result = {
        "status": "not_downloaded",
        "profile": profile,
        "model_id": profile_cfg["model_id"],
        "revision": profile_cfg.get("revision") or "main",
        "description": profile_cfg.get("description"),
        "source": normalized["source"],
        "internal_model_ref": normalized.get("internal_model_ref") or None,
        "exists": model_dir.exists(),
        "model_dir": str(model_dir),
        "bridge_instance_id": INSTANCE_ID,
        "supported_profiles": list(TRAINING_MODEL_PROFILES.keys()),
    }
    live_probe = {}
    if normalized["source"] in TRAINING_INTERNAL_MODEL_SOURCES:
        source_path, path_error = _internal_training_model_path(normalized, profile_cfg)
        if path_error:
            discovery = _discover_internal_training_model_candidates(profile, profile_cfg)
            live_probe.update({
                "source_exists": False,
                "source_error": path_error,
                "source_candidates": discovery.get("candidates") or None,
                "scan_summary": discovery.get("scan_summary"),
                "candidate_roots": discovery.get("roots"),
            })
        else:
            live_probe.update({
                "source_exists": True,
                "source_path": str(source_path),
                "source_probe": _internal_training_model_probe(source_path),
            })
    if stored:
        result.update(stored)
        result["exists"] = model_dir.exists()
    result.update(live_probe)
    return _redact_training_value(result)


def _prepare_training_model_sync(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    bootstrap_profile = normalized["bootstrap_profile"]
    bootstrap_status = _current_training_bootstrap_status(bootstrap_profile)
    python_path = _training_bootstrap_python_path(bootstrap_profile)
    model_base = _training_model_profile_dir(profile)
    model_dir = model_base / "model"
    started_at = datetime.utcnow().isoformat() + "Z"
    status = {
        "status": "planned" if normalized["dry_run"] else "running",
        "profile": profile,
        "model_id": normalized["model_id"],
        "modelscope_model_id": normalized["modelscope_model_id"],
        "revision": normalized["revision"],
        "modelscope_revision": normalized["modelscope_revision"],
        "description": profile_cfg.get("description"),
        "bootstrap_profile": bootstrap_profile,
        "dry_run": normalized["dry_run"],
        "force": normalized["force"],
        "auto_discover": normalized.get("auto_discover"),
        "validate_mode": normalized["validate_mode"],
        "source": normalized["source"],
        "internal_model_ref": normalized.get("internal_model_ref") or None,
        "bridge_instance_id": INSTANCE_ID,
        "started_at": started_at,
        "updated_at": started_at,
        "model_dir": str(model_dir),
        "python": str(python_path),
        "events": [],
    }
    internal_source = normalized["source"] in TRAINING_INTERNAL_MODEL_SOURCES
    if not internal_source and (bootstrap_status.get("status") != "succeeded" or not python_path.exists()):
        status["status"] = "failed"
        status["error"] = "training environment is not installed"
        status["bootstrap_env"] = bootstrap_status
        return _redact_training_value(status)
    if normalized["dry_run"]:
        return _redact_training_value(status)
    existing = _load_training_model_status(profile)
    if (
        not normalized["force"]
        and existing.get("status") == "succeeded"
        and model_dir.exists()
        and existing.get("revision") == normalized["revision"]
        and existing.get("validate_mode") == normalized["validate_mode"]
    ):
        skipped = dict(existing)
        skipped.update({
            "status": "succeeded",
            "skipped": True,
            "reason": "training model already prepared",
            "exists": True,
            "model_dir": str(model_dir),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        return _redact_training_value(skipped)
    try:
        model_base.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(model_base).free / (1 << 30)
    except Exception:
        free_gb = 0
    status["free_disk_gb_before"] = round(free_gb, 2)
    if free_gb and free_gb < float(profile_cfg.get("min_free_disk_gb") or 0):
        status["status"] = "failed"
        status["error"] = "insufficient free disk for model download"
        _write_training_model_status(profile, status)
        return _redact_training_value(status)
    _write_training_model_status(profile, status)
    if internal_source and profile == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive":
        configured_prepare_packages = [
            str(item) for item in (profile_cfg.get("prepare_packages") or []) if str(item).strip()
        ]
        prepare_packages = [
            item
            for item in configured_prepare_packages
            if item.split("==", 1)[0].split(">=", 1)[0].split("<", 1)[0].strip() == "kernels"
        ] if python_path.exists() else []
    elif internal_source:
        prepare_packages = []
    else:
        prepare_packages = [str(item) for item in (profile_cfg.get("prepare_packages") or []) if str(item).strip()]
    if prepare_packages:
        event = {
            "step": "install_model_prepare_packages",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "packages": prepare_packages,
        }
        try:
            command_result = _run_training_bootstrap_command(
                [str(python_path), "-m", "pip", "install", *prepare_packages],
                timeout=min(normalized["timeout_seconds"], 3600),
            )
        except subprocess.TimeoutExpired as exc:
            command_result = {
                "returncode": 124,
                "stdout": _redact_training_text(getattr(exc, "stdout", "") or "", limit=12000),
                "stderr": _redact_training_text(getattr(exc, "stderr", "") or "command timed out", limit=12000),
            }
        except Exception as exc:
            command_result = {
                "returncode": 1,
                "stdout": "",
                "stderr": _redact_training_text(exc, limit=12000),
            }
        event.update({
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "returncode": command_result["returncode"],
            "stdout_tail": command_result.get("stdout", "")[-4000:],
            "stderr_tail": command_result.get("stderr", "")[-4000:],
        })
        status["events"].append(event)
        status["updated_at"] = event["completed_at"]
        _write_training_model_status(profile, status)
        if command_result["returncode"] != 0:
            status["status"] = "failed"
            status["error"] = f"install_model_prepare_packages failed with exit code {command_result['returncode']}"
            _write_training_model_status(profile, status)
            return _redact_training_value(status)
    if internal_source:
        event = {
            "step": "link_internal_model",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "source": normalized["source"],
            "internal_model_ref": normalized.get("internal_model_ref") or None,
        }
        source_path, path_error = _internal_training_model_path(normalized, profile_cfg)
        if path_error and normalized.get("auto_discover"):
            discovery = _discover_internal_training_model_candidates(profile, profile_cfg)
            status["discover"] = discovery
            status["source_candidates"] = discovery.get("candidates") or None
            status["scan_summary"] = discovery.get("scan_summary")
            status["candidate_roots"] = discovery.get("roots")
            candidates = discovery.get("candidates") or []
            if candidates:
                candidate_path = str(candidates[0].get("path") or "").strip()
                if candidate_path:
                    normalized = dict(normalized)
                    normalized["internal_model_ref"] = candidate_path
                    status["internal_model_ref"] = candidate_path
                    event["auto_discovered"] = True
                    event["candidate_path"] = candidate_path
                    source_path, path_error = _internal_training_model_path(normalized, profile_cfg)
                    env_key = str(profile_cfg.get("internal_model_env") or "")
                    if source_path is not None and env_key:
                        try:
                            save_installed_config(extra={env_key: str(source_path)})
                            os.environ[env_key] = str(source_path)
                            event["configured_env"] = env_key
                        except Exception as exc:
                            event["configure_env_error"] = _redact_training_text(exc, limit=500)
        if path_error:
            event.update({"completed_at": datetime.utcnow().isoformat() + "Z", "error": path_error})
            status["events"].append(event)
            status["status"] = "failed"
            status["error"] = path_error
            status["source_exists"] = False
            status["source_error"] = path_error
            if "discover" not in status:
                discovery = _discover_internal_training_model_candidates(profile, profile_cfg)
                status["discover"] = discovery
                status["source_candidates"] = discovery.get("candidates") or None
                status["scan_summary"] = discovery.get("scan_summary")
                status["candidate_roots"] = discovery.get("roots")
            _write_training_model_status(profile, status)
            return _redact_training_value(status)
        link_result = _link_internal_training_model(model_dir, source_path, force=normalized["force"])
        event.update({
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "source_path": str(source_path),
            "link_result": link_result,
        })
        status["events"].append(event)
        if link_result.startswith("model_dir_exists"):
            status["status"] = "failed"
            status["error"] = link_result
            status["updated_at"] = event["completed_at"]
            _write_training_model_status(profile, status)
            return _redact_training_value(status)
        probe = _internal_training_model_probe(source_path)
        status.update({
            "status": "succeeded",
            "configured": True,
            "exists": model_dir.exists(),
            "model_dir": str(model_dir),
            "source_exists": True,
            "source_path": str(source_path),
            "snapshot_path": probe.get("snapshot_path"),
            "file_count": probe.get("file_count"),
            "config": probe.get("config"),
            "tokenizer": probe.get("tokenizer"),
            "sample_files": probe.get("sample_files"),
            "completed_at": event["completed_at"],
            "updated_at": event["completed_at"],
        })
        _write_training_model_status(profile, status)
        return _redact_training_value(status)
    env = _training_subprocess_env({
        "HF_HOME": str(STATE_DIR / "hf_home"),
        "HUGGINGFACE_HUB_CACHE": str(STATE_DIR / "hf_home" / "hub"),
        "TRANSFORMERS_CACHE": str(STATE_DIR / "hf_home" / "transformers"),
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "TQDM_DISABLE": "1",
        "DISABLE_TQDM": "1",
        "MODELSCOPE_DISABLE_TQDM": "1",
        "SKILLFORGE_MODEL_ID": normalized["model_id"],
        "SKILLFORGE_MODELSCOPE_MODEL_ID": normalized["modelscope_model_id"],
        "SKILLFORGE_MODEL_REVISION": normalized["revision"],
        "SKILLFORGE_MODELSCOPE_REVISION": normalized["modelscope_revision"],
        "SKILLFORGE_MODEL_DIR": str(model_dir),
        "SKILLFORGE_VALIDATE_MODE": normalized["validate_mode"],
        "SKILLFORGE_MODEL_SOURCE": normalized["source"],
    })
    event = {
        "step": "download_and_validate_model",
        "started_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        proc = subprocess.run(
            [str(python_path), "-c", _training_model_script(normalized["validate_mode"])],
            capture_output=True,
            text=True,
            timeout=normalized["timeout_seconds"],
            env=env,
        )
        command_result = {
            "returncode": int(proc.returncode),
            "stdout": _redact_training_text(proc.stdout, limit=20000),
            "stderr": _redact_training_text(proc.stderr, limit=20000),
        }
    except subprocess.TimeoutExpired as exc:
        command_result = {
            "returncode": 124,
            "stdout": _redact_training_text(getattr(exc, "stdout", "") or "", limit=20000),
            "stderr": _redact_training_text(getattr(exc, "stderr", "") or "command timed out", limit=20000),
        }
    except Exception as exc:
        command_result = {
            "returncode": 1,
            "stdout": "",
            "stderr": _redact_training_text(exc, limit=20000),
        }
    event.update({
        "completed_at": datetime.utcnow().isoformat() + "Z",
        "returncode": command_result["returncode"],
        "stdout_tail": command_result.get("stdout", "")[-8000:],
        "stderr_tail": command_result.get("stderr", "")[-8000:],
    })
    validation_result = _training_model_validation_from_stdout(command_result.get("stdout") or "")
    if validation_result:
        event["validation"] = validation_result
    else:
        stdout_lines = (command_result.get("stdout") or "").strip().splitlines()
        if stdout_lines:
            event["validation"] = {"raw": stdout_lines[-1][:1000]}
    status["events"].append(event)
    status["updated_at"] = event["completed_at"]
    if command_result["returncode"] != 0:
        status["status"] = "failed"
        status["error"] = f"model preparation failed with exit code {command_result['returncode']}"
        validation = event.get("validation") if isinstance(event.get("validation"), dict) else {}
        if validation:
            status["download_attempts"] = validation.get("download_attempts")
            status["error_detail"] = validation.get("error") or validation.get("traceback_tail")
        _write_training_model_status(profile, status)
        return _redact_training_value(status)
    validation = event.get("validation") if isinstance(event.get("validation"), dict) else {}
    status.update({
        "status": "succeeded",
        "configured": True,
        "exists": model_dir.exists(),
        "model_dir": str(model_dir),
        "snapshot_path": validation.get("snapshot_path"),
        "size_gb": validation.get("size_gb"),
        "file_count": validation.get("file_count"),
        "config": validation.get("config"),
        "tokenizer": validation.get("tokenizer"),
        "load_4bit": validation.get("load_4bit"),
        "download_attempts": validation.get("download_attempts"),
        "sample_files": validation.get("sample_files"),
        "completed_at": datetime.utcnow().isoformat() + "Z",
    })
    status["updated_at"] = status["completed_at"]
    _write_training_model_status(profile, status)
    return _redact_training_value(status)


INTELLIGENCE_GATEWAY_RESPONSE_MAX_BYTES = 512 * 1024
INTELLIGENCE_GATEWAY_PAYLOAD_MAX_BYTES = 2 * 1024 * 1024


def _intelligence_gateway_base_url():
    return (
        os.environ.get("LOCAL_INTELLIGENCE_GATEWAY_URL")
        or LOCAL_INTELLIGENCE_GATEWAY_URL
        or os.environ.get("INTELLIGENCE_GATEWAY_URL")
        or os.environ.get("AGENT_ANALYSIS_GATEWAY_URL")
        or ""
    ).strip().rstrip("/")


def _intelligence_gateway_token():
    return (
        os.environ.get("LOCAL_INTELLIGENCE_GATEWAY_TOKEN")
        or LOCAL_INTELLIGENCE_GATEWAY_TOKEN
        or os.environ.get("INTELLIGENCE_GATEWAY_TOKEN")
        or os.environ.get("AGENT_ANALYSIS_GATEWAY_TOKEN")
        or ""
    ).strip()


def _intelligence_gateway_request(payload, timeout=120):
    base_url = _intelligence_gateway_base_url()
    if not base_url:
        raise RuntimeError("local intelligence gateway is not configured")
    raw_payload = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw_payload) > INTELLIGENCE_GATEWAY_PAYLOAD_MAX_BYTES:
        raise RuntimeError("intelligence gateway payload too large")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    gateway_token = _intelligence_gateway_token()
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    url = f"{base_url}/intelligence/analyze"
    req = urllib.request.Request(url, data=raw_payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=max(1, int(timeout))) as resp:
            raw = resp.read(INTELLIGENCE_GATEWAY_RESPONSE_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"intelligence gateway HTTP {exc.code}: {_redact_training_text(body, limit=1000)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"intelligence gateway request failed: {_redact_training_text(exc, limit=1000)}") from exc
    if len(raw) > INTELLIGENCE_GATEWAY_RESPONSE_MAX_BYTES:
        raise RuntimeError("intelligence gateway response too large")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("intelligence gateway response is not JSON") from exc
    return parsed if isinstance(parsed, dict) else {"output": parsed}


async def _call_intelligence_gateway(payload, timeout=120):
    return await asyncio.to_thread(_intelligence_gateway_request, payload, timeout)


def _safe_text(value, limit=2000):
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(value)
    return _redact_training_text(text, limit=limit).strip()


def _nested_get(root, path):
    current = root
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _context_value(context, *keys):
    if not isinstance(context, dict):
        return None
    for key in keys:
        if key in context:
            return context.get(key)
    for parent in ("facts", "input", "metrics", "video", "target", "current_video", "payload"):
        nested = context.get(parent)
        if isinstance(nested, dict):
            for key in keys:
                if key in nested:
                    return nested.get(key)
    return None


def _list_titles(value, *, limit=5):
    if not isinstance(value, list):
        return []
    titles = []
    for item in value[:limit]:
        if isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("summary") or item.get("reason")
            if title:
                titles.append(_safe_text(title, limit=120))
        elif item not in (None, ""):
            titles.append(_safe_text(item, limit=120))
    return [item for item in titles if item]


def _bridge_local_intelligence_analyze(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("payload must be object")
    context = payload.get("context_pack") if isinstance(payload.get("context_pack"), dict) else {}
    skillforge = context.get("skillforge") if isinstance(context.get("skillforge"), dict) else {}
    inference = skillforge.get("deployed_model_inference") if isinstance(skillforge.get("deployed_model_inference"), dict) else {}
    inference_text = _safe_text(inference.get("text"), limit=1800)

    target_video = _context_value(context, "video_id", "videoId", "video_name", "videoName", "title")
    topic = _context_value(context, "topic_key", "topic", "theme", "category")
    current_cost = _context_value(context, "cost", "spend", "consume", "消耗")
    high_refs = (
        _nested_get(context, ("high_consumption_videos",))
        or _nested_get(context, ("facts", "high_consumption_videos"))
        or _nested_get(context, ("benchmarks", "high_consumption"))
        or _nested_get(context, ("paid_compare", "high_consumption_videos"))
    )
    todo_titles = _list_titles(
        _nested_get(context, ("todos",))
        or _nested_get(context, ("output", "todos"))
        or _nested_get(context, ("facts", "todos")),
        limit=6,
    )
    high_ref_titles = _list_titles(high_refs, limit=5)

    evidence = []
    if inference_text:
        evidence.append({
            "type": "deployed_model_inference",
            "summary": inference_text[:600],
            "text_sha256": inference.get("text_sha256"),
            "model_deployment_id": inference.get("model_deployment_id"),
        })
    if high_ref_titles:
        evidence.append({"type": "high_consumption_reference", "items": high_ref_titles})
    if current_cost not in (None, ""):
        evidence.append({"type": "current_consumption", "value": current_cost})

    gaps = []
    if not high_ref_titles:
        gaps.append("同主题高消耗参照不足，需要继续拉取相似主题/同品类高消耗样本。")
    if not inference_text:
        gaps.append("未收到已部署小模型推理文本，本次只能做规则化结构整理。")
    if not todo_titles:
        gaps.append("输出中缺少明确待办，建议补齐负责人、验证指标和投放承接字段。")

    recommendations = []
    if inference_text:
        recommendations.append("优先复核小模型诊断中指出的素材钩子、点击承接和证据缺口。")
    if high_ref_titles:
        recommendations.append("将低消耗视频与高消耗参照逐项对比开头三秒、利益点、镜头节奏和人群承接。")
    recommendations.append("补查预算、出价、人群包、学习期和频控，判断低消耗是否由投放承接限制导致。")
    recommendations.extend(todo_titles[:3])

    summary_bits = ["Bridge 本地分析 Agent 已完成结构化分析。"]
    if target_video:
        summary_bits.append(f"目标: {_safe_text(target_video, limit=120)}。")
    if topic:
        summary_bits.append(f"主题: {_safe_text(topic, limit=120)}。")
    if inference_text:
        summary_bits.append(f"小模型依据: {inference_text[:220]}")
    elif gaps:
        summary_bits.append(gaps[0])

    return {
        "output": {
            "summary": " ".join(summary_bits)[:1000],
            "key_findings": [
                item for item in [
                    "已读取已部署模型推理文本" if inference_text else "",
                    f"可用高消耗参照 {len(high_ref_titles)} 条" if high_ref_titles else "",
                    f"待办线索 {len(todo_titles)} 条" if todo_titles else "",
                ] if item
            ],
            "evidence": evidence,
            "gaps": gaps,
            "recommendations": recommendations[:8],
            "manager_value": "把低消耗问题拆成素材内容差距、同主题参照差距和投放承接缺口，便于管理者分派补查与复盘。",
            "consumer_value": "把消费者可感知的开头吸引、利益点清晰度和行动理由作为优化重点，而不是只看消耗数字。",
        },
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": max(1, len(inference_text) // 4),
            "total_tokens": max(1, len(inference_text) // 4),
        },
        "raw_output": inference_text,
        "backend": "bridge_local_structured_analyzer",
        "model": "bridge-local-analysis-agent",
    }


def _training_jobs_dir():
    return STATE_DIR / "training_jobs"


def _training_job_path(job_id):
    safe_name = job_id.replace(":", "_")
    return _training_jobs_dir() / f"{safe_name}.json"


def _normalize_training_job_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception as exc:
        return None, f"payload is not JSON serializable: {exc}"
    if len(raw) > TRAINING_JOB_PAYLOAD_MAX_BYTES:
        return None, f"payload exceeds {TRAINING_JOB_PAYLOAD_MAX_BYTES} bytes"
    job_id = payload.get("job_id")
    if not _valid_training_job_id(job_id):
        return None, "invalid job_id"
    job_type = str(payload.get("job_type") or "eval").strip().lower()
    if job_type not in TRAINING_ALLOWED_TASKS:
        return None, "unsupported training job_type"
    spec = payload.get("spec") or {}
    if not isinstance(spec, dict):
        return None, "spec must be object"
    normalized = {
        "job_id": job_id,
        "job_type": job_type,
        "title": str(payload.get("title") or job_id)[:200],
        "department": str(payload.get("department") or "")[:50],
        "target_skill_id": str(payload.get("target_skill_id") or "")[:50],
        "target_gateway_id": str(payload.get("target_gateway_id") or "")[:50],
        "dataset_ref": str(payload.get("dataset_ref") or "")[:500],
        "training_strategy": str(payload.get("training_strategy") or "")[:100],
        "objective": str(payload.get("objective") or "")[:4000],
        "risk_level": str(payload.get("risk_level") or "")[:20],
        "spec": spec,
        "control": payload.get("control") if isinstance(payload.get("control"), dict) else {},
    }
    dataset_package = payload.get("dataset_package")
    if isinstance(dataset_package, dict):
        samples = dataset_package.get("samples")
        if not isinstance(samples, list):
            return None, "dataset_package.samples must be list"
        normalized["dataset_package"] = dataset_package
    dataset_package_ref = payload.get("dataset_package_ref") or payload.get("datasetPackageRef")
    if isinstance(dataset_package_ref, dict):
        dataset_path = str(dataset_package_ref.get("dataset_path") or dataset_package_ref.get("path") or "").strip()
        if dataset_path:
            path = _safe_local_path(dataset_path)
            if path is None or not path.is_file():
                return None, "dataset_package_ref.dataset_path must be a bridge-local dataset file"
            dataset_package_ref = dict(dataset_package_ref)
            dataset_package_ref["dataset_path"] = str(path)
            normalized["dataset_package_ref"] = dataset_package_ref
    return normalized, None


def _load_training_job_record(job_id):
    path = _training_job_path(job_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_training_job_record(job_id, record):
    base = _training_jobs_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = _training_job_path(job_id)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _training_dataset_package_summary(package):
    if not isinstance(package, dict):
        return None
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    return {
        "format": package.get("format"),
        "source": package.get("source"),
        "dataset_ref": package.get("dataset_ref"),
        "target_skill_id": package.get("target_skill_id"),
        "manifest_hash": package.get("manifest_hash"),
        "manifest_sample_count": package.get("manifest_sample_count"),
        "manifest_train_count": package.get("manifest_train_count"),
        "manifest_eval_count": package.get("manifest_eval_count"),
        "inline_limit": package.get("inline_limit"),
        "inline_eval_limit": package.get("inline_eval_limit"),
        "inline_max_content_bytes": package.get("inline_max_content_bytes"),
        "sample_count": package.get("sample_count"),
        "train_count": package.get("train_count"),
        "eval_count": package.get("eval_count"),
        "input_contract": package.get("input_contract") if isinstance(package.get("input_contract"), dict) else {},
        "output_contract": package.get("output_contract") if isinstance(package.get("output_contract"), dict) else {},
        "lineage": lineage,
        "sample_fingerprints": [
            {
                "id": item.get("id"),
                "split": item.get("split"),
                "input_sha256": (item.get("metadata") or {}).get("input_sha256") if isinstance(item.get("metadata"), dict) else None,
                "output_sha256": (item.get("metadata") or {}).get("output_sha256") if isinstance(item.get("metadata"), dict) else None,
                "sample_sha256": (item.get("metadata") or {}).get("sample_sha256") if isinstance(item.get("metadata"), dict) else None,
            }
            for item in (package.get("samples") if isinstance(package.get("samples"), list) else [])[:50]
            if isinstance(item, dict)
        ],
        "raw_payload_returned": False,
        "samples": "[REDACTED]",
    }


def _training_payload_for_record(normalized):
    safe = _redact_training_value(normalized)
    if isinstance(safe, dict) and isinstance(normalized.get("dataset_package"), dict):
        safe["dataset_package"] = _training_dataset_package_summary(normalized.get("dataset_package"))
    if isinstance(safe, dict) and isinstance(normalized.get("dataset_package_ref"), dict):
        ref = dict(normalized.get("dataset_package_ref") or {})
        ref["raw_payload_returned"] = False
        safe["dataset_package_ref"] = _redact_training_value(ref)
    return safe


def _append_training_job_event(record, event_type, message, **extra):
    now_iso = datetime.utcnow().isoformat() + "Z"
    event = {
        "ts": now_iso,
        "type": str(event_type or "log")[:80],
        "message": _redact_training_text(message, limit=2000),
    }
    for key, value in extra.items():
        event[str(key)[:80]] = _redact_training_value(value)
    events = record.get("events") if isinstance(record.get("events"), list) else []
    events.append(event)
    record["events"] = events[-200:]
    record["updated_at"] = now_iso
    return event


def _local_training_job_dir(job_id):
    safe_name = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(job_id or "training_job"))[:120]
    return STATE_DIR / "training_runs" / safe_name


def _training_sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _training_artifact_local_path(value):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme == "file":
        text = urllib.parse.unquote(parsed.path or "")
    elif parsed.scheme:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    try:
        path = path.resolve(strict=True)
    except Exception:
        return None
    try:
        path.relative_to(STATE_DIR.resolve(strict=False))
    except Exception:
        return None
    if not path.is_file():
        return None
    return path


def _training_artifact_record_matches(artifact_id, item, *, artifact_name="", sha256=""):
    if not isinstance(item, dict):
        return False
    expected = {
        str(artifact_id or "").strip(),
        str(artifact_name or "").strip(),
        str(sha256 or "").strip(),
    }
    expected.discard("")
    candidates = {
        str(item.get("id") or ""),
        str(item.get("name") or ""),
        str(item.get("filename") or ""),
        str(item.get("sha256") or item.get("hash") or ""),
    }
    candidates.discard("")
    return bool(expected & candidates)


def _training_artifact_find_record(record, artifact_id, *, artifact_name="", sha256=""):
    artifacts = record.get("artifacts") if isinstance(record, dict) and isinstance(record.get("artifacts"), list) else []
    for item in artifacts:
        if isinstance(item, dict) and _training_artifact_record_matches(
            artifact_id,
            item,
            artifact_name=artifact_name,
            sha256=sha256,
        ):
            return item
    return None


def _training_artifact_file_payload(job_id, artifact_id, item, *, include_content):
    path = _training_artifact_local_path(
        item.get("uri")
        or item.get("artifact_uri")
        or item.get("path")
        or item.get("url")
    )
    if path is None:
        return None, "training artifact file not found"
    size = int(path.stat().st_size)
    if include_content and size > TRAINING_ARTIFACT_DOWNLOAD_MAX_BYTES:
        return None, "training artifact is too large"
    sha = str(item.get("sha256") or item.get("hash") or "").strip()
    if not sha:
        sha = _training_sha256_file(path)
    payload = {
        "job_id": job_id,
        "artifact_id": artifact_id,
        "filename": item.get("name") or item.get("filename") or path.name,
        "content_type": item.get("content_type") or item.get("mime_type") or "application/octet-stream",
        "sha256": sha,
        "size_bytes": size,
        "uri": str(path),
        "exists": True,
    }
    if include_content:
        payload["content_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
    return payload, None


def _training_imports_dir():
    return STATE_DIR / "training_imports"


def _training_datasets_dir():
    return STATE_DIR / "training_datasets"


def _valid_training_dataset_id(value):
    return bool(re.match(r"^[A-Za-z0-9_.:-]{1,160}$", str(value or "")))


def _safe_training_dataset_id(value):
    text = str(value or "").strip()
    if not text:
        text = "dataset-" + hashlib.sha256(f"{INSTANCE_ID}:{time.time()}".encode()).hexdigest()[:24]
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:160]
    return text or "dataset"


def _training_dataset_record_dir(dataset_id):
    return _training_datasets_dir() / _safe_training_dataset_id(dataset_id)


def _training_dataset_incoming_dir(dataset_id):
    return _training_datasets_dir() / ".incoming" / _safe_training_dataset_id(dataset_id)


def _training_dataset_hash_file(path):
    return _training_model_hash_file(path)


def _training_dataset_validate_source_path(value):
    text = str(value or "").strip()
    if text.startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path or "")
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None, "dataset source_path must be absolute"
    try:
        resolved = path.resolve(strict=True)
    except Exception:
        return None, "dataset source_path does not exist"
    try:
        resolved.relative_to(_training_datasets_dir().resolve(strict=False))
    except Exception:
        return None, "dataset source_path must be under bridge training_datasets dir"
    if resolved.is_file():
        if resolved.name != "dataset.json":
            return None, "dataset source file must be dataset.json"
        return resolved.parent, None
    if not resolved.is_dir():
        return None, "dataset source_path must be a directory or dataset.json"
    if not (resolved / "dataset.json").is_file():
        return None, "dataset source_path must contain dataset.json"
    return resolved, None


def _training_dataset_manifest(source_path, *, dataset_id=None, hash_files=True):
    source_path = Path(source_path).resolve(strict=True)
    files = []
    total_size = 0
    skipped = []
    root = source_path
    for item in sorted(source_path.rglob("*"), key=lambda value: str(value)):
        try:
            if not item.is_file():
                continue
            rel = item.relative_to(root).as_posix()
            if not rel or rel.startswith("../") or "/../" in rel:
                skipped.append({"path": rel, "reason": "unsafe_relative_path"})
                continue
            resolved = item.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except Exception:
                skipped.append({"path": rel, "reason": "symlink_target_outside_dataset"})
                continue
            stat = resolved.stat()
            entry = {"path": rel, "size_bytes": int(stat.st_size)}
            if hash_files:
                entry["sha256"] = _training_dataset_hash_file(resolved)
            files.append(entry)
            total_size += int(stat.st_size)
        except Exception as exc:
            skipped.append({"path": str(item), "reason": exc.__class__.__name__})
    metadata = {}
    metadata_path = source_path / "manifest.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
    manifest = {
        "asset_type": "training_dataset",
        "dataset_id": str(dataset_id or metadata.get("dataset_id") or source_path.name)[:160],
        "source_path": str(source_path),
        "bridge_instance_id": INSTANCE_ID,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "file_count": len(files),
        "total_size_bytes": total_size,
        "files": files,
        "skipped_files": skipped[:100],
        "hash_files": bool(hash_files),
        "dataset": _redact_training_value(metadata.get("dataset") if isinstance(metadata.get("dataset"), dict) else {}),
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


def _training_dataset_write_chunk(payload):
    payload = payload if isinstance(payload, dict) else {}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId"))
    if not _valid_training_dataset_id(dataset_id):
        return {"status": "failed", "error": "invalid dataset_id"}
    try:
        offset = int(payload.get("offset") or 0)
    except Exception:
        offset = 0
    offset = max(0, offset)
    content_b64 = str(payload.get("content_base64") or payload.get("base64") or "").strip()
    try:
        raw = base64.b64decode(content_b64, validate=True) if content_b64 else b""
    except Exception:
        return {"status": "failed", "error": "content_base64 is invalid"}
    incoming = _training_dataset_incoming_dir(dataset_id)
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / "dataset.json.part"
    try:
        mode = "wb" if offset == 0 else "r+b"
        with open(dest, mode) as fh:
            fh.seek(offset)
            fh.write(raw)
        size = dest.stat().st_size
    except FileNotFoundError:
        return {"status": "failed", "error": "dataset upload must start at offset 0"}
    except Exception as exc:
        return {"status": "failed", "error": _redact_training_text(exc, limit=1000)}
    return {
        "status": "ok",
        "dataset_id": dataset_id,
        "offset": offset,
        "size_bytes": len(raw),
        "written_size_bytes": int(size),
        "bridge_instance_id": INSTANCE_ID,
    }


def _training_dataset_commit(payload):
    payload = payload if isinstance(payload, dict) else {}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId"))
    if not _valid_training_dataset_id(dataset_id):
        return {"status": "failed", "error": "invalid dataset_id"}
    incoming = _training_dataset_incoming_dir(dataset_id)
    part = incoming / "dataset.json.part"
    if not part.is_file():
        return {"status": "failed", "error": "dataset upload not found"}
    expected_sha = str(payload.get("sha256") or payload.get("dataset_sha256") or "").strip().lower()
    actual_sha = _training_dataset_hash_file(part)
    if expected_sha and expected_sha != actual_sha:
        return {"status": "failed", "error": "dataset sha256 mismatch", "sha256": actual_sha}
    target_dir = _training_dataset_record_dir(dataset_id)
    force = _training_bootstrap_bool(payload.get("force"))
    if force and target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = target_dir / "dataset.json"
    tmp_path = target_dir / "dataset.json.tmp"
    shutil.copy2(part, tmp_path)
    tmp_path.replace(dataset_path)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    manifest = {
        "dataset_id": dataset_id,
        "dataset_path": str(dataset_path),
        "sha256": actual_sha,
        "size_bytes": int(dataset_path.stat().st_size),
        "bridge_instance_id": INSTANCE_ID,
        "committed_at": datetime.utcnow().isoformat() + "Z",
        "dataset": _redact_training_value(metadata),
    }
    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(json.dumps(_redact_training_value(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(incoming, ignore_errors=True)
    return {
        "status": "succeeded",
        "dataset_id": dataset_id,
        "dataset_dir": str(target_dir),
        "dataset_path": str(dataset_path),
        "manifest_path": str(manifest_path),
        "sha256": actual_sha,
        "size_bytes": int(dataset_path.stat().st_size),
        "bridge_instance_id": INSTANCE_ID,
    }


def _training_dataset_status(payload):
    payload = payload if isinstance(payload, dict) else {}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId"))
    target_dir = _training_dataset_record_dir(dataset_id)
    dataset_path = target_dir / "dataset.json"
    manifest_path = target_dir / "manifest.json"
    if not dataset_path.is_file():
        return {
            "status": "missing",
            "exists": False,
            "dataset_id": dataset_id,
            "dataset_dir": str(target_dir),
            "bridge_instance_id": INSTANCE_ID,
        }
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    return _redact_training_value({
        "status": "ready",
        "exists": True,
        "dataset_id": dataset_id,
        "dataset_dir": str(target_dir),
        "dataset_path": str(dataset_path),
        "manifest_path": str(manifest_path),
        "sha256": manifest.get("sha256") or _training_dataset_hash_file(dataset_path),
        "size_bytes": int(dataset_path.stat().st_size),
        "manifest": manifest,
        "bridge_instance_id": INSTANCE_ID,
    })


def _training_dataset_export_manifest_payload(payload):
    payload = payload if isinstance(payload, dict) else {}
    source_raw = (
        payload.get("source_path")
        or payload.get("sourcePath")
        or payload.get("path")
        or (_training_dataset_record_dir(payload.get("dataset_id") or payload.get("datasetId")) if payload.get("dataset_id") or payload.get("datasetId") else "")
    )
    source_path, path_error = _training_dataset_validate_source_path(source_raw)
    if path_error:
        return {"status": "failed", "error": path_error}
    hash_files = not str(payload.get("hash_files") or payload.get("hashFiles") or "").lower() in {"0", "false", "no"}
    manifest = _training_dataset_manifest(source_path, dataset_id=payload.get("dataset_id") or payload.get("datasetId"), hash_files=hash_files)
    manifest["status"] = "ready"
    return _redact_training_value(manifest)


def _training_dataset_export_file_chunk(payload):
    payload = payload if isinstance(payload, dict) else {}
    source_raw = (
        payload.get("source_path")
        or payload.get("sourcePath")
        or payload.get("path")
        or (_training_dataset_record_dir(payload.get("dataset_id") or payload.get("datasetId")) if payload.get("dataset_id") or payload.get("datasetId") else "")
    )
    source_path, path_error = _training_dataset_validate_source_path(source_raw)
    if path_error:
        return {"status": "failed", "error": path_error}
    relpath = str(payload.get("file_path") or payload.get("filePath") or payload.get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    try:
        offset = int(payload.get("offset") or 0)
    except Exception:
        offset = 0
    offset = max(0, offset)
    max_bytes = _safe_int_between(payload.get("max_bytes") or payload.get("maxBytes"), TRAINING_MODEL_TRANSFER_CHUNK_BYTES, 1, TRAINING_MODEL_TRANSFER_CHUNK_BYTES)
    try:
        file_path = (source_path / relpath).resolve(strict=True)
        file_path.relative_to(source_path.resolve(strict=True))
    except Exception:
        return {"status": "failed", "error": "file not found"}
    if not file_path.is_file():
        return {"status": "failed", "error": "file not found"}
    try:
        total_size = file_path.stat().st_size
        with open(file_path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(max_bytes)
    except Exception as exc:
        return {"status": "failed", "error": _redact_training_text(exc, limit=1000)}
    return {
        "status": "ok",
        "asset_type": "training_dataset",
        "source_path": str(source_path),
        "file_path": relpath,
        "offset": offset,
        "size_bytes": len(raw),
        "total_size_bytes": int(total_size),
        "eof": offset + len(raw) >= int(total_size),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


def _training_dataset_export_start(payload):
    payload = payload if isinstance(payload, dict) else {}
    manifest = _training_dataset_export_manifest_payload(payload)
    if not isinstance(manifest, dict) or manifest.get("status") != "ready":
        return manifest if isinstance(manifest, dict) else {"status": "failed", "error": "dataset export manifest failed"}
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not files:
        return {"status": "failed", "error": "dataset source has no exportable files"}
    source_path = str(manifest.get("source_path") or "")
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    session_id = (
        re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(payload.get("session_id") or ""))[:80]
        or "ds-" + hashlib.sha256(f"{INSTANCE_ID}:{source_path}:{time.time()}".encode()).hexdigest()[:24]
    )
    ttl = _safe_int_between(payload.get("ttl_seconds") or payload.get("ttlSeconds"), TRAINING_MODEL_EXPORT_SESSION_TTL_SECONDS, 60, 24 * 60 * 60)
    expires_ts = time.time() + ttl
    base_url = _training_model_export_server_base_url()
    export_url = f"{base_url}/training-model-export/{urllib.parse.quote(session_id, safe='')}"
    session = {
        "session_id": session_id,
        "token": token,
        "asset_type": "training_dataset",
        "source_path": source_path,
        "content_roots": [source_path],
        "manifest": manifest,
        "file_map": {item["path"]: item for item in files if isinstance(item, dict) and item.get("path")},
        "created_at": manifest["created_at"],
        "expires_at": datetime.fromtimestamp(expires_ts, timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at_ts": expires_ts,
        "export_url": export_url,
    }
    with _MODEL_EXPORT_LOCK:
        _training_model_export_cleanup_locked()
        _MODEL_EXPORT_SESSIONS[session_id] = session
    return {
        "status": "ready",
        "asset_type": "training_dataset",
        "session_id": session_id,
        "dataset_id": manifest.get("dataset_id"),
        "source_path": source_path,
        "file_count": manifest["file_count"],
        "total_size_bytes": manifest["total_size_bytes"],
        "manifest_sha256": manifest["manifest_sha256"],
        "export_url": export_url,
        "manifest_url": f"{export_url}/manifest",
        "token": token,
        "expires_at": session["expires_at"],
        "bridge_instance_id": INSTANCE_ID,
    }


def _training_dataset_import_status_path(import_id):
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(import_id or "dataset"))[:160]
    return _training_datasets_dir() / "status" / f"{safe_id}.json"


def _write_training_dataset_import_status(import_id, status):
    path = _training_dataset_import_status_path(import_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(status), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_training_dataset_import_status(import_id):
    path = _training_dataset_import_status_path(import_id)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _training_dataset_target_dir(dataset_id, manifest_sha):
    safe_id = _safe_training_dataset_id(dataset_id)
    safe_sha = re.sub(r"[^a-fA-F0-9]+", "", str(manifest_sha or ""))[:64]
    suffix = safe_sha[:24] if safe_sha else hashlib.sha256(f"{safe_id}:{time.time()}".encode()).hexdigest()[:24]
    return _training_datasets_dir() / safe_id / suffix


def _training_dataset_import_from_export(payload):
    payload = payload if isinstance(payload, dict) else {}
    token = str(payload.get("token") or payload.get("export_token") or "").strip()
    manifest_url = str(payload.get("manifest_url") or payload.get("manifestUrl") or "").strip()
    export_url = str(payload.get("export_url") or payload.get("exportUrl") or "").strip().rstrip("/")
    if not manifest_url and export_url:
        manifest_url = f"{export_url}/manifest"
    if not manifest_url:
        return {"status": "failed", "error": "manifest_url or export_url is required"}
    timeout = _safe_int_between(payload.get("timeout_seconds") or payload.get("timeoutSeconds"), 21600, 60, 86400)
    manifest = _training_model_transfer_request_json(manifest_url, token, timeout=min(timeout, 300))
    if str(manifest.get("asset_type") or "") != "training_dataset":
        return {"status": "failed", "error": "manifest asset_type mismatch"}
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not files:
        return {"status": "failed", "error": "manifest has no files"}
    manifest_sha = str(manifest.get("manifest_sha256") or "").strip().lower()
    if not manifest_sha:
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    expected_manifest_sha = str(payload.get("manifest_sha256") or payload.get("manifestSha256") or "").strip().lower()
    if expected_manifest_sha and expected_manifest_sha != manifest_sha:
        return {"status": "failed", "error": "manifest sha256 mismatch"}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or manifest.get("dataset_id") or f"dataset:{manifest_sha[:12]}")
    import_id = (
        re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(payload.get("import_id") or payload.get("importId") or ""))[:120]
        or f"{dataset_id}:{manifest_sha[:24]}"
    )
    target_dir = _training_dataset_target_dir(dataset_id, manifest_sha)
    force = _training_bootstrap_bool(payload.get("force"))
    if force and target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.utcnow().isoformat() + "Z"
    status = {
        "status": "running",
        "asset_type": "training_dataset",
        "import_id": import_id,
        "dataset_id": dataset_id,
        "manifest_sha256": manifest_sha,
        "source_gateway_id": str(payload.get("source_gateway_id") or payload.get("sourceGatewayId") or manifest.get("bridge_instance_id") or "")[:120],
        "source_path": str(payload.get("source_path") or payload.get("sourcePath") or manifest.get("source_path") or "")[:1000],
        "target_dir": str(target_dir),
        "file_count": len(files),
        "total_size_bytes": int(manifest.get("total_size_bytes") or 0),
        "imported_files": 0,
        "skipped_files": 0,
        "started_at": started_at,
        "updated_at": started_at,
    }
    _write_training_dataset_import_status(import_id, status)
    try:
        base_url = export_url
        if not base_url:
            parsed = urllib.parse.urlparse(manifest_url)
            base_path = parsed.path.rsplit("/manifest", 1)[0]
            base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))
        imported = 0
        skipped = 0
        for index, entry in enumerate(files, 1):
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path") or "").strip().lstrip("/")
            if not rel or rel.startswith("../") or "/../" in rel:
                raise RuntimeError(f"unsafe manifest file path: {rel}")
            expected_size = int(entry.get("size_bytes") or 0)
            expected_sha = str(entry.get("sha256") or "").strip().lower()
            dest = target_dir / rel
            if dest.exists() and dest.stat().st_size == expected_size:
                if not expected_sha or _training_dataset_hash_file(dest) == expected_sha:
                    skipped += 1
                    continue
            file_url = f"{base_url}/files/{urllib.parse.quote(rel, safe='/')}"
            _training_model_transfer_download_file(file_url, token, dest, expected_size, timeout=min(timeout, 3600))
            if expected_sha and _training_dataset_hash_file(dest) != expected_sha:
                raise RuntimeError(f"file sha256 mismatch: {rel}")
            imported += 1
            status.update({
                "imported_files": imported,
                "skipped_files": skipped,
                "current_file": rel,
                "progress": round(index / max(len(files), 1), 4),
                "updated_at": datetime.utcnow().isoformat() + "Z",
            })
            _write_training_dataset_import_status(import_id, status)
        manifest_path = target_dir / ".skillforge_dataset_manifest.json"
        manifest_path.write_text(json.dumps(_redact_training_value(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        completed_at = datetime.utcnow().isoformat() + "Z"
        dataset_path = target_dir / "dataset.json"
        result = {
            **status,
            "status": "succeeded",
            "progress": 1.0,
            "imported_files": imported,
            "skipped_files": skipped,
            "completed_at": completed_at,
            "updated_at": completed_at,
            "dataset_dir": str(target_dir),
            "dataset_path": str(dataset_path),
            "manifest_path": str(manifest_path),
            "sha256": _training_dataset_hash_file(dataset_path) if dataset_path.is_file() else "",
            "bridge_instance_id": INSTANCE_ID,
        }
        _write_training_dataset_import_status(import_id, result)
        return _redact_training_value(result)
    except Exception as exc:
        failed = {
            **status,
            "status": "failed",
            "error": _redact_training_text(exc, limit=1000),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_training_dataset_import_status(import_id, failed)
        return _redact_training_value(failed)


def _training_dataset_import_status(payload):
    payload = payload if isinstance(payload, dict) else {}
    import_id = str(payload.get("import_id") or payload.get("importId") or "").strip()
    if not import_id:
        dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId") or "dataset")
        manifest_sha = str(payload.get("manifest_sha256") or payload.get("manifestSha256") or "").strip().lower()
        if manifest_sha:
            import_id = f"{dataset_id}:{manifest_sha[:24]}"
    status = _load_training_dataset_import_status(import_id)
    if not status:
        return {"status": "not_found", "import_id": import_id}
    return _redact_training_value(status)


def _training_dataset_import_relay_chunk(payload):
    payload = payload if isinstance(payload, dict) else {}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId") or "dataset")
    manifest_sha = str(payload.get("manifest_sha256") or payload.get("manifestSha256") or "").strip().lower()
    relpath = str(payload.get("file_path") or payload.get("filePath") or payload.get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    offset = max(0, _safe_int_between(payload.get("offset"), 0, 0, 10**18))
    content_b64 = str(payload.get("content_base64") or payload.get("base64") or "").strip()
    try:
        raw = base64.b64decode(content_b64, validate=True) if content_b64 else b""
    except Exception:
        return {"status": "failed", "error": "content_base64 is invalid"}
    target_dir = _training_dataset_target_dir(dataset_id, manifest_sha)
    dest = target_dir / relpath
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if offset == 0 else "r+b"
        with open(dest, mode) as fh:
            fh.seek(offset)
            fh.write(raw)
        size = dest.stat().st_size
    except Exception as exc:
        return {"status": "failed", "error": _redact_training_text(exc, limit=1000)}
    return {
        "status": "ok",
        "asset_type": "training_dataset",
        "dataset_id": dataset_id,
        "manifest_sha256": manifest_sha,
        "file_path": relpath,
        "offset": offset,
        "size_bytes": len(raw),
        "written_size_bytes": int(size),
        "target_dir": str(target_dir),
    }


def _training_dataset_import_relay_file_status(payload):
    payload = payload if isinstance(payload, dict) else {}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or payload.get("datasetId") or "dataset")
    manifest_sha = str(payload.get("manifest_sha256") or payload.get("manifestSha256") or "").strip().lower()
    relpath = str(payload.get("file_path") or payload.get("filePath") or payload.get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    target_dir = _training_dataset_target_dir(dataset_id, manifest_sha)
    path = target_dir / relpath
    result = {
        "status": "ok",
        "asset_type": "training_dataset",
        "dataset_id": dataset_id,
        "manifest_sha256": manifest_sha,
        "file_path": relpath,
        "target_dir": str(target_dir),
        "exists": False,
        "size_bytes": 0,
    }
    try:
        if not path.exists():
            return result
        if not path.is_file():
            return {**result, "status": "failed", "error": "target path is not a file"}
        result.update({"exists": True, "size_bytes": int(path.stat().st_size)})
        if _training_bootstrap_bool(payload.get("hash_file") or payload.get("hashFile")):
            result["sha256"] = _training_dataset_hash_file(path)
        return _redact_training_value(result)
    except Exception as exc:
        return {**result, "status": "failed", "error": _redact_training_text(exc, limit=1000)}


def _training_dataset_import_relay_commit(payload):
    payload = payload if isinstance(payload, dict) else {}
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return {"status": "failed", "error": "manifest is required"}
    if str(manifest.get("asset_type") or "") != "training_dataset":
        return {"status": "failed", "error": "manifest asset_type mismatch"}
    manifest_sha = str(manifest.get("manifest_sha256") or payload.get("manifest_sha256") or "").strip().lower()
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not manifest_sha or not files:
        return {"status": "failed", "error": "manifest_sha256 and files are required"}
    dataset_id = _safe_training_dataset_id(payload.get("dataset_id") or manifest.get("dataset_id") or f"dataset:{manifest_sha[:12]}")
    target_dir = _training_dataset_target_dir(dataset_id, manifest_sha)
    missing = []
    verified = 0
    for entry in files:
        if not isinstance(entry, dict):
            continue
        relpath = str(entry.get("path") or "").strip().lstrip("/")
        if not relpath or relpath.startswith("../") or "/../" in relpath:
            return {"status": "failed", "error": f"invalid manifest file path: {relpath}"}
        path = target_dir / relpath
        if not path.is_file():
            missing.append(relpath)
            continue
        expected_size = int(entry.get("size_bytes") or 0)
        if expected_size and path.stat().st_size != expected_size:
            return {"status": "failed", "error": f"file size mismatch: {relpath}"}
        expected_sha = str(entry.get("sha256") or "").strip().lower()
        if expected_sha and _training_dataset_hash_file(path) != expected_sha:
            return {"status": "failed", "error": f"file sha256 mismatch: {relpath}"}
        verified += 1
    if missing:
        return {"status": "failed", "error": "missing files", "missing_files": missing[:20]}
    manifest_path = target_dir / ".skillforge_dataset_manifest.json"
    manifest_path.write_text(json.dumps(_redact_training_value(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    dataset_path = target_dir / "dataset.json"
    result = {
        "status": "succeeded",
        "asset_type": "training_dataset",
        "dataset_id": dataset_id,
        "manifest_sha256": manifest_sha,
        "file_count": len(files),
        "verified_files": verified,
        "total_size_bytes": int(manifest.get("total_size_bytes") or 0),
        "dataset_dir": str(target_dir),
        "dataset_path": str(dataset_path),
        "manifest_path": str(manifest_path),
        "sha256": _training_dataset_hash_file(dataset_path) if dataset_path.is_file() else "",
        "bridge_instance_id": INSTANCE_ID,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }
    return _redact_training_value(result)


def _openwebui_registry_path():
    return STATE_DIR / "openwebui_models.json"


def _load_openwebui_registry():
    path = _openwebui_registry_path()
    if not path.exists():
        return {"models": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"models": {}}
    if not isinstance(value, dict):
        return {"models": {}}
    models = value.get("models")
    if not isinstance(models, dict):
        value["models"] = {}
    return value


def _write_openwebui_registry(registry):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _openwebui_registry_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(registry), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _openwebui_enabled():
    raw = str(os.environ.get("SKILLFORGE_OPENWEBUI_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    return platform.system() == "Darwin" or str(INSTANCE_ID or "") == "inference-primary"


def _openwebui_host():
    return str(os.environ.get("SKILLFORGE_OPENWEBUI_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def _openwebui_local_host():
    host = _openwebui_host()
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def _local_lan_ip_candidates():
    candidates = []

    def add(value):
        text = str(value or "").strip()
        if not text or text.startswith("127.") or text in {"0.0.0.0", "::", "::1", "localhost"}:
            return
        if ":" in text:
            return
        if text not in candidates:
            candidates.append(text)

    try:
        parsed = urllib.parse.urlparse(SKILLFORGE_HTTP_BASE or "")
        target_host = parsed.hostname
        target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if target_host:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((target_host, target_port))
                add(sock.getsockname()[0])
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for item in socket.gethostbyname_ex(hostname)[2]:
            add(item)
    except Exception:
        pass
    try:
        if platform.system() == "Darwin":
            proc = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3)
            text = proc.stdout if proc.returncode == 0 else ""
        else:
            proc = subprocess.run(["ip", "-4", "addr", "show"], capture_output=True, text=True, timeout=3)
            text = proc.stdout if proc.returncode == 0 else ""
        for match in re.finditer(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", text):
            add(match.group(1))
    except Exception:
        pass
    return candidates


def _openwebui_public_host():
    explicit = str(os.environ.get("SKILLFORGE_OPENWEBUI_PUBLIC_HOST") or "").strip()
    if explicit:
        return explicit
    host = _openwebui_host()
    if host not in {"0.0.0.0", "::", ""}:
        return host
    candidates = _local_lan_ip_candidates()
    return candidates[0] if candidates else "127.0.0.1"


def _openwebui_port():
    return _safe_int_between(os.environ.get("SKILLFORGE_OPENWEBUI_PORT"), OPENWEBUI_DEFAULT_PORT, 1, 65535)


def _openwebui_base_url():
    return f"http://{_openwebui_public_host()}:{_openwebui_port()}/v1"


def _openwebui_local_base_url():
    return f"http://{_openwebui_local_host()}:{_openwebui_port()}/v1"


def _openwebui_model_id(value=None):
    text = str(value or os.environ.get("SKILLFORGE_OPENWEBUI_MODEL_ID") or OPENWEBUI_DEFAULT_MODEL_ID).strip()
    text = re.sub(r"\s+", "-", text)
    return text[:180] or OPENWEBUI_DEFAULT_MODEL_ID


def _openwebui_artifact_path(value):
    text = str(value or "").strip()
    if not text:
        return None, "artifact_uri is required"
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme == "file":
        text = urllib.parse.unquote(parsed.path or "")
    elif parsed.scheme:
        return None, "artifact_uri must be a local file path"
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None, "artifact_uri must be absolute"
    try:
        path = path.resolve(strict=True)
    except Exception:
        return None, "artifact_uri does not exist"
    try:
        path.relative_to(STATE_DIR.resolve(strict=False))
    except Exception:
        return None, "artifact_uri must be under bridge state dir"
    if path.is_dir():
        return path, None
    if path.is_file() and path.suffixes[-2:] == [".tar", ".gz"]:
        return path, None
    return None, "artifact_uri must be adapter directory or .tar.gz"


def _normalize_openwebui_model_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    model_id = _openwebui_model_id(payload.get("model_id") or payload.get("openwebui_model_id") or payload.get("model"))
    profile = str(payload.get("profile") or payload.get("model_profile") or TRAINING_MODEL_DEFAULT_PROFILE).strip().lower()
    if profile not in TRAINING_MODEL_PROFILES:
        return None, "unsupported training model profile"
    base_model_only = _training_bootstrap_bool(payload.get("base_model_only") or payload.get("base_only"))
    artifact_uri = str(payload.get("artifact_uri") or payload.get("adapter_uri") or "").strip()
    artifact_format = "none" if base_model_only else ""
    artifact_path = None
    if not base_model_only:
        artifact_path, artifact_error = _openwebui_artifact_path(artifact_uri)
        if artifact_error:
            return None, artifact_error
        artifact_format = "dir" if artifact_path.is_dir() else "tar.gz"
    aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else []
    clean_aliases = []
    for item in aliases:
        alias = _openwebui_model_id(item)
        if alias and alias != model_id and alias not in clean_aliases:
            clean_aliases.append(alias)
    now_iso = datetime.utcnow().isoformat() + "Z"
    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "skillforge",
        "profile": profile,
        "runtime_profile": str(
            payload.get("runtime_profile")
            or payload.get("deployment_runtime_profile")
            or OPENWEBUI_DEFAULT_RUNTIME_PROFILE
        )[:80],
        "base_model_only": base_model_only,
        "artifact_uri": str(artifact_path) if artifact_path is not None else "",
        "artifact_format": artifact_format,
        "artifact_sha256": str(payload.get("artifact_sha256") or payload.get("sha256") or "")[:64],
        "deployment_id": str(payload.get("deployment_id") or payload.get("model_deployment_id") or "")[:120],
        "job_id": str(payload.get("job_id") or payload.get("training_job_id") or "")[:120],
        "model_family": str(payload.get("model_family") or "")[:180],
        "display_name": str(payload.get("display_name") or model_id)[:180],
        "source_type": str(payload.get("source_type") or "skillforge_finetuned_adapter")[:80],
        "updated_at": now_iso,
        "aliases": clean_aliases[:10],
    }, None


def _register_openwebui_model(payload):
    normalized, error = _normalize_openwebui_model_payload(payload)
    if error:
        return {"registered": False, "error": error}
    registry = _load_openwebui_registry()
    models = registry.setdefault("models", {})
    models[normalized["id"]] = normalized
    for alias in normalized.get("aliases") or []:
        alias_record = dict(normalized)
        alias_record["id"] = alias
        alias_record["alias_of"] = normalized["id"]
        models[alias] = alias_record
    registry["updated_at"] = normalized["updated_at"]
    _write_openwebui_registry(registry)
    return {
        "registered": True,
        "model_id": normalized["id"],
        "aliases": normalized.get("aliases") or [],
        "base_url": _openwebui_base_url(),
        "enabled": _openwebui_enabled(),
        "registry_path": str(_openwebui_registry_path()),
    }


def _import_training_artifact_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    job_id = str(payload.get("job_id") or payload.get("training_job_id") or "imported").strip()
    if not re.match(r"^[A-Za-z0-9_.:-]{1,120}$", job_id):
        return None, "invalid job_id"
    filename = str(payload.get("filename") or payload.get("name") or "adapter.tar.gz").strip()
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)[:120] or "adapter.tar.gz"
    content_b64 = str(payload.get("content_base64") or payload.get("base64") or "").strip()
    if not content_b64:
        return None, "content_base64 is required"
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except Exception:
        return None, "content_base64 is invalid"
    if len(raw) > TRAINING_ARTIFACT_DOWNLOAD_MAX_BYTES:
        return None, "training artifact is too large"
    expected_sha = str(payload.get("sha256") or payload.get("artifact_sha256") or "").strip().lower()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and expected_sha != actual_sha:
        return None, "artifact sha256 mismatch"
    target_dir = _training_imports_dir() / re.sub(r"[^A-Za-z0-9_.:-]+", "_", job_id)[:120]
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    item = {
        "job_id": job_id,
        "artifact_id": str(payload.get("artifact_id") or payload.get("id") or actual_sha)[:160],
        "id": str(payload.get("artifact_id") or payload.get("id") or actual_sha)[:160],
        "name": filename,
        "type": str(payload.get("type") or payload.get("artifact_type") or "lora_adapter")[:80],
        "filename": filename,
        "sha256": actual_sha,
        "size_bytes": len(raw),
        "uri": str(path),
        "imported_at": datetime.utcnow().isoformat() + "Z",
        "bridge_instance_id": INSTANCE_ID,
        "exists": True,
    }
    record = _load_training_job_record(job_id) or {
        "job_id": job_id,
        "status": "artifact_imported",
        "bridge_instance_id": INSTANCE_ID,
        "events": [],
        "artifacts": [],
    }
    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), list) else []
    artifacts = [
        artifact for artifact in artifacts
        if not _training_artifact_record_matches(
            item["artifact_id"],
            artifact,
            artifact_name=filename,
            sha256=actual_sha,
        )
    ]
    artifacts.append(item)
    record["artifacts"] = artifacts[-100:]
    record["status"] = record.get("status") or "artifact_imported"
    _append_training_job_event(record, "artifact_imported", "training artifact imported for deployment", artifact_id=item["artifact_id"])
    _write_training_job_record(job_id, record)
    return item, None


def _training_model_transfer_import_root(profile):
    safe_profile = re.sub(r"[^a-z0-9_.-]+", "_", str(profile or TRAINING_MODEL_DEFAULT_PROFILE).lower())
    return STATE_DIR / "training_model_imports" / safe_profile


def _path_under_any(path, roots):
    try:
        resolved = Path(path).resolve(strict=True)
    except Exception:
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve(strict=True))
            return True
        except Exception:
            continue
    return False


def _training_model_content_roots(source_path):
    source = Path(source_path).resolve(strict=True)
    roots = [source]
    parts = source.parts
    if "snapshots" in parts:
        idx = parts.index("snapshots")
        if idx > 0:
            try:
                repo_root = Path(*parts[:idx])
                if repo_root.exists():
                    roots.append(repo_root.resolve(strict=True))
            except Exception:
                pass
    return roots


def _training_model_hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(TRAINING_MODEL_TRANSFER_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _training_model_export_validate_source(normalized, source_path):
    raw = str(source_path or "").strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None, "source_path must be absolute"
    try:
        resolved = path.resolve(strict=True)
    except Exception:
        return None, "source_path does not exist"
    if not resolved.is_dir():
        return None, "source_path must be a directory"
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    markers = _training_model_dir_markers(resolved)
    if not markers.get("has_config") or not (markers.get("has_tokenizer") or markers.get("has_vocab")) or not markers.get("has_weights"):
        return None, "source_path is not a complete training model directory"
    if not _training_model_path_matches_profile(resolved, profile, profile_cfg):
        return None, "source_path does not match requested profile"
    return resolved, None


def _training_model_export_source_path(normalized, source_path):
    raw = str(source_path or "").strip()
    if raw.startswith("file://"):
        raw = raw[7:]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None, "source_path must be absolute"
    try:
        resolved = path.resolve(strict=True)
    except Exception:
        return None, "source_path does not exist"
    if not resolved.is_dir():
        return None, "source_path must be a directory"
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    if not _training_model_path_matches_profile(resolved, profile, profile_cfg):
        return None, "source_path does not match requested profile"
    return resolved, None


def _training_model_export_manifest(source_path, normalized, *, hash_files=True):
    source_path = Path(source_path).resolve(strict=True)
    content_roots = _training_model_content_roots(source_path)
    files = []
    total_size = 0
    skipped = []
    for item in sorted(source_path.rglob("*"), key=lambda value: str(value)):
        try:
            if not item.is_file():
                continue
            rel = item.relative_to(source_path).as_posix()
            if not rel or rel.startswith("../") or "/../" in rel:
                skipped.append({"path": rel, "reason": "unsafe_relative_path"})
                continue
            resolved = item.resolve(strict=True)
            if not _path_under_any(resolved, content_roots):
                skipped.append({"path": rel, "reason": "symlink_target_outside_model"})
                continue
            stat = resolved.stat()
            entry = {
                "path": rel,
                "size_bytes": int(stat.st_size),
            }
            if hash_files:
                entry["sha256"] = _training_model_hash_file(resolved)
            files.append(entry)
            total_size += int(stat.st_size)
        except Exception as exc:
            skipped.append({"path": str(item), "reason": exc.__class__.__name__})
            continue
    manifest = {
        "profile": normalized["profile"],
        "model_id": normalized["model_id"],
        "source_path": str(source_path),
        "bridge_instance_id": INSTANCE_ID,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "file_count": len(files),
        "total_size_bytes": total_size,
        "files": files,
        "skipped_files": skipped[:100],
        "hash_files": bool(hash_files),
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


def _training_model_export_manifest_payload(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    source_raw = (
        (payload or {}).get("source_path")
        or (payload or {}).get("sourcePath")
        or (payload or {}).get("path")
        or normalized.get("internal_model_ref")
    )
    source_path, path_error = _training_model_export_validate_source(normalized, source_raw)
    if path_error:
        return {"status": "failed", "error": path_error}
    hash_files = not str((payload or {}).get("hash_files") or (payload or {}).get("hashFiles") or "").lower() in {"0", "false", "no"}
    manifest = _training_model_export_manifest(source_path, normalized, hash_files=hash_files)
    manifest["status"] = "ready"
    return _redact_training_value(manifest)


def _training_model_export_file_chunk(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    source_raw = (
        (payload or {}).get("source_path")
        or (payload or {}).get("sourcePath")
        or (payload or {}).get("path")
        or normalized.get("internal_model_ref")
    )
    source_path, path_error = _training_model_export_source_path(normalized, source_raw)
    if path_error:
        return {"status": "failed", "error": path_error}
    relpath = str((payload or {}).get("file_path") or (payload or {}).get("filePath") or (payload or {}).get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    try:
        offset = int((payload or {}).get("offset") or 0)
    except Exception:
        offset = 0
    offset = max(0, offset)
    try:
        max_bytes = int((payload or {}).get("max_bytes") or (payload or {}).get("maxBytes") or TRAINING_MODEL_TRANSFER_CHUNK_BYTES)
    except Exception:
        max_bytes = TRAINING_MODEL_TRANSFER_CHUNK_BYTES
    max_bytes = max(1, min(max_bytes, TRAINING_MODEL_TRANSFER_CHUNK_BYTES))
    try:
        file_path = (source_path / relpath).resolve(strict=True)
    except Exception:
        return {"status": "failed", "error": "file not found"}
    if not _path_under_any(file_path, _training_model_content_roots(source_path)):
        return {"status": "failed", "error": "file outside export scope"}
    try:
        total_size = file_path.stat().st_size
        with open(file_path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(max_bytes)
    except Exception as exc:
        return {"status": "failed", "error": _redact_training_text(exc, limit=1000)}
    return {
        "status": "ok",
        "profile": normalized["profile"],
        "source_path": str(source_path),
        "file_path": relpath,
        "offset": offset,
        "size_bytes": len(raw),
        "total_size_bytes": int(total_size),
        "eof": offset + len(raw) >= int(total_size),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


def _training_model_export_advertise_host():
    explicit = str(os.environ.get("SKILLFORGE_MODEL_EXPORT_PUBLIC_HOST") or "").strip()
    if explicit:
        return explicit
    bind_host = str(os.environ.get("SKILLFORGE_MODEL_EXPORT_BIND_HOST") or "").strip()
    if bind_host and bind_host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return bind_host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 1))  # route probe using a reserved documentation address
            host = sock.getsockname()[0]
            if host:
                return host
    except Exception:
        pass
    try:
        host = socket.gethostbyname(socket.gethostname())
        if host:
            return host
    except Exception:
        pass
    return "127.0.0.1"


def _training_model_export_server_base_url():
    server = _ensure_training_model_export_server()
    host = _training_model_export_advertise_host()
    port = int(server.server_address[1])
    return f"http://{host}:{port}"


def _training_model_export_cleanup_locked(now=None):
    now_ts = float(now if now is not None else time.time())
    expired = [
        session_id
        for session_id, session in _MODEL_EXPORT_SESSIONS.items()
        if float(session.get("expires_at_ts") or 0) <= now_ts
    ]
    for session_id in expired:
        _MODEL_EXPORT_SESSIONS.pop(session_id, None)


def _training_model_export_session(session_id):
    if not TRAINING_MODEL_EXPORT_SESSION_ID_RE.match(str(session_id or "")):
        return None
    with _MODEL_EXPORT_LOCK:
        _training_model_export_cleanup_locked()
        session = _MODEL_EXPORT_SESSIONS.get(str(session_id))
        return dict(session) if isinstance(session, dict) else None


def _training_model_export_file_entry(session, relpath):
    relpath = str(relpath or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return None
    files = session.get("file_map") if isinstance(session.get("file_map"), dict) else {}
    return files.get(relpath)


class _TrainingModelExportHandler(BaseHTTPRequestHandler):
    server_version = "SkillForgeModelExport/1.0"

    def log_message(self, fmt, *args):
        try:
            setup_logging().debug("model_export: " + fmt, *args)
        except Exception:
            pass

    def _send_json(self, status, payload):
        body = json.dumps(_redact_training_value(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if token:
            return str(token)
        auth = str(self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _session(self, session_id):
        session = _training_model_export_session(session_id)
        if not session:
            return None
        expected = str(session.get("token") or "")
        if not expected or not hmac.compare_digest(expected, self._token()):
            return None
        return session

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        prefix = "/training-model-export/"
        if not parsed.path.startswith(prefix):
            return self._send_json(404, {"error": {"message": "not found"}})
        rest = parsed.path[len(prefix):]
        session_id, _, tail = rest.partition("/")
        session = self._session(session_id)
        if not session:
            return self._send_json(403, {"error": {"message": "model export session not found or unauthorized"}})
        if tail == "manifest":
            return self._send_json(200, session.get("manifest") or {})
        file_prefix = "files/"
        if not tail.startswith(file_prefix):
            return self._send_json(404, {"error": {"message": "not found"}})
        relpath = urllib.parse.unquote(tail[len(file_prefix):])
        entry = _training_model_export_file_entry(session, relpath)
        if not isinstance(entry, dict):
            return self._send_json(404, {"error": {"message": "file not found"}})
        source_path = Path(session["source_path"])
        try:
            file_path = (source_path / entry["path"]).resolve(strict=True)
        except Exception:
            return self._send_json(404, {"error": {"message": "file unavailable"}})
        if not _path_under_any(file_path, session.get("content_roots") or [source_path]):
            return self._send_json(403, {"error": {"message": "file outside export scope"}})
        size = int(entry.get("size_bytes") or file_path.stat().st_size)
        start = 0
        end = size - 1
        status = 200
        range_header = str(self.headers.get("Range") or "").strip()
        if range_header.startswith("bytes="):
            raw_range = range_header[6:].split(",", 1)[0].strip()
            raw_start, _, raw_end = raw_range.partition("-")
            try:
                if raw_start:
                    start = int(raw_start)
                if raw_end:
                    end = int(raw_end)
            except Exception:
                return self._send_json(416, {"error": {"message": "invalid range"}})
            if start < 0 or start >= size or end < start:
                return self._send_json(416, {"error": {"message": "range not satisfiable"}})
            end = min(end, size - 1)
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(file_path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(TRAINING_MODEL_TRANSFER_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def _ensure_training_model_export_server():
    global _MODEL_EXPORT_SERVER
    with _MODEL_EXPORT_SERVER_LOCK:
        if _MODEL_EXPORT_SERVER is not None:
            return _MODEL_EXPORT_SERVER
        bind_host = str(os.environ.get("SKILLFORGE_MODEL_EXPORT_BIND_HOST") or "0.0.0.0").strip() or "0.0.0.0"
        try:
            port = int(os.environ.get("SKILLFORGE_MODEL_EXPORT_PORT") or "0")
        except Exception:
            port = 0
        server = ThreadingHTTPServer((bind_host, max(0, min(port, 65535))), _TrainingModelExportHandler)
        thread = threading.Thread(target=server.serve_forever, name="sf-training-model-export", daemon=True)
        thread.start()
        _MODEL_EXPORT_SERVER = server
        setup_logging().info("training model export server started at %s:%s", bind_host, server.server_address[1])
        return server


def _training_model_export_start(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    source_raw = (
        (payload or {}).get("source_path")
        or (payload or {}).get("sourcePath")
        or (payload or {}).get("path")
        or normalized.get("internal_model_ref")
    )
    source_path, path_error = _training_model_export_validate_source(normalized, source_raw)
    if path_error:
        return {"status": "failed", "error": path_error}
    hash_files = not str((payload or {}).get("hash_files") or (payload or {}).get("hashFiles") or "").lower() in {"0", "false", "no"}
    manifest = _training_model_export_manifest(source_path, normalized, hash_files=hash_files)
    if not manifest["files"]:
        return {"status": "failed", "error": "source_path has no exportable files"}
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    session_id = (
        re.sub(r"[^A-Za-z0-9_.:-]+", "_", str((payload or {}).get("session_id") or ""))[:80]
        or "mx-" + hashlib.sha256(f"{INSTANCE_ID}:{source_path}:{time.time()}".encode()).hexdigest()[:24]
    )
    ttl = _safe_int_between((payload or {}).get("ttl_seconds") or (payload or {}).get("ttlSeconds"), TRAINING_MODEL_EXPORT_SESSION_TTL_SECONDS, 60, 24 * 60 * 60)
    expires_ts = time.time() + ttl
    base_url = _training_model_export_server_base_url()
    export_url = f"{base_url}/training-model-export/{urllib.parse.quote(session_id, safe='')}"
    session = {
        "session_id": session_id,
        "token": token,
        "source_path": str(source_path),
        "content_roots": [str(item) for item in _training_model_content_roots(source_path)],
        "manifest": manifest,
        "file_map": {item["path"]: item for item in manifest["files"]},
        "created_at": manifest["created_at"],
        "expires_at": datetime.fromtimestamp(expires_ts, timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at_ts": expires_ts,
        "export_url": export_url,
    }
    with _MODEL_EXPORT_LOCK:
        _training_model_export_cleanup_locked()
        _MODEL_EXPORT_SESSIONS[session_id] = session
    return {
        "status": "ready",
        "session_id": session_id,
        "profile": normalized["profile"],
        "source_path": str(source_path),
        "file_count": manifest["file_count"],
        "total_size_bytes": manifest["total_size_bytes"],
        "manifest_sha256": manifest["manifest_sha256"],
        "export_url": export_url,
        "manifest_url": f"{export_url}/manifest",
        "token": token,
        "expires_at": session["expires_at"],
        "bridge_instance_id": INSTANCE_ID,
    }


def _training_model_export_status(payload):
    session_id = str((payload or {}).get("session_id") or (payload or {}).get("sessionId") or "").strip()
    session = _training_model_export_session(session_id)
    if not session:
        return {"status": "not_found", "session_id": session_id}
    manifest = session.get("manifest") if isinstance(session.get("manifest"), dict) else {}
    return {
        "status": "ready",
        "session_id": session_id,
        "profile": manifest.get("profile"),
        "source_path": session.get("source_path"),
        "file_count": manifest.get("file_count"),
        "total_size_bytes": manifest.get("total_size_bytes"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "export_url": session.get("export_url"),
        "expires_at": session.get("expires_at"),
    }


def _training_model_export_cancel(payload):
    session_id = str((payload or {}).get("session_id") or (payload or {}).get("sessionId") or "").strip()
    with _MODEL_EXPORT_LOCK:
        existed = _MODEL_EXPORT_SESSIONS.pop(session_id, None) is not None
    return {"status": "cancelled" if existed else "not_found", "session_id": session_id}


def _training_model_import_status_path(import_id):
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(import_id or "import"))[:160]
    return STATE_DIR / "training_model_imports" / "status" / f"{safe_id}.json"


def _write_training_model_import_status(import_id, status):
    path = _training_model_import_status_path(import_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(status), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_training_model_import_status(import_id):
    path = _training_model_import_status_path(import_id)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _training_model_transfer_request_json(url, token, *, timeout=60):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(32 * 1024 * 1024)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("manifest response must be a JSON object")
    return value


def _training_model_transfer_download_file(url, token, dest, expected_size, *, timeout=300, cancel_event=None):
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    resume_at = tmp.stat().st_size if tmp.exists() else 0
    if resume_at > expected_size:
        tmp.unlink()
        resume_at = 0
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
    request = urllib.request.Request(url, headers=headers, method="GET")
    mode = "ab" if resume_at else "wb"
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(tmp, mode) as fh:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("model import cancelled")
                chunk = response.read(TRAINING_MODEL_TRANSFER_CHUNK_BYTES)
                if not chunk:
                    break
                fh.write(chunk)
    actual_size = tmp.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(f"downloaded file size mismatch: expected={expected_size} actual={actual_size}")
    tmp.replace(dest)


def _training_model_import_from_export(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    token = str((payload or {}).get("token") or (payload or {}).get("export_token") or "").strip()
    manifest_url = str((payload or {}).get("manifest_url") or (payload or {}).get("manifestUrl") or "").strip()
    export_url = str((payload or {}).get("export_url") or (payload or {}).get("exportUrl") or "").strip().rstrip("/")
    if not manifest_url and export_url:
        manifest_url = f"{export_url}/manifest"
    if not manifest_url:
        return {"status": "failed", "error": "manifest_url or export_url is required"}
    timeout = _safe_int_between((payload or {}).get("timeout_seconds") or (payload or {}).get("timeoutSeconds"), 21600, 60, 86400)
    manifest = _training_model_transfer_request_json(manifest_url, token, timeout=min(timeout, 300))
    if str(manifest.get("profile") or "").strip().lower() != normalized["profile"]:
        return {"status": "failed", "error": "manifest profile mismatch"}
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not files:
        return {"status": "failed", "error": "manifest has no files"}
    manifest_sha = str(manifest.get("manifest_sha256") or "").strip().lower()
    if not manifest_sha:
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    expected_manifest_sha = str((payload or {}).get("manifest_sha256") or (payload or {}).get("manifestSha256") or "").strip().lower()
    if expected_manifest_sha and expected_manifest_sha != manifest_sha:
        return {"status": "failed", "error": "manifest sha256 mismatch"}
    import_id = (
        re.sub(r"[^A-Za-z0-9_.:-]+", "_", str((payload or {}).get("import_id") or (payload or {}).get("importId") or ""))[:120]
        or f"{normalized['profile']}:{manifest_sha[:24]}"
    )
    target_dir = _training_model_transfer_import_root(normalized["profile"]) / manifest_sha[:24]
    force = _training_bootstrap_bool((payload or {}).get("force"))
    if force and target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    cancel_event = threading.Event()
    with _MODEL_IMPORT_CANCEL_LOCK:
        _MODEL_IMPORT_CANCEL_FLAGS[import_id] = cancel_event
    started_at = datetime.utcnow().isoformat() + "Z"
    status = {
        "status": "running",
        "import_id": import_id,
        "profile": normalized["profile"],
        "manifest_sha256": manifest_sha,
        "source_gateway_id": str((payload or {}).get("source_gateway_id") or (payload or {}).get("sourceGatewayId") or manifest.get("bridge_instance_id") or "")[:120],
        "source_path": str((payload or {}).get("source_path") or (payload or {}).get("sourcePath") or manifest.get("source_path") or "")[:1000],
        "target_dir": str(target_dir),
        "file_count": len(files),
        "total_size_bytes": int(manifest.get("total_size_bytes") or 0),
        "imported_files": 0,
        "skipped_files": 0,
        "started_at": started_at,
        "updated_at": started_at,
    }
    _write_training_model_import_status(import_id, status)
    try:
        base_url = export_url
        if not base_url:
            parsed = urllib.parse.urlparse(manifest_url)
            base_path = parsed.path.rsplit("/manifest", 1)[0]
            base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))
        imported = 0
        skipped = 0
        for index, entry in enumerate(files, 1):
            if cancel_event.is_set():
                raise RuntimeError("model import cancelled")
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path") or "").strip().lstrip("/")
            if not rel or rel.startswith("../") or "/../" in rel:
                raise RuntimeError(f"unsafe manifest file path: {rel}")
            expected_size = int(entry.get("size_bytes") or 0)
            expected_sha = str(entry.get("sha256") or "").strip().lower()
            dest = target_dir / rel
            if dest.exists() and dest.stat().st_size == expected_size:
                if not expected_sha or _training_model_hash_file(dest) == expected_sha:
                    skipped += 1
                    status.update({"skipped_files": skipped, "current_file": rel, "updated_at": datetime.utcnow().isoformat() + "Z"})
                    _write_training_model_import_status(import_id, status)
                    continue
            file_url = f"{base_url}/files/{urllib.parse.quote(rel, safe='/')}"
            _training_model_transfer_download_file(file_url, token, dest, expected_size, timeout=min(timeout, 3600), cancel_event=cancel_event)
            if expected_sha:
                actual_sha = _training_model_hash_file(dest)
                if actual_sha != expected_sha:
                    raise RuntimeError(f"file sha256 mismatch: {rel}")
            imported += 1
            status.update({
                "imported_files": imported,
                "skipped_files": skipped,
                "current_file": rel,
                "progress": round(index / max(len(files), 1), 4),
                "updated_at": datetime.utcnow().isoformat() + "Z",
            })
            _write_training_model_import_status(import_id, status)
        manifest_path = target_dir / ".skillforge_model_manifest.json"
        manifest_path.write_text(json.dumps(_redact_training_value(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        completed_at = datetime.utcnow().isoformat() + "Z"
        result = {
            **status,
            "status": "succeeded",
            "progress": 1.0,
            "imported_files": imported,
            "skipped_files": skipped,
            "completed_at": completed_at,
            "updated_at": completed_at,
            "internal_model_ref": str(target_dir),
            "model_dir": str(target_dir),
            "manifest_path": str(manifest_path),
            "bridge_instance_id": INSTANCE_ID,
        }
        _write_training_model_import_status(import_id, result)
        return _redact_training_value(result)
    except Exception as exc:
        failed = {
            **status,
            "status": "failed",
            "error": _redact_training_text(exc, limit=1000),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_training_model_import_status(import_id, failed)
        return _redact_training_value(failed)
    finally:
        with _MODEL_IMPORT_CANCEL_LOCK:
            _MODEL_IMPORT_CANCEL_FLAGS.pop(import_id, None)


def _training_model_import_status(payload):
    import_id = str((payload or {}).get("import_id") or (payload or {}).get("importId") or "").strip()
    if not import_id:
        profile = str((payload or {}).get("profile") or TRAINING_MODEL_DEFAULT_PROFILE).strip().lower()
        manifest_sha = str((payload or {}).get("manifest_sha256") or (payload or {}).get("manifestSha256") or "").strip().lower()
        if manifest_sha:
            import_id = f"{profile}:{manifest_sha[:24]}"
    status = _load_training_model_import_status(import_id)
    if not status:
        return {"status": "not_found", "import_id": import_id}
    return _redact_training_value(status)


def _training_model_import_cancel(payload):
    import_id = str((payload or {}).get("import_id") or (payload or {}).get("importId") or "").strip()
    with _MODEL_IMPORT_CANCEL_LOCK:
        event = _MODEL_IMPORT_CANCEL_FLAGS.get(import_id)
        if event is not None:
            event.set()
            return {"status": "cancelling", "import_id": import_id}
    return {"status": "not_found", "import_id": import_id}


def _training_model_relay_target_dir(profile, manifest_sha):
    safe_sha = re.sub(r"[^a-fA-F0-9]+", "", str(manifest_sha or ""))[:64]
    if not safe_sha:
        safe_sha = hashlib.sha256(f"{profile}:{time.time()}".encode()).hexdigest()
    return _training_model_transfer_import_root(profile) / safe_sha[:24]


def _training_model_import_relay_chunk(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    manifest_sha = str((payload or {}).get("manifest_sha256") or (payload or {}).get("manifestSha256") or "").strip().lower()
    relpath = str((payload or {}).get("file_path") or (payload or {}).get("filePath") or (payload or {}).get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    try:
        offset = int((payload or {}).get("offset") or 0)
    except Exception:
        offset = 0
    offset = max(0, offset)
    content_b64 = str((payload or {}).get("content_base64") or (payload or {}).get("base64") or "").strip()
    if content_b64:
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return {"status": "failed", "error": "content_base64 is invalid"}
    else:
        raw = b""
    target_dir = _training_model_relay_target_dir(normalized["profile"], manifest_sha)
    dest = target_dir / relpath
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if offset == 0 else "r+b"
        with open(dest, mode) as fh:
            fh.seek(offset)
            fh.write(raw)
        size = dest.stat().st_size
    except Exception as exc:
        return {"status": "failed", "error": _redact_training_text(exc, limit=1000)}
    return {
        "status": "ok",
        "profile": normalized["profile"],
        "manifest_sha256": manifest_sha,
        "file_path": relpath,
        "offset": offset,
        "size_bytes": len(raw),
        "written_size_bytes": int(size),
        "target_dir": str(target_dir),
    }


def _training_model_import_relay_file_status(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    manifest_sha = str((payload or {}).get("manifest_sha256") or (payload or {}).get("manifestSha256") or "").strip().lower()
    relpath = str((payload or {}).get("file_path") or (payload or {}).get("filePath") or (payload or {}).get("relative_path") or "").strip().lstrip("/")
    if not relpath or relpath.startswith("../") or "/../" in relpath:
        return {"status": "failed", "error": "invalid file_path"}
    target_dir = _training_model_relay_target_dir(normalized["profile"], manifest_sha)
    path = target_dir / relpath
    result = {
        "status": "ok",
        "profile": normalized["profile"],
        "manifest_sha256": manifest_sha,
        "file_path": relpath,
        "target_dir": str(target_dir),
        "exists": False,
        "size_bytes": 0,
    }
    try:
        if not path.exists():
            return result
        if not path.is_file():
            return {**result, "status": "failed", "error": "target path is not a file"}
        result.update({
            "exists": True,
            "size_bytes": int(path.stat().st_size),
        })
        if _training_bootstrap_bool((payload or {}).get("hash_file") or (payload or {}).get("hashFile")):
            result["sha256"] = _training_model_hash_file(path)
        return _redact_training_value(result)
    except Exception as exc:
        return {**result, "status": "failed", "error": _redact_training_text(exc, limit=1000)}


def _training_model_import_relay_commit(payload):
    normalized, error = _normalize_training_model_payload(payload or {})
    if error:
        return {"status": "failed", "error": error}
    manifest = (payload or {}).get("manifest")
    if not isinstance(manifest, dict):
        return {"status": "failed", "error": "manifest is required"}
    manifest_sha = str(manifest.get("manifest_sha256") or (payload or {}).get("manifest_sha256") or "").strip().lower()
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not manifest_sha or not files:
        return {"status": "failed", "error": "manifest_sha256 and files are required"}
    target_dir = _training_model_relay_target_dir(normalized["profile"], manifest_sha)
    missing = []
    verified = 0
    for entry in files:
        if not isinstance(entry, dict):
            continue
        relpath = str(entry.get("path") or "").strip().lstrip("/")
        if not relpath or relpath.startswith("../") or "/../" in relpath:
            return {"status": "failed", "error": f"invalid manifest file path: {relpath}"}
        path = target_dir / relpath
        if not path.is_file():
            missing.append(relpath)
            continue
        expected_size = int(entry.get("size_bytes") or 0)
        actual_size = path.stat().st_size
        if expected_size and actual_size != expected_size:
            return {"status": "failed", "error": f"file size mismatch: {relpath}"}
        expected_sha = str(entry.get("sha256") or "").strip().lower()
        if expected_sha and _training_model_hash_file(path) != expected_sha:
            return {"status": "failed", "error": f"file sha256 mismatch: {relpath}"}
        verified += 1
    if missing:
        return {"status": "failed", "error": "missing files", "missing_files": missing[:20]}
    manifest_path = target_dir / ".skillforge_model_manifest.json"
    manifest_path.write_text(json.dumps(_redact_training_value(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "status": "succeeded",
        "profile": normalized["profile"],
        "manifest_sha256": manifest_sha,
        "file_count": len(files),
        "verified_files": verified,
        "total_size_bytes": int(manifest.get("total_size_bytes") or 0),
        "internal_model_ref": str(target_dir),
        "model_dir": str(target_dir),
        "manifest_path": str(manifest_path),
        "bridge_instance_id": INSTANCE_ID,
        "completed_at": datetime.utcnow().isoformat() + "Z",
    }
    return _redact_training_value(result)


def _openwebui_messages_to_prompt(messages):
    if not isinstance(messages, list):
        return ""
    parts = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content = item.get("content")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content_text = "\n".join(part for part in text_parts if part)
        else:
            content_text = str(content or "")
        if not content_text.strip():
            continue
        parts.append(f"<|im_start|>{role}\n{content_text.strip()}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _openwebui_model_records():
    registry = _load_openwebui_registry()
    models = registry.get("models") if isinstance(registry.get("models"), dict) else {}
    rows = []
    for item in models.values():
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        rows.append(item)
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return rows


def _openwebui_resident_models():
    if not _openwebui_enabled():
        return []
    rows = []
    for item in _openwebui_model_records()[:20]:
        model_id = str(item.get("id") or "").strip()[:180]
        if not model_id:
            continue
        rows.append({
            "model": model_id,
            "display_name": str(item.get("display_name") or model_id)[:180],
            "deployment_id": str(item.get("deployment_id") or "")[:120] or None,
            "job_id": str(item.get("job_id") or "")[:120] or None,
            "runtime_profile": str(item.get("runtime_profile") or "")[:80] or None,
            "status": "loaded",
            "loaded": True,
            "last_heartbeat_at": datetime.utcnow().isoformat() + "Z",
        })
    return rows


def _openwebui_model_record(model_id):
    models = _load_openwebui_registry().get("models") or {}
    item = models.get(str(model_id or ""))
    return item if isinstance(item, dict) else None


def _openwebui_status_sync():
    host = _openwebui_local_host()
    bind_host = _openwebui_host()
    public_host = _openwebui_public_host()
    port = _openwebui_port()
    reachable = False
    socket_error = ""
    try:
        import socket

        with socket.create_connection((host, port), timeout=0.5):
            reachable = True
    except Exception as exc:
        socket_error = _redact_training_text(exc, limit=500)
    models = []
    for item in _openwebui_model_records():
        if not isinstance(item, dict):
            continue
        models.append({
            "id": item.get("id"),
            "alias_of": item.get("alias_of"),
            "profile": item.get("profile"),
            "runtime_profile": item.get("runtime_profile"),
            "base_model_only": item.get("base_model_only"),
            "deployment_id": item.get("deployment_id"),
            "job_id": item.get("job_id"),
            "display_name": item.get("display_name"),
            "artifact_format": item.get("artifact_format"),
            "artifact_uri_present": bool(item.get("artifact_uri")),
            "updated_at": item.get("updated_at"),
        })
    return _redact_training_value({
        "enabled": _openwebui_enabled(),
        "host": public_host,
        "bind_host": bind_host,
        "local_host": host,
        "port": port,
        "base_url": _openwebui_base_url(),
        "local_base_url": _openwebui_local_base_url(),
        "health_url": f"http://{public_host}:{port}/health",
        "local_health_url": f"http://{host}:{port}/health",
        "lan_hosts": _local_lan_ip_candidates(),
        "registry_path": str(_openwebui_registry_path()),
        "registry_exists": _openwebui_registry_path().exists(),
        "model_count": len(models),
        "models": models[:100],
        "server_reachable": reachable,
        "socket_error": None if reachable else socket_error,
        "bridge_instance_id": INSTANCE_ID,
    })


def _openwebui_chat_test_sync(payload):
    payload = payload if isinstance(payload, dict) else {}
    records = _openwebui_model_records()
    model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
    if not model_id and records:
        model_id = str(records[0].get("id") or "").strip()
    if not model_id:
        return {
            "status": "failed",
            "ok": False,
            "error": "no OpenWebUI model registered",
            "base_url": _openwebui_base_url(),
            "registry_path": str(_openwebui_registry_path()),
        }
    prompt = str(payload.get("prompt") or "Respond with one short sentence.").strip()
    if not prompt:
        return {"status": "failed", "ok": False, "error": "prompt is required", "model": model_id}
    max_tokens = _safe_int_between(payload.get("max_tokens") or payload.get("max_new_tokens"), 32, 1, 4096)
    timeout = _safe_int_between(payload.get("timeout_seconds") or payload.get("timeoutSeconds"), 600, 30, 3600)
    bridge_max_input_chars = _safe_int_between(
        os.environ.get("SKILLFORGE_OPENWEBUI_MAX_INPUT_CHARS"),
        2 * 1024 * 1024,
        1,
        4 * 1024 * 1024,
    )
    max_input_chars = _safe_int_between(
        payload.get("max_input_chars") or payload.get("maxInputChars"),
        bridge_max_input_chars,
        1,
        bridge_max_input_chars,
    )
    if len(prompt) > max_input_chars:
        return {
            "status": "failed",
            "ok": False,
            "model": model_id,
            "base_url": _openwebui_base_url(),
            "error": f"prompt exceeds Bridge input limit: {len(prompt)}/{max_input_chars} chars",
            "prompt_chars": len(prompt),
            "max_input_chars": max_input_chars,
        }
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "timeout_seconds": timeout,
    }
    context_window = _safe_int_between(
        payload.get("context_window") or payload.get("contextWindow") or payload.get("num_ctx") or payload.get("numCtx"),
        0,
        0,
        1_000_000,
    )
    if context_window and str(os.environ.get("SKILLFORGE_OPENWEBUI_FORWARD_CONTEXT_WINDOW") or "").strip().lower() in {"1", "true", "yes", "on"}:
        body["context_window"] = context_window
        body["num_ctx"] = context_window
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://{_openwebui_local_host()}:{_openwebui_port()}/v1/chat/completions",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = str(os.environ.get("SKILLFORGE_OPENWEBUI_API_KEY") or "").strip()
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(response_body) if response_body else {}
            http_status = int(getattr(resp, "status", None) or getattr(resp, "code", 200) or 200)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": "failed",
            "ok": False,
            "model": model_id,
            "http_status": int(exc.code),
            "base_url": _openwebui_base_url(),
            "error": _redact_training_text(response_body or exc, limit=8000),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "ok": False,
            "model": model_id,
            "base_url": _openwebui_base_url(),
            "error": _redact_training_text(exc, limit=8000),
        }
    choices = parsed.get("choices") if isinstance(parsed.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = str(message.get("content") or first.get("text") or "").strip()
    return _redact_training_value({
        "status": "succeeded" if text else "empty",
        "ok": bool(text),
        "model": model_id,
        "http_status": http_status,
        "base_url": _openwebui_base_url(),
        "text": text,
        "response_id": parsed.get("id") if isinstance(parsed, dict) else None,
        "prompt_chars": len(prompt),
        "max_input_chars": max_input_chars,
        "context_window": context_window or None,
        "finish_reason": first.get("finish_reason"),
    })


class _OpenWebUIHandler(BaseHTTPRequestHandler):
    server_version = "SkillForgeOpenWebUI/1.0"

    def log_message(self, fmt, *args):
        try:
            setup_logging().debug("openwebui: " + fmt, *args)
        except Exception:
            pass

    def _send_json(self, status, payload):
        body = json.dumps(_redact_training_value(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, payloads):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for payload in payloads:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except Exception:
            length = 0
        if length <= 0 or length > 4 * 1024 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def _authorized(self):
        expected = str(os.environ.get("SKILLFORGE_OPENWEBUI_API_KEY") or "").strip()
        if not expected:
            return True
        auth = str(self.headers.get("Authorization") or "").strip()
        return auth == f"Bearer {expected}"

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/health", "/v1/health"}:
            return self._send_json(200, {
                "ok": True,
                "instance_id": INSTANCE_ID,
                "models": len(_openwebui_model_records()),
            })
        if parsed.path == "/v1/models":
            if not self._authorized():
                return self._send_json(401, {"error": {"message": "unauthorized"}})
            return self._send_json(200, {
                "object": "list",
                "data": [
                    {
                        "id": item["id"],
                        "object": "model",
                        "created": int(item.get("created") or time.time()),
                        "owned_by": item.get("owned_by") or "skillforge",
                    }
                    for item in _openwebui_model_records()
                ],
            })
        return self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/v1/chat/completions":
            return self._send_json(404, {"error": {"message": "not found"}})
        if not self._authorized():
            return self._send_json(401, {"error": {"message": "unauthorized"}})
        body = self._read_json()
        if body is None:
            return self._send_json(400, {"error": {"message": "invalid json body"}})
        model_id = str(body.get("model") or "").strip()
        record = _openwebui_model_record(model_id)
        if record is None:
            return self._send_json(404, {"error": {"message": f"model not found: {model_id}"}})
        prompt = _openwebui_messages_to_prompt(body.get("messages"))
        if not prompt.strip():
            return self._send_json(400, {"error": {"message": "messages are required"}})
        max_tokens = _safe_int_between(body.get("max_tokens") or body.get("max_completion_tokens"), 768, 1, 4096)
        try:
            inference_payload = {
                "profile": record.get("profile") or TRAINING_MODEL_DEFAULT_PROFILE,
                "runtime_profile": record.get("runtime_profile") or OPENWEBUI_DEFAULT_RUNTIME_PROFILE,
                "base_model_only": record.get("base_model_only"),
                "artifact_uri": record.get("artifact_uri") or "",
                "artifact_sha256": record.get("artifact_sha256") or "",
                "prompt": prompt,
                "max_new_tokens": max_tokens,
                "timeout_seconds": _safe_int_between(body.get("timeout_seconds"), 1800, 30, 3600),
                "deployment_id": record.get("deployment_id") or "",
            }
            normalized, normalize_error = _normalize_training_inference_payload(inference_payload)
            if normalize_error:
                return self._send_json(500, {"error": {"message": normalize_error}})
            result = _run_local_training_inference(normalized)
        except Exception as exc:
            return self._send_json(500, {"error": {"message": _redact_training_text(exc, limit=8000)}})
        text = str(result.get("text") or "")
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        response_id = "chatcmpl-sf-" + hashlib.sha256(f"{time.time()}:{model_id}".encode()).hexdigest()[:16]
        created = int(time.time())
        finish_reason = result.get("finish_reason") or "stop"
        if body.get("stream") is True:
            return self._send_sse([
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
                },
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                },
            ])
        return self._send_json(200, {
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": int(metrics.get("prompt_tokens") or 0),
                "completion_tokens": int(metrics.get("generated_tokens") or 0),
                "total_tokens": int(metrics.get("prompt_tokens") or 0) + int(metrics.get("generated_tokens") or 0),
            },
        })


def _start_openwebui_server_if_enabled():
    if not _openwebui_enabled():
        return None
    logger = setup_logging()
    host = _openwebui_host()
    port = _openwebui_port()
    try:
        server = ThreadingHTTPServer((host, port), _OpenWebUIHandler)
    except OSError as exc:
        logger.warning("openwebui: server not started host=%s port=%s err=%s", host, port, exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="sf-openwebui-server", daemon=True)
    thread.start()
    logger.info("openwebui: OpenAI-compatible endpoint started at http://%s:%s/v1", host, port)
    return server


def _local_training_model_profile(normalized):
    spec = normalized.get("spec") if isinstance(normalized.get("spec"), dict) else {}
    model = spec.get("model") if isinstance(spec.get("model"), dict) else {}
    profile = str(model.get("profile") or spec.get("model_profile") or "").strip().lower()
    if profile in TRAINING_MODEL_PROFILES:
        return profile
    base_model = str(spec.get("base_model") or model.get("base_model") or "").lower()
    if "qwen3.6" in base_model and "35b" in base_model:
        return "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    if "qwen3.5" in base_model and "4b" in base_model:
        return "qwen3.5-4b"
    return TRAINING_MODEL_DEFAULT_PROFILE


def _local_training_parameters(normalized):
    spec = normalized.get("spec") if isinstance(normalized.get("spec"), dict) else {}
    params = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    training_args = spec.get("training_args") if isinstance(spec.get("training_args"), dict) else {}
    merged = {}
    merged.update(training_args)
    merged.update(params)
    return merged


def _safe_local_path(value):
    text = str(value or "").strip()
    if not text or "://" in text:
        return None
    path = Path(text).expanduser()
    try:
        path = path.resolve()
    except Exception:
        return None
    try:
        state_root = STATE_DIR.resolve()
    except Exception:
        state_root = STATE_DIR
    try:
        if not path.is_relative_to(state_root):
            return None
    except AttributeError:
        try:
            path.relative_to(state_root)
        except ValueError:
            return None
    except ValueError:
        return None
    return path


def _adapter_dir_from_artifact_path(path, work_dir):
    if path is None or not path.exists():
        return None
    if path.is_dir():
        if (path / "adapter_config.json").exists():
            return path
        nested = path / "adapter"
        if nested.exists() and (nested / "adapter_config.json").exists():
            return nested
        return None
    if not path.is_file():
        return None
    extract_dir = work_dir / "parent_adapter"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    if path.suffixes[-2:] == [".tar", ".gz"] or path.name.endswith(".tgz"):
        import tarfile
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(extract_dir)
    elif path.suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(path) as zf:
            zf.extractall(extract_dir)
    else:
        return None
    if (extract_dir / "adapter_config.json").exists():
        return extract_dir
    nested = extract_dir / "adapter"
    if nested.exists() and (nested / "adapter_config.json").exists():
        return nested
    matches = list(extract_dir.rglob("adapter_config.json"))
    if matches:
        return matches[0].parent
    return None


def _local_parent_adapter_dir(normalized, work_dir):
    spec = normalized.get("spec") if isinstance(normalized.get("spec"), dict) else {}
    parent_model = spec.get("parent_model") if isinstance(spec.get("parent_model"), dict) else {}
    if not parent_model:
        return None, "none"
    artifact_ref = parent_model.get("artifact_ref") if isinstance(parent_model.get("artifact_ref"), dict) else {}
    raw_uri = (
        parent_model.get("artifact_uri")
        or parent_model.get("uri")
        or artifact_ref.get("uri")
        or artifact_ref.get("path")
        or ""
    )
    path = _safe_local_path(raw_uri)
    if path is None:
        return None, "parent_model_artifact_not_local"
    adapter_dir = _adapter_dir_from_artifact_path(path, work_dir)
    if adapter_dir is None:
        return None, "parent_model_adapter_not_found"
    return adapter_dir, "loaded"


def _safe_int_between(value, default, minimum, maximum):
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(int(minimum), min(int(number), int(maximum)))


def _safe_float_between(value, default, minimum, maximum):
    try:
        number = float(value)
    except Exception:
        number = float(default)
    if number != number or number == float("inf") or number == float("-inf"):
        number = float(default)
    return max(float(minimum), min(float(number), float(maximum)))


def _should_run_local_training_job(normalized):
    if normalized.get("job_type") not in {"lora", "qlora"}:
        return False
    package = normalized.get("dataset_package")
    ref = normalized.get("dataset_package_ref") if isinstance(normalized.get("dataset_package_ref"), dict) else {}
    if isinstance(package, dict):
        samples = package.get("samples")
        if not isinstance(samples, list) or not samples:
            return False
    elif ref.get("dataset_path"):
        path = _safe_local_path(ref.get("dataset_path"))
        if path is None or not path.is_file():
            return False
    else:
        return False
    profile = _local_training_model_profile(normalized)
    return profile in TRAINING_MODEL_PROFILES


def _local_training_runner_script():
    return r'''
import hashlib
import json
import os
import re
import tarfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("DISABLE_TQDM", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

result = {"ok": False, "metrics": {}, "artifacts": []}

def emit():
    print("SKILLFORGE_TRAINING_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

try:
    import torch
    if not hasattr(torch.nn.Module, "set_submodule"):
        def _sf_set_submodule(self, target, module):
            parts = str(target).split(".")
            parent = self.get_submodule(".".join(parts[:-1])) if len(parts) > 1 else self
            setattr(parent, parts[-1], module)
        torch.nn.Module.set_submodule = _sf_set_submodule
    from torch.utils.data import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
        FineGrainedFP8Config,
        Trainer,
        TrainingArguments,
    )
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training

    job_id = os.environ["SKILLFORGE_JOB_ID"]
    model_dir = Path(os.environ["SKILLFORGE_MODEL_DIR"])
    dataset_path = Path(os.environ["SKILLFORGE_DATASET_JSON"])
    output_dir = Path(os.environ["SKILLFORGE_OUTPUT_DIR"])
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir = output_dir / "adapter"
    max_steps = max(1, int(os.environ.get("SKILLFORGE_MAX_STEPS") or "3"))
    max_seq_len = max(128, min(int(os.environ.get("SKILLFORGE_MAX_SEQ_LEN") or "768"), 4096))
    learning_rate = float(os.environ.get("SKILLFORGE_LEARNING_RATE") or "0.0002")
    grad_accum = max(1, int(os.environ.get("SKILLFORGE_GRAD_ACCUM") or "8"))
    micro_batch = max(1, int(os.environ.get("SKILLFORGE_MICRO_BATCH") or "1"))
    lora_rank = max(1, int(os.environ.get("SKILLFORGE_LORA_RANK") or "8"))
    lora_alpha = max(1, int(os.environ.get("SKILLFORGE_LORA_ALPHA") or str(lora_rank * 2)))
    lora_dropout = float(os.environ.get("SKILLFORGE_LORA_DROPOUT") or "0.05")
    parent_adapter = os.environ.get("SKILLFORGE_PARENT_ADAPTER_DIR") or ""
    eval_max_samples = max(0, min(int(os.environ.get("SKILLFORGE_EVAL_MAX_SAMPLES") or "4"), 128))
    eval_max_new_tokens = max(1, min(int(os.environ.get("SKILLFORGE_EVAL_MAX_NEW_TOKENS") or "48"), 512))
    target_modules = [
        item.strip()
        for item in (os.environ.get("SKILLFORGE_LORA_TARGET_MODULES") or "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj").split(",")
        if item.strip()
    ]

    package = json.loads(dataset_path.read_text(encoding="utf-8"))
    samples = package.get("samples") if isinstance(package, dict) else []
    train_samples = [item for item in samples if isinstance(item, dict) and item.get("split") != "eval"]
    eval_samples = [item for item in samples if isinstance(item, dict) and item.get("split") == "eval"]
    sample_ids = [str(item.get("id") or "")[:120] for item in samples if isinstance(item, dict) and str(item.get("id") or "")]
    if not train_samples:
        train_samples = [item for item in samples if isinstance(item, dict)]
    if not train_samples:
        raise RuntimeError("dataset has no usable training samples")

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def render_sample(item):
        return (
            "### 指令\n" + str(item.get("instruction") or "") +
            "\n\n### 输入\n" + str(item.get("input") or "") +
            "\n\n### 输出\n" + str(item.get("output") or "")
        )

    def render_prompt(item):
        return (
            "### 指令\n" + str(item.get("instruction") or "") +
            "\n\n### 输入\n" + str(item.get("input") or "") +
            "\n\n### 输出\n"
        )

    def text_fingerprint(value):
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]

    def normalize_text(value):
        return re.sub(r"\s+", "", str(value or "")).lower()

    def score_generation(generated_text, expected_text):
        cleaned = normalize_text(generated_text)
        if len(cleaned) < 8:
            return 0.0, "too_short"
        expected = normalize_text(expected_text)
        if not expected:
            return 1.0, "non_empty_generation"
        expected_terms = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", expected))
        generated_terms = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", cleaned))
        if expected_terms:
            overlap = len(expected_terms & generated_terms) / max(len(expected_terms), 1)
            if overlap >= 0.08:
                return min(1.0, 0.55 + overlap), "term_overlap"
        return 0.6, "non_empty_generation"

    class SFTDataset(Dataset):
        def __init__(self, rows):
            self.rows = rows
        def __len__(self):
            return len(self.rows)
        def __getitem__(self, idx):
            encoded = tokenizer(
                render_sample(self.rows[idx]),
                truncation=True,
                max_length=max_seq_len,
                padding=False,
            )
            return encoded

    model_config = {}
    try:
        model_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    except Exception:
        model_config = {}
    existing_quant = model_config.get("quantization_config") if isinstance(model_config, dict) else None
    existing_quant_method = ""
    if isinstance(existing_quant, dict):
        existing_quant_method = str(existing_quant.get("quant_method") or existing_quant.get("_load_in_4bit") or "").lower()
    use_bnb_quant = not existing_quant_method or existing_quant_method in {"bitsandbytes", "bnb", "bnb_4bit"}
    fp8_compute_dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    fp8_training_dequantized = False
    load_kwargs = {
        "device_map": "auto",
        "trust_remote_code": True,
    }
    if existing_quant_method == "fp8":
        if torch.cuda.is_available():
            load_kwargs["device_map"] = {"": "cuda:0"}
        else:
            load_kwargs.pop("device_map", None)
        raw_block_size = existing_quant.get("weight_block_size") if isinstance(existing_quant, dict) else None
        if isinstance(raw_block_size, list):
            raw_block_size = tuple(raw_block_size)
        if not isinstance(raw_block_size, tuple):
            raw_block_size = (128, 128)
        try:
            load_kwargs["quantization_config"] = FineGrainedFP8Config(
                activation_scheme=str(existing_quant.get("activation_scheme") or "dynamic"),
                weight_block_size=raw_block_size,
                dequantize=True,
                modules_to_not_convert=existing_quant.get("modules_to_not_convert") or existing_quant.get("ignored_layers"),
                scale_fmt=str(existing_quant.get("scale_fmt") or "float"),
                quant_method=str(existing_quant.get("quant_method") or "fp8"),
            )
            load_kwargs["torch_dtype"] = fp8_compute_dtype
            fp8_training_dequantized = True
        except Exception as exc:
            raise RuntimeError(f"failed to configure FP8 dequantized training load: {exc}") from exc
    elif use_bnb_quant:
        quant = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
        load_kwargs["quantization_config"] = quant
    else:
        load_kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir),
        **load_kwargs,
    )
    if existing_quant_method == "fp8":
        meta_params = [name for name, param in model.named_parameters() if getattr(param, "device", None) is not None and param.device.type == "meta"]
        if meta_params:
            raise RuntimeError("FP8 dequantized training load left meta parameters: " + ", ".join(meta_params[:8]))
    def allow_fp8_lora_training(obj, *, disable_quantized_gate=False):
        changed = False
        queue = [obj]
        seen_ids = set()
        while queue:
            current = queue.pop(0)
            if current is None or id(current) in seen_ids:
                continue
            seen_ids.add(id(current))
            quantizer = getattr(current, "hf_quantizer", None)
            if quantizer is not None:
                try:
                    quantizer.is_trainable = True
                    changed = True
                except Exception:
                    pass
            if disable_quantized_gate and hasattr(current, "is_quantized"):
                try:
                    setattr(current, "is_quantized", False)
                    changed = True
                except Exception:
                    pass
            for attr in ("base_model", "model"):
                try:
                    child = getattr(current, attr, None)
                except Exception:
                    child = None
                if child is not None and id(child) not in seen_ids:
                    queue.append(child)
        return changed

    fp8_lora_training_override = False
    if existing_quant_method == "fp8":
        fp8_lora_training_override = allow_fp8_lora_training(model)
    model.config.use_cache = False
    if str(os.environ.get("SKILLFORGE_GRADIENT_CHECKPOINTING") or "true").lower() in {"1", "true", "yes", "on"}:
        model.gradient_checkpointing_enable()
    if existing_quant_method == "fp8":
        for param in model.parameters():
            param.requires_grad = False
    else:
        model = prepare_model_for_kbit_training(model)
    if parent_adapter:
        model = PeftModel.from_pretrained(model, parent_adapter, is_trainable=True)
    else:
        lora = LoraConfig(
            r=lora_rank,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        )
        model = get_peft_model(model, lora)
    if existing_quant_method == "fp8":
        fp8_lora_training_override = allow_fp8_lora_training(model, disable_quantized_gate=True) or fp8_lora_training_override
        for param in model.parameters():
            if param.requires_grad and param.dtype == torch.float32:
                param.data = param.data.to(fp8_compute_dtype)
    trainable_params = 0
    total_params = 0
    for _, param in model.named_parameters():
        total_params += param.numel()
        if param.requires_grad:
            trainable_params += param.numel()

    use_bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    args = TrainingArguments(
        output_dir=str(output_dir / "trainer"),
        per_device_train_batch_size=micro_batch,
        gradient_accumulation_steps=grad_accum,
        max_steps=max_steps,
        learning_rate=learning_rate,
        fp16=not use_bf16,
        bf16=use_bf16,
        gradient_checkpointing=str(os.environ.get("SKILLFORGE_GRADIENT_CHECKPOINTING") or "true").lower() in {"1", "true", "yes", "on"},
        logging_steps=1,
        save_strategy="no",
        report_to=[],
        remove_unused_columns=False,
        optim="paged_adamw_8bit" if use_bnb_quant else "adamw_torch",
    )
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=SFTDataset(train_samples),
        data_collator=collator,
    )
    started = time.time()
    train_result = trainer.train()
    runtime = time.time() - started

    eval_rows = eval_samples[:eval_max_samples] if eval_max_samples else []
    eval_report = []
    eval_pass_count = 0
    eval_score_total = 0.0
    if eval_rows:
        model.eval()
        for row in eval_rows:
            prompt = render_prompt(row)
            encoded = tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=max_seq_len,
                padding=False,
            )
            try:
                device = next(model.parameters()).device
            except StopIteration:
                device = None
            if device is not None and str(device) != "meta":
                encoded = {key: value.to(device) for key, value in encoded.items()}
            input_len = int(encoded["input_ids"].shape[-1])
            with torch.no_grad():
                generated = model.generate(
                    **encoded,
                    max_new_tokens=eval_max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )
            generated_text = tokenizer.decode(generated[0][input_len:], skip_special_tokens=True).strip()
            score, reason = score_generation(generated_text, row.get("output"))
            passed = score >= 0.5
            eval_pass_count += 1 if passed else 0
            eval_score_total += float(score)
            eval_report.append({
                "id": str(row.get("id") or "")[:120],
                "passed": passed,
                "score": round(float(score), 4),
                "reason": reason,
                "prompt_sha256_16": text_fingerprint(prompt),
                "expected_sha256_16": text_fingerprint(row.get("output")),
                "generated_chars": len(generated_text),
                "generated_preview": generated_text[:240],
            })

    adapter_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(adapter_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(adapter_dir))
    tar_path = output_dir / "adapter.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(str(adapter_dir), arcname="adapter")
    sha = sha256_file(tar_path)
    eval_report_path = output_dir / "eval_report.json"
    eval_report_payload = {
        "mode": "holdout_generation_smoke",
        "job_id": job_id,
        "model_profile": os.environ.get("SKILLFORGE_MODEL_PROFILE") or "",
        "eval_requested_samples": len(eval_samples),
        "eval_evaluated_samples": len(eval_rows),
        "eval_pass_count": eval_pass_count,
        "eval_max_new_tokens": eval_max_new_tokens,
        "items": eval_report,
    }
    eval_report_path.write_text(json.dumps(eval_report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    eval_report_sha = sha256_file(eval_report_path)
    eval_generation_success_rate = (
        round(float(eval_pass_count) / max(float(len(eval_rows)), 1.0), 6)
        if eval_rows else 0.0
    )
    eval_avg_score = (
        round(float(eval_score_total) / max(float(len(eval_rows)), 1.0), 6)
        if eval_rows else 0.0
    )
    metrics = dict(getattr(train_result, "metrics", {}) or {})
    metrics.update({
        "dataset_ref": str(package.get("dataset_ref") or "")[:500] if isinstance(package, dict) else "",
        "dataset_manifest_hash": str(package.get("manifest_hash") or "")[:128] if isinstance(package, dict) else "",
        "deployment_runtime_profile": os.environ.get("SKILLFORGE_DEPLOYMENT_RUNTIME_PROFILE") or "",
        "dataset_sample_ids": sample_ids[:200],
        "train_samples": len(train_samples),
        "eval_samples": len(eval_samples),
        "eval_mode": "holdout_generation_smoke",
        "eval_requested_samples": len(eval_samples),
        "eval_evaluated_samples": len(eval_rows),
        "eval_pass_count": eval_pass_count,
        "eval_fail_count": max(len(eval_rows) - eval_pass_count, 0),
        "eval_generation_success_rate": eval_generation_success_rate,
        "eval_avg_score": eval_avg_score,
        "eval_report_sha256": eval_report_sha,
        "win_rate": eval_generation_success_rate,
        "max_steps": max_steps,
        "max_seq_len": max_seq_len,
        "parent_adapter_loaded": bool(parent_adapter),
        "base_quant_method": existing_quant_method or ("bitsandbytes_4bit" if use_bnb_quant else "none"),
        "fp8_training_dequantized": fp8_training_dequantized,
        "fp8_lora_training_override": fp8_lora_training_override,
        "train_runtime_seconds": round(runtime, 3),
        "trainable_params": int(trainable_params),
        "total_params": int(total_params),
        "trainable_param_ratio": round(float(trainable_params) / max(float(total_params), 1.0), 6),
        "adapter_sha256": sha,
        "cuda_available": bool(torch.cuda.is_available()),
    })
    if torch.cuda.is_available():
        metrics["cuda_device"] = torch.cuda.get_device_name(0)
        metrics["cuda_memory_allocated_mb"] = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 2)
        metrics["cuda_memory_reserved_mb"] = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 2)
    result.update({
        "ok": True,
        "status": "completed",
        "metrics": metrics,
        "artifacts": [{
            "id": job_id + ":adapter",
            "type": "lora_adapter",
            "name": "adapter.tar.gz",
            "uri": str(tar_path),
            "sha256": sha,
            "size_bytes": tar_path.stat().st_size,
            "model_profile": os.environ.get("SKILLFORGE_MODEL_PROFILE") or "",
            "deployment_runtime_profile": os.environ.get("SKILLFORGE_DEPLOYMENT_RUNTIME_PROFILE") or "",
            "deployment_format": "lora_adapter",
        }, {
            "id": job_id + ":eval_report",
            "type": "eval_report",
            "name": "eval_report.json",
            "uri": str(eval_report_path),
            "sha256": eval_report_sha,
            "size_bytes": eval_report_path.stat().st_size,
            "model_profile": os.environ.get("SKILLFORGE_MODEL_PROFILE") or "",
        }],
    })
    emit()
except Exception as exc:
    result.update({
        "ok": False,
        "status": "failed",
        "error": repr(exc)[-4000:],
        "traceback_tail": traceback.format_exc()[-8000:],
    })
    emit()
    raise
'''


def _local_training_result_from_stdout(stdout):
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("SKILLFORGE_TRAINING_RESULT="):
            line = line.split("=", 1)[1].strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _local_training_job_worker(normalized):
    job_id = normalized["job_id"]
    record = _load_training_job_record(job_id) or {
        "job_id": job_id,
        "status": "running",
        "bridge_instance_id": INSTANCE_ID,
        "payload": _training_payload_for_record(normalized),
        "events": [],
    }
    try:
        profile = _local_training_model_profile(normalized)
        profile_cfg = TRAINING_MODEL_PROFILES[profile]
        bootstrap_profile = str(profile_cfg.get("bootstrap_profile") or TRAINING_BOOTSTRAP_DEFAULT_PROFILE)
        params = _local_training_parameters(normalized)
        max_steps = _safe_int_between(
            params.get("max_steps") or os.getenv("LOCAL_TRAINING_RUNNER_DEFAULT_MAX_STEPS"),
            LOCAL_TRAINING_RUNNER_DEFAULT_MAX_STEPS,
            1,
            10000,
        )
        max_seq_len = _safe_int_between(
            params.get("max_seq_len")
            or params.get("max_seq_length")
            or params.get("max_sequence_length")
            or os.getenv("LOCAL_TRAINING_RUNNER_MAX_SEQ_LEN"),
            LOCAL_TRAINING_RUNNER_MAX_SEQ_LEN,
            128,
            4096,
        )
        timeout_seconds = _safe_int_between(
            params.get("timeout_seconds") or os.getenv("LOCAL_TRAINING_RUNNER_TIMEOUT_SECONDS"),
            LOCAL_TRAINING_RUNNER_TIMEOUT_SECONDS,
            60,
            86400,
        )
        python_path = _training_bootstrap_python_path(bootstrap_profile)
        model_dir = _training_model_profile_dir(profile) / "model"
        package = normalized.get("dataset_package") if isinstance(normalized.get("dataset_package"), dict) else {}
        dataset_ref_record = normalized.get("dataset_package_ref") if isinstance(normalized.get("dataset_package_ref"), dict) else {}
        if not package and dataset_ref_record.get("dataset_path"):
            dataset_path_ref = _safe_local_path(dataset_ref_record.get("dataset_path"))
            if dataset_path_ref is None or not dataset_path_ref.is_file():
                raise RuntimeError("dataset_package_ref dataset file not found")
            expected_sha = str(dataset_ref_record.get("sha256") or dataset_ref_record.get("dataset_sha256") or "").strip().lower()
            if expected_sha and _training_sha256_file(dataset_path_ref) != expected_sha:
                raise RuntimeError("dataset_package_ref sha256 mismatch")
            try:
                loaded_package = json.loads(dataset_path_ref.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"dataset_package_ref is not valid JSON: {exc}") from exc
            if not isinstance(loaded_package, dict):
                raise RuntimeError("dataset_package_ref JSON must be an object")
            package = loaded_package
        samples = package.get("samples") if isinstance(package.get("samples"), list) else []

        record["status"] = "running"
        record["progress"] = 0.05
        record["worker_id"] = INSTANCE_ID or "local-bridge"
        _append_training_job_event(
            record,
            "local_runner_started",
            "local QLoRA runner started",
            profile=profile,
            bootstrap_profile=bootstrap_profile,
            sample_count=len(samples),
            max_steps=max_steps,
            dataset_ref_mode="bridge_dataset" if dataset_ref_record else "inline",
        )
        _write_training_job_record(job_id, record)

        if not python_path.exists():
            raise RuntimeError(f"training python not found: {python_path}")
        if not model_dir.exists():
            raise RuntimeError(f"training model not found: {model_dir}")
        if not samples:
            raise RuntimeError("dataset_package has no samples")

        work_dir = _local_training_job_dir(job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        parent_adapter_dir, parent_adapter_status = _local_parent_adapter_dir(normalized, work_dir)
        spec = normalized.get("spec") if isinstance(normalized.get("spec"), dict) else {}
        deployment_spec = spec.get("deployment") if isinstance(spec.get("deployment"), dict) else {}
        deployment_runtime_profile = str(
            deployment_spec.get("deployment_runtime_profile")
            or deployment_spec.get("runtime_profile")
            or ""
        )[:80]
        parent_required = str(spec.get("training_mode") or "").strip().lower() in {
            "daily_incremental",
            "full_history_incremental",
        } and bool(spec.get("parent_model"))
        if parent_required and parent_adapter_dir is None:
            raise RuntimeError(f"daily incremental training requires previous adapter: {parent_adapter_status}")
        _append_training_job_event(
            record,
            "local_parent_adapter_checked",
            "parent adapter checked",
            parent_adapter_status=parent_adapter_status,
            parent_adapter_loaded=bool(parent_adapter_dir),
        )
        _write_training_job_record(job_id, record)
        dataset_path = work_dir / "dataset.json"
        dataset_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        output_dir = work_dir / "output"
        target_modules = params.get("target_modules") or params.get("lora_target_modules")
        if isinstance(target_modules, list):
            target_modules_value = ",".join(str(item) for item in target_modules if str(item or "").strip())
        else:
            target_modules_value = str(target_modules or "")

        env = _training_subprocess_env({
            "SKILLFORGE_JOB_ID": job_id,
            "SKILLFORGE_MODEL_PROFILE": profile,
            "SKILLFORGE_DEPLOYMENT_RUNTIME_PROFILE": deployment_runtime_profile,
            "SKILLFORGE_MODEL_DIR": str(model_dir),
            "SKILLFORGE_DATASET_JSON": str(dataset_path),
            "SKILLFORGE_OUTPUT_DIR": str(output_dir),
            "SKILLFORGE_MAX_STEPS": str(max_steps),
            "SKILLFORGE_MAX_SEQ_LEN": str(max_seq_len),
            "SKILLFORGE_LEARNING_RATE": str(
                _safe_float_between(params.get("learning_rate") or params.get("lr"), 0.0002, 1e-7, 0.01)
            ),
            "SKILLFORGE_GRAD_ACCUM": str(_safe_int_between(params.get("grad_accum") or params.get("gradient_accumulation_steps"), 8, 1, 256)),
            "SKILLFORGE_MICRO_BATCH": str(_safe_int_between(params.get("micro_batch_size") or params.get("batch_size"), 1, 1, 16)),
            "SKILLFORGE_LORA_RANK": str(_safe_int_between(params.get("lora_rank") or params.get("rank"), 8, 1, 128)),
            "SKILLFORGE_LORA_ALPHA": str(_safe_int_between(params.get("lora_alpha") or params.get("alpha"), 16, 1, 512)),
            "SKILLFORGE_LORA_DROPOUT": str(_safe_float_between(params.get("dropout") or params.get("lora_dropout"), 0.05, 0.0, 0.5)),
            "SKILLFORGE_EVAL_MAX_SAMPLES": str(_safe_int_between(params.get("eval_max_samples"), LOCAL_TRAINING_RUNNER_EVAL_MAX_SAMPLES, 0, 128)),
            "SKILLFORGE_EVAL_MAX_NEW_TOKENS": str(_safe_int_between(params.get("eval_max_new_tokens"), LOCAL_TRAINING_RUNNER_EVAL_MAX_NEW_TOKENS, 1, 512)),
            "SKILLFORGE_GRADIENT_CHECKPOINTING": "true" if _training_bootstrap_bool(params.get("gradient_checkpointing", True)) else "false",
            "PYTORCH_CUDA_ALLOC_CONF": os.environ.get("PYTORCH_CUDA_ALLOC_CONF") or "expandable_segments:True",
        })
        if parent_adapter_dir is not None:
            env["SKILLFORGE_PARENT_ADAPTER_DIR"] = str(parent_adapter_dir)
        if target_modules_value:
            env["SKILLFORGE_LORA_TARGET_MODULES"] = target_modules_value

        proc = subprocess.run(
            [str(python_path), "-c", _local_training_runner_script()],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
        parsed = _local_training_result_from_stdout(proc.stdout)
        record["returncode"] = int(proc.returncode)
        record["stdout_tail"] = _redact_training_text(proc.stdout[-12000:], limit=12000)
        record["stderr_tail"] = _redact_training_text(proc.stderr[-12000:], limit=12000)
        if proc.returncode != 0 or not isinstance(parsed, dict) or parsed.get("ok") is not True:
            error = ""
            if isinstance(parsed, dict):
                error = parsed.get("error") or ""
                traceback_tail = parsed.get("traceback_tail") or ""
                if traceback_tail:
                    error = (error + "\n" + traceback_tail) if error else traceback_tail
            error = error or proc.stderr or f"local training runner exited with {proc.returncode}"
            record["status"] = "failed"
            record["progress"] = 1
            record["failure_stage"] = "local_training"
            record["error"] = _redact_training_text(error, limit=12000)
            record["metrics"] = _redact_training_value(parsed.get("metrics") if isinstance(parsed, dict) else {})
            _append_training_job_event(record, "local_runner_failed", record["error"])
            _write_training_job_record(job_id, record)
            return
        record["status"] = "completed"
        record["progress"] = 1
        record["failure_stage"] = None
        metrics = parsed.get("metrics") if isinstance(parsed.get("metrics"), dict) else {}
        metrics = dict(metrics)
        metrics["parent_adapter_status"] = parent_adapter_status
        metrics["parent_adapter_loaded"] = bool(parent_adapter_dir)
        metrics.setdefault("dataset_ref", str(package.get("dataset_ref") or "")[:500] if isinstance(package, dict) else "")
        metrics.setdefault("dataset_manifest_hash", str(package.get("manifest_hash") or "")[:128] if isinstance(package, dict) else "")
        metrics.setdefault(
            "dataset_sample_ids",
            [str(item.get("id") or "")[:120] for item in samples if isinstance(item, dict) and str(item.get("id") or "")][:200],
        )
        record["metrics"] = _redact_training_value(metrics)
        record["artifacts"] = _redact_training_value(parsed.get("artifacts") or [])
        _append_training_job_event(
            record,
            "local_runner_completed",
            "local QLoRA runner completed",
            metrics=record["metrics"],
            artifacts=record["artifacts"],
        )
        _write_training_job_record(job_id, record)
    except subprocess.TimeoutExpired as exc:
        record["status"] = "failed"
        record["progress"] = 1
        record["failure_stage"] = "local_training_timeout"
        record["error"] = "local training runner timed out"
        record["stdout_tail"] = _redact_training_text(getattr(exc, "stdout", "") or "", limit=12000)
        record["stderr_tail"] = _redact_training_text(getattr(exc, "stderr", "") or "", limit=12000)
        _append_training_job_event(record, "local_runner_timeout", record["error"])
        _write_training_job_record(job_id, record)
    except Exception as exc:
        record["status"] = "failed"
        record["progress"] = 1
        record["failure_stage"] = "local_training"
        record["error"] = _redact_training_text(exc, limit=4000)
        _append_training_job_event(record, "local_runner_failed", record["error"])
        _write_training_job_record(job_id, record)


def _start_local_training_job(normalized):
    thread = threading.Thread(
        target=_local_training_job_worker,
        args=(normalized,),
        name=f"sf-local-training-{normalized.get('job_id')}",
        daemon=True,
    )
    thread.start()
    return thread


def _normalize_training_inference_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    profile = str(payload.get("profile") or payload.get("model_profile") or TRAINING_MODEL_DEFAULT_PROFILE).strip().lower()
    if profile not in TRAINING_MODEL_PROFILES:
        return None, "unsupported training model profile"
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    bootstrap_profile = str(payload.get("bootstrap_profile") or profile_cfg.get("bootstrap_profile") or TRAINING_BOOTSTRAP_DEFAULT_PROFILE).strip().lower()
    if bootstrap_profile not in TRAINING_BOOTSTRAP_PROFILES:
        return None, "unsupported training bootstrap profile"
    base_model_only = bool(payload.get("base_model_only") or payload.get("base_only"))
    artifact_uri = str(payload.get("artifact_uri") or payload.get("adapter_uri") or "").strip()
    artifact_path = None
    adapter_format = "none" if base_model_only else ""
    if not artifact_uri and not base_model_only:
        return None, "artifact_uri is required"
    if artifact_uri:
        parsed_uri = urllib.parse.urlparse(artifact_uri)
        if parsed_uri.scheme == "file":
            artifact_uri = urllib.parse.unquote(parsed_uri.path or "")
        elif parsed_uri.scheme:
            return None, "artifact_uri must be an absolute local path"
        artifact_path = Path(artifact_uri).expanduser()
        if not artifact_path.is_absolute():
            return None, "artifact_uri must be an absolute local path"
        try:
            artifact_path = artifact_path.resolve(strict=True)
        except Exception:
            return None, "artifact_uri does not exist"
        try:
            artifact_path.relative_to(STATE_DIR.resolve(strict=False))
        except Exception:
            return None, "artifact_uri must be under bridge state dir"
        if artifact_path.is_dir():
            adapter_format = "dir"
        elif artifact_path.is_file() and artifact_path.suffixes[-2:] == [".tar", ".gz"]:
            adapter_format = "tar.gz"
        elif artifact_path.is_file():
            return None, "artifact_uri must be adapter directory or .tar.gz"
        else:
            return None, "artifact_uri is invalid"
    prompt = str(payload.get("prompt") or "")
    if not prompt.strip():
        return None, "prompt is required"
    max_prompt_chars = _safe_int_between(payload.get("max_prompt_chars"), 6000, 100, 20000)
    requested_max_new_tokens = _safe_int_between(
        payload.get("max_new_tokens"),
        96,
        1,
        TRAINING_INFERENCE_ABSOLUTE_MAX_NEW_TOKENS,
    )
    max_new_tokens = _training_inference_effective_max_new_tokens(profile, requested_max_new_tokens)
    timeout_seconds = _safe_int_between(payload.get("timeout_seconds"), 600, 30, 1800)
    return {
        "profile": profile,
        "runtime_profile": str(payload.get("runtime_profile") or payload.get("deployment_runtime_profile") or "")[:80],
        "bootstrap_profile": bootstrap_profile,
        "base_model_only": base_model_only,
        "artifact_uri": str(artifact_path) if artifact_path is not None else "",
        "artifact_format": adapter_format,
        "artifact_sha256": str(payload.get("artifact_sha256") or "")[:64],
        "prompt": prompt[:max_prompt_chars],
        "requested_max_new_tokens": requested_max_new_tokens,
        "max_new_tokens": max_new_tokens,
        "timeout_seconds": timeout_seconds,
        "deployment_id": str(payload.get("deployment_id") or payload.get("model_deployment_id") or "")[:120],
    }, None


def _training_inference_runner_script():
    return r'''
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("DISABLE_TQDM", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

result = {"ok": False, "backend": "bridge_local_inference"}

def emit():
    print("SKILLFORGE_INFERENCE_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}

def model_uses_language_model_prefix(model_dir):
    cfg = read_json(model_dir / "config.json")
    model_type = str(cfg.get("model_type") or cfg.get("_skillforge_original_model_type") or "").lower()
    return model_type == "qwen3_5_moe" or isinstance(cfg.get("text_config"), dict) or bool(cfg.get("model_file"))

def save_mlx_safetensors(path, weights):
    import mlx.core as mx

    if hasattr(mx, "save_safetensors"):
        mx.save_safetensors(str(path), weights)
        return
    import numpy as np
    from safetensors.numpy import save_file

    arrays = {}
    for key, value in weights.items():
        try:
            arrays[key] = np.asarray(value)
        except Exception:
            arrays[key] = np.asarray(value.tolist(), dtype=np.float32)
    save_file(arrays, str(path))

def convert_peft_lora_adapter_for_mlx(adapter_dir, model_dir, expected_sha):
    adapter_dir = Path(adapter_dir)
    config_path = adapter_dir / "adapter_config.json"
    mlx_weight_path = adapter_dir / "adapters.safetensors"
    peft_weight_path = adapter_dir / "adapter_model.safetensors"
    config = read_json(config_path)
    if config.get("num_layers") and isinstance(config.get("lora_parameters"), dict) and mlx_weight_path.exists():
        return adapter_dir
    if str(config.get("peft_type") or "").upper() != "LORA" or not peft_weight_path.exists():
        return adapter_dir

    cache_root = Path(os.environ.get("SKILLFORGE_ADAPTER_MLX_CACHE_DIR") or "").expanduser()
    cache_key_raw = "|".join([
        str(adapter_dir),
        str(peft_weight_path.stat().st_size if peft_weight_path.exists() else ""),
        str(expected_sha or ""),
        json.dumps(config, sort_keys=True, ensure_ascii=False),
    ])
    cache_key = hashlib.sha256(cache_key_raw.encode("utf-8")).hexdigest()[:24]
    if cache_root:
        out_dir = cache_root / cache_key
        marker_path = out_dir / ".skillforge_mlx_adapter.json"
        marker = {"source": str(adapter_dir), "sha256": expected_sha or "", "version": 2}
        if (
            out_dir.is_dir()
            and (out_dir / "adapter_config.json").is_file()
            and (out_dir / "adapters.safetensors").is_file()
            and read_json(marker_path) == marker
        ):
            return out_dir
        tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        write_marker_path = tmp_dir / ".skillforge_mlx_adapter.json"
    else:
        tmp_handle = tempfile.TemporaryDirectory(prefix="sf_mlx_adapter_")
        tmp_dir = Path(tmp_handle.name)
        out_dir = tmp_dir
        marker = {"source": str(adapter_dir), "sha256": expected_sha or "", "version": 2}
        write_marker_path = out_dir / ".skillforge_mlx_adapter.json"

    import mlx.core as mx

    source_weights = mx.load(str(peft_weight_path))
    converted = {}
    modules = set()
    layers = []
    language_prefix = model_uses_language_model_prefix(model_dir)
    for key, value in source_weights.items():
        text_key = str(key)
        suffix = None
        if text_key.endswith(".lora_A.weight"):
            suffix = "lora_a"
            stem = text_key[: -len(".lora_A.weight")]
        elif text_key.endswith(".lora_B.weight"):
            suffix = "lora_b"
            stem = text_key[: -len(".lora_B.weight")]
        else:
            continue
        for prefix in ("base_model.model.", "model."):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
                break
        match = re.search(r"layers\.(\d+)\.(.+)$", stem)
        if match:
            layers.append(int(match.group(1)))
            modules.add(match.group(2))
        target_key = f"{stem}.{suffix}"
        if language_prefix and not target_key.startswith("language_model."):
            target_key = "language_model." + target_key
        if getattr(value, "ndim", None) == 2:
            value = value.T
        converted[target_key] = value
    if not converted:
        return adapter_dir

    rank = int(config.get("r") or config.get("rank") or 8)
    alpha = float(config.get("lora_alpha") or config.get("alpha") or rank)
    scale = alpha / max(rank, 1)
    mlx_config = {
        "fine_tune_type": "lora",
        "num_layers": (max(layers) + 1) if layers else int(config.get("num_layers") or 1),
        "lora_parameters": {
            "rank": rank,
            "dropout": float(config.get("lora_dropout") or 0.0),
            "scale": scale,
            "keys": sorted(modules),
        },
        "source_format": "peft_lora",
        "source_peft_version": str(config.get("peft_version") or ""),
    }
    save_mlx_safetensors(tmp_dir / "adapters.safetensors", converted)
    (tmp_dir / "adapter_config.json").write_text(json.dumps(mlx_config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    if cache_root:
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        tmp_dir.replace(out_dir)
    return out_dir

try:
    model_dir = Path(os.environ["SKILLFORGE_MODEL_DIR"])
    runtime_profile = os.environ.get("SKILLFORGE_RUNTIME_PROFILE") or ""
    base_model_only = os.environ.get("SKILLFORGE_BASE_MODEL_ONLY") == "1"
    raw_artifact_uri = os.environ.get("SKILLFORGE_ADAPTER_URI") or ""
    artifact_uri = Path(raw_artifact_uri) if raw_artifact_uri else None
    artifact_format = os.environ.get("SKILLFORGE_ADAPTER_FORMAT") or ""
    expected_sha = os.environ.get("SKILLFORGE_ADAPTER_SHA256") or ""
    prompt = os.environ.get("SKILLFORGE_PROMPT") or ""
    requested_max_new_tokens = max(
        1,
        min(
            int(os.environ.get("SKILLFORGE_REQUESTED_MAX_NEW_TOKENS") or os.environ.get("SKILLFORGE_MAX_NEW_TOKENS") or "96"),
            4096,
        ),
    )
    max_new_tokens = max(1, min(int(os.environ.get("SKILLFORGE_MAX_NEW_TOKENS") or "96"), 4096))
    started = time.time()

    if expected_sha and artifact_uri is not None and artifact_uri.is_file():
        actual_sha = sha256_file(artifact_uri)
        if actual_sha.lower() != expected_sha.lower():
            raise RuntimeError("adapter sha256 mismatch")

    cleanup = None
    adapter_dir = artifact_uri
    if artifact_format == "tar.gz" and artifact_uri is not None:
        cleanup = tempfile.TemporaryDirectory(prefix="sf_adapter_")
        with tarfile.open(artifact_uri, "r:gz") as tar:
            def _is_safe(member):
                name = str(member.name or "")
                return name and not name.startswith("/") and ".." not in Path(name).parts
            members = [member for member in tar.getmembers() if _is_safe(member)]
            tar.extractall(cleanup.name, members=members)
        extracted = Path(cleanup.name)
        if (extracted / "adapter").exists():
            adapter_dir = extracted / "adapter"
        else:
            adapter_dir = extracted

    if runtime_profile.lower().startswith("mlx-") or os.environ.get("SKILLFORGE_USE_MLX") == "1":
        if not base_model_only and adapter_dir is not None:
            adapter_dir = convert_peft_lora_adapter_for_mlx(adapter_dir, model_dir, expected_sha)
        from mlx_lm import generate, load

        adapter_path = None if base_model_only else str(adapter_dir)
        model, tokenizer = load(str(model_dir), adapter_path=adapter_path)
        try:
            prompt_tokens = len(tokenizer.encode(prompt))
        except Exception:
            prompt_tokens = max(1, len(prompt) // 4)
        text = str(generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_new_tokens,
            verbose=False,
        ) or "").strip()
        generated_tokens = max(1, len(text) // 4) if text else 0
        duration_ms = int((time.time() - started) * 1000)
        metrics = {
            "prompt_tokens": prompt_tokens,
            "generated_tokens": generated_tokens,
            "requested_max_new_tokens": requested_max_new_tokens,
            "effective_max_new_tokens": max_new_tokens,
            "duration_ms": duration_ms,
            "tokens_per_second": round(generated_tokens / (duration_ms / 1000), 2) if duration_ms > 0 else None,
            "inference_backend": "bridge_local_mlx_base_inference" if base_model_only else "bridge_local_mlx_lora_inference",
            "inference_runtime": "local_bridge_mlx",
            "runtime_profile": runtime_profile,
            "base_model_only": base_model_only,
            "mlx_available": True,
        }
        result.update({
            "ok": True,
            "status": "completed",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "finish_reason": "length" if generated_tokens >= max_new_tokens else "stop",
            "metrics": metrics,
        })
        if cleanup:
            cleanup.cleanup()
        emit()
    else:
        import torch
        if not hasattr(torch.nn.Module, "set_submodule"):
            def _sf_set_submodule(self, target, module):
                parts = str(target).split(".")
                parent = self.get_submodule(".".join(parts[:-1])) if len(parts) > 1 else self
                setattr(parent, parts[-1], module)
            torch.nn.Module.set_submodule = _sf_set_submodule
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from peft import PeftModel

        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model_config = {}
        try:
            model_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        except Exception:
            model_config = {}
        existing_quant = model_config.get("quantization_config") if isinstance(model_config, dict) else None
        existing_quant_method = ""
        if isinstance(existing_quant, dict):
            existing_quant_method = str(existing_quant.get("quant_method") or existing_quant.get("_load_in_4bit") or "").lower()
        use_bnb_quant = not existing_quant_method or existing_quant_method in {"bitsandbytes", "bnb", "bnb_4bit"}
        load_kwargs = {
            "device_map": "auto",
            "trust_remote_code": True,
        }
        if use_bnb_quant:
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
            load_kwargs["quantization_config"] = quant
        else:
            load_kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
        base_model = AutoModelForCausalLM.from_pretrained(
            str(model_dir),
            **load_kwargs,
        )
        model = base_model if base_model_only else PeftModel.from_pretrained(base_model, str(adapter_dir))
        model.eval()
        encoded = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=4096, padding=False)
        try:
            device = next(model.parameters()).device
        except StopIteration:
            device = None
        if device is not None and str(device) != "meta":
            encoded = {key: value.to(device) for key, value in encoded.items()}
        input_len = int(encoded["input_ids"].shape[-1])
        eos_token_ids = []
        for token in (tokenizer.eos_token_id, tokenizer.convert_tokens_to_ids("<|im_end|>"), tokenizer.convert_tokens_to_ids("<|endoftext|>")):
            if isinstance(token, int) and token >= 0 and token not in eos_token_ids:
                eos_token_ids.append(token)
        if not eos_token_ids:
            eos_token_ids = [tokenizer.eos_token_id]
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                repetition_penalty=1.08,
                no_repeat_ngram_size=6,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=eos_token_ids,
            )
        text = tokenizer.decode(generated[0][input_len:], skip_special_tokens=True).strip()
        generated_tokens = int(generated.shape[-1] - input_len)
        duration_ms = int((time.time() - started) * 1000)
        metrics = {
            "prompt_tokens": input_len,
            "generated_tokens": generated_tokens,
            "requested_max_new_tokens": requested_max_new_tokens,
            "effective_max_new_tokens": max_new_tokens,
            "duration_ms": duration_ms,
            "tokens_per_second": round(generated_tokens / (duration_ms / 1000), 2) if duration_ms > 0 else None,
            "inference_backend": "bridge_local_base_inference" if base_model_only else "bridge_local_lora_inference",
            "inference_runtime": "local_bridge",
            "runtime_profile": runtime_profile,
            "base_model_only": base_model_only,
            "cuda_available": bool(torch.cuda.is_available()),
            "base_quant_method": existing_quant_method or ("bitsandbytes_4bit" if use_bnb_quant else "none"),
        }
        if torch.cuda.is_available():
            metrics["cuda_device"] = torch.cuda.get_device_name(0)
            metrics["cuda_memory_allocated_mb"] = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 2)
            metrics["cuda_memory_reserved_mb"] = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 2)
        result.update({
            "ok": True,
            "status": "completed",
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "finish_reason": "length" if generated_tokens >= max_new_tokens else "stop",
            "metrics": metrics,
        })
        if cleanup:
            cleanup.cleanup()
        emit()
except Exception as exc:
    result.update({
        "ok": False,
        "status": "failed",
        "error": repr(exc)[-4000:],
        "traceback_tail": traceback.format_exc()[-8000:],
    })
    emit()
    raise
'''


def _training_inference_result_from_stdout(stdout):
    for line in reversed((stdout or "").strip().splitlines()):
        candidate = line.strip()
        if candidate.startswith("SKILLFORGE_INFERENCE_RESULT="):
            candidate = candidate.split("=", 1)[1].strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _python_has_module(python_path, module_name):
    try:
        proc = subprocess.run(
            [
                str(python_path),
                "-c",
                "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)",
                str(module_name),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=_training_subprocess_env(),
        )
        return proc.returncode == 0
    except Exception:
        return False


def _python_module_probe(python_path, module_name):
    try:
        proc = subprocess.run(
            [
                str(python_path),
                "-c",
                (
                    "import importlib, json, sys, traceback\n"
                    "name = sys.argv[1]\n"
                    "result = {'module': name, 'ok': False}\n"
                    "try:\n"
                    "    mod = importlib.import_module(name)\n"
                    "    result.update({'ok': True, 'file': getattr(mod, '__file__', ''), 'version': getattr(mod, '__version__', '')})\n"
                    "except Exception as exc:\n"
                    "    result.update({'error': str(exc), 'traceback_tail': traceback.format_exc()[-2000:]})\n"
                    "print(json.dumps(result, ensure_ascii=False))\n"
                ),
                str(module_name),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=_training_subprocess_env(),
        )
        text = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
        if text:
            try:
                result = json.loads(text)
            except Exception:
                result = {"module": module_name, "ok": False, "stdout": text[-2000:]}
        else:
            result = {"module": module_name, "ok": False}
        result.setdefault("module", module_name)
        result.setdefault("ok", proc.returncode == 0)
        if proc.returncode != 0:
            result["returncode"] = proc.returncode
            result["stderr_tail"] = (proc.stderr or "")[-2000:]
        return result
    except Exception as exc:
        return {"module": module_name, "ok": False, "error": str(exc)}


def _training_python_env_candidates(extra=None):
    raw_candidates = []
    if isinstance(extra, list):
        raw_candidates.extend(str(item) for item in extra if str(item or "").strip())
    raw_candidates.extend([
        os.environ.get("SKILLFORGE_TRAINING_PYTHON"),
        os.environ.get("LOCAL_TRAINING_PYTHON"),
        str(_training_bootstrap_python_path(TRAINING_BOOTSTRAP_DEFAULT_PROFILE)),
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "/opt/conda/bin/python",
        "/usr/local/cuda/bin/python",
    ])
    home = Path.home()
    raw_candidates.extend([
        str(home / "miniforge3" / "bin" / "python"),
        str(home / "mambaforge" / "bin" / "python"),
        str(home / "micromamba" / "bin" / "python"),
        str(home / "anaconda3" / "bin" / "python"),
    ])
    for pattern in (
        home / "miniforge3" / "envs" / "*" / "bin" / "python",
        home / "mambaforge" / "envs" / "*" / "bin" / "python",
        home / "micromamba" / "envs" / "*" / "bin" / "python",
        home / "anaconda3" / "envs" / "*" / "bin" / "python",
        home / ".conda" / "envs" / "*" / "bin" / "python",
        Path("/opt/conda/envs") / "*" / "bin" / "python",
        Path("/usr/local") / "*" / "bin" / "python",
    ):
        try:
            raw_candidates.extend(glob.glob(str(pattern)))
        except Exception:
            continue
    candidates = []
    seen = set()
    for raw in raw_candidates:
        if not raw:
            continue
        try:
            path = Path(raw)
        except Exception:
            continue
        if not path.is_absolute() or not path.exists() or not os.access(path, os.X_OK):
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(str(path))
    return candidates


def _discover_training_python_envs_sync(payload=None):
    payload = payload if isinstance(payload, dict) else {}
    try:
        max_candidates = max(1, min(int(payload.get("max_candidates") or 30), 80))
    except Exception:
        max_candidates = 30
    try:
        timeout_seconds = max(2, min(int(payload.get("timeout_seconds") or 12), 60))
    except Exception:
        timeout_seconds = 12
    candidates = _training_python_env_candidates(payload.get("candidates") if isinstance(payload.get("candidates"), list) else None)
    probe_code = r'''
import importlib
import json
import os
import platform
import sys
import traceback

result = {
    "python": sys.executable,
    "version": sys.version.split()[0],
    "prefix": sys.prefix,
    "base_prefix": getattr(sys, "base_prefix", ""),
    "machine": platform.machine(),
    "platform": platform.platform(),
    "ok": True,
    "modules": {},
}

def probe_module(name):
    item = {"ok": False}
    try:
        mod = importlib.import_module(name)
        item.update({
            "ok": True,
            "version": str(getattr(mod, "__version__", "") or ""),
            "file": str(getattr(mod, "__file__", "") or ""),
        })
    except Exception as exc:
        item.update({
            "error": repr(exc),
            "traceback_tail": traceback.format_exc()[-1200:],
        })
    result["modules"][name] = item
    return item

for module_name in ("torch", "transformers", "accelerate", "peft", "bitsandbytes", "kernels"):
    probe_module(module_name)

if result["modules"].get("torch", {}).get("ok"):
    try:
        import torch
        devices = []
        if torch.cuda.is_available():
            for idx in range(torch.cuda.device_count()):
                try:
                    props = torch.cuda.get_device_properties(idx)
                    devices.append({
                        "index": idx,
                        "name": torch.cuda.get_device_name(idx),
                        "total_memory_mb": int(getattr(props, "total_memory", 0) / (1024 * 1024)),
                        "capability": list(torch.cuda.get_device_capability(idx)),
                    })
                except Exception as exc:
                    devices.append({"index": idx, "error": repr(exc)})
        result["torch"] = {
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_count": int(torch.cuda.device_count()),
            "cuda_version": str(getattr(torch.version, "cuda", "") or ""),
            "hip_version": str(getattr(torch.version, "hip", "") or ""),
            "mps_available": bool(getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)()),
            "devices": devices,
        }
    except Exception as exc:
        result["torch"] = {"error": repr(exc), "traceback_tail": traceback.format_exc()[-1200:]}

print(json.dumps(result, ensure_ascii=False))
'''
    results = []
    for candidate in candidates[:max_candidates]:
        item = {"python": candidate, "ok": False}
        try:
            proc = subprocess.run(
                [candidate, "-c", probe_code],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=_training_subprocess_env(),
            )
            item["returncode"] = int(proc.returncode)
            stdout = (proc.stdout or "").strip()
            if stdout:
                try:
                    parsed = json.loads(stdout.splitlines()[-1])
                    if isinstance(parsed, dict):
                        item.update(parsed)
                        item["ok"] = proc.returncode == 0 and bool(parsed.get("ok", True))
                except Exception:
                    item["stdout_tail"] = stdout[-2000:]
            if proc.stderr:
                item["stderr_tail"] = _redact_training_text(proc.stderr, limit=3000)
        except subprocess.TimeoutExpired as exc:
            item.update({
                "returncode": 124,
                "error": "probe timed out",
                "stdout_tail": _redact_training_text(getattr(exc, "stdout", "") or "", limit=1000),
                "stderr_tail": _redact_training_text(getattr(exc, "stderr", "") or "", limit=1000),
            })
        except Exception as exc:
            item["error"] = _redact_training_text(exc, limit=1000)
        results.append(_redact_training_value(item))

    commands = {}
    for name, args in {
        "nvidia_smi": ["nvidia-smi", "-L"],
        "nvidia_smi_query": ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        "docker_version": ["docker", "version", "--format", "{{json .}}"],
        "docker_info": ["docker", "info", "--format", "{{json .}}"],
        "docker_images": ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}"],
    }.items():
        if shutil.which(args[0]) is None:
            commands[name] = {"available": False}
            continue
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=8)
            commands[name] = {
                "available": True,
                "returncode": int(proc.returncode),
                "stdout_tail": _redact_training_text((proc.stdout or "")[-6000:], limit=6000),
                "stderr_tail": _redact_training_text((proc.stderr or "")[-2000:], limit=2000),
            }
        except Exception as exc:
            commands[name] = {"available": True, "error": _redact_training_text(exc, limit=1000)}

    cuda_ready = [
        item for item in results
        if isinstance(item.get("torch"), dict) and item["torch"].get("cuda_available")
    ]
    return {
        "status": "succeeded",
        "bridge_instance_id": INSTANCE_ID,
        "candidate_count": len(candidates),
        "probed_count": len(results),
        "cuda_ready_count": len(cuda_ready),
        "candidates": results,
        "commands": commands,
    }


def _mlx_python_candidates(preferred_python):
    raw_candidates = [
        os.environ.get("SKILLFORGE_OPENWEBUI_MLX_PYTHON"),
        os.environ.get("MLX_PYTHON"),
        str(Path(os.environ["CONDA_PREFIX"]) / "bin" / "python") if os.environ.get("CONDA_PREFIX") else None,
        preferred_python,
        str(_openwebui_mlx_python_path()) if "_openwebui_mlx_python_path" in globals() else None,
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/bin/python",
        "/usr/local/bin/python3",
        "/usr/local/bin/python",
        "/usr/bin/python3",
    ]
    home = Path.home()
    raw_candidates.extend([
        str(home / "miniforge3" / "bin" / "python"),
        str(home / "mambaforge" / "bin" / "python"),
        str(home / "micromamba" / "bin" / "python"),
        str(home / "anaconda3" / "bin" / "python"),
        "/usr/bin/python3",
        "/opt/homebrew/Caskroom/miniforge/base/bin/python",
        "/opt/homebrew/anaconda3/bin/python",
    ])
    try:
        raw_candidates.extend(
            str(item)
            for item in (STATE_DIR / "openwebui_env").glob("mlx_*/*/bin/python")
            if Path(item).is_file()
        )
    except Exception:
        pass
    for pattern in (
        home / "miniforge3" / "envs" / "*" / "bin" / "python",
        home / "mambaforge" / "envs" / "*" / "bin" / "python",
        home / "micromamba" / "envs" / "*" / "bin" / "python",
        home / "anaconda3" / "envs" / "*" / "bin" / "python",
        home / ".conda" / "envs" / "*" / "bin" / "python",
        Path("/opt/homebrew/Caskroom/miniforge/base/envs") / "*" / "bin" / "python",
    ):
        try:
            raw_candidates.extend(str(item) for item in glob.glob(str(pattern)) if Path(item).is_file())
        except Exception:
            continue
    candidates = []
    for raw in raw_candidates:
        if not raw:
            continue
        try:
            path = Path(raw)
        except Exception:
            continue
        if not path.is_absolute():
            continue
        try:
            resolved = path.resolve(strict=True)
        except Exception:
            continue
        if resolved not in candidates:
            candidates.append(resolved)
    return candidates[:40]


def _openwebui_mlx_env_root():
    return STATE_DIR / "openwebui_env"


def _python_identity(python_path):
    try:
        proc = subprocess.run(
            [
                str(python_path),
                "-c",
                (
                    "import json, platform, sys\n"
                    "print(json.dumps({"
                    "'major': sys.version_info.major, "
                    "'minor': sys.version_info.minor, "
                    "'micro': sys.version_info.micro, "
                    "'machine': platform.machine() or 'unknown'}))\n"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            env=_training_subprocess_env(),
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    text = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _openwebui_mlx_python_path(seed_python=None):
    identity = _python_identity(seed_python) if seed_python else {}
    major = int(identity.get("major") or sys.version_info.major)
    minor = int(identity.get("minor") or sys.version_info.minor)
    machine = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(identity.get("machine") or platform.machine() or "unknown"))
    env_dir = _openwebui_mlx_env_root() / f"mlx_py{major}{minor}_{machine}" / "venv"
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _create_openwebui_mlx_env(seed_python):
    seed = Path(seed_python).resolve(strict=True)
    python_path = _openwebui_mlx_python_path(seed)
    env_dir = python_path.parents[1] if os.name != "nt" else python_path.parents[1]
    if python_path.exists():
        return python_path
    if env_dir.exists() and not python_path.exists():
        shutil.rmtree(env_dir, ignore_errors=True)
    env_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(seed), "-m", "venv", str(env_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        env=_training_subprocess_env({"PIP_DISABLE_PIP_VERSION_CHECK": "1"}),
    )
    if proc.returncode != 0 or not python_path.exists():
        detail = (proc.stderr or proc.stdout or f"venv exited with {proc.returncode}")[-3000:]
        raise RuntimeError(f"create OpenWebUI MLX venv failed for {seed}: {_redact_training_text(detail, limit=3000)}")
    return python_path


def _pip_install_from_openwebui_wheelhouse(python_path, package, *, no_deps=False, force_reinstall=False):
    package = str(package or "").strip()
    if not package:
        raise RuntimeError("pip package is empty")
    find_links_url = (
        os.environ.get("SKILLFORGE_OPENWEBUI_MLX_FIND_LINKS_URL")
        or (f"{SKILLFORGE_HTTP_BASE.rstrip('/')}/api/aiclaw/bridge/mlx-wheelhouse/" if SKILLFORGE_HTTP_BASE else "")
    )
    pip_args = [str(python_path), "-m", "pip", "install"]
    if force_reinstall:
        pip_args.extend(["--upgrade", "--force-reinstall"])
    if find_links_url:
        pip_args.extend(["--no-index", "--find-links", find_links_url])
        parsed_links = urllib.parse.urlparse(find_links_url)
        if parsed_links.hostname:
            pip_args.extend(["--trusted-host", parsed_links.hostname])
    if no_deps:
        pip_args.append("--no-deps")
    pip_args.append(package)
    try:
        proc = subprocess.run(
            pip_args,
            capture_output=True,
            text=True,
            timeout=max(60, OPENWEBUI_MLX_INSTALL_TIMEOUT_SECONDS),
            env=_training_subprocess_env({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"}),
        )
    except Exception as exc:
        raise RuntimeError(f"pip install {package} failed: {_redact_training_text(exc, limit=1000)}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"pip exited with {proc.returncode}")[-4000:]
        raise RuntimeError(f"pip install {package} failed: {_redact_training_text(detail, limit=4000)}")


def _patch_mlx_lm_future_annotations(python_path):
    patch_script = r'''
import ast
import importlib.util
import json
from pathlib import Path

result = {"patched": 0, "checked": 0, "root": ""}
spec = importlib.util.find_spec("mlx_lm")
if spec and spec.submodule_search_locations:
    root = Path(list(spec.submodule_search_locations)[0])
    result["root"] = str(root)
    for path in root.rglob("*.py"):
        result["checked"] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        changed = False
        head = "".join(text.splitlines(True)[:20])
        if "from __future__ import annotations" not in head:
            lines = text.splitlines(True)
            insert_at = 0
            try:
                tree = ast.parse(text)
                if (
                    tree.body
                    and isinstance(tree.body[0], ast.Expr)
                    and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
                    and isinstance(tree.body[0].value.value, str)
                ):
                    insert_at = int(getattr(tree.body[0], "end_lineno", 0) or 0)
            except Exception:
                if lines and lines[0].startswith("#!"):
                    insert_at = 1
                if len(lines) > insert_at and "coding" in lines[insert_at][:80]:
                    insert_at += 1
            lines.insert(insert_at, "from __future__ import annotations\n")
            text = "".join(lines)
            changed = True
        if (
            "mx.device_info()" in text
            and "_skillforge_mx_device_info_compat" not in text
            and "import mlx.core as mx\n" in text
        ):
            text = text.replace(
                "import mlx.core as mx\n",
                (
                    "import mlx.core as mx\n\n"
                    "# _skillforge_mx_device_info_compat\n"
                    "if not hasattr(mx, \"device_info\") and hasattr(mx, \"metal\"):\n"
                    "    mx.device_info = mx.metal.device_info\n"
                ),
                1,
            )
            changed = True
        if not changed:
            continue
        try:
            path.write_text(text, encoding="utf-8")
            result["patched"] += 1
        except Exception:
            continue
print(json.dumps(result, ensure_ascii=False))
'''
    try:
        proc = subprocess.run(
            [str(python_path), "-c", patch_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        raise RuntimeError(f"mlx_lm compatibility patch failed: {_redact_training_text(exc, limit=1000)}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"python exited with {proc.returncode}")[-3000:]
        raise RuntimeError(f"mlx_lm compatibility patch failed: {_redact_training_text(detail, limit=3000)}")


def _ensure_qwen35_moe_mlx_support(python_path):
    if _python_has_module(python_path, "mlx_lm.models.qwen3_5_moe"):
        _patch_mlx_lm_future_annotations(python_path)
        return
    package = str(OPENWEBUI_MLX_QWEN35_MOE_PACKAGE or "").strip()
    if not package:
        return
    _pip_install_from_openwebui_wheelhouse(python_path, package, no_deps=True, force_reinstall=True)
    _patch_mlx_lm_future_annotations(python_path)
    if not _python_has_module(python_path, "mlx_lm.models.qwen3_5_moe"):
        probe = _python_module_probe(python_path, "mlx_lm.models.qwen3_5_moe")
        raise RuntimeError(
            "mlx_lm qwen3_5_moe support is not available after installing "
            f"{package}: {_redact_training_text(probe, limit=3000)}"
        )


def _ensure_mlx_inference_python(preferred_python, *, require_qwen35_moe=False):
    for candidate in _mlx_python_candidates(preferred_python):
        if _python_has_module(candidate, "mlx_lm"):
            if require_qwen35_moe:
                _ensure_qwen35_moe_mlx_support(candidate)
            return candidate

    install_errors = []
    package = str(OPENWEBUI_MLX_PACKAGE or "mlx-lm").strip() or "mlx-lm"
    for seed in _mlx_python_candidates(preferred_python):
        try:
            install_target = _create_openwebui_mlx_env(seed)
        except Exception as exc:
            install_errors.append(_redact_training_text(exc, limit=2000))
            continue
        try:
            _pip_install_from_openwebui_wheelhouse(install_target, package)
            if not _python_has_module(install_target, "mlx_lm"):
                raise RuntimeError("mlx_lm is not available after pip install")
            if require_qwen35_moe:
                _ensure_qwen35_moe_mlx_support(install_target)
            return install_target
        except Exception as exc:
            install_errors.append(_redact_training_text(exc, limit=4000))
            continue
    detail = "; ".join(item for item in install_errors if item)[-8000:]
    raise RuntimeError(f"mlx_lm is not installed and OpenWebUI MLX env bootstrap failed: {detail}")


def _mlx_model_dir(profile):
    return _training_model_profile_dir(profile) / "mlx_model"


def _mlx_hf_compat_dir(profile):
    return _training_model_profile_dir(profile) / "mlx_hf_compat"


def _mlx_model_dir_ready(path):
    path = Path(path)
    return path.is_dir() and (path / "config.json").is_file() and any(path.glob("*.safetensors"))


def _needs_mlx_conversion(model_dir):
    model_dir = Path(model_dir)
    return (model_dir / "model.safetensors.index.json").is_file()


def _hf_config_model_type(model_dir):
    config_path = Path(model_dir) / "config.json"
    if not config_path.is_file():
        return ""
    try:
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(config_value.get("model_type") or "") if isinstance(config_value, dict) else ""


def _link_or_copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(source), str(target))
        return
    except Exception:
        pass
    try:
        os.link(str(source), str(target))
        return
    except Exception:
        pass
    shutil.copy2(source, target)


def _qwen35_moe_custom_model_file_text():
    return r'''from dataclasses import dataclass

import mlx.core as mx
from mlx.utils import tree_flatten, tree_unflatten

from mlx_lm.models.base import BaseModelArgs
from mlx_lm.models.qwen3_5 import Model as Qwen3_5Model


@dataclass
class ModelArgs(BaseModelArgs):
    model_type: str
    text_config: dict

    @classmethod
    def from_dict(cls, params):
        if "text_config" not in params:
            return cls(model_type=params["model_type"], text_config=params)
        return super().from_dict(params)


class Model(Qwen3_5Model):
    def sanitize(self, weights):
        def dequant(weight, scale_inv):
            dtype = mx.bfloat16
            if hasattr(mx, "from_fp8"):
                weight = mx.from_fp8(weight, dtype=dtype)
            else:
                weight = weight.astype(dtype)
            block_size = 128
            rows, cols = weight.shape
            pad_rows = (-rows) % block_size
            pad_cols = (-cols) % block_size
            weight = mx.pad(weight, ((0, pad_rows), (0, pad_cols)))
            weight = weight.reshape(
                (
                    (rows + pad_rows) // block_size,
                    block_size,
                    (cols + pad_cols) // block_size,
                    block_size,
                )
            )
            weight = (weight * scale_inv[:, None, :, None]).reshape(rows + pad_rows, cols + pad_cols)
            return weight[:rows, :cols].astype(dtype)

        dequantized = {}
        for key, value in weights.items():
            if "weight_scale_inv" in key:
                weight_key = key.replace("_scale_inv", "")
                if weight_key in weights:
                    dequantized[weight_key] = dequant(weights[weight_key], value)
            elif key not in dequantized:
                dequantized[key] = value
        weights = {
            key: value
            for key, value in dequantized.items()
            if not key.endswith("_scale_inv") and ".mtp." not in key and not key.startswith("mtp.")
        }
        weights = tree_unflatten(list(weights.items()))
        weights = dict(tree_flatten(weights))

        new_weights = {}
        for key, value in weights.items():
            if key.startswith("model.visual"):
                continue
            if key.endswith("_scale_inv") or ".mtp." in key or key.startswith("mtp."):
                continue
            if key.startswith("model.language_model"):
                key = key.replace("model.language_model", "language_model.model", 1)
            elif key.startswith("language_model."):
                pass
            else:
                key = "language_model." + key
            if key.endswith("_scale_inv"):
                continue
            new_weights[key] = value

        for layer_index in range(self.language_model.args.num_hidden_layers):
            prefix = f"language_model.model.layers.{layer_index}.mlp"
            gate_up_key = f"{prefix}.experts.gate_up_proj"
            if gate_up_key in new_weights:
                gate_up = new_weights.pop(gate_up_key)
                mid = gate_up.shape[-2] // 2
                new_weights[f"{prefix}.switch_mlp.gate_proj.weight"] = gate_up[..., :mid, :]
                new_weights[f"{prefix}.switch_mlp.up_proj.weight"] = gate_up[..., mid:, :]
                new_weights[f"{prefix}.switch_mlp.down_proj.weight"] = new_weights.pop(
                    f"{prefix}.experts.down_proj"
                )
                continue

            for name in ["up_proj", "down_proj", "gate_proj"]:
                first_key = f"{prefix}.experts.0.{name}.weight"
                if first_key not in new_weights:
                    continue
                to_join = []
                for expert_index in range(self.language_model.args.num_experts):
                    key = f"{prefix}.experts.{expert_index}.{name}.weight"
                    if key not in new_weights:
                        to_join = []
                        break
                    to_join.append(new_weights.pop(key))
                if to_join:
                    new_weights[f"{prefix}.switch_mlp.{name}.weight"] = mx.stack(to_join)

        return self.language_model.sanitize(new_weights)
'''


def _mlx_compatible_hf_model_dir(profile, model_dir, python_path=None):
    model_dir = Path(model_dir)
    if list(model_dir.glob("model*.safetensors")):
        return model_dir
    weight_files = sorted(path for path in model_dir.glob("*.safetensors") if path.is_file())
    if not weight_files:
        return model_dir
    compat_dir = _mlx_hf_compat_dir(profile)
    marker_path = compat_dir / ".skillforge_source.json"
    config_alias = None
    custom_model_file = None
    config_path = model_dir / "config.json"
    if config_path.is_file():
        try:
            config_value = json.loads(config_path.read_text(encoding="utf-8"))
            has_native_qwen35_moe = (
                python_path is not None
                and _python_has_module(python_path, "mlx_lm.models.qwen3_5_moe")
            )
            has_native_qwen35 = (
                python_path is not None
                and _python_has_module(python_path, "mlx_lm.models.qwen3_5")
            )
            if (
                isinstance(config_value, dict)
                and config_value.get("model_type") == "qwen3_5_moe"
                and not has_native_qwen35_moe
            ):
                config_alias = {"from": "qwen3_5_moe", "to": "qwen3_moe"}
            elif (
                isinstance(config_value, dict)
                and config_value.get("model_type") == "qwen3_5"
                and not has_native_qwen35
            ):
                config_alias = {"from": "qwen3_5", "to": "qwen3"}
            elif (
                isinstance(config_value, dict)
                and config_value.get("model_type") == "qwen3_5_moe"
                and has_native_qwen35_moe
            ):
                custom_model_file = "skillforge_qwen3_5_moe.py"
        except Exception:
            config_alias = None
            custom_model_file = None
    expected_marker = {
        "compat_version": 9,
        "source": str(model_dir),
        "weights": [path.name for path in weight_files],
        "config_alias": config_alias,
        "custom_model_file": custom_model_file,
    }
    try:
        existing_marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else None
    except Exception:
        existing_marker = None
    if (
        existing_marker == expected_marker
        and (compat_dir / "config.json").is_file()
        and len(list(compat_dir.glob("model*.safetensors"))) == len(weight_files)
    ):
        return compat_dir
    if compat_dir.exists() or compat_dir.is_symlink():
        if compat_dir.is_dir() and not compat_dir.is_symlink():
            shutil.rmtree(compat_dir)
        else:
            compat_dir.unlink()
    compat_dir.mkdir(parents=True, exist_ok=True)
    for item in model_dir.iterdir():
        if not item.is_file() or item.suffix == ".safetensors":
            continue
        if item.name == "config.json" and config_alias:
            config_value = json.loads(item.read_text(encoding="utf-8"))
            text_config = config_value.get("text_config") if isinstance(config_value.get("text_config"), dict) else None
            if text_config:
                config_value = {**config_value, **text_config}
            config_value["_skillforge_original_model_type"] = config_alias["from"]
            config_value["model_type"] = config_alias["to"]
            if "intermediate_size" not in config_value and "moe_intermediate_size" in config_value:
                config_value["intermediate_size"] = config_value["moe_intermediate_size"]
            config_value.setdefault("decoder_sparse_step", 1)
            config_value.setdefault("mlp_only_layers", [])
            config_value.setdefault("rope_theta", 1000000.0)
            config_value.setdefault("norm_topk_prob", False)
            (compat_dir / item.name).write_text(json.dumps(config_value, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        if item.name == "config.json" and custom_model_file:
            config_value = json.loads(item.read_text(encoding="utf-8"))
            if isinstance(config_value, dict):
                config_value["model_file"] = custom_model_file
            (compat_dir / item.name).write_text(json.dumps(config_value, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        _link_or_copy_file(item, compat_dir / item.name)
    if custom_model_file:
        (compat_dir / custom_model_file).write_text(_qwen35_moe_custom_model_file_text(), encoding="utf-8")
    total = len(weight_files)
    for index, source in enumerate(weight_files, start=1):
        _link_or_copy_file(source, compat_dir / f"model-{index:05d}-of-{total:05d}.safetensors")
    marker_path.write_text(json.dumps(expected_marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return compat_dir


def _ensure_mlx_model_dir(python_path, profile, model_dir):
    model_dir = Path(model_dir)
    if not _needs_mlx_conversion(model_dir):
        return model_dir
    convert_source_dir = _mlx_compatible_hf_model_dir(profile, model_dir, python_path)
    target_dir = _mlx_model_dir(profile)
    source_marker_path = Path(convert_source_dir) / ".skillforge_source.json"
    try:
        source_marker = json.loads(source_marker_path.read_text(encoding="utf-8")) if source_marker_path.exists() else {}
    except Exception:
        source_marker = {}
    expected_conversion_marker = {
        "conversion_version": 2,
        "source_dir": str(convert_source_dir),
        "source_marker": source_marker,
    }
    conversion_marker_path = target_dir / ".skillforge_conversion_source.json"
    try:
        existing_conversion_marker = (
            json.loads(conversion_marker_path.read_text(encoding="utf-8"))
            if conversion_marker_path.exists()
            else None
        )
    except Exception:
        existing_conversion_marker = None
    if _mlx_model_dir_ready(target_dir) and existing_conversion_marker == expected_conversion_marker:
        return target_dir
    tmp_dir = target_dir.with_name(target_dir.name + ".tmp")
    if tmp_dir.exists() or tmp_dir.is_symlink():
        if tmp_dir.is_dir() and not tmp_dir.is_symlink():
            shutil.rmtree(tmp_dir)
        else:
            tmp_dir.unlink()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(python_path),
        "-m",
        "mlx_lm.convert",
        "--hf-path",
        str(convert_source_dir),
        "--mlx-path",
        str(tmp_dir),
        "--dtype",
        "float16",
        "--trust-remote-code",
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(300, OPENWEBUI_MLX_CONVERT_TIMEOUT_SECONDS),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"mlx_lm.convert exited with {proc.returncode}")[-8000:]
        raise RuntimeError(f"mlx_lm.convert failed: {_redact_training_text(detail, limit=8000)}")
    if not _mlx_model_dir_ready(tmp_dir):
        raise RuntimeError(f"mlx_lm.convert did not produce a loadable model dir: {tmp_dir}")
    (tmp_dir / ".skillforge_conversion_source.json").write_text(
        json.dumps(expected_conversion_marker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if target_dir.exists() or target_dir.is_symlink():
        if target_dir.is_dir() and not target_dir.is_symlink():
            shutil.rmtree(target_dir)
        else:
            target_dir.unlink()
    tmp_dir.replace(target_dir)
    return target_dir


def _run_local_training_inference(normalized):
    profile = normalized["profile"]
    profile_cfg = TRAINING_MODEL_PROFILES[profile]
    bootstrap_profile = normalized["bootstrap_profile"]
    python_path = _training_bootstrap_python_path(bootstrap_profile)
    model_dir = _training_model_profile_dir(profile) / "model"
    runtime_profile = str(normalized.get("runtime_profile") or "")
    use_mlx = runtime_profile.lower().startswith("mlx-") or os.environ.get("SKILLFORGE_USE_MLX") == "1"
    if not python_path.exists():
        if use_mlx:
            python_path = Path(sys.executable)
        else:
            raise RuntimeError(f"training python not found: {python_path}")
    if not model_dir.exists():
        raise RuntimeError(f"training model not found: {model_dir}")
    if use_mlx:
        python_path = _ensure_mlx_inference_python(
            python_path,
            require_qwen35_moe=_hf_config_model_type(model_dir) == "qwen3_5_moe",
        )
    if use_mlx:
        model_dir = _ensure_mlx_model_dir(python_path, profile, model_dir)
    env = _training_subprocess_env({
        "SKILLFORGE_MODEL_PROFILE": profile,
        "SKILLFORGE_RUNTIME_PROFILE": runtime_profile,
        "SKILLFORGE_MODEL_ID": profile_cfg.get("model_id") or "",
        "SKILLFORGE_MODEL_DIR": str(model_dir),
        "SKILLFORGE_BASE_MODEL_ONLY": "1" if normalized.get("base_model_only") else "0",
        "SKILLFORGE_ADAPTER_URI": normalized["artifact_uri"],
        "SKILLFORGE_ADAPTER_FORMAT": normalized["artifact_format"],
        "SKILLFORGE_ADAPTER_SHA256": normalized["artifact_sha256"],
        "SKILLFORGE_ADAPTER_MLX_CACHE_DIR": str(STATE_DIR / "openwebui_adapter_cache"),
        "SKILLFORGE_PROMPT": normalized["prompt"],
        "SKILLFORGE_REQUESTED_MAX_NEW_TOKENS": str(normalized["requested_max_new_tokens"]),
        "SKILLFORGE_MAX_NEW_TOKENS": str(normalized["max_new_tokens"]),
        "PYTORCH_CUDA_ALLOC_CONF": os.environ.get("PYTORCH_CUDA_ALLOC_CONF") or "expandable_segments:True",
    })
    try:
        proc = subprocess.run(
            [str(python_path), "-c", _training_inference_runner_script()],
            capture_output=True,
            text=True,
            timeout=normalized["timeout_seconds"],
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_tail = _redact_training_text(getattr(exc, "stdout", "") or "", limit=4000)
        stderr_tail = _redact_training_text(getattr(exc, "stderr", "") or "", limit=4000)
        detail = f"local inference runner timed out after {normalized['timeout_seconds']}s"
        if stdout_tail:
            detail += f"\nstdout_tail:\n{stdout_tail}"
        if stderr_tail:
            detail += f"\nstderr_tail:\n{stderr_tail}"
        raise RuntimeError(detail) from exc
    parsed = _training_inference_result_from_stdout(proc.stdout)
    if proc.returncode != 0 or not isinstance(parsed, dict) or parsed.get("ok") is not True:
        error = ""
        if isinstance(parsed, dict):
            error = parsed.get("error") or ""
            traceback_tail = parsed.get("traceback_tail") or ""
            if traceback_tail:
                error = (error + "\n" + traceback_tail) if error else traceback_tail
        error = error or proc.stderr or f"local inference runner exited with {proc.returncode}"
        raise RuntimeError(_redact_training_text(error, limit=12000))
    parsed["profile"] = profile
    parsed["deployment_id"] = normalized.get("deployment_id") or None
    return _redact_training_value(parsed)


def _candidate_skill_dirs(payload):
    cap = discover_local_capabilities()
    candidates = []
    if payload.get("target_dir"):
        candidates.append(payload["target_dir"])
    candidates.extend(cap.get("skills_dirs") or [])
    if cap.get("gateway_kind") == "openclaw":
        candidates.extend([
            str(Path.home() / ".openclaw" / "skills"),
            str(Path.home() / ".aiclaw" / "skills"),
        ])
    else:
        candidates.extend([
            str(Path.home() / ".aiclaw" / "skills"),
            str(Path.home() / ".openclaw" / "skills"),
        ])
    return candidates


def _find_skill_root(skill_id, payload):
    for d in _candidate_skill_dirs(payload):
        try:
            base = Path(d).resolve(strict=False)
            p = base / skill_id
            if p.exists() and p.is_dir() and not p.is_symlink():
                resolved = p.resolve(strict=False)
                try:
                    resolved.relative_to(base)
                except ValueError:
                    continue
                return resolved
        except Exception:
            continue
    return None


def _build_skill_script_env(skill_root, run_payload=None):
    safe_keys = {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY",
        "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    }
    env = {key: value for key, value in os.environ.items() if key in safe_keys}
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    env.setdefault("HOME", os.environ.get("HOME", str(Path.home())))

    repo_sdk_dir = Path(__file__).resolve().parents[1] / "app" / "skill_runtime_sdk"
    shared_dir = skill_root.parent / "_shared"
    repo_scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    python_paths = []
    for path in (
        shared_dir,
        shared_dir / "scripts",
        repo_sdk_dir,
        repo_scripts_dir,
        skill_root / "scripts",
    ):
        if path.exists():
            python_paths.append(str(path))
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    if repo_sdk_dir.exists():
        env["SKILLFORGE_TRUSTED_SDK_DIR"] = str(repo_sdk_dir)
    if (shared_dir / "mcp").exists():
        env["SKILLFORGE_MCP_DIR"] = str(shared_dir / "mcp")
    elif (shared_dir / "scripts").exists():
        env["SKILLFORGE_MCP_DIR"] = str(shared_dir / "scripts")

    run_payload = dict(run_payload or {}) if isinstance(run_payload, dict) else {}
    script_input = run_payload.get("payload") if isinstance(run_payload.get("payload"), dict) else {}
    runtime = run_payload.get("runtime") if isinstance(run_payload.get("runtime"), dict) else {}
    model_context = _payload_model_context(run_payload)
    if model_context:
        active_model = _active_model_deployment_from_context(model_context)
        try:
            env["SKILLFORGE_MODEL_CONTEXT_JSON"] = json.dumps(model_context, ensure_ascii=False, sort_keys=True)
        except Exception:
            env["SKILLFORGE_MODEL_CONTEXT_JSON"] = "{}"
        for env_key, short_key, value in (
            (
                "SKILLFORGE_MODEL_DEPLOYMENT_ID",
                "MODEL_DEPLOYMENT_ID",
                model_context.get("model_deployment_id") or active_model.get("model_deployment_id"),
            ),
            (
                "SKILLFORGE_MODEL_FAMILY",
                "MODEL_FAMILY",
                model_context.get("model_family") or active_model.get("model_family"),
            ),
            (
                "SKILLFORGE_MODEL_ARTIFACT_SHA256",
                "MODEL_ARTIFACT_SHA256",
                model_context.get("artifact_sha256") or active_model.get("artifact_sha256"),
            ),
        ):
            if value:
                text = str(value)
                env.setdefault(env_key, text)
                env.setdefault(short_key, text)
    env.setdefault("SKILLFORGE_PLATFORM_URL", SKILLFORGE_HTTP_BASE)
    env.setdefault("SKILLFORGE_INSTANCE_ID", INSTANCE_ID)
    env.setdefault("INSTANCE_ID", INSTANCE_ID)
    run_mode = (
        run_payload.get("run_mode")
        or runtime.get("run_mode")
        or script_input.get("run_mode")
    )
    if run_payload.get("run_id") or run_payload.get("run_token"):
        env.setdefault("SKILLFORGE_RUN_MODE", str(run_mode or "scheduled_real"))
        env.setdefault("RUN_MODE", str(run_mode or "scheduled_real"))
    remote_run_id = (
        run_payload.get("remote_run_id")
        or runtime.get("remote_run_id")
        or script_input.get("remote_run_id")
    )
    if remote_run_id:
        remote_run_id = str(remote_run_id)
        env.setdefault("SKILLFORGE_REMOTE_RUN_ID", remote_run_id)
        env.setdefault("OPENCLAW_REMOTE_RUN_ID", remote_run_id)
    for env_key, short_key, *sources in (
        (
            "SKILLFORGE_SKILL_ID",
            "SKILL_ID",
            run_payload.get("skill_id"),
            script_input.get("skill_id"),
            os.environ.get("SKILLFORGE_SKILL_ID"),
            os.environ.get("SKILL_ID"),
        ),
        (
            "SKILLFORGE_RUN_TOKEN",
            "RUN_TOKEN",
            run_payload.get("run_token"),
            runtime.get("run_token"),
            script_input.get("run_token"),
            os.environ.get("SKILLFORGE_RUN_TOKEN"),
            os.environ.get("RUN_TOKEN"),
        ),
        (
            "SKILLFORGE_RUN_ID",
            "RUN_ID",
            run_payload.get("run_id"),
            script_input.get("run_id"),
            os.environ.get("SKILLFORGE_RUN_ID"),
            os.environ.get("RUN_ID"),
        ),
        (
            "SKILLFORGE_SKILL_GIT_COMMIT_FULL",
            "SKILL_GIT_COMMIT_FULL",
            run_payload.get("skill_git_commit_full"),
            runtime.get("skill_git_commit_full"),
            script_input.get("skill_git_commit_full"),
            os.environ.get("SKILLFORGE_SKILL_GIT_COMMIT_FULL"),
            os.environ.get("SKILL_GIT_COMMIT_FULL"),
            os.environ.get("OPENCLAW_SKILL_GIT_COMMIT_FULL"),
        ),
    ):
        value = next((str(item) for item in sources if item), "")
        if value:
            env.setdefault(env_key, value)
            env.setdefault(short_key, value)
            if env_key == "SKILLFORGE_RUN_TOKEN":
                env.setdefault("OPENCLAW_RUN_TOKEN", value)
            elif env_key == "SKILLFORGE_RUN_ID":
                env.setdefault("OPENCLAW_RUN_ID", value)
            elif env_key == "SKILLFORGE_SKILL_GIT_COMMIT_FULL":
                env.setdefault("OPENCLAW_SKILL_GIT_COMMIT_FULL", value)
    return env


def _parse_script_stdout(stdout):
    raw = (stdout or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    if "```json" in raw:
        block = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        try:
            return json.loads(block)
        except Exception:
            return None
    return None


def _normalize_skill_file_payloads(files):
    """Decode and validate install_skill payload into {relative_path: bytes}.

    install_skill is a full snapshot sync. Invalid paths are ignored, but an empty
    valid payload is rejected so a malformed request cannot wipe an existing skill.
    """
    decoded = {}
    for item in files or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").lstrip("/")
        parts = rel.split("/")
        if not rel or rel.startswith("/") or ".." in parts or any(not part for part in parts):
            continue
        try:
            decoded[rel] = base64.b64decode(item.get("content_b64") or "", validate=True)
        except Exception:
            continue
    return decoded


def _write_bridge_files(root: Path, file_map: dict[str, bytes]) -> tuple[list[str], list[str]]:
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve(strict=True)
    written = []
    skipped = []
    for rel, content in file_map.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        try:
            fp.parent.resolve(strict=True).relative_to(root_resolved)
        except (ValueError, OSError):
            skipped.append(rel)
            continue
        if fp.is_symlink():
            try:
                fp.unlink()
            except OSError:
                skipped.append(rel)
                continue
        try:
            fd = os.open(str(fp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
        except OSError:
            skipped.append(rel)
            continue
        written.append(rel)
    return written, skipped


def _prune_skill_root_to_manifest(root: Path, expected_paths: set[str]) -> tuple[int, int]:
    """Remove local files not present in the incoming full snapshot.

    The cleanup is intentionally scoped to the already-validated skill root and only
    unlinks files/symlinks; it never follows symlink directories.
    """
    removed = 0
    pruned_dirs = 0
    if not root.exists():
        return removed, pruned_dirs

    entries = sorted(root.rglob("*"), key=lambda p: len(p.relative_to(root).parts), reverse=True)
    for path in entries:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        if rel in expected_paths:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue

    for path in entries:
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            path.rmdir()
            pruned_dirs += 1
        except OSError:
            continue
    return removed, pruned_dirs


class _MediaBootstrapCancelled(RuntimeError):
    pass


def _normalize_media_bootstrap_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    forbidden = {"url", "urls", "command", "commands", "workflow", "model_url", "repo_url"}
    if forbidden.intersection(payload):
        return None, "arbitrary URL, command or workflow is forbidden"
    profile = str(payload.get("profile") or MEDIA_BOOTSTRAP_PROFILE).strip()
    if profile != MEDIA_BOOTSTRAP_PROFILE:
        return None, "unsupported media bootstrap profile"
    try:
        bandwidth = int(payload.get("bandwidth_limit_mbps") or MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS)
    except (TypeError, ValueError):
        return None, "bandwidth_limit_mbps must be integer"
    if bandwidth != MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS:
        return None, "bandwidth_limit_mbps must be 3 for this governed profile"
    dry_run = bool(payload.get("dry_run"))
    if not dry_run and not bool(payload.get("accept_license")):
        return None, "MiniMax H3 community license must be explicitly accepted"
    return {
        "profile": profile,
        "bandwidth_limit_mbps": bandwidth,
        "dry_run": dry_run,
        "accept_license": bool(payload.get("accept_license")),
        "force": bool(payload.get("force")),
    }, None


def _media_bootstrap_public_manifest():
    return [
        {
            "name": Path(item["path"]).name,
            "target": item["path"],
            "size_bytes": item["size"],
            "sha256": item["sha256"],
        }
        for item in MEDIA_BOOTSTRAP_MODELS
    ]


def _load_media_bootstrap_status():
    try:
        value = json.loads(MEDIA_BOOTSTRAP_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write_media_bootstrap_status(status):
    MEDIA_BOOTSTRAP_ROOT.mkdir(parents=True, exist_ok=True)
    value = dict(status or {})
    value["profile"] = MEDIA_BOOTSTRAP_PROFILE
    value["bandwidth_limit_mbps"] = MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS
    value["license_url"] = MEDIA_BOOTSTRAP_LICENSE_URL
    value["updated_at"] = datetime.utcnow().isoformat() + "Z"
    tmp = MEDIA_BOOTSTRAP_STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_redact_training_value(value), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEDIA_BOOTSTRAP_STATUS_PATH)


def _current_media_bootstrap_status():
    status = _load_media_bootstrap_status()
    if not status:
        status = {
            "status": "not_started",
            "profile": MEDIA_BOOTSTRAP_PROFILE,
            "bandwidth_limit_mbps": MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS,
            "total_bytes": sum(item["size"] for item in MEDIA_BOOTSTRAP_MODELS),
            "downloaded_bytes": 0,
            "manifest": _media_bootstrap_public_manifest(),
        }
    thread = _MEDIA_BOOTSTRAP_THREAD
    status["worker_active"] = bool(thread and thread.is_alive())
    downloaded = int(status.get("downloaded_bytes") or 0)
    total = int(status.get("total_bytes") or sum(item["size"] for item in MEDIA_BOOTSTRAP_MODELS))
    status["progress_percent"] = round(downloaded * 100 / total, 2) if total else 0
    speed = float(status.get("speed_bytes_per_second") or 0)
    status["eta_seconds"] = round(max(total - downloaded, 0) / speed) if speed > 0 else None
    return _redact_training_value(status)


def _media_bootstrap_update(**changes):
    status = _load_media_bootstrap_status()
    status.update(changes)
    _write_media_bootstrap_status(status)
    return status


def _media_bootstrap_check_cancelled():
    if _MEDIA_BOOTSTRAP_CANCEL.is_set():
        raise _MediaBootstrapCancelled("media bootstrap cancelled")


def _media_bootstrap_run(command, *, cwd=None, timeout=7200, step="command"):
    global _MEDIA_BOOTSTRAP_PROCESS
    _media_bootstrap_check_cancelled()
    _media_bootstrap_update(current_step=step)
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _MEDIA_BOOTSTRAP_PROCESS = proc
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if _MEDIA_BOOTSTRAP_CANCEL.wait(0.5):
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise _MediaBootstrapCancelled("media bootstrap cancelled")
            if time.monotonic() - started > timeout:
                proc.terminate()
                raise RuntimeError(f"{step} timed out")
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            detail = _redact_training_text(stderr or stdout or f"exit {proc.returncode}", limit=3000)
            raise RuntimeError(f"{step} failed: {detail}")
        return _redact_training_text(stdout, limit=3000)
    finally:
        _MEDIA_BOOTSTRAP_PROCESS = None


def _media_bootstrap_file_sha256(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            _media_bootstrap_check_cancelled()
            hasher.update(chunk)
    return hasher.hexdigest()


def _media_bootstrap_existing_model_bytes(item):
    """Count verified targets and resumable partial/range files without overlap."""
    target = MEDIA_BOOTSTRAP_MODEL_DIR / str(item["path"])
    expected_size = int(item["size"])
    if target.is_file() and target.stat().st_size == expected_size:
        return expected_size

    part = target.with_suffix(target.suffix + ".part")
    contiguous_bytes = min(part.stat().st_size, expected_size) if part.is_file() else 0
    intervals = [(0, contiguous_bytes)] if contiguous_bytes else []
    range_prefix = part.name + ".range-"
    if part.parent.is_dir():
        for path in part.parent.glob(part.name + ".range-*"):
            if not path.is_file() or not path.name.startswith(range_prefix):
                continue
            match = re.fullmatch(r"(\d+)-(\d+)", path.name[len(range_prefix):])
            if match is None:
                continue
            start, declared_end = int(match.group(1)), int(match.group(2))
            if start < 0 or declared_end <= start or declared_end > expected_size:
                continue
            actual_end = min(start + path.stat().st_size, declared_end, expected_size)
            if actual_end > start:
                intervals.append((start, actual_end))

    covered = 0
    merged_end = 0
    for start, end in sorted(intervals):
        if end <= merged_end:
            continue
        covered += end - max(start, merged_end)
        merged_end = max(merged_end, end)
    return min(covered, expected_size)


def _media_bootstrap_download_model_parallel(
    *,
    item,
    part,
    offset,
    url,
    completed_before,
    total_bytes,
    bandwidth_limit_mbps,
):
    """Download the remaining allow-listed model with bounded Range workers.

    Some Hugging Face/Xet routes cap a single HTTPS stream well below the
    configured 3 MB/s ceiling.  Independent fixed-size Range files allow the
    Bridge to aggregate several slow streams while one shared clock preserves
    the overall bandwidth limit.  Each range is resumable across Bridge
    restarts and is merged into the ordinary contiguous ``.part`` only after
    all ranges are complete.
    """

    expected_size = int(item["size"])
    range_chunk_bytes = max(8 * 1024 * 1024, int(MEDIA_BOOTSTRAP_RANGE_CHUNK_BYTES))
    ranges = [
        (start, min(start + range_chunk_bytes, expected_size))
        for start in range(offset, expected_size, range_chunk_bytes)
    ]
    if not ranges:
        return offset

    state_lock = threading.Lock()
    abort = threading.Event()
    session_start = time.monotonic()
    session_bytes = 0
    completed_range_bytes = 0
    retry_count = 0
    last_status_at = 0.0
    limit_bps = max(int(bandwidth_limit_mbps) * 1024 * 1024, 1)

    def range_path(start, end):
        return part.with_name(part.name + f".range-{start}-{end}")

    for start, end in ranges:
        path = range_path(start, end)
        size = path.stat().st_size if path.is_file() else 0
        if size > end - start:
            bad = path.with_name(path.name + ".bad-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"))
            path.replace(bad)
            size = 0
        completed_range_bytes += size

    def record_progress(byte_count, *, retry_reason=None):
        nonlocal session_bytes, retry_count, last_status_at
        with state_lock:
            session_bytes += byte_count
            if retry_reason is not None:
                retry_count += 1
            expected_elapsed = session_bytes / limit_bps
            actual_elapsed = time.monotonic() - session_start
            delay = max(0.0, expected_elapsed - actual_elapsed)
            now = time.monotonic()
            if retry_reason is not None or now - last_status_at >= 1:
                changes = {
                    "status": "downloading",
                    "current_step": "download_models_parallel",
                    "current_file": part.name.removesuffix(".part"),
                    "downloaded_bytes": completed_before + offset + completed_range_bytes + session_bytes,
                    "total_bytes": total_bytes,
                    "speed_bytes_per_second": round(session_bytes / max(actual_elapsed, 0.001)),
                    "parallelism": min(MEDIA_BOOTSTRAP_PARALLELISM, len(ranges)),
                    "retry_count": retry_count,
                }
                if retry_reason is not None:
                    changes["last_retry_reason"] = retry_reason
                _media_bootstrap_update(**changes)
                last_status_at = now
            return delay

    def download_range(start, end):
        path = range_path(start, end)
        local_size = path.stat().st_size if path.is_file() else 0
        no_progress_count = 0
        attempts = 0
        while local_size < end - start:
            _media_bootstrap_check_cancelled()
            if abort.is_set():
                raise RuntimeError("parallel model download aborted")
            request_start = start + local_size
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": f"SkillForge-Bridge/{BRIDGE_VERSION}",
                    "Accept-Encoding": "identity",
                    "Range": f"bytes={request_start}-{end - 1}",
                },
            )
            response_start_size = local_size
            retry_reason = "remote range stream ended before expected size"
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    status_code = int(getattr(response, "status", response.getcode()) or 0)
                    response_headers = getattr(response, "headers", {})
                    content_range = str(response_headers.get("Content-Range") or "")
                    content_range_match = re.fullmatch(
                        r"bytes\s+(\d+)-(\d+)/(\d+)", content_range.strip()
                    )
                    if (
                        status_code != 206
                        or content_range_match is None
                        or int(content_range_match.group(1)) != request_start
                        or int(content_range_match.group(2)) > end - 1
                        or int(content_range_match.group(3)) != expected_size
                    ):
                        raise RuntimeError(
                            f"unexpected parallel Content-Range for {part.name}: {content_range}"
                        )
                    with path.open("ab") as handle:
                        while local_size < end - start:
                            _media_bootstrap_check_cancelled()
                            if abort.is_set():
                                raise RuntimeError("parallel model download aborted")
                            chunk = response.read(min(1024 * 1024, end - start - local_size))
                            if not chunk:
                                break
                            handle.write(chunk)
                            local_size += len(chunk)
                            delay = record_progress(len(chunk))
                            if delay and _MEDIA_BOOTSTRAP_CANCEL.wait(delay):
                                raise _MediaBootstrapCancelled("media bootstrap cancelled")
            except _MediaBootstrapCancelled:
                raise
            except Exception as exc:
                retry_reason = _redact_training_text(exc, limit=500)

            if local_size >= end - start:
                return
            progressed = local_size > response_start_size
            no_progress_count = 0 if progressed else no_progress_count + 1
            attempts += 1
            record_progress(0, retry_reason=retry_reason)
            if attempts > 256 or no_progress_count >= 8:
                raise RuntimeError(
                    f"parallel download retry exhausted for {part.name}: "
                    f"range={start}-{end} offset={local_size} reason={retry_reason}"
                )
            retry_delay = min(30, max(1, no_progress_count * 2))
            if _MEDIA_BOOTSTRAP_CANCEL.wait(retry_delay):
                raise _MediaBootstrapCancelled("media bootstrap cancelled")

    workers = min(max(1, int(MEDIA_BOOTSTRAP_PARALLELISM)), len(ranges))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(download_range, start, end) for start, end in ranges]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception:
                abort.set()
                for pending in futures:
                    pending.cancel()
                raise

    _media_bootstrap_update(
        status="downloading",
        current_step="merge_model_ranges",
        current_file=part.name.removesuffix(".part"),
        downloaded_bytes=completed_before + expected_size,
        total_bytes=total_bytes,
        retry_count=retry_count,
        parallelism=workers,
    )
    with part.open("ab") as destination:
        for start, end in ranges:
            path = range_path(start, end)
            if not path.is_file() or path.stat().st_size != end - start:
                raise RuntimeError(f"parallel range is incomplete for {part.name}: {start}-{end}")
            with path.open("rb") as source:
                shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
            path.unlink()
    return part.stat().st_size


def _media_bootstrap_download_model(item, completed_before, total_bytes, bandwidth_limit_mbps):
    relative = str(item["path"])
    target = MEDIA_BOOTSTRAP_MODEL_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == int(item["size"]):
        _media_bootstrap_update(current_step="verifying_model", current_file=target.name)
        if _media_bootstrap_file_sha256(target) == item["sha256"]:
            return int(item["size"])
        bad = target.with_name(target.name + ".bad-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        target.replace(bad)
    part = target.with_suffix(target.suffix + ".part")
    offset = part.stat().st_size if part.is_file() else 0
    if offset > int(item["size"]):
        bad = part.with_name(part.name + ".bad-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        part.replace(bad)
        offset = 0
    url = (
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/"
        + MEDIA_BOOTSTRAP_MODEL_REPO_REVISION
        + "/"
        + urllib.parse.quote(relative, safe="/")
        + "?download=true"
    )
    _media_bootstrap_update(
        status="downloading",
        current_step="download_models",
        current_file=target.name,
        downloaded_bytes=completed_before + offset,
        total_bytes=total_bytes,
    )
    limit_bps = bandwidth_limit_mbps * 1024 * 1024
    session_start = time.monotonic()
    session_bytes = 0
    expected_size = int(item["size"])
    if expected_size - offset >= MEDIA_BOOTSTRAP_PARALLEL_THRESHOLD_BYTES:
        offset = _media_bootstrap_download_model_parallel(
            item=item,
            part=part,
            offset=offset,
            url=url,
            completed_before=completed_before,
            total_bytes=total_bytes,
            bandwidth_limit_mbps=bandwidth_limit_mbps,
        )
    retry_count = 0
    no_progress_count = 0
    max_retries = 2048
    max_no_progress = 8
    while offset < expected_size:
        _media_bootstrap_check_cancelled()
        request_offset = offset
        headers = {
            "User-Agent": f"SkillForge-Bridge/{BRIDGE_VERSION}",
            "Accept-Encoding": "identity",
        }
        if request_offset:
            headers["Range"] = f"bytes={request_offset}-"
        request = urllib.request.Request(url, headers=headers)
        response_start = offset
        retry_reason = "remote stream ended before expected size"
        _media_bootstrap_update(
            status="downloading",
            current_step="download_models",
            current_file=target.name,
            downloaded_bytes=completed_before + offset,
            total_bytes=total_bytes,
            retry_count=retry_count,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status_code = int(getattr(response, "status", response.getcode()) or 0)
                mode = "ab" if request_offset else "wb"
                if request_offset and status_code != 206:
                    # The origin ignored Range. Restart safely from the full
                    # response instead of appending duplicate bytes.
                    offset = 0
                    response_start = 0
                    mode = "wb"
                elif request_offset:
                    response_headers = getattr(response, "headers", {})
                    content_range = str(response_headers.get("Content-Range") or "")
                    content_range_match = re.fullmatch(
                        r"bytes\s+(\d+)-(\d+)/(\d+)",
                        content_range.strip(),
                    )
                    if (
                        content_range_match is None
                        or int(content_range_match.group(1)) != request_offset
                        or int(content_range_match.group(2)) < request_offset
                        or int(content_range_match.group(3)) != expected_size
                    ):
                        raise RuntimeError(
                            f"unexpected Content-Range for {target.name}: {content_range}"
                        )
                with part.open(mode) as handle:
                    while offset < expected_size:
                        _media_bootstrap_check_cancelled()
                        chunk = response.read(min(1024 * 1024, expected_size - offset))
                        if not chunk:
                            break
                        handle.write(chunk)
                        offset += len(chunk)
                        session_bytes += len(chunk)
                        expected_elapsed = session_bytes / max(limit_bps, 1)
                        actual_elapsed = time.monotonic() - session_start
                        if expected_elapsed > actual_elapsed:
                            if _MEDIA_BOOTSTRAP_CANCEL.wait(expected_elapsed - actual_elapsed):
                                raise _MediaBootstrapCancelled("media bootstrap cancelled")
                        actual_elapsed = max(time.monotonic() - session_start, 0.001)
                        _media_bootstrap_update(
                            status="downloading",
                            current_file=target.name,
                            downloaded_bytes=completed_before + offset,
                            total_bytes=total_bytes,
                            speed_bytes_per_second=round(session_bytes / actual_elapsed),
                            retry_count=retry_count,
                        )
        except _MediaBootstrapCancelled:
            raise
        except Exception as exc:
            retry_reason = _redact_training_text(exc, limit=500)

        if offset >= expected_size:
            break
        progressed = offset > response_start
        no_progress_count = 0 if progressed else no_progress_count + 1
        retry_count += 1
        if retry_count > max_retries or no_progress_count >= max_no_progress:
            raise RuntimeError(
                f"download retry exhausted for {target.name}: "
                f"offset={offset} retries={retry_count} reason={retry_reason}"
            )
        _media_bootstrap_update(
            status="downloading",
            current_step="download_models_retrying",
            current_file=target.name,
            downloaded_bytes=completed_before + offset,
            total_bytes=total_bytes,
            retry_count=retry_count,
            last_retry_reason=retry_reason,
        )
        retry_delay = min(30, max(1, no_progress_count * 2))
        if _MEDIA_BOOTSTRAP_CANCEL.wait(retry_delay):
            raise _MediaBootstrapCancelled("media bootstrap cancelled")
    if offset != int(item["size"]):
        raise RuntimeError(f"download size mismatch for {target.name}: expected={item['size']} actual={offset}")
    _media_bootstrap_update(status="verifying", current_step="verify_model_sha256", current_file=target.name)
    actual_sha = _media_bootstrap_file_sha256(part)
    if actual_sha != item["sha256"]:
        bad = part.with_name(part.name + ".bad-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        part.replace(bad)
        raise RuntimeError(f"SHA256 mismatch for {target.name}")
    part.replace(target)
    return int(item["size"])


def _media_bootstrap_preflight():
    if os.name == "nt" or platform.system().lower() != "linux":
        raise RuntimeError("h3_all_modes_v1 bootstrap currently supports Linux only")
    for command in ("git", "python3", "nvidia-smi", "systemctl"):
        if not shutil.which(command):
            raise RuntimeError(f"required command is missing: {command}")
    gpu = _media_bootstrap_run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        timeout=30,
        step="gpu_preflight",
    )
    if not gpu.strip():
        raise RuntimeError("no NVIDIA GPU detected")
    missing_bytes = 0
    for item in MEDIA_BOOTSTRAP_MODELS:
        expected_size = int(item["size"])
        partial_size = _media_bootstrap_existing_model_bytes(item)
        missing_bytes += expected_size - partial_size
    MEDIA_BOOTSTRAP_ROOT.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(MEDIA_BOOTSTRAP_ROOT).free
    required_bytes = missing_bytes + 20 * 1024 * 1024 * 1024
    if free_bytes < required_bytes:
        raise RuntimeError(f"insufficient disk: required={required_bytes} free={free_bytes}")
    return {"gpu": gpu.strip().splitlines()[:8], "free_disk_bytes": free_bytes, "required_disk_bytes": required_bytes}


def _media_bootstrap_install_comfy():
    if not (MEDIA_BOOTSTRAP_COMFY_DIR / "main.py").is_file():
        if MEDIA_BOOTSTRAP_COMFY_DIR.exists():
            raise RuntimeError("managed ComfyUI directory exists but is incomplete")
        _media_bootstrap_run(
            [
                "git",
                "clone",
                "--branch",
                MEDIA_BOOTSTRAP_COMFY_TAG,
                "--depth",
                "1",
                MEDIA_BOOTSTRAP_COMFY_REPO,
                str(MEDIA_BOOTSTRAP_COMFY_DIR),
            ],
            timeout=1800,
            step="clone_comfyui",
        )
    python_path = MEDIA_BOOTSTRAP_VENV_DIR / "bin" / "python"
    if not python_path.is_file():
        _media_bootstrap_run(
            ["python3", "-m", "venv", str(MEDIA_BOOTSTRAP_VENV_DIR)],
            timeout=300,
            step="create_comfyui_venv",
        )
    _media_bootstrap_run(
        [str(python_path), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
        timeout=1800,
        step="upgrade_comfyui_pip",
    )
    _media_bootstrap_run(
        [
            str(python_path),
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cu130",
        ],
        timeout=7200,
        step="install_pytorch_cu130",
    )
    _media_bootstrap_run(
        [str(python_path), "-m", "pip", "install", "-r", str(MEDIA_BOOTSTRAP_COMFY_DIR / "requirements.txt")],
        timeout=7200,
        step="install_comfyui_requirements",
    )
    _media_bootstrap_run(
        [
            str(python_path),
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            *MEDIA_BOOTSTRAP_RUNTIME_PACKAGES,
        ],
        timeout=1800,
        step="install_governed_media_runtime",
    )


def _media_bootstrap_configure_service():
    MEDIA_BOOTSTRAP_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_BOOTSTRAP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = MEDIA_BOOTSTRAP_ROOT / "extra_model_paths.yaml"
    config.write_text(
        "skillforge_h3:\n"
        f"  base_path: {MEDIA_BOOTSTRAP_MODEL_DIR}\n"
        "  diffusion_models: diffusion_models\n"
        "  text_encoders: text_encoders\n"
        "  vae: vae\n",
        encoding="utf-8",
    )
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_name = f"skillforge-comfyui-{_INSTANCE_SLUG}.service"
    unit_path = unit_dir / unit_name
    python_path = MEDIA_BOOTSTRAP_VENV_DIR / "bin" / "python"
    unit_path.write_text(
        "[Unit]\nDescription=SkillForge governed MiniMax H3 ComfyUI\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={MEDIA_BOOTSTRAP_COMFY_DIR}\n"
        f"ExecStart={python_path} {MEDIA_BOOTSTRAP_COMFY_DIR / 'main.py'} --listen 127.0.0.1 --port 8188 --disable-auto-launch --extra-model-paths-config {config}\n"
        "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
        encoding="utf-8",
    )
    _media_bootstrap_run(["systemctl", "--user", "daemon-reload"], timeout=60, step="systemd_reload")
    _media_bootstrap_run(
        ["systemctl", "--user", "enable", "--now", unit_name],
        timeout=180,
        step="start_comfyui",
    )
    deadline = time.monotonic() + 300
    last_error = ""
    while time.monotonic() < deadline:
        _media_bootstrap_check_cancelled()
        try:
            _media_http_json("GET", "/system_stats", timeout=5)
            object_info = _media_http_json("GET", "/object_info", timeout=30)
            required = {"MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"}
            if required.issubset(set(object_info)):
                return {"unit": unit_name, "comfyui_url": MEDIA_COMFYUI_URL, "nodes": sorted(required)}
            last_error = "required MiniMax H3 nodes are missing"
        except Exception as exc:
            last_error = _redact_training_text(exc, limit=1000)
        if _MEDIA_BOOTSTRAP_CANCEL.wait(5):
            raise _MediaBootstrapCancelled("media bootstrap cancelled")
    raise RuntimeError("ComfyUI health check failed: " + last_error)


def _media_bootstrap_worker(normalized):
    total_bytes = sum(item["size"] for item in MEDIA_BOOTSTRAP_MODELS)
    existing_bytes = sum(_media_bootstrap_existing_model_bytes(item) for item in MEDIA_BOOTSTRAP_MODELS)
    worker_started_at = datetime.utcnow().isoformat() + "Z"
    started_at = str(normalized.get("started_at") or worker_started_at)
    _write_media_bootstrap_status(
        {
            "status": "preflighting",
            "started_at": started_at,
            "last_worker_started_at": worker_started_at,
            "license_accepted_at": normalized.get("license_accepted_at") or worker_started_at,
            "resume_count": int(normalized.get("resume_count") or 0),
            "resumed_after_bridge_restart_at": normalized.get("resumed_after_bridge_restart_at"),
            "total_bytes": total_bytes,
            "downloaded_bytes": existing_bytes,
            "speed_bytes_per_second": 0,
            "manifest": _media_bootstrap_public_manifest(),
        }
    )
    try:
        preflight = _media_bootstrap_preflight()
        _media_bootstrap_update(status="installing", current_step="install_comfyui", preflight=preflight)
        _media_bootstrap_install_comfy()
        completed = 0
        for item in MEDIA_BOOTSTRAP_MODELS:
            completed += _media_bootstrap_download_model(
                item,
                completed,
                total_bytes,
                normalized["bandwidth_limit_mbps"],
            )
            _media_bootstrap_update(downloaded_bytes=completed, total_bytes=total_bytes)
        _media_bootstrap_update(status="configuring", current_step="configure_comfyui")
        service = _media_bootstrap_configure_service()
        capability = _media_capability_snapshot(discover_local_capabilities().get("gpu") or [], include_hashes=True)
        if not capability.get("configured") or set(capability.get("supported_modes") or []) != {
            "text_to_video",
            "image_to_video",
            "reference_replay",
        }:
            raise RuntimeError("H3 capability self-check did not enable all modes")
        if not bool((capability.get("postprocess") or {}).get("governed_product_overlay")):
            raise RuntimeError("governed product overlay runtime self-check failed")
        completed_at = datetime.utcnow().isoformat() + "Z"
        _media_bootstrap_update(
            status="succeeded",
            current_step="completed",
            current_file=None,
            downloaded_bytes=total_bytes,
            total_bytes=total_bytes,
            completed_at=completed_at,
            service=service,
            capability=capability,
            error=None,
        )
    except _MediaBootstrapCancelled as exc:
        _media_bootstrap_update(status="cancelled", current_step="cancelled", error=str(exc))
    except Exception as exc:
        _media_bootstrap_update(
            status="failed",
            current_step="failed",
            error=_redact_training_text(exc, limit=4000),
        )


def _start_media_bootstrap(normalized):
    global _MEDIA_BOOTSTRAP_THREAD
    if normalized.get("dry_run"):
        preflight = _media_bootstrap_preflight()
        return {
            "status": "planned",
            "profile": MEDIA_BOOTSTRAP_PROFILE,
            "bandwidth_limit_mbps": MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS,
            "total_bytes": sum(item["size"] for item in MEDIA_BOOTSTRAP_MODELS),
            "comfyui_tag": MEDIA_BOOTSTRAP_COMFY_TAG,
            "manifest": _media_bootstrap_public_manifest(),
            "license_url": MEDIA_BOOTSTRAP_LICENSE_URL,
            "preflight": preflight,
        }
    with _MEDIA_BOOTSTRAP_LOCK:
        if _MEDIA_BOOTSTRAP_THREAD and _MEDIA_BOOTSTRAP_THREAD.is_alive():
            return _current_media_bootstrap_status()
        existing = _load_media_bootstrap_status()
        if existing.get("status") == "succeeded" and not normalized.get("force"):
            return _current_media_bootstrap_status()
        now = datetime.utcnow().isoformat() + "Z"
        worker_config = dict(normalized)
        worker_config["started_at"] = existing.get("started_at") or now
        worker_config["license_accepted_at"] = existing.get("license_accepted_at") or now
        worker_config["resume_count"] = int(existing.get("resume_count") or 0)
        worker_config["resumed_after_bridge_restart_at"] = existing.get("resumed_after_bridge_restart_at")
        if normalized.get("_resume_after_bridge_restart"):
            worker_config["resume_count"] += 1
            worker_config["resumed_after_bridge_restart_at"] = now
        _MEDIA_BOOTSTRAP_CANCEL.clear()
        _MEDIA_BOOTSTRAP_THREAD = threading.Thread(
            target=_media_bootstrap_worker,
            args=(worker_config,),
            name="skillforge-h3-bootstrap",
            daemon=True,
        )
        _MEDIA_BOOTSTRAP_THREAD.start()
    return _current_media_bootstrap_status()


def _resume_media_bootstrap_if_needed():
    status = _load_media_bootstrap_status()
    persisted_status = str(status.get("status") or "").strip().lower()
    thread = _MEDIA_BOOTSTRAP_THREAD
    if persisted_status not in MEDIA_BOOTSTRAP_RESUMABLE_STATUSES:
        return False
    if thread and thread.is_alive():
        return False
    _start_media_bootstrap(
        {
            "profile": MEDIA_BOOTSTRAP_PROFILE,
            "bandwidth_limit_mbps": MEDIA_BOOTSTRAP_BANDWIDTH_LIMIT_MBPS,
            "dry_run": False,
            "accept_license": True,
            "force": False,
            "_resume_after_bridge_restart": True,
        }
    )
    return True


def _cancel_media_bootstrap():
    _MEDIA_BOOTSTRAP_CANCEL.set()
    proc = _MEDIA_BOOTSTRAP_PROCESS
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    status = _current_media_bootstrap_status()
    if status.get("status") not in {"succeeded", "failed", "cancelled", "not_started"}:
        _media_bootstrap_update(status="cancelling", current_step="cancelling")
    return _current_media_bootstrap_status()


def _media_effective_input_dir():
    status = _load_media_bootstrap_status()
    if status.get("status") == "succeeded" and MEDIA_BOOTSTRAP_INPUT_DIR.exists():
        return MEDIA_BOOTSTRAP_INPUT_DIR
    return MEDIA_INPUT_DIR


def _media_effective_output_dir():
    status = _load_media_bootstrap_status()
    if status.get("status") == "succeeded" and MEDIA_BOOTSTRAP_OUTPUT_DIR.exists():
        return MEDIA_BOOTSTRAP_OUTPUT_DIR
    return MEDIA_OUTPUT_DIR


MEDIA_INLINE_TEMPLATES = {
    "video_upscale_realesrgan_v1": {
        "version": "realesrgan-x4-1080p-v1",
        "model_version": "RealESRGAN-x4plus",
        "workflow": {
            "1": {"class_type": "LoadVideo", "inputs": {"file": ""}},
            "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
            "3": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.pth"}},
            "4": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["3", 0], "image": ["2", 0]}},
            "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": 1080, "height": 1920, "crop": "disabled"}},
            "6": {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "audio": ["2", 1], "fps": ["2", 2], "bit_depth": 8}},
            "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": "skillforge/upscale", "format": "mp4", "codec": "auto"}},
        },
        "bindings": {
            "source_video_file": ["1", "inputs", "file"],
            "width": ["5", "inputs", "width"],
            "height": ["5", "inputs", "height"],
            "output_prefix": ["7", "inputs", "filename_prefix"],
        },
        "controls": {"create_video_node_id": "6"},
    }
}
MEDIA_LOCAL_EDIT_TEMPLATE_ID = "video_local_edit_sam2_propainter_data_patch_v2"
MEDIA_TEMPLATE_IDS = {"h3_t2v_v1", "h3_i2v_v1", "h3_r2v_v1"} | set(MEDIA_INLINE_TEMPLATES)
MEDIA_CONTROLLED_TEMPLATE_IDS = MEDIA_TEMPLATE_IDS | {MEDIA_LOCAL_EDIT_TEMPLATE_ID}
# The Bridge updater distributes a single audited Python file.  Keep compressed
# copies of the platform-maintained templates in that file so GPU nodes without
# a separate asset deployment still receive exactly the allow-listed graphs.
MEDIA_EMBEDDED_TEMPLATES = {"h3_i2v_v1":"eJylVl2v0zgQfb+/ospzv5NbSt/uXhCgBRbtZXlBleUmk8Tg2CF2UhDqf9+xnThp1yvtird0ZjzneD6O+/NuNos6aBSTIjrMolRW+Y+FzHOWMsoXFROsot8XZbxg227RbaK5OVDJDDiZHHuHce8w7nW8yPm2owsm9H6RStE1Urszba10A7Qy4aXWtTqsVgXTZXtaIujq0QL/0RSrs2y+5lyeiYaq5lSDWp24PK0qysRqtHUsA0l6gqSMCRJcflHIx8Ihtm4kVwj3M0oRWANxRwSSJywzPDZxNJ9FinYhX2J8OWuUJnlDK7hy3kcXC3NiImOisDD4Gy21VEwzzFg3sqo1Oj5/ju5NLibqVivz1buOx7k7c2aZLoORzuMDS2BFGc7Zu3yo5ayCoRxEMc2a132cq8cYaBw+SmkY4p5fhznPGAiQubjddZyQTAGx7hF7UuCcceiJ3DDGHhcwHpKtRg9WGHL2vT+R3FDHXMIk7YOORzzqejYM2NizjZsSTpUi+kdtSER/vX/58a2kGTSTxCasFaCJSW2iJvNnBx/h0J8RM/+kn/+lojloEEo2rqe2UyQbkDLIact1dLn019sG6Dy+ffMhSCflrPZ0vp1BxB0n8fY0XQ3R5XVC6PnbLZUhex9rTBl0LA3zigO8Pj28DNLqKISK5NbMOPN6s7ui42GSX4ehbcYGmHgbhrkPwPQ69jp+Ywbuo/xk6AYKbmYO2zSfrY/oRBxriAfDZKitw46z9XhRiMbtPsyS/Xo+We79Lhm39DDbbBNPehcg/ScVmazem+W6oTpZOEzjkzwLJPmNKpa+atk/S2zF3l1juAUOdoYiJwVKn/W4+3mAfQDg9yeK0g3NE3BI9Q2Gcj7fygYUqXD2mJGWsWXP/434U1pC1vL/wl35UDysmMEdNeww267tDtjKmaIt1x59sw7A97d6bJWW1UPWUZFitQNtsCx2A4vCVdrYnnlmLpU17r2RFRW1smtV19rMAyg0caI4lH8zln8T0jPcoBeQYj2CpXcIeMPgSI+ZQ9LkMz+Yrftf6ZOb9CGFebSPd2gTbQH61L6/dvWdzS+oe+C2ydI098RQe6G2q7UfoUOq84T/DELAVsUcxmTnrx8cM11fGee5bApYlfZlxe+K2u2nrbZJTdlSbxjZhMTJCKDVpVAZrKRcLuaRu7vc/Q0bl/6m","h3_r2v_v1":"eJylVU2P2zgMvc+vCHzOl2NPmuY2O1tsi227RaftpQgExaJttbbkWrLTD+S/l5JsOZOqwC72ZpNPfI8USf24mc2iHlrFpYj2syiTdf5tIfOcZ5xWi5oLXtOvizJZtJt+0cfR3ByoJYOKXBx7hbhXiHuOOMg3PV1woXeLTIq+ldod6hqlW6C1wZdaN2q/WhVcl91xiayre8v8T1usTrL9nFfyRDTUTUU1qNWxksdVTblYTbaeM5BkUEjKhKDC5SeFgiwdcutWVgrpfkQZEmsg7ohA9YQzoyNOovksUrQP+VLjw3SgBZHBpes2OluOIxeMi8Jy4D9aGqm45hiuaWXdaHR8/IhwDMRF02llvgbX4TB3Z06c6TKIdB4PLIEXZTjm4PLQvKU1qCC0AlFcRs2bAeeKMQGNw6OUhhH39DHMeSYgAHO47WOckFwBsW4Plp1GNxYLcv51UJFeqeAoGJMZQYcDHnXlHxtlKn/sbruiShH9rQFzWe9fP3v3UlIG7UVgA+sEaGJCG9RlH9kORj4EMGIamQyNvFQ0Bw1Cydbdj606YSMVg5x2lY7O5yG/TUDP/csXb4J6soo3Xs+XE4ikr0iyOV72uOjzJiX09OVayhh9wBoTg55nYV1JQNeHu2dBWT2FUJXcvBhn3sTbR3I8Tfr/aWjH+EiTbMI0twGaYSM9T96OA/xOfjCSA0U3jYdXNZ+tD+hELmtIRoOXYM3paPYTHk2jup+lu/X8YlJ323Qauf0s3qRupxDMr8Bp4N9dwlRn5ZTQNpDQWyqYrF+bIbpK4WKwkMEHeRII8gdVPPur47+W3650m2A8JohNz3CZSYErznpurccT7AIEfz9Q3M/QPkAFmb7iUM7nr7kFRWrsS25WyJT9098Jf8hKYF31b7QrD8XDihveaVftZ5u1nQ9bOVO05dqzx+sA/ZDVfae0rO9YT7GfWOgarIrtqKJwlTa2J16ZC2WNO2/kRU3terXb1drMKye0axRf/ngqfxxadjhdf0KG9QiW3jFghsFWnyKH1paPfGfG4T+FT6/Ch7bPvX2hQxNqCzCEjh/NpLP5wXUP2SZdmss9ctzL0Nip203UoY30gM9/iNhuOMfhd8H1a2S66zOvqly2BaxK+4LiN86zcdFO26CmbJk3nM/mBbs53/wEkyjlpg==","h3_t2v_v1":"eJylVduO0zAQfd+vqPLcW5psKX1bFgQrlotY4AVVlptMErOJnY2ddBHqvzO2E6dbjATiLZk5nnM8N/+8mEyCDhrJBA+2kyARVfZjJrKMJYyWs4pxVtHHWRHN1KqbdWEw1QcqkUJJTo69Q9w7xL2JZlm56uiMcbWZJYJ3jVD2TFtL1QCtNLxQqpbbxSJnqmj3cyRdXBviD02+OIjmPivFgSio6pIqkIt9KfaLijK+GG0dS0GQXiApIoIC598l6jF0yK0aUUqk+xkkSKyA2CMcxROWah1hFEwngaSdzxcHRxNpz3jKeG4i4T9aaiGZYniobkRVK3R8+xZc6lCM162S+qt37XZTe+bAUlV4kdbjgAWwvPDH7F0OmjW0AumFlsDz06hZ3ePslUegdjiUVDDgnj+FWc8IBEgtbv0UxwWTQIzbgUWr0I3Jgow99iriMxUMBeNlBtBuh0dt+od2GNMf2pqWVEqiftSgq/Xl/avPt4Km0JwE1rCWgyI6tEaddItpU6RDf0p0t5K+W+eSZqCAS9HY8pikk3RgSiGjbamC47G/3soj5/r25qNXTlKy2sl5OACPupJEq/1pI/Muq2NCDw/nUoboPVabUuhY4tcVeXR9vXrlldVR8CXJDoV2ZnW4fiLH0cT/T0PblA000cpPc+mh6bfOm+imojl8Fl+1XE/Cdc9hmaaT5Q6dyGMM0WBwUxyM47idxJvl9GQaN+t4HKvtJFzFTtraI+0T5amo3utpOBN0MiEYxgV55gnygkqWvG7Z74k0C9jcIhxuge2b4lYSHHeV8VwajyPYeAje3lFcp9DcQQmJOuOQ1ucK1oAkFXYY07tgLMzzPwm/SwpI2/JvtEsHxcOSad5x6Wwnq6XpdJM5nbT50rGHSw99f6vrVipRXaUd5Qlm21MGo2I9qMhtprXtmVNmQxnjxhlZXlGzJ82aNDb9KHFFmO5El/5wTH/o21o4Jy8hwXx4U28Z8Ibexh0j+xaQi3ylZ+ufwsdn4X175No8qL55MwnoQ7v6mgG3NjeG9kVaxXNd3D3DDQu1Ga3NSO3bLXf4WvuIza6yHG6yz58V3V33rCwz0eSwKMxTiN8VNdNPW2WC6rQlznA86qfo4njxC0B0y64="}
MEDIA_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MEDIA_JOB_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
MEDIA_REFERENCE_MAX_BYTES = 512 * 1024 * 1024
MEDIA_PRODUCT_OVERLAY_ALPHA_POLICY_VERSION = "product-overlay-alpha-v1"
MEDIA_RESULT_CHUNK_MAX_BYTES = 4 * 1024 * 1024
MEDIA_PROMPT_ORPHAN_GRACE_SECONDS = 90
MEDIA_PROMPT_ORPHAN_ERROR_CODE = "MEDIA_PROMPT_ORPHANED"


def _media_local_edit_runtime():
    runner = MEDIA_LOCAL_EDIT_RUNNER
    sam2_root = MEDIA_SAM2_ROOT
    propainter_root = MEDIA_PROPAINTER_ROOT
    configured = bool(
        runner and runner.is_file()
        and sam2_root and sam2_root.is_dir()
        and propainter_root and propainter_root.is_dir()
    )
    return {
        "configured": configured,
        "profile": MEDIA_LOCAL_EDIT_TEMPLATE_ID,
        "runner": str(runner) if runner else "",
        "sam2_root": str(sam2_root) if sam2_root else "",
        "propainter_root": str(propainter_root) if propainter_root else "",
    }


def _media_jobs_dir():
    path = STATE_DIR / "media_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _media_job_record_path(job_id):
    if not isinstance(job_id, str) or not MEDIA_JOB_ID_RE.match(job_id) or ".." in job_id:
        return None
    return _media_jobs_dir() / f"{job_id}.json"


def _load_media_job_record(job_id):
    path = _media_job_record_path(job_id)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _write_media_job_record(job_id, value):
    path = _media_job_record_path(job_id)
    if path is None:
        raise ValueError("invalid media job_id")
    safe_value = _redact_training_value(value)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(safe_value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _media_http_json(method, path, payload=None, timeout=10):
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{MEDIA_COMFYUI_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MEDIA_JOB_RESPONSE_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {detail[:1000]}") from exc
    if len(raw) > MEDIA_JOB_RESPONSE_MAX_BYTES:
        raise RuntimeError("ComfyUI response too large")
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {"data": value}


def _media_template_path(template_id):
    if template_id not in MEDIA_CONTROLLED_TEMPLATE_IDS:
        return None
    path = (MEDIA_TEMPLATE_DIR / f"{template_id}.json").resolve(strict=False)
    try:
        path.relative_to(MEDIA_TEMPLATE_DIR.resolve(strict=False))
    except (ValueError, OSError):
        return None
    return path


def _load_media_template(template_id):
    path = _media_template_path(template_id)
    try:
        if path is not None and path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
        elif template_id in MEDIA_INLINE_TEMPLATES:
            value = json.loads(json.dumps(MEDIA_INLINE_TEMPLATES[template_id]))
        else:
            encoded = MEDIA_EMBEDDED_TEMPLATES.get(template_id)
            if not encoded:
                raise RuntimeError(f"allow-listed media template is missing: {template_id}")
            value = json.loads(zlib.decompress(base64.b64decode(encoded)).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid media template: {template_id}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("workflow"), dict):
        raise RuntimeError("media template must contain workflow object")
    if not isinstance(value.get("bindings"), dict):
        raise RuntimeError("media template must contain bindings object")
    return value


def _media_set_binding(workflow, path, value):
    parts = path if isinstance(path, list) else str(path or "").split(".")
    if not parts or any(str(part) in {"", "..", "__class__", "__dict__"} for part in parts):
        raise RuntimeError("invalid template binding path")
    current = workflow
    for raw_part in parts[:-1]:
        if isinstance(current, list):
            index = int(raw_part)
            if index < 0 or index >= len(current):
                raise RuntimeError("template binding index out of range")
            current = current[index]
        elif isinstance(current, dict) and str(raw_part) in current:
            current = current[str(raw_part)]
        else:
            raise RuntimeError("template binding path does not exist")
    last = parts[-1]
    if isinstance(current, list):
        index = int(last)
        if index < 0 or index >= len(current):
            raise RuntimeError("template binding index out of range")
        current[index] = value
    elif isinstance(current, dict) and str(last) in current:
        current[str(last)] = value
    else:
        raise RuntimeError("template binding target does not exist")


def _media_apply_bindings(template, values):
    workflow = json.loads(json.dumps(template["workflow"]))
    bindings = template.get("bindings") or {}
    for name, paths in bindings.items():
        if name not in values:
            continue
        path_list = paths if isinstance(paths, list) and paths and isinstance(paths[0], (list, str)) else [paths]
        # A single JSON path is commonly represented as ["node", "inputs", "seed"].
        if path_list and all(isinstance(item, str) for item in path_list) and len(path_list) > 1:
            path_list = [path_list]
        for path in path_list:
            _media_set_binding(workflow, path, values[name])
    return workflow


def _media_prompt_fragment(value):
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "").strip()


def _media_compose_prompt(prompt):
    integrated = _media_prompt_fragment(prompt.get("integrated_multimodal_description"))
    if integrated:
        return integrated
    video = _media_prompt_fragment(prompt.get("video_prompt") or prompt.get("prompt"))
    audio = _media_prompt_fragment(prompt.get("audio_prompt"))
    negative = _media_prompt_fragment(prompt.get("negative_constraints") or prompt.get("negative_prompt"))
    parts = [video]
    if audio:
        parts.append("Audio requirements: " + audio)
    if negative:
        parts.append("Avoid: " + negative)
    return "\n\n".join(part for part in parts if part)


def _media_prepare_workflow(template, values, local_refs, mode):
    """Apply only maintained bindings and inject allow-listed local media loaders."""
    workflow = _media_apply_bindings(template, values)
    controls = template.get("controls") if isinstance(template.get("controls"), dict) else {}
    create_node = workflow.get(str(controls.get("create_video_node_id") or ""))
    if not values.get("audio_enabled") and isinstance(create_node, dict):
        inputs = create_node.get("inputs") if isinstance(create_node.get("inputs"), dict) else {}
        inputs.pop("audio", None)

    if mode == "reference_replay":
        ref_node = workflow.get(str(controls.get("reference_node_id") or ""))
        ref_inputs = ref_node.get("inputs") if isinstance(ref_node, dict) and isinstance(ref_node.get("inputs"), dict) else None
        if ref_inputs is None:
            raise RuntimeError("reference template is missing its controlled reference node")
        image_index = video_index = audio_index = 0
        for item in local_refs:
            mime = str(item.get("mime_type") or "")
            comfy_path = str(item.get("comfy_path") or "")
            if mime.startswith("image/"):
                load_id = f"sf_ref_image_{image_index}"
                workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": comfy_path}}
                ref_inputs[f"ref_images.ref_image_{image_index}"] = [load_id, 0]
                image_index += 1
            elif mime.startswith("video/"):
                load_id = f"sf_ref_video_load_{video_index}"
                components_id = f"sf_ref_video_components_{video_index}"
                workflow[load_id] = {"class_type": "LoadVideo", "inputs": {"file": comfy_path}}
                workflow[components_id] = {"class_type": "GetVideoComponents", "inputs": {"video": [load_id, 0]}}
                ref_inputs[f"ref_videos.ref_video_{video_index}"] = [components_id, 0]
                ref_inputs[f"ref_video_audios.ref_video_audio_{video_index}"] = [components_id, 1]
                video_index += 1
            elif mime.startswith("audio/"):
                load_id = f"sf_ref_audio_{audio_index}"
                workflow[load_id] = {"class_type": "LoadAudio", "inputs": {"audio": comfy_path}}
                ref_inputs[f"ref_audios.ref_audio_{audio_index}"] = [load_id, 0]
                audio_index += 1
    return workflow


def _media_file_sha256(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _media_model_manifest(include_hashes=False):
    cache_path = STATE_DIR / "media_model_hashes.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    except Exception:
        cache = {}
    cache_files = cache.get("files") if isinstance(cache.get("files"), dict) else {}
    expected_by_name = {Path(item["path"]).name: item for item in MEDIA_BOOTSTRAP_MODELS}
    entries = []
    changed = False
    for root in MEDIA_MODEL_ROOTS:
        if not root.is_dir():
            continue
        count = 0
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".safetensors", ".gguf", ".pt", ".pth", ".bin"}:
                continue
            lower = path.name.lower()
            if not any(token in lower for token in ("minimax", "h3", "vae", "qwen", "text_encoder", "realesrgan", "upscale")):
                continue
            stat = path.stat()
            key = str(path.resolve(strict=False))
            cached = cache_files.get(key) if isinstance(cache_files.get(key), dict) else {}
            expected = expected_by_name.get(path.name)
            size_matches = not expected or stat.st_size == int(expected["size"])
            complete = not Path(str(path) + ".aria2").exists() and size_matches
            sha = ""
            if cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
                sha = str(cached.get("sha256") or "")
            if include_hashes and complete:
                if not sha:
                    sha = _media_file_sha256(path)
                    cache_files[key] = {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": sha,
                    }
                    changed = True
            if expected and sha and sha != str(expected["sha256"]):
                complete = False
            entries.append(
                {
                    "name": path.name,
                    "path": key,
                    "size_bytes": stat.st_size,
                    "sha256": sha or None,
                    "complete": complete,
                }
            )
            count += 1
            if count >= 80:
                break
    if changed:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"files": cache_files}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cache_path)
    bundle_payload = [
        {
            "name": item["name"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
            "complete": item["complete"],
        }
        for item in sorted(entries, key=lambda item: item["path"])
    ]
    bundle_hash = hashlib.sha256(json.dumps(bundle_payload, sort_keys=True).encode("utf-8")).hexdigest() if entries else ""
    return {
        "files": entries,
        "model_sha256": bundle_hash or None,
        "hashes_complete": bool(entries) and all(item["complete"] and item["sha256"] for item in entries),
    }


def _media_queue_depth():
    try:
        value = _media_http_json("GET", "/queue", timeout=1)
    except Exception:
        return 0
    return len(value.get("queue_running") or []) + len(value.get("queue_pending") or [])


def _media_release_idle_memory():
    """Release cached H3 weights only when ComfyUI has no queued work.

    A 16 GB 5080 can keep roughly 11 GB of model cache after a successful
    generation.  The control plane correctly treats that as insufficient
    headroom for a fresh cold start, so leaving the cache resident can make an
    otherwise healthy media node look permanently unschedulable.  ComfyUI's
    governed ``/free`` endpoint is safe only when the queue is empty.
    """

    try:
        queue = _media_http_json("GET", "/queue", timeout=5)
        if (queue.get("queue_running") or []) or (queue.get("queue_pending") or []):
            return False
        _media_http_json(
            "POST",
            "/free",
            {"unload_models": True, "free_memory": True},
            timeout=15,
        )
        return True
    except Exception:
        return False


def _media_maybe_release_idle_memory(queue_depth, gpu_info=None):
    """Rate-limit idle cache release from capability heartbeats.

    A Bridge disconnect can let ComfyUI finish a prompt while the platform has
    already returned the job to ``queued``.  In that case the platform no
    longer polls ``media.get_job``, so its normal completion-time ``/free``
    call is never reached.  Releasing from an idle capability heartbeat breaks
    that deadlock without unloading models during active work or hammering the
    endpoint on every heartbeat.
    """

    if int(queue_depth or 0) != 0:
        return False
    devices = gpu_info if isinstance(gpu_info, list) else []
    peak_used_mb = max(
        (_safe_int_between(item.get("vram_used_mb"), 0, 0, 10**9) for item in devices if isinstance(item, dict)),
        default=0,
    )
    if peak_used_mb < MEDIA_IDLE_RELEASE_VRAM_THRESHOLD_MB:
        return False
    now = time.monotonic()
    global _MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC
    with _MEDIA_IDLE_RELEASE_LOCK:
        if now < _MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC:
            return False
        _MEDIA_IDLE_RELEASE_NEXT_ALLOWED_MONOTONIC = now + MEDIA_IDLE_RELEASE_MIN_INTERVAL_SECONDS
    return _media_release_idle_memory()


def _media_gpu_metrics():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        first = (result.stdout or "").strip().splitlines()[0]
        used, free, util = [_safe_int_between(item.strip(), 0, 0, 10**9) for item in first.split(",")[:3]]
        return {"vram_used_mb": used, "vram_free_mb": free, "gpu_util_pct": util}
    except Exception:
        return {}


def _media_bootstrap_ffmpeg_candidates():
    patterns = [
        MEDIA_BOOTSTRAP_VENV_DIR / "lib" / "python*" / "site-packages" / "imageio_ffmpeg" / "binaries" / "ffmpeg*",
        MEDIA_BOOTSTRAP_VENV_DIR / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries" / "ffmpeg*",
    ]
    candidates = []
    for pattern in patterns:
        for value in glob.glob(str(pattern)):
            path = Path(value)
            if path.is_file():
                candidates.append(path)
    return candidates


def _media_has_governed_product_overlay_runtime():
    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg

        bundled = Path(str(imageio_ffmpeg.get_ffmpeg_exe() or ""))
        if bundled.is_file():
            return True
    except Exception:
        pass
    return bool(_media_bootstrap_ffmpeg_candidates())


def _media_capability_snapshot(gpu_info=None, include_hashes=False):
    templates = []
    for template_id in sorted(MEDIA_TEMPLATE_IDS):
        template_path = _media_template_path(template_id)
        if (template_path is not None and template_path.is_file()) or template_id in MEDIA_EMBEDDED_TEMPLATES or template_id in MEDIA_INLINE_TEMPLATES:
            templates.append(template_id)
    local_edit_runtime = _media_local_edit_runtime()
    if local_edit_runtime["configured"] and MEDIA_LOCAL_EDIT_TEMPLATE_ID not in templates:
        templates.append(MEDIA_LOCAL_EDIT_TEMPLATE_ID)
    online = False
    comfy_version = None
    try:
        stats = _media_http_json("GET", "/system_stats", timeout=1)
        online = True
        comfy_version = (stats.get("system") or {}).get("comfyui_version") or stats.get("comfyui_version")
    except Exception:
        stats = {}
    model = _media_model_manifest(include_hashes=include_hashes)
    complete_names = {
        str(item.get("name") or "")
        for item in (model.get("files") or [])
        if isinstance(item, dict) and item.get("complete") is not False
    }
    common_models = {
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "minimax_h3_video_vae_fp16.safetensors",
        "minimax_h3_audio_vae_fp32.safetensors",
    }
    mode_requirements = {
        "text_to_video": ("h3_t2v_v1", common_models | {"minimax_h3_fl2va_pruned_int8_convrot.safetensors"}),
        "image_to_video": ("h3_i2v_v1", common_models | {"minimax_h3_fl2va_pruned_int8_convrot.safetensors"}),
        "reference_replay": ("h3_r2v_v1", common_models | {"minimax_h3_ref2va_pruned_int8_convrot.safetensors"}),
        "video_enhance": ("video_upscale_realesrgan_v1", {"RealESRGAN_x4plus.pth"}),
    }
    supported_modes = [
        mode
        for mode, (template_id, required_models) in mode_requirements.items()
        if template_id in templates and required_models <= complete_names
    ]
    if local_edit_runtime["configured"]:
        supported_modes.append("video_local_edit")
    configured = bool(online and supported_modes)
    queue_depth = _media_queue_depth() if online else 0
    snapshot_gpu = [dict(item) for item in (gpu_info or []) if isinstance(item, dict)]
    if online and _media_maybe_release_idle_memory(queue_depth, snapshot_gpu):
        refreshed_gpu = _media_gpu_metrics()
        if snapshot_gpu and refreshed_gpu:
            snapshot_gpu[0].update(refreshed_gpu)
    return {
        "configured": configured,
        "online": online,
        "comfyui_url": MEDIA_COMFYUI_URL,
        "comfyui_version": comfy_version,
        "template_ids": templates,
        "supported_modes": supported_modes,
        "profiles": [MEDIA_LOCAL_EDIT_TEMPLATE_ID] if local_edit_runtime["configured"] else [],
        "workload_roles": ["video_generation"] if configured else [],
        "queue_depth": queue_depth,
        "model_files": model.get("files") or [],
        "model_sha256": model.get("model_sha256"),
        "hashes_complete": model.get("hashes_complete", False),
        "gpu": snapshot_gpu,
        "presets": {
            "vertical_5s": {"width": 480, "height": 864, "frames": 124, "fps": 24, "steps": 20},
            "vertical_8s_benchmark": {"width": 480, "height": 864, "frames": 192, "fps": 24, "steps": 20},
            "full_hd_vertical": {"width": 1080, "height": 1920, "fps": 24, "upscale_model": "RealESRGAN_x4plus.pth"},
        },
        "postprocess": {
            "governed_product_overlay": _media_has_governed_product_overlay_runtime(),
            "overlay_roles": ["product_packshot", "product_detail"],
            "anchor": "bottom_right",
            "anchors": ["bottom_right", "center"],
            "content_crop_policies": ["alpha_bbox_v1"],
            "motion_profiles": [
                "static_verified_product",
                "static_verified_product_dynamic_background",
            ],
        },
        "local_edit": {
            "configured": local_edit_runtime["configured"],
            "profile": MEDIA_LOCAL_EDIT_TEMPLATE_ID,
            "immutable_outside_mask_required": True,
            "runner_contract": "sam2_track_propainter_inpaint_composite_data_patch_v2",
            "features": ["targeted_text_replace", "targeted_audio_replace", "visual_audio_sync", "product_replace"],
        },
    }


def _normalize_media_job_payload(payload):
    if not isinstance(payload, dict):
        return None, "payload must be object"
    if "workflow" in payload or "prompt_graph" in payload or "command" in payload:
        return None, "arbitrary workflow or command payload is forbidden"
    job_id = str(payload.get("job_id") or "").strip()
    if not MEDIA_JOB_ID_RE.match(job_id) or ".." in job_id:
        return None, "invalid job_id"
    template_id = str(payload.get("template_id") or "").strip()
    if template_id not in MEDIA_CONTROLLED_TEMPLATE_IDS:
        return None, "unsupported template_id"
    mode = str(payload.get("mode") or "").strip().lower()
    expected = {
        "h3_t2v_v1": "text_to_video",
        "h3_i2v_v1": "image_to_video",
        "h3_r2v_v1": "reference_replay",
        "video_upscale_realesrgan_v1": "video_enhance",
        MEDIA_LOCAL_EDIT_TEMPLATE_ID: "video_local_edit",
    }[template_id]
    if mode != expected:
        return None, "mode does not match allow-listed template"
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    executable_prompt = str(prompt.get("integrated_multimodal_description") or prompt.get("video_prompt") or "")
    if mode != "video_enhance" and (not executable_prompt or len(executable_prompt) > 7000):
        return None, "H3 executable prompt must contain 1 to 7000 characters"
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    dimension_max = 1920 if mode in {"video_enhance", "video_local_edit"} else 1536
    width = _safe_int_between(params.get("width"), 480, 256, dimension_max)
    height = _safe_int_between(params.get("height"), 864, 256, dimension_max)
    frames = _safe_int_between(params.get("frames"), 124, 21, 9000 if mode in {"video_enhance", "video_local_edit"} else 361)
    fps = _safe_int_between(params.get("fps"), 24, 12, 30)
    steps = _safe_int_between(params.get("steps"), 20, 4, 60)
    batch_count = _safe_int_between(params.get("batch_count"), 1, 1, 8)
    seed = _safe_int_between(params.get("seed"), -1, -1, 2**63 - 1)
    attempt_no = _safe_int_between(payload.get("attempt_no"), 1, 1, 20)
    if mode == "video_enhance" and (width, height) not in {(1080, 1920), (1920, 1080)}:
        return None, "video_enhance target must be 1080p portrait or landscape"
    if mode != "video_enhance" and (width % 32 or height % 32):
        return None, "width and height must be multiples of 32"
    references = payload.get("references") if isinstance(payload.get("references"), list) else []
    if len(references) > 12:
        return None, "mixed reference count exceeds H3 limit"
    counts = {"image": 0, "video": 0, "audio": 0}
    role_kinds = {
        "first_frame": "image",
        "reference_image": "image",
        "reference_video": "video",
        "reference_audio": "audio",
        "overlay_image": "image",
    }
    max_bytes = {"image": 30 * 1024 * 1024, "video": 50 * 1024 * 1024, "audio": 15 * 1024 * 1024}
    for item in references:
        if not isinstance(item, dict):
            return None, "reference item must be object"
        mime = str(item.get("mime_type") or "")
        kind = mime.split("/", 1)[0]
        if kind not in counts:
            return None, "reference mime type is not allowed"
        role = str(item.get("role") or "").strip()
        if role_kinds.get(role) != kind:
            return None, "reference role does not match mime type"
        try:
            byte_size = int(item.get("byte_size") or 0)
        except (TypeError, ValueError):
            return None, "reference byte_size is invalid"
        allowed_bytes = MEDIA_REFERENCE_MAX_BYTES if mode in {"video_enhance", "video_local_edit"} and kind == "video" else max_bytes[kind]
        if byte_size <= 0 or byte_size > allowed_bytes:
            return None, "reference file exceeds H3 size limit"
        counts[kind] += 1
    if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3:
        return None, "reference count exceeds H3 limits"
    generation_references = [item for item in references if str(item.get("role") or "") != "overlay_image"]
    overlay_references = [item for item in references if str(item.get("role") or "") == "overlay_image"]
    if any(str(item.get("business_role") or "") not in {"product_packshot", "product_detail"} for item in overlay_references):
        return None, "overlay_image is restricted to governed product assets"
    overlay_config = prompt.get("product_overlay") if isinstance(prompt.get("product_overlay"), dict) else {}
    overlay_enabled = overlay_config.get("enabled") is True
    overlay_count = len(overlay_references)
    requested_overlay_anchor = str(overlay_config.get("anchor") or "").strip().lower().replace("-", "_")
    overlay_anchor = "center" if requested_overlay_anchor == "center" else "bottom_right"
    overlay_preset = (
        {
            "anchor": "center",
            "width_ratio": 0.38,
            "safe_margin_ratio": 0.08,
            "motion_profile": "static_verified_product_dynamic_background",
            "content_crop_policy": "alpha_bbox_v1",
        }
        if overlay_anchor == "center"
        else {
            "anchor": "bottom_right",
            "width_ratio": 0.22,
            "safe_margin_ratio": 0.05,
            "motion_profile": "static_verified_product",
            "content_crop_policy": "alpha_bbox_v1",
        }
    )
    if overlay_count > 2:
        return None, "governed product overlay supports at most two overlay_image assets"
    if overlay_count == 2 and {
        str(item.get("business_role") or "") for item in overlay_references
    } != {"product_packshot", "product_detail"}:
        return None, "two governed product overlays require one product_packshot and one product_detail"
    if overlay_references and (not overlay_enabled or mode == "video_enhance"):
        return None, "overlay_image requires a governed product overlay configuration"
    if overlay_enabled and overlay_count not in {1, 2}:
        return None, "governed product overlay requires one or two overlay_image assets"
    if overlay_count == 2:
        overlay_preset = {
            **overlay_preset,
            "layout": "packshot_detail_duo_v1",
            "asset_count": 2,
            "primary_width_ratio": 0.30 if overlay_anchor == "center" else 0.18,
            "secondary_width_ratio": 0.16 if overlay_anchor == "center" else 0.12,
            "group_gap_ratio": 0.02,
        }
    if mode == "text_to_video" and generation_references:
        return None, "text_to_video does not accept reference media"
    if mode != "text_to_video" and not generation_references:
        return None, "selected mode requires reference media"
    if mode == "image_to_video" and (
        len(generation_references) != 1 or str(generation_references[0].get("role") or "") != "first_frame"
    ):
        return None, "image_to_video requires exactly one first_frame image"
    if mode == "reference_replay" and any(str(item.get("role") or "") == "first_frame" for item in generation_references):
        return None, "reference_replay does not accept first_frame"
    if mode == "video_enhance" and (
        len(generation_references) != 1 or str(generation_references[0].get("role") or "") != "reference_video"
    ):
        return None, "video_enhance requires exactly one reference_video"
    if mode == "video_local_edit":
        local_videos = [item for item in generation_references if str(item.get("role") or "") == "reference_video"]
        local_images = [item for item in generation_references if str(item.get("role") or "") == "reference_image"]
        if len(local_videos) != 1 or len(local_images) > 2 or overlay_references:
            return None, "video_local_edit requires one source video and at most two product images"
        local_config = prompt.get("local_edit_config") if isinstance(prompt.get("local_edit_config"), dict) else {}
        if local_config.get("product_mode") == "replace" and not local_images:
            return None, "video_local_edit product replacement requires a product image"
        raw_replacements = local_config.get("replacement_items")
        if raw_replacements is not None and not isinstance(raw_replacements, list):
            return None, "video_local_edit replacement_items must be a list"
        replacements = raw_replacements or []
        if len(replacements) > 12:
            return None, "video_local_edit accepts at most 12 replacement items"
        for index, item in enumerate(replacements):
            if not isinstance(item, dict):
                return None, f"video_local_edit replacement item {index} must be an object"
            if item.get("scope") not in {"visual_audio", "visual_only", "audio_only"}:
                return None, f"video_local_edit replacement item {index} has an invalid scope"
            if not str(item.get("original_text") or "").strip() or not str(item.get("replacement_text") or "").strip():
                return None, f"video_local_edit replacement item {index} requires original_text and replacement_text"
        if not replacements and not local_config.get("remove_text") and local_config.get("product_mode", "keep") == "keep":
            return None, "video_local_edit requires at least one explicit edit target"
    return {
        "job_id": job_id,
        "idempotency_key": str(payload.get("idempotency_key") or job_id)[:128],
        "attempt_no": attempt_no,
        "template_id": template_id,
        "mode": mode,
        "prompt": prompt,
        "params": {
            "width": width,
            "height": height,
            "frames": frames,
            "fps": fps,
            "steps": steps,
            "batch_count": batch_count,
            "seed": seed,
            "audio_enabled": bool(params.get("audio_enabled", True)),
        },
        "references": references,
        "postprocess": {
            "product_overlay": {
                "enabled": overlay_enabled,
                "role": "overlay_image",
                **overlay_preset,
            }
        } if overlay_enabled else {},
    }, None


def _media_probe_product_overlay_transparency(path):
    ffmpeg = _media_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("governed product overlay requires an alpha-capable ffmpeg runtime")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "alphaextract,signalstats,metadata=print:file=-",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    output = "%s\n%s" % (completed.stdout or "", completed.stderr or "")

    def signal(name):
        match = re.search(r"lavfi\.signalstats\.%s=([0-9.]+)" % re.escape(name), output)
        return float(match.group(1)) if match else None

    alpha_min = signal("YMIN")
    alpha_max = signal("YMAX")
    passed = bool(
        completed.returncode == 0
        and alpha_min is not None
        and alpha_max is not None
        and alpha_min < 255
        and alpha_max > 0
    )
    detail = "verified_non_empty_alpha_plane" if passed else (
        "image_has_no_transparent_pixels"
        if completed.returncode == 0 and alpha_min is not None and alpha_min >= 255
        else "image_has_no_visible_subject"
        if completed.returncode == 0 and alpha_max is not None and alpha_max <= 0
        else "alpha_plane_unavailable"
    )
    return {
        "policy_version": MEDIA_PRODUCT_OVERLAY_ALPHA_POLICY_VERSION,
        "passed": passed,
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "detail": detail,
    }


def _media_download_reference(job_id, item):
    source = str(item.get("source_url") or "").strip()
    if source.startswith("/"):
        source = urllib.parse.urljoin(SKILLFORGE_HTTP_BASE.rstrip("/") + "/", source.lstrip("/"))
    source_url = urllib.parse.urlparse(source)
    platform_url = urllib.parse.urlparse(SKILLFORGE_HTTP_BASE)
    source_port = source_url.port or (443 if source_url.scheme == "https" else 80)
    platform_port = platform_url.port or (443 if platform_url.scheme == "https" else 80)
    if (
        source_url.scheme not in {"http", "https"}
        or source_url.scheme != platform_url.scheme
        or source_url.hostname != platform_url.hostname
        or source_port != platform_port
    ):
        raise RuntimeError("reference URL must be a signed SkillForge asset URL")
    try:
        declared_size = int(item.get("byte_size") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size <= 0 or declared_size > MEDIA_REFERENCE_MAX_BYTES:
        raise RuntimeError("reference asset size is invalid")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(str(item.get("file_name") or "asset")).name)[:160]
    asset_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(item.get("asset_id") or "asset"))[:80]
    target_dir = (_media_effective_input_dir() / "skillforge" / job_id).resolve(strict=False)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / f"{asset_id}-{name}").resolve(strict=False)
    target.relative_to(target_dir)
    request = urllib.request.Request(source, headers={"Accept": "application/octet-stream"})
    hasher = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MEDIA_REFERENCE_MAX_BYTES or size > declared_size + 1024:
                    raise RuntimeError("reference asset exceeds declared size")
                hasher.update(chunk)
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    expected_sha = str(item.get("sha256") or "").strip().lower()
    if size != declared_size or (expected_sha and hasher.hexdigest() != expected_sha):
        target.unlink(missing_ok=True)
        raise RuntimeError("reference asset hash or size mismatch")
    alpha_probe = None
    if str(item.get("role") or "") == "overlay_image":
        try:
            alpha_probe = _media_probe_product_overlay_transparency(target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if alpha_probe.get("passed") is not True:
            target.unlink(missing_ok=True)
            raise RuntimeError(
                "governed product overlay requires a non-empty transparent PNG/WebP: "
                + str(alpha_probe.get("detail") or "alpha verification failed")
            )
    return {
        "file_name": target.name,
        "comfy_path": f"skillforge/{job_id}/{target.name}",
        "path": str(target),
        "mime_type": item.get("mime_type"),
        "role": str(item.get("role") or "")[:40],
        "business_role": str(item.get("business_role") or "")[:40],
        "sha256": hasher.hexdigest(),
        "product_overlay_transparency_probe": alpha_probe,
    }


def _media_output_candidates(history_item):
    outputs = history_item.get("outputs") if isinstance(history_item, dict) else {}
    candidates = []
    for node in (outputs or {}).values():
        if not isinstance(node, dict):
            continue
        for key in ("videos", "gifs", "images", "audio"):
            for item in node.get(key) or []:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                candidates.append(item)
    return candidates


def _media_output_path(item):
    subfolder = str(item.get("subfolder") or "").strip().replace("\\", "/")
    filename = Path(str(item.get("filename") or "")).name
    if not filename or ".." in subfolder.split("/"):
        return None
    root = _media_effective_output_dir().resolve(strict=False)
    path = (root / subfolder / filename).resolve(strict=False)
    try:
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


def _media_process_alive(pid):
    try:
        pid = int(pid or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _media_submit_local_edit(normalized, recovery_history):
    runtime = _media_local_edit_runtime()
    if not runtime["configured"]:
        return None, "SAM2/ProPainter local edit runtime is not configured"
    local_refs = [_media_download_reference(normalized["job_id"], item) for item in normalized["references"]]
    source = next((item for item in local_refs if item.get("role") == "reference_video"), None)
    products = [item for item in local_refs if item.get("role") == "reference_image"]
    if source is None:
        return None, "video_local_edit source video is missing"
    output_root = _media_effective_output_dir().resolve(strict=False)
    output_dir = (output_root / "skillforge" / "local-edit").resolve(strict=False)
    output_dir.relative_to(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{normalized['job_id']}.mp4"
    job_dir = (_media_jobs_dir() / normalized["job_id"]).resolve(strict=False)
    job_dir.relative_to(_media_jobs_dir().resolve(strict=False))
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = job_dir / "request.json"
    log_path = job_dir / "runner.log"
    error_path = job_dir / "runner.error"
    local_config = normalized["prompt"].get("local_edit_config")
    local_config = local_config if isinstance(local_config, dict) else {}
    request = {
        "contract_version": "sam2_track_propainter_inpaint_composite_data_patch_v2",
        "job_id": normalized["job_id"],
        "source_video": source["path"],
        "product_images": [item["path"] for item in products],
        "output_video": str(output_path),
        "error_file": str(error_path),
        "sam2_root": runtime["sam2_root"],
        "propainter_root": runtime["propainter_root"],
        "controls": {
            "replace_people": bool(local_config.get("replace_people")),
            "remove_text": bool(local_config.get("remove_text")),
            "product_mode": str(local_config.get("product_mode") or "keep"),
            "audio_mode": str(local_config.get("audio_mode") or "original"),
            "keep_scene": local_config.get("keep_scene") is not False,
            "keep_camera_motion": local_config.get("keep_camera_motion") is not False,
            "replacement_items": local_config.get("replacement_items") or [],
            "immutable_outside_mask": True,
        },
    }
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    log_handle = log_path.open("ab")
    creation_kwargs = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    }
    try:
        process = subprocess.Popen(
            [sys.executable, str(MEDIA_LOCAL_EDIT_RUNNER.resolve(strict=True)), "--request-json", str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(job_dir),
            **creation_kwargs,
        )
    finally:
        log_handle.close()
    now_iso = datetime.utcnow().isoformat() + "Z"
    record = {
        "job_id": normalized["job_id"],
        "idempotency_key": normalized["idempotency_key"],
        "attempt_no": normalized["attempt_no"],
        "prompt_id": f"local-edit:{process.pid}",
        "prompt_ids": [],
        "pid": process.pid,
        "status": "running",
        "mode": "video_local_edit",
        "template_id": MEDIA_LOCAL_EDIT_TEMPLATE_ID,
        "workflow_version": "sam2-track-propainter-data-patch-v2",
        "model_version": "SAM2+ProPainter",
        "model_sha256": None,
        "params": normalized["params"],
        "references": [{
            "file_name": item["file_name"], "path": item["path"], "mime_type": item["mime_type"],
            "role": item["role"], "business_role": item["business_role"], "sha256": item["sha256"],
        } for item in local_refs],
        "request_path": str(request_path),
        "runner_log_path": str(log_path),
        "runner_error_path": str(error_path),
        "expected_output_path": str(output_path),
        "submitted_at": now_iso,
        "updated_at": now_iso,
        "metrics": _media_gpu_metrics(),
        "recovery_history": recovery_history,
    }
    _write_media_job_record(normalized["job_id"], record)
    return record, None


def _media_submit_job(payload):
    normalized, error = _normalize_media_job_payload(payload)
    if error:
        return None, error
    existing = _load_media_job_record(normalized["job_id"])
    recovery_history = []
    if existing:
        if existing.get("idempotency_key") != normalized["idempotency_key"]:
            return None, "job_id already exists with another idempotency key"
        existing_attempt_no = _safe_int_between(existing.get("attempt_no"), 1, 1, 20)
        recoverable_orphan = (
            existing.get("status") == "orphaned"
            and existing.get("error_code") == MEDIA_PROMPT_ORPHAN_ERROR_CODE
            and normalized["attempt_no"] > existing_attempt_no
        )
        if not recoverable_orphan:
            return existing, None
        recovery_history = list(existing.get("recovery_history") or [])[-4:]
        recovery_history.append({
            "attempt_no": existing_attempt_no,
            "status": existing.get("status"),
            "error_code": existing.get("error_code"),
            "error": existing.get("error"),
            "prompt_ids": list(existing.get("prompt_ids") or []),
            "submitted_at": existing.get("submitted_at"),
            "completed_at": existing.get("completed_at"),
        })
        logging.getLogger("skillforge-bridge").warning(
            "media job orphan recovery resubmit job=%s old_attempt=%s new_attempt=%s",
            normalized["job_id"],
            existing_attempt_no,
            normalized["attempt_no"],
        )
    if normalized["mode"] == "video_local_edit":
        return _media_submit_local_edit(normalized, recovery_history)
    template = _load_media_template(normalized["template_id"])
    local_refs = [_media_download_reference(normalized["job_id"], item) for item in normalized["references"]]
    seed = normalized["params"]["seed"]
    if seed < 0:
        seed = int(hashlib.sha256(f"{normalized['job_id']}:{time.time_ns()}".encode()).hexdigest()[:15], 16)
        normalized["params"]["seed"] = seed
    prompt = normalized["prompt"]
    generation_refs = [item for item in local_refs if item.get("role") != "overlay_image"]
    first_image = next((item for item in generation_refs if str(item.get("mime_type") or "").startswith("image/")), None)
    first_video = next((item for item in generation_refs if str(item.get("mime_type") or "").startswith("video/")), None)
    if normalized["mode"] == "image_to_video" and first_image is None:
        return None, "image_to_video requires an image reference"
    values = {
        "positive_prompt": _media_compose_prompt(prompt),
        "negative_prompt": str(prompt.get("negative_constraints") or prompt.get("negative_prompt") or ""),
        "audio_prompt": str(prompt.get("audio_prompt") or ""),
        "width": normalized["params"]["width"],
        "height": normalized["params"]["height"],
        "frames": normalized["params"]["frames"],
        "fps": normalized["params"]["fps"],
        "steps": normalized["params"]["steps"],
        "seed": seed,
        "batch_count": normalized["params"]["batch_count"],
        "audio_enabled": normalized["params"]["audio_enabled"],
        "first_frame_file": first_image.get("comfy_path") if first_image else "",
        "source_video_file": first_video.get("comfy_path") if first_video else "",
        "output_prefix": f"skillforge/{normalized['job_id']}-01",
    }
    prompt_ids = []
    try:
        for batch_index in range(normalized["params"]["batch_count"]):
            batch_values = dict(values)
            batch_values["seed"] = seed + batch_index
            batch_values["output_prefix"] = f"skillforge/{normalized['job_id']}-{batch_index + 1:02d}"
            workflow = _media_prepare_workflow(template, batch_values, generation_refs, normalized["mode"])
            response = _media_http_json(
                "POST",
                "/prompt",
                {"prompt": workflow, "client_id": f"skillforge-{INSTANCE_ID or 'bridge'}"},
                timeout=60,
            )
            prompt_id = str(response.get("prompt_id") or "").strip()
            if not prompt_id:
                raise RuntimeError("ComfyUI did not return prompt_id")
            prompt_ids.append(prompt_id)
    except Exception:
        if prompt_ids:
            try:
                _media_http_json("POST", "/queue", {"delete": prompt_ids}, timeout=10)
            except Exception:
                pass
        raise
    # First governed submission computes and caches the real file hashes.  Later
    # jobs reuse the size/mtime cache instead of re-hashing tens of gigabytes.
    model = _media_model_manifest(include_hashes=True)
    now_iso = datetime.utcnow().isoformat() + "Z"
    gpu_metrics = _media_gpu_metrics()
    record = {
        "job_id": normalized["job_id"],
        "idempotency_key": normalized["idempotency_key"],
        "attempt_no": normalized["attempt_no"],
        "prompt_id": prompt_ids[0],
        "prompt_ids": prompt_ids,
        "status": "queued",
        "template_id": normalized["template_id"],
        "workflow_version": str(template.get("version") or template.get("workflow_version") or "v1")[:80],
        "model_version": str(template.get("model_version") or "MiniMax-H3")[:180],
        "model_sha256": model.get("model_sha256"),
        "params": normalized["params"],
        "references": [
            {
                "file_name": item["file_name"],
                "path": item["path"],
                "mime_type": item["mime_type"],
                "role": item["role"],
                "business_role": item["business_role"],
                "sha256": item["sha256"],
                "product_overlay_transparency_probe": item.get("product_overlay_transparency_probe"),
            }
            for item in local_refs
        ],
        "postprocess": normalized.get("postprocess") or {},
        "submitted_at": now_iso,
        "updated_at": now_iso,
        "metrics": {**gpu_metrics, "peak_vram_used_mb": gpu_metrics.get("vram_used_mb", 0)},
        "recovery_history": recovery_history,
    }
    _write_media_job_record(normalized["job_id"], record)
    return record, None


def _media_get_job(payload):
    job_id = str((payload or {}).get("job_id") or "").strip()
    record = _load_media_job_record(job_id)
    if not record:
        return {"job_id": job_id, "status": "not_found"}
    previous_status = str(record.get("status") or "")
    current_gpu = _media_gpu_metrics()
    old_metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
    record["metrics"] = {
        **old_metrics,
        **current_gpu,
        "peak_vram_used_mb": max(
            int(old_metrics.get("peak_vram_used_mb") or 0),
            int(current_gpu.get("vram_used_mb") or 0),
        ),
    }
    if record.get("mode") == "video_local_edit":
        output_path = Path(str(record.get("expected_output_path") or ""))
        error_path = Path(str(record.get("runner_error_path") or ""))
        log_path = Path(str(record.get("runner_log_path") or ""))
        if output_path.is_file() and output_path.stat().st_size > 0:
            record["status"] = "completed"
            record["outputs"] = [{"filename": output_path.name, "subfolder": "skillforge/local-edit", "path": str(output_path)}]
            record["output"] = record["outputs"][0]
            record["completed_at"] = record.get("completed_at") or datetime.utcnow().isoformat() + "Z"
        elif error_path.is_file():
            record["status"] = "failed"
            record["error"] = _redact_training_text(error_path.read_text(encoding="utf-8", errors="ignore"), limit=4000)
        elif _media_process_alive(record.get("pid")):
            record["status"] = "running"
        else:
            record["status"] = "failed"
            record["error"] = "governed local edit runner exited without an output or error artifact"
        if log_path.is_file():
            try:
                with log_path.open("rb") as stream:
                    stream.seek(max(0, log_path.stat().st_size - 16000))
                    record["log_tail"] = _redact_training_text(stream.read().decode("utf-8", "ignore"), limit=16000)
            except OSError:
                pass
        record["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _write_media_job_record(job_id, record)
        return record
    prompt_ids = [str(item) for item in (record.get("prompt_ids") or [record.get("prompt_id")]) if str(item or "")]
    queue = _media_http_json("GET", "/queue", timeout=10)
    running_ids = {
        str(item[1]) for item in (queue.get("queue_running") or []) if isinstance(item, list) and len(item) > 1
    }
    pending_ids = {
        str(item[1]) for item in (queue.get("queue_pending") or []) if isinstance(item, list) and len(item) > 1
    }
    states = []
    outputs = []
    log_messages = []
    errors = []
    missing_prompt_ids = []
    for prompt_id in prompt_ids:
        history = _media_http_json("GET", f"/history/{urllib.parse.quote(prompt_id, safe='')}", timeout=15)
        item = history.get(prompt_id) if isinstance(history.get(prompt_id), dict) else None
        if item:
            status_info = item.get("status") if isinstance(item.get("status"), dict) else {}
            status_text = str(status_info.get("status_str") or "").lower()
            candidates = _media_output_candidates(item)
            usable = [(candidate, _media_output_path(candidate)) for candidate in candidates]
            usable = [(candidate, path) for candidate, path in usable if path is not None]
            if usable:
                preferred = next(((candidate, path) for candidate, path in usable if path.suffix.lower() == ".mp4"), usable[0])
                states.append("completed")
                outputs.append(
                    {
                        "prompt_id": prompt_id,
                        "filename": preferred[0].get("filename"),
                        "subfolder": preferred[0].get("subfolder") or "",
                        "path": str(preferred[1]),
                    }
                )
            elif status_text in {"error", "failed"}:
                states.append("failed")
                messages = status_info.get("messages") or []
                errors.append(_redact_training_text(messages[-1] if messages else status_text, limit=2000))
            else:
                states.append("running")
            log_messages.append(status_info.get("messages") or "")
        elif prompt_id in running_ids:
            states.append("running")
        elif prompt_id in pending_ids:
            states.append("queued")
        else:
            states.append("missing")
            missing_prompt_ids.append(prompt_id)
    now_iso = datetime.utcnow().isoformat() + "Z"
    if missing_prompt_ids:
        missing_observed_at = str(record.get("prompt_missing_observed_at") or "").strip()
        if not missing_observed_at:
            missing_observed_at = now_iso
            record["prompt_missing_observed_at"] = missing_observed_at
        try:
            missing_age_seconds = max(
                0.0,
                time.time() - datetime.fromisoformat(missing_observed_at.replace("Z", "+00:00")).timestamp(),
            )
        except (TypeError, ValueError):
            missing_age_seconds = 0.0
        record["recovery"] = {
            "status": "waiting_for_prompt_reappearance"
            if missing_age_seconds < MEDIA_PROMPT_ORPHAN_GRACE_SECONDS
            else "orphaned",
            "missing_prompt_ids": missing_prompt_ids,
            "observed_at": missing_observed_at,
            "age_seconds": round(missing_age_seconds, 3),
            "grace_seconds": MEDIA_PROMPT_ORPHAN_GRACE_SECONDS,
        }
    else:
        record.pop("prompt_missing_observed_at", None)
        record.pop("recovery", None)
        if record.get("error_code") == MEDIA_PROMPT_ORPHAN_ERROR_CODE:
            record.pop("error_code", None)
            record.pop("error", None)
    if "failed" in states:
        record["status"] = "failed"
        record["error"] = errors[0] if errors else "ComfyUI batch failed"
    elif prompt_ids and len(outputs) == len(prompt_ids):
        record["status"] = "completed"
        record["outputs"] = outputs
        record["output"] = outputs[0]
        record["completed_at"] = datetime.utcnow().isoformat() + "Z"
        try:
            submitted_at = datetime.fromisoformat(str(record.get("submitted_at") or "").replace("Z", "+00:00"))
            completed_at = datetime.fromisoformat(record["completed_at"].replace("Z", "+00:00"))
            total_seconds = max(0.0, (completed_at - submitted_at).total_seconds())
        except Exception:
            total_seconds = 0.0
        record["metrics"] = {
            **(record.get("metrics") if isinstance(record.get("metrics"), dict) else {}),
            "total_seconds": round(total_seconds, 3),
            "file_size_bytes": sum(Path(item["path"]).stat().st_size for item in outputs),
            "result_count": len(outputs),
        }
    elif missing_prompt_ids and record.get("recovery", {}).get("status") == "orphaned":
        record["status"] = "orphaned"
        record["error_code"] = MEDIA_PROMPT_ORPHAN_ERROR_CODE
        record["error"] = (
            "ComfyUI prompt disappeared from both queue and history; "
            "the persisted Bridge status is no longer trusted."
        )
        if previous_status != "orphaned":
            logging.getLogger("skillforge-bridge").error(
                "media job prompt orphaned job=%s prompt_ids=%s grace_seconds=%s",
                job_id,
                ",".join(missing_prompt_ids),
                MEDIA_PROMPT_ORPHAN_GRACE_SECONDS,
            )
    elif "running" in states or missing_prompt_ids:
        record["status"] = "running"
    else:
        record["status"] = "queued"
    if record["status"] in {"completed", "failed"} and not record.get("idle_memory_released_at"):
        if _media_release_idle_memory():
            record["idle_memory_released_at"] = datetime.utcnow().isoformat() + "Z"
    record["log_tail"] = _redact_training_text(log_messages, limit=16000)
    record["updated_at"] = now_iso
    _write_media_job_record(job_id, record)
    return record


def _media_cancel_job(payload):
    job_id = str((payload or {}).get("job_id") or "").strip()
    record = _load_media_job_record(job_id)
    if not record:
        return {"job_id": job_id, "status": "not_found"}
    if record.get("mode") == "video_local_edit":
        try:
            pid = int(record.get("pid") or 0)
            if pid > 0 and _media_process_alive(pid):
                if os.name != "nt":
                    os.killpg(pid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            pass
        record["status"] = "cancelled"
        record["cancelled_at"] = datetime.utcnow().isoformat() + "Z"
        record["updated_at"] = record["cancelled_at"]
        _write_media_job_record(job_id, record)
        return record
    prompt_ids = [str(item) for item in (record.get("prompt_ids") or [record.get("prompt_id")]) if str(item or "")]
    try:
        _media_http_json("POST", "/queue", {"delete": prompt_ids}, timeout=10)
    except Exception:
        pass
    try:
        _media_http_json("POST", "/interrupt", {}, timeout=10)
    except Exception:
        pass
    record["status"] = "cancelled"
    record["cancelled_at"] = datetime.utcnow().isoformat() + "Z"
    record["updated_at"] = record["cancelled_at"]
    if _media_release_idle_memory():
        record["idle_memory_released_at"] = record["updated_at"]
    _write_media_job_record(job_id, record)
    return record


def _media_iter_isobmff_boxes(stream, start, end, *, max_boxes=4096):
    """Yield bounded ISO-BMFF boxes without loading media payloads into memory."""

    position = max(0, int(start or 0))
    boundary = max(position, int(end or 0))
    seen = 0
    while position + 8 <= boundary and seen < max_boxes:
        stream.seek(position)
        header = stream.read(16)
        if len(header) < 8:
            return
        size = int.from_bytes(header[0:4], "big")
        kind = header[4:8]
        header_size = 8
        if size == 1:
            if len(header) < 16:
                return
            size = int.from_bytes(header[8:16], "big")
            header_size = 16
        elif size == 0:
            size = boundary - position
        if size < header_size or position + size > boundary:
            return
        yield kind, position + header_size, position + size
        position += size
        seen += 1


def _media_find_isobmff_box(stream, start, end, expected):
    for kind, payload_start, box_end in _media_iter_isobmff_boxes(stream, start, end):
        if kind == expected:
            return payload_start, box_end
    return None


def _media_read_isobmff_payload(stream, box, *, limit=256):
    if not box:
        return b""
    start, end = box
    stream.seek(start)
    return stream.read(min(max(0, end - start), max(0, int(limit))))


def _media_isobmff_timescale_duration(payload):
    if not payload:
        return 0, 0
    version = payload[0]
    if version == 0 and len(payload) >= 20:
        return int.from_bytes(payload[12:16], "big"), int.from_bytes(payload[16:20], "big")
    if version == 1 and len(payload) >= 32:
        return int.from_bytes(payload[20:24], "big"), int.from_bytes(payload[24:32], "big")
    return 0, 0


def _media_mp4_codec_name(sample_type):
    return {
        b"avc1": "h264",
        b"avc3": "h264",
        b"hvc1": "hevc",
        b"hev1": "hevc",
        b"av01": "av1",
        b"vp09": "vp9",
        b"mp4a": "aac",
        b"ac-3": "ac3",
        b"ec-3": "eac3",
        b"Opus": "opus",
    }.get(sample_type, sample_type.decode("ascii", errors="replace").strip())


def _media_parse_mp4_track(stream, track_box):
    track_start, track_end = track_box
    mdia = _media_find_isobmff_box(stream, track_start, track_end, b"mdia")
    if not mdia:
        return None
    hdlr = _media_read_isobmff_payload(
        stream,
        _media_find_isobmff_box(stream, mdia[0], mdia[1], b"hdlr"),
        limit=32,
    )
    handler = hdlr[8:12] if len(hdlr) >= 12 else b""
    codec_type = {b"vide": "video", b"soun": "audio"}.get(handler)
    if not codec_type:
        return None

    mdhd = _media_read_isobmff_payload(
        stream,
        _media_find_isobmff_box(stream, mdia[0], mdia[1], b"mdhd"),
        limit=48,
    )
    timescale, media_duration = _media_isobmff_timescale_duration(mdhd)
    minf = _media_find_isobmff_box(stream, mdia[0], mdia[1], b"minf")
    stbl = _media_find_isobmff_box(stream, minf[0], minf[1], b"stbl") if minf else None
    stsd = _media_find_isobmff_box(stream, stbl[0], stbl[1], b"stsd") if stbl else None
    sample_entry = None
    if stsd and stsd[0] + 8 <= stsd[1]:
        sample_entry = next(_media_iter_isobmff_boxes(stream, stsd[0] + 8, stsd[1], max_boxes=8), None)
    sample_type = sample_entry[0] if sample_entry else b""
    sample_payload = b""
    if sample_entry:
        stream.seek(sample_entry[1])
        sample_payload = stream.read(min(96, sample_entry[2] - sample_entry[1]))

    row = {
        "codec_type": codec_type,
        "codec_name": _media_mp4_codec_name(sample_type) if sample_type else "",
        "width": 0,
        "height": 0,
        "avg_frame_rate": "",
        "sample_rate": 0,
        "channels": 0,
    }
    if codec_type == "video":
        if len(sample_payload) >= 28:
            row["width"] = int.from_bytes(sample_payload[24:26], "big")
            row["height"] = int.from_bytes(sample_payload[26:28], "big")
        if not row["width"] or not row["height"]:
            tkhd = _media_read_isobmff_payload(
                stream,
                _media_find_isobmff_box(stream, track_start, track_end, b"tkhd"),
                limit=128,
            )
            if len(tkhd) >= 8:
                row["width"] = int.from_bytes(tkhd[-8:-4], "big") >> 16
                row["height"] = int.from_bytes(tkhd[-4:], "big") >> 16
        stts = _media_read_isobmff_payload(
            stream,
            _media_find_isobmff_box(stream, stbl[0], stbl[1], b"stts") if stbl else None,
            limit=4096,
        )
        if timescale > 0 and len(stts) >= 8:
            entry_count = min(int.from_bytes(stts[4:8], "big"), (len(stts) - 8) // 8, 256)
            samples = 0
            ticks = 0
            for index in range(entry_count):
                offset = 8 + index * 8
                count = int.from_bytes(stts[offset : offset + 4], "big")
                delta = int.from_bytes(stts[offset + 4 : offset + 8], "big")
                samples += count
                ticks += count * delta
            if samples > 0 and ticks > 0:
                fps = samples * timescale / ticks
                row["avg_frame_rate"] = f"{round(fps, 3):g}/1"
    elif len(sample_payload) >= 28:
        row["channels"] = int.from_bytes(sample_payload[16:18], "big")
        row["sample_rate"] = int.from_bytes(sample_payload[24:28], "big") >> 16
    if timescale > 0 and media_duration > 0:
        row["duration_seconds"] = round(media_duration / timescale, 3)
    return row


def _media_probe_mp4_without_ffprobe(path):
    """Read standard MP4 container metadata when an otherwise healthy node lacks ffprobe."""

    try:
        file_size = path.stat().st_size
        if file_size <= 0:
            return {}
        with path.open("rb") as stream:
            moov = _media_find_isobmff_box(stream, 0, file_size, b"moov")
            if not moov:
                return {}
            mvhd = _media_read_isobmff_payload(
                stream,
                _media_find_isobmff_box(stream, moov[0], moov[1], b"mvhd"),
                limit=48,
            )
            timescale, movie_duration = _media_isobmff_timescale_duration(mvhd)
            duration = movie_duration / timescale if timescale > 0 and movie_duration > 0 else 0
            streams = []
            for kind, track_start, track_end in _media_iter_isobmff_boxes(stream, moov[0], moov[1]):
                if kind != b"trak" or len(streams) >= 8:
                    continue
                row = _media_parse_mp4_track(stream, (track_start, track_end))
                if row:
                    streams.append(row)
    except (OSError, ValueError, OverflowError):
        return {}
    if duration <= 0:
        duration_candidates = [float(item.get("duration_seconds") or 0) for item in streams]
        duration = max(duration_candidates, default=0)
    if duration <= 0 or not any(item.get("codec_type") == "video" for item in streams):
        return {}
    return {
        "status": "ok",
        "source": "bridge_mp4_parser",
        "duration_seconds": round(duration, 3),
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "bit_rate": max(0, int(file_size * 8 / duration)),
        "streams": streams,
    }


def _media_probe_output(path):
    """Probe a governed media result once and persist only bounded metadata."""

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        fallback = _media_probe_mp4_without_ffprobe(path)
        if fallback:
            return fallback
        return {
            "status": "unavailable",
            "source": "bridge_ffprobe",
            "detail": "ffprobe is not installed on the media node",
        }
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "source": "bridge_ffprobe",
            "detail": _redact_training_text(exc, limit=500),
        }
    if proc.returncode != 0:
        return {
            "status": "failed",
            "source": "bridge_ffprobe",
            "detail": _redact_training_text(proc.stderr or "ffprobe failed", limit=500),
        }
    try:
        parsed = json.loads(proc.stdout or "{}")
    except (TypeError, json.JSONDecodeError):
        return {
            "status": "failed",
            "source": "bridge_ffprobe",
            "detail": "ffprobe returned invalid JSON",
        }
    format_row = parsed.get("format") if isinstance(parsed.get("format"), dict) else {}
    stream_rows = [item for item in (parsed.get("streams") or []) if isinstance(item, dict)][:8]
    try:
        duration = float(format_row.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        return {
            "status": "failed",
            "source": "bridge_ffprobe",
            "detail": "media duration is unavailable",
        }

    def bounded_int(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "status": "ok",
        "source": "bridge_ffprobe",
        "duration_seconds": round(duration, 3),
        "format_name": str(format_row.get("format_name") or "")[:120],
        "bit_rate": bounded_int(format_row.get("bit_rate")),
        "streams": [
            {
                "codec_type": str(item.get("codec_type") or "")[:30],
                "codec_name": str(item.get("codec_name") or "")[:60],
                "width": bounded_int(item.get("width")),
                "height": bounded_int(item.get("height")),
                "avg_frame_rate": str(item.get("avg_frame_rate") or "")[:30],
                "sample_rate": bounded_int(item.get("sample_rate")),
                "channels": bounded_int(item.get("channels")),
            }
            for item in stream_rows
        ],
    }


def _media_ffmpeg_executable():
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled = Path(str(imageio_ffmpeg.get_ffmpeg_exe() or "")).resolve(strict=True)
        if bundled.is_file():
            return str(bundled)
    except Exception:
        pass

    candidates = _media_bootstrap_ffmpeg_candidates()
    if candidates:
        return str(candidates[0].resolve(strict=True))

    bootstrap_python = MEDIA_BOOTSTRAP_VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not bootstrap_python.is_file():
        return None
    try:
        proc = subprocess.run(
            [
                str(bootstrap_python),
                "-c",
                "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        candidate = Path(str(proc.stdout or "").strip().splitlines()[-1]).resolve(strict=True) if proc.returncode == 0 and str(proc.stdout or "").strip() else None
        if candidate and candidate.is_file():
            candidate.relative_to(MEDIA_BOOTSTRAP_VENV_DIR.resolve(strict=True))
            return str(candidate)
    except (IndexError, OSError, ValueError):
        return None
    return None


def _media_strip_audio_track(source_path, temporary_path):
    ffmpeg = _media_ffmpeg_executable()
    if ffmpeg:
        return subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-an",
                "-movflags",
                "+faststart",
                "-y",
                str(temporary_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    bootstrap_python = MEDIA_BOOTSTRAP_VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not bootstrap_python.is_file():
        raise RuntimeError("audio is disabled but no governed media remuxer is available")
    pyav_remux = (
        "import av,sys\n"
        "source,target=sys.argv[1],sys.argv[2]\n"
        "with av.open(source) as inp, av.open(target, 'w') as out:\n"
        "    video=next((stream for stream in inp.streams if stream.type == 'video'), None)\n"
        "    if video is None: raise RuntimeError('video stream missing')\n"
        "    copied=(out.add_stream_from_template(video) if hasattr(out, 'add_stream_from_template') else out.add_stream(template=video))\n"
        "    for packet in inp.demux(video):\n"
        "        if packet.dts is None: continue\n"
        "        packet.stream=copied\n"
        "        out.mux(packet)\n"
    )
    return subprocess.run(
        [str(bootstrap_python), "-c", pyav_remux, str(source_path), str(temporary_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _media_trim_delivery_output(source_path, temporary_path, *, duration_seconds, audio_enabled):
    ffmpeg = _media_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("3-second delivery requires the governed ffmpeg media runtime")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-t",
        f"{float(duration_seconds):g}",
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
    ]
    if audio_enabled:
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", "-y", str(temporary_path)])
    return subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)


def _media_product_overlay_references(record):
    config = record.get("postprocess") if isinstance(record.get("postprocess"), dict) else {}
    overlay = config.get("product_overlay") if isinstance(config.get("product_overlay"), dict) else {}
    if overlay.get("enabled") is not True:
        return []
    candidates = [
        item for item in (record.get("references") or [])
        if isinstance(item, dict)
        and item.get("role") == "overlay_image"
        and item.get("business_role") in {"product_packshot", "product_detail"}
    ]
    if len(candidates) not in {1, 2}:
        raise RuntimeError("governed product overlay reference is missing or ambiguous")
    if len(candidates) == 2 and {
        str(item.get("business_role") or "") for item in candidates
    } != {"product_packshot", "product_detail"}:
        raise RuntimeError("governed product duo requires one product_packshot and one product_detail")
    resolved_candidates = []
    for candidate in candidates:
        path = Path(str(candidate.get("path") or ""))
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(_media_effective_input_dir().resolve(strict=False))
        except (ValueError, OSError):
            raise RuntimeError("product overlay image is outside configured input root")
        if not resolved.is_file() or resolved.suffix.lower() not in {".png", ".webp"}:
            raise RuntimeError("product overlay requires a governed transparent PNG or WebP")
        resolved_candidates.append({**candidate, "path": resolved})
    role_priority = {"product_packshot": 0, "product_detail": 1}
    return sorted(
        resolved_candidates,
        key=lambda item: (role_priority.get(item.get("business_role"), 9), str(item.get("asset_id") or "")),
    )


def _media_overlay_product_packshot(
    source_path,
    product_references,
    temporary_path,
    *,
    width,
    height,
    duration_seconds,
    audio_enabled,
    overlay_config=None,
    overlay_diagnostics=None,
):
    ffmpeg = _media_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("governed product overlay requires ffmpeg")
    overlay_config = overlay_config if isinstance(overlay_config, dict) else {}
    overlay_diagnostics = overlay_diagnostics if isinstance(overlay_diagnostics, dict) else {}
    anchor = "center" if overlay_config.get("anchor") == "center" else "bottom_right"
    references = [item for item in product_references if isinstance(item, dict)]
    if len(references) not in {1, 2}:
        raise RuntimeError("governed product overlay requires one or two resolved product references")
    width_ratio = 0.38 if anchor == "center" else 0.22
    safe_margin_ratio = 0.08 if anchor == "center" else 0.05
    margin_x = max(16, round(int(width) * safe_margin_ratio))
    margin_y = max(16, round(int(height) * safe_margin_ratio))
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
    ]
    for reference in references:
        command.extend(["-loop", "1", "-i", str(reference["path"])])
    alpha_crops = []
    if overlay_config.get("content_crop_policy") == "alpha_bbox_v1":
        alpha_crops = [_media_detect_product_alpha_crop(reference["path"], ffmpeg=ffmpeg) for reference in references]
    else:
        alpha_crops = [None for _reference in references]
    overlay_diagnostics["content_crop_policy"] = overlay_config.get("content_crop_policy") or None
    overlay_diagnostics["alpha_content_crops"] = [
        {
            "asset_id": reference.get("asset_id"),
            "sha256": reference.get("sha256"),
            "business_role": reference.get("business_role"),
            "bbox": crop,
        }
        for reference, crop in zip(references, alpha_crops)
    ]
    crop_filters = [
        f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}," if crop else ""
        for crop in alpha_crops
    ]
    if len(references) == 1:
        product_width = max(64, round(int(width) * width_ratio))
        overlay_position = (
            "x=(W-w)/2:y=(H-h)/2"
            if anchor == "center"
            else f"x=W-w-{margin_x}:y=H-h-{margin_y}"
        )
        filter_graph = (
            f"[1:v]{crop_filters[0]}scale={product_width}:-1:flags=lanczos,format=rgba[product];"
            f"[0:v][product]overlay={overlay_position}:shortest=1:format=auto[v]"
        )
    else:
        primary_ratio = 0.30 if anchor == "center" else 0.18
        secondary_ratio = 0.16 if anchor == "center" else 0.12
        gap = max(8, round(int(width) * 0.02))
        primary_width = max(64, round(int(width) * primary_ratio))
        secondary_width = max(48, round(int(width) * secondary_ratio))
        group_width = primary_width + secondary_width + gap
        group_x = f"(W-{group_width})/2" if anchor == "center" else f"W-{group_width}-{margin_x}"
        primary_y = "(H-h)/2" if anchor == "center" else f"H-h-{margin_y}"
        secondary_y = "(H-h)/2" if anchor == "center" else f"H-h-{margin_y}"
        filter_graph = (
            f"[1:v]{crop_filters[0]}scale={primary_width}:-1:flags=lanczos,format=rgba[product_primary];"
            f"[2:v]{crop_filters[1]}scale={secondary_width}:-1:flags=lanczos,format=rgba[product_secondary];"
            f"[0:v][product_primary]overlay=x={group_x}:y={primary_y}:shortest=1:format=auto[with_primary];"
            f"[with_primary][product_secondary]overlay=x={group_x}+{primary_width + gap}:y={secondary_y}:shortest=1:format=auto[v]"
        )
    command.extend([
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
    ])
    if duration_seconds > 0:
        command.extend(["-t", f"{float(duration_seconds):g}"])
    if audio_enabled:
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", "-shortest", "-y", str(temporary_path)])
    return subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)


def _media_detect_product_alpha_crop(path, *, ffmpeg=None):
    """Return the opaque-content bounds of an approved transparent product asset.

    Product source files often use a square transparent canvas. Scaling that canvas
    makes the visible package much smaller than the business width ratio. Probe the
    alpha plane first so the governed compositor scales visible product pixels, while
    preserving those pixels unchanged.
    """

    ffmpeg = ffmpeg or _media_ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "info",
                "-loop",
                "1",
                "-i",
                str(path),
                "-vf",
                "alphaextract,cropdetect=limit=0.01:round=2:reset=0",
                "-frames:v",
                "3",
                "-f",
                "null",
                os.devnull,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", f"{completed.stdout}\n{completed.stderr}")
    if not matches:
        return None
    width, height, x, y = (int(value) for value in matches[-1])
    if min(width, height) < 2 or min(x, y) < 0:
        return None
    return {"width": width, "height": height, "x": x, "y": y}


def _media_prepare_delivery_output(record, source_path, result_index):
    """Create the governed delivery file and enforce duration/audio semantics."""

    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    audio_enabled = bool(params.get("audio_enabled", True))
    try:
        delivery_duration = float(params.get("delivery_duration_seconds") or 0)
    except (TypeError, ValueError):
        delivery_duration = 0
    is_mp4 = source_path.suffix.lower() == ".mp4"
    needs_trim = is_mp4 and delivery_duration > 0
    needs_audio_strip = is_mp4 and not audio_enabled
    product_overlays = _media_product_overlay_references(record) if is_mp4 else []
    needs_product_overlay = bool(product_overlays)
    postprocess = record.get("postprocess") if isinstance(record.get("postprocess"), dict) else {}
    product_overlay_config = (
        postprocess.get("product_overlay")
        if isinstance(postprocess.get("product_overlay"), dict)
        else {}
    )
    product_overlay_config_sha256 = (
        hashlib.sha256(
            json.dumps(product_overlay_config, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if needs_product_overlay
        else None
    )
    product_overlay_runtime = {}
    if not needs_trim and not needs_audio_strip and not needs_product_overlay:
        return source_path, None, False

    index_key = str(result_index)
    delivery_outputs = record.get("delivery_outputs") if isinstance(record.get("delivery_outputs"), dict) else {}
    existing = delivery_outputs.get(index_key) if isinstance(delivery_outputs.get(index_key), dict) else {}
    existing_path = Path(str(existing.get("path") or "")) if existing.get("path") else None
    if existing_path:
        try:
            resolved_existing = existing_path.resolve(strict=True)
            resolved_existing.relative_to(source_path.parent.resolve(strict=True))
            probe = existing.get("media_probe") if isinstance(existing.get("media_probe"), dict) else _media_probe_output(resolved_existing)
            streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
            duration = float(probe.get("duration_seconds") or 0)
            duration_ok = not needs_trim or abs(duration - delivery_duration) <= 0.2
            audio_ok = audio_enabled or not any(item.get("codec_type") == "audio" for item in streams)
            overlay_ok = (
                not needs_product_overlay
                or (
                    existing.get("product_overlay_applied") is True
                    and (
                        existing.get("product_overlay_sha256s")
                        == [item.get("sha256") for item in product_overlays]
                        or (
                            len(product_overlays) == 1
                            and existing.get("product_overlay_sha256") == product_overlays[0].get("sha256")
                        )
                    )
                    and existing.get("product_overlay_config_sha256") == product_overlay_config_sha256
                )
            )
            if probe.get("status") == "ok" and duration_ok and audio_ok and overlay_ok and any(item.get("codec_type") == "video" for item in streams):
                return resolved_existing, probe, False
        except (ValueError, OSError):
            pass

    suffixes = []
    if needs_trim:
        suffixes.append(f"{delivery_duration:g}s")
    if needs_audio_strip:
        suffixes.append("video-only")
    if needs_product_overlay:
        suffixes.append("product-overlay")
    target = source_path.with_name(f"{source_path.stem}-{'-'.join(suffixes)}.mp4")
    temporary = target.with_name(f".{target.stem}.tmp.mp4")
    try:
        proc = (
            _media_overlay_product_packshot(
                source_path,
                product_overlays,
                temporary,
                width=params.get("width") or 480,
                height=params.get("height") or 864,
                duration_seconds=delivery_duration,
                audio_enabled=audio_enabled,
                overlay_config=product_overlay_config,
                overlay_diagnostics=product_overlay_runtime,
            )
            if needs_product_overlay
            else (
                _media_trim_delivery_output(
                    source_path,
                    temporary,
                    duration_seconds=delivery_duration,
                    audio_enabled=audio_enabled,
                )
                if needs_trim
                else _media_strip_audio_track(source_path, temporary)
            )
        )
        if proc.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError(_redact_training_text(proc.stderr or "ffmpeg did not create a delivery file", limit=500))
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    probe = _media_probe_output(target)
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    if probe.get("status") != "ok" or not any(item.get("codec_type") == "video" for item in streams):
        raise RuntimeError("governed delivery output failed media validation")
    if needs_trim and abs(float(probe.get("duration_seconds") or 0) - delivery_duration) > 0.2:
        raise RuntimeError("trimmed delivery duration failed media validation")
    if needs_audio_strip and any(item.get("codec_type") == "audio" for item in streams):
        raise RuntimeError("audio-disabled delivery still contains an audio stream")

    delivery_outputs[index_key] = {
        "path": str(target),
        "source_path": str(source_path),
        "audio_stripped": needs_audio_strip,
        "duration_trimmed": needs_trim,
        "delivery_duration_seconds": delivery_duration if needs_trim else None,
        "product_overlay_applied": needs_product_overlay,
        "product_overlay_sha256": product_overlays[0].get("sha256") if product_overlays else None,
        "product_overlay_sha256s": [item.get("sha256") for item in product_overlays],
        "product_overlay_roles": [item.get("business_role") for item in product_overlays],
        "product_overlay_config": product_overlay_config if needs_product_overlay else None,
        "product_overlay_config_sha256": product_overlay_config_sha256,
        "product_overlay_runtime": product_overlay_runtime if needs_product_overlay else None,
        "media_probe": probe,
    }
    record["delivery_outputs"] = delivery_outputs
    if needs_audio_strip:
        record["audio_stripped_for_delivery"] = True
    if needs_trim:
        record["duration_trimmed_for_delivery"] = delivery_duration
    if needs_product_overlay:
        record["product_overlay_applied_for_delivery"] = True
    for cache_key in ("result_sha256s", "result_probes"):
        cache = record.get(cache_key) if isinstance(record.get(cache_key), dict) else {}
        cache.pop(index_key, None)
        record[cache_key] = cache
    if result_index == 0:
        record.pop("result_sha256", None)
        record.pop("media_probe", None)
    return target.resolve(strict=True), probe, True


def _media_collect_result(payload):
    job_id = str((payload or {}).get("job_id") or "").strip()
    record = _media_get_job({"job_id": job_id})
    if record.get("status") != "completed":
        raise RuntimeError(f"media job is not completed: {record.get('status')}")
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else [record.get("output")]
    result_index = _safe_int_between((payload or {}).get("result_index"), 0, 0, max(0, len(outputs) - 1))
    output = outputs[result_index] if result_index < len(outputs) and isinstance(outputs[result_index], dict) else {}
    path = Path(str(output.get("path") or ""))
    root = _media_effective_output_dir().resolve(strict=False)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (ValueError, OSError):
        raise RuntimeError("media result is outside configured output root")
    if not resolved.is_file():
        raise RuntimeError("media result file not found")
    resolved, delivery_probe, delivery_changed = _media_prepare_delivery_output(record, resolved, result_index)
    size = resolved.stat().st_size
    record_changed = delivery_changed
    result_hashes = record.get("result_sha256s") if isinstance(record.get("result_sha256s"), dict) else {}
    sha = str(result_hashes.get(str(result_index)) or "")
    if not sha:
        sha = _media_file_sha256(resolved)
        result_hashes[str(result_index)] = sha
        record["result_sha256s"] = result_hashes
        if result_index == 0:
            record["result_sha256"] = sha
        record_changed = True
    result_probes = record.get("result_probes") if isinstance(record.get("result_probes"), dict) else {}
    probe = result_probes.get(str(result_index)) if isinstance(result_probes.get(str(result_index)), dict) else None
    if not probe:
        probe = delivery_probe or _media_probe_output(resolved)
        result_probes[str(result_index)] = probe
        record["result_probes"] = result_probes
        if result_index == 0:
            record["media_probe"] = probe
        record_changed = True
    if record_changed:
        _write_media_job_record(job_id, record)
    delivery_outputs = record.get("delivery_outputs") if isinstance(record.get("delivery_outputs"), dict) else {}
    delivery = delivery_outputs.get(str(result_index)) if isinstance(delivery_outputs.get(str(result_index)), dict) else {}
    postprocess_provenance = {
        "product_overlay_applied": delivery.get("product_overlay_applied") is True,
        "product_overlay_sha256s": list(delivery.get("product_overlay_sha256s") or []),
        "product_overlay_roles": list(delivery.get("product_overlay_roles") or []),
        "product_overlay_config": delivery.get("product_overlay_config") if isinstance(delivery.get("product_overlay_config"), dict) else {},
        "product_overlay_config_sha256": delivery.get("product_overlay_config_sha256"),
        "product_overlay_runtime": delivery.get("product_overlay_runtime") if isinstance(delivery.get("product_overlay_runtime"), dict) else {},
    } if delivery.get("product_overlay_applied") is True else {}
    return {
        "job_id": job_id,
        "status": "completed",
        "result_index": result_index,
        "result_count": len(outputs),
        "file_name": resolved.name,
        "mime_type": "video/mp4" if resolved.suffix.lower() == ".mp4" else "application/octet-stream",
        "byte_size": size,
        "sha256": sha,
        "model_version": record.get("model_version"),
        "model_sha256": record.get("model_sha256"),
        "workflow_version": record.get("workflow_version"),
        "metrics": record.get("metrics") or {},
        "media_probe": probe,
        "postprocess_provenance": postprocess_provenance,
    }


def _media_read_result_chunk(payload):
    meta = _media_collect_result(payload)
    job_id = meta["job_id"]
    record = _load_media_job_record(job_id) or {}
    outputs = record.get("outputs") if isinstance(record.get("outputs"), list) else [record.get("output")]
    result_index = int(meta.get("result_index") or 0)
    output = outputs[result_index] if result_index < len(outputs) and isinstance(outputs[result_index], dict) else {}
    delivery_outputs = record.get("delivery_outputs") if isinstance(record.get("delivery_outputs"), dict) else {}
    delivery = delivery_outputs.get(str(result_index)) if isinstance(delivery_outputs.get(str(result_index)), dict) else {}
    path = Path(str(delivery.get("path") or output.get("path") or "")).resolve(strict=True)
    root = _media_effective_output_dir().resolve(strict=False)
    try:
        path.relative_to(root)
    except (ValueError, OSError):
        raise RuntimeError("media result is outside configured output root")
    offset = _safe_int_between((payload or {}).get("offset"), 0, 0, meta["byte_size"])
    limit = _safe_int_between((payload or {}).get("limit"), MEDIA_RESULT_CHUNK_MAX_BYTES, 1, MEDIA_RESULT_CHUNK_MAX_BYTES)
    with path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(min(limit, meta["byte_size"] - offset))
    return {
        "job_id": job_id,
        "offset": offset,
        "size_bytes": len(raw),
        "total_size_bytes": meta["byte_size"],
        "eof": offset + len(raw) >= meta["byte_size"],
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }


async def handle_bridge_op(op, payload):
    """bridge 在本地执行的操作（与 forward_request 不同，不转发到 AIClaw）。"""
    payload = payload or {}
    if op == "install_skill":
        # [H2] symlink 防御 + skill_id 校验
        # 攻击模型: 攻击者预先在 target_dir 放置 `skill_id` 作为 symlink 指向 /etc/cron.d/,
        # 正常 install 流程会跟随 symlink 把文件写到敏感目录
        skill_id = payload.get("skill_id")
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        file_map = _normalize_skill_file_payloads(payload.get("files") or [])
        if not file_map:
            return False, None, {"message": "no valid files in install_skill payload"}
        target_dir = payload.get("target_dir") or discover_local_capabilities().get("skills_dir_default")
        if not target_dir:
            return False, None, {"message": "no writable skills dir on this host"}
        try:
            base = Path(target_dir).resolve(strict=False)
            root = base / skill_id
            shared_file_map = _normalize_skill_file_payloads(payload.get("shared_files") or [])

            # 1. 拒绝 root 已经存在但是 symlink (任何指向 — 即便指向合法目录都拒)
            if root.is_symlink():
                return False, None, {"message": f"refuse to install: {root} is a symlink"}

            # 2. mkdir; 之后检查 resolved 路径仍在 base 之下 (防 root 是 symlink dir)
            root.mkdir(parents=True, exist_ok=True)
            root_resolved = root.resolve(strict=True)
            try:
                root_resolved.relative_to(base)
            except (ValueError, OSError):
                return False, None, {"message": f"skill root escapes base dir: {root}"}

            removed, pruned_dirs = _prune_skill_root_to_manifest(root, set(file_map))

            written, skipped = _write_bridge_files(root, file_map)
            if skipped:
                return False, None, {"message": "some files could not be written", "skipped": skipped[:20]}
            shared_written = []
            if shared_file_map:
                shared_root = base / "_shared"
                if shared_root.is_symlink():
                    return False, None, {"message": f"refuse to install shared runtime: {shared_root} is a symlink"}
                shared_written, shared_skipped = _write_bridge_files(shared_root, shared_file_map)
                if shared_skipped:
                    return False, None, {"message": "some shared files could not be written", "skipped": shared_skipped[:20]}
            return True, {
                "path": str(root),
                "files": written,
                "shared_files": shared_written,
                "count": len(written),
                "removed": removed,
                "pruned_dirs": pruned_dirs,
                "sync_mode": "replace",
            }, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "remove_skill":
        # [H2] symlink 防御 — 拒绝跟随 symlink 删外部目录
        skill_id = payload.get("skill_id")
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        target_dir = payload.get("target_dir") or discover_local_capabilities().get("skills_dir_default")
        if not target_dir:
            return False, None, {"message": "no skills dir"}
        try:
            import shutil
            base = Path(target_dir).resolve(strict=False)
            root = base / skill_id

            if not root.exists():
                return True, {"removed": str(root), "note": "not exists"}, None

            # 1. 拒绝 root 是 symlink (避免误删被指向目录)
            if root.is_symlink():
                return False, None, {"message": f"refuse to remove: {root} is a symlink"}

            # 2. resolved 路径必须仍在 base 之下 — 防止 root 通过 symlink 跳到 base 外
            try:
                resolved = root.resolve(strict=True)
                resolved.relative_to(base)
            except (ValueError, OSError):
                return False, None, {"message": f"skill root escapes base dir: {root}"}

            # 3. rmtree 用 onerror 拒绝跟随子目录 symlink
            #    Python rmtree 默认对 symlink dir 只 unlink 不递归, 已是安全的;
            #    这里通过 onerror 把异常转化为日志告警 (而不是静默吞掉)
            errors: list[str] = []
            def _onerror(func, path, exc_info):
                errors.append(f"{func.__name__}({path}): {exc_info[1]}")
            shutil.rmtree(root, onerror=_onerror)
            if errors:
                return False, None, {"message": "rmtree partial failure", "errors": errors[:5]}
            return True, {"removed": str(root)}, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "list_local_skills":
        cap = discover_local_capabilities()
        target_dir = payload.get("target_dir") or cap.get("skills_dir_default")
        if not target_dir or not Path(target_dir).exists():
            return True, {"skills": [], "dir": target_dir}, None
        items = sorted(child.name for child in Path(target_dir).iterdir() if child.is_dir())
        return True, {"skills": items, "dir": target_dir}, None
    if op == "register_skill":
        skill_id = payload.get("skill_id")
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        runtime = _normalize_runtime_config(payload.get("runtime"))
        entry = runtime.get("entry") or "SKILL.md"
        if not isinstance(entry, str) or entry.startswith("/") or ".." in entry.split("/"):
            return False, None, {"message": "invalid entry"}
        script_entry = runtime.get("script_entry") or "scripts/main.py"
        if not isinstance(script_entry, str) or script_entry.startswith("/") or ".." in script_entry.split("/"):
            return False, None, {"message": "invalid script_entry"}
        root = _find_skill_root(skill_id, payload)
        if not root:
            return False, None, {"message": f"skill {skill_id} not found in any local skills dir"}
        timeout = payload.get("timeout") or 30
        try:
            timeout = int(timeout)
        except Exception:
            timeout = 30
        timeout = max(1, min(timeout, 120))
        params = {
            "skill_id": skill_id,
            "skill_dir": str(root),
            "entry": entry,
            "runtime": runtime,
            "context": {
                "instance_id": INSTANCE_ID,
                "skillforge_base_url": SKILLFORGE_HTTP_BASE,
            },
        }
        # bridge 直接管理 Skill 注册 — 文件已由 install_skill 写入磁盘，
        # 本地注册表记录 skill 元数据供后续执行使用，无需依赖 gateway 支持。
        _reg_path = STATE_DIR / "agent_skills_registry.json"
        try:
            _registry = json.loads(_reg_path.read_text()) if _reg_path.exists() else {}
        except Exception:
            _registry = {}
        _now = datetime.utcnow().isoformat()
        _existing = _registry.get(skill_id, {})
        _registry[skill_id] = {
            **_existing,
            "skill_id": skill_id,
            "skill_dir": str(root),
            "entry": entry,
            "script_entry": script_entry,
            "runtime": runtime,
            "updated_at": _now,
            "registered_at": _existing.get("registered_at", _now),
            "status": "active",
        }
        try:
            _reg_path.write_text(json.dumps(_registry, ensure_ascii=False, indent=2))
        except Exception as exc:
            return False, None, {"message": f"failed to write local registry: {exc}"}

        return True, {
            "registered": True,
            "run_backend": runtime["backend"],
            "method": "bridge",
            "ok": True,
            "status": "ok",
        }, None
    if op == "run_agent_skill":
        skill_id = payload.get("skill_id")
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        runtime = _normalize_runtime_config(payload.get("runtime"))
        entry = runtime.get("entry") or "SKILL.md"
        if not isinstance(entry, str) or entry.startswith("/") or ".." in entry.split("/"):
            return False, None, {"message": "invalid entry"}
        script_entry = runtime.get("script_entry") or "scripts/main.py"
        if not isinstance(script_entry, str) or script_entry.startswith("/") or ".." in script_entry.split("/"):
            return False, None, {"message": "invalid script_entry"}
        script_input = payload.get("payload") or payload.get("params") or {}
        if not isinstance(script_input, dict):
            return False, None, {"message": "payload must be object"}
        root = _find_skill_root(skill_id, payload)
        if not root:
            return False, None, {"message": f"skill {skill_id} not found in any local skills dir"}
        run_id = payload.get("run_id") or f"node-{int(time.time())}"
        timeout = payload.get("timeout") or runtime.get("timeout") or 300
        try:
            timeout = int(timeout)
        except Exception:
            timeout = 300
        timeout = max(1, min(timeout, 3600))
        params = {
            "skill_id": skill_id,
            "run_id": run_id,
            "run_token": payload.get("run_token"),
            "skill_git_commit_full": payload.get("skill_git_commit_full"),
            "skill_dir": str(root),
            "entry": entry,
            "params": script_input,
            "runtime": runtime,
            "context": {
                "triggered_by": "node_scheduler",
                "instance_id": INSTANCE_ID,
                "scheduled_at": payload.get("scheduled_at"),
                "skillforge_base_url": SKILLFORGE_HTTP_BASE,
                "run_token": payload.get("run_token"),
                "skill_git_commit_full": payload.get("skill_git_commit_full"),
            },
        }
        try:
            ok, result, error = await _call_local_gateway("skill.run", params, timeout=timeout + 10)
        except Exception as exc:
            return False, None, {"message": str(exc)}
        result = result if isinstance(result, dict) else {"output": result}
        result.setdefault("run_backend", runtime["backend"])
        if ok and result.get("success") is False:
            return False, result, {"message": result.get("error") or "skill.run returned success=false"}
        return ok, result, error
    if op == "run_skill_script":
        skill_id = payload.get("skill_id")
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        script_path = payload.get("script_path") or "scripts/main.py"
        if not isinstance(script_path, str) or not script_path.startswith("scripts/") or script_path.startswith("/") or ".." in script_path.split("/"):
            return False, None, {"message": "invalid script_path"}
        if not script_path.endswith(".py"):
            return False, None, {"message": "only python scripts are supported"}
        timeout = payload.get("timeout", 120)
        try:
            timeout = int(timeout)
        except Exception:
            return False, None, {"message": "invalid timeout"}
        timeout = max(1, min(timeout, 3600))
        script_input = payload.get("payload") or {}
        if not isinstance(script_input, dict):
            return False, None, {"message": "payload must be object"}

        root = _find_skill_root(skill_id, payload)
        if not root:
            return False, None, {"message": f"skill {skill_id} not found in any local skills dir"}
        script = root / script_path
        try:
            script_resolved = script.resolve(strict=True)
            script_resolved.relative_to(root)
        except Exception:
            return False, None, {"message": f"script not found or escapes skill root: {script_path}"}

        started = time.time()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(script_resolved)],
                input=json.dumps(script_input, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(root),
                env=_build_skill_script_env(root, payload),
            )
        except subprocess.TimeoutExpired:
            return False, None, {"message": f"script timeout after {timeout}s"}
        except Exception as exc:
            return False, None, {"message": str(exc)}

        duration_ms = int((time.time() - started) * 1000)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed = _parse_script_stdout(stdout)
        success = proc.returncode == 0 and isinstance(parsed, dict)
        result = {
            "success": success,
            "skill_id": skill_id,
            "script": script_path,
            "source_dir": str(root),
            "returncode": proc.returncode,
            "duration_ms": duration_ms,
            "output": parsed,
            "raw": stdout[-20000:],
            "stderr": stderr[-8000:],
            "run_backend": "bridge_script",
        }
        if not success:
            message = stderr or "script stdout is not a JSON object"
            return False, result, {"message": message, "returncode": proc.returncode}
        return True, result, None
    if op == "media.bootstrap_status":
        return True, _current_media_bootstrap_status(), None
    if op == "media.bootstrap_h3":
        normalized, error = _normalize_media_bootstrap_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_start_media_bootstrap, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "media.bootstrap_cancel":
        try:
            result = await asyncio.to_thread(_cancel_media_bootstrap)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "media.capabilities":
        cap = discover_local_capabilities()
        include_hashes = bool((payload or {}).get("include_hashes") or (payload or {}).get("includeHashes"))
        try:
            result = await asyncio.to_thread(
                _media_capability_snapshot,
                cap.get("gpu") or [],
                include_hashes,
            )
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=2000)}
        return True, result, None
    if op == "media.submit_job":
        try:
            result, error = await asyncio.to_thread(_media_submit_job, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        if error:
            return False, None, {"message": error}
        return True, result, None
    if op == "media.get_job":
        try:
            result = await asyncio.to_thread(_media_get_job, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "media.cancel_job":
        try:
            result = await asyncio.to_thread(_media_cancel_job, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "media.collect_result":
        try:
            result = await asyncio.to_thread(_media_collect_result, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "media.read_result_chunk":
        try:
            result = await asyncio.to_thread(_media_read_result_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=4000)}
        return True, result, None
    if op == "training.gateway_capabilities":
        cap = discover_local_capabilities()
        bootstrap_status = _current_training_bootstrap_status()
        model_status = _current_training_model_status()
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway("GET", "/training/capabilities", timeout=10)
                safe_gateway_result = _redact_training_value(gateway_result)
                return True, {
                    **(safe_gateway_result if isinstance(safe_gateway_result, dict) else {}),
                    "gateway_configured": True,
                    "bridge_version": cap.get("bridge_version"),
                    "gateway_kind": cap.get("gateway_kind"),
                    "bootstrap_env": bootstrap_status,
                    "model": model_status,
                }, None
            except Exception as exc:
                return True, {
                    "gateway": cap.get("training") or {},
                    "gpu": cap.get("gpu") or [],
                    "ops": [item for item in (cap.get("ops") or []) if str(item).startswith("training.")],
                    "bridge_version": cap.get("bridge_version"),
                    "gateway_kind": cap.get("gateway_kind"),
                    "bootstrap_env": bootstrap_status,
                    "model": model_status,
                    "gateway_configured": True,
                    "status": "offline",
                    "error": _redact_training_text(exc, limit=1000),
                }, None
        return True, {
            "gateway": cap.get("training") or {},
            "gpu": cap.get("gpu") or [],
            "ops": [item for item in (cap.get("ops") or []) if str(item).startswith("training.")],
            "bridge_version": cap.get("bridge_version"),
            "gateway_kind": cap.get("gateway_kind"),
            "bootstrap_env": bootstrap_status,
            "model": model_status,
        }, None
    if op == "training.bootstrap_status":
        normalized, error = _normalize_training_bootstrap_payload(payload or {})
        if error:
            return False, None, {"message": error}
        return True, _current_training_bootstrap_status(normalized["profile"]), None
    if op == "training.bootstrap_env":
        normalized, error = _normalize_training_bootstrap_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_bootstrap_training_env_sync, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=12000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.model_status":
        normalized, error = _normalize_training_model_payload(payload or {})
        if error:
            return False, None, {"message": error}
        return True, _current_training_model_status(normalized["profile"], normalized=normalized), None
    if op == "training.discover_models":
        normalized, error = _normalize_training_model_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_discover_training_models_sync, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=12000)}
        return True, result if isinstance(result, dict) else {"candidates": []}, None
    if op == "training.explore_model_paths":
        normalized, error = _normalize_training_model_explore_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_explore_training_model_paths_sync, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"candidates": []}, None
    if op == "training.configure_runtime":
        try:
            result = await asyncio.to_thread(_configure_training_runtime_sync, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("configured") is not True:
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "runtime configure failed"}
        return True, result, None
    if op == "training.discover_python_envs":
        try:
            result = await asyncio.to_thread(_discover_training_python_envs_sync, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=12000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.prepare_model":
        normalized, error = _normalize_training_model_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_prepare_training_model_sync, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.import_artifact":
        try:
            result, error = await asyncio.to_thread(_import_training_artifact_payload, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if error:
            return False, None, {"message": error}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.dataset_write_chunk":
        try:
            result = await asyncio.to_thread(_training_dataset_write_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset write failed"}
        return True, result, None
    if op == "training.dataset_commit":
        try:
            result = await asyncio.to_thread(_training_dataset_commit, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset commit failed"}
        return True, result, None
    if op == "training.dataset_status":
        try:
            result = await asyncio.to_thread(_training_dataset_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.dataset_export_start":
        try:
            result = await asyncio.to_thread(_training_dataset_export_start, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ready":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset export start failed"}
        return True, result, None
    if op == "training.dataset_export_manifest":
        try:
            result = await asyncio.to_thread(_training_dataset_export_manifest_payload, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ready":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset export manifest failed"}
        return True, result, None
    if op == "training.dataset_export_file_chunk":
        try:
            result = await asyncio.to_thread(_training_dataset_export_file_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset export file chunk failed"}
        return True, result, None
    if op == "training.dataset_import_from_export":
        try:
            result = await asyncio.to_thread(_training_dataset_import_from_export, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset import failed"}
        return True, result, None
    if op == "training.dataset_import_status":
        try:
            result = await asyncio.to_thread(_training_dataset_import_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.dataset_import_relay_file_status":
        try:
            result = await asyncio.to_thread(_training_dataset_import_relay_file_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset import relay file status failed"}
        return True, result, None
    if op == "training.dataset_import_relay_chunk":
        try:
            result = await asyncio.to_thread(_training_dataset_import_relay_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset import relay chunk failed"}
        return True, result, None
    if op == "training.dataset_import_relay_commit":
        try:
            result = await asyncio.to_thread(_training_dataset_import_relay_commit, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "dataset import relay commit failed"}
        return True, result, None
    if op == "training.model_export_start":
        try:
            result = await asyncio.to_thread(_training_model_export_start, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ready":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model export start failed"}
        return True, result, None
    if op == "training.model_export_status":
        try:
            result = await asyncio.to_thread(_training_model_export_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.model_export_cancel":
        try:
            result = await asyncio.to_thread(_training_model_export_cancel, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op in {"training.model_export_manifest", "training.model_stream_manifest"}:
        try:
            result = await asyncio.to_thread(_training_model_export_manifest_payload, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ready":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model export manifest failed"}
        return True, result, None
    if op in {"training.model_export_file_chunk", "training.model_stream_chunk"}:
        try:
            result = await asyncio.to_thread(_training_model_export_file_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model export file chunk failed"}
        return True, result, None
    if op == "training.model_import_from_export":
        try:
            result = await asyncio.to_thread(_training_model_import_from_export, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model import failed"}
        return True, result, None
    if op == "training.model_import_status":
        try:
            result = await asyncio.to_thread(_training_model_import_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.model_import_cancel":
        try:
            result = await asyncio.to_thread(_training_model_import_cancel, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"status": "unknown"}, None
    if op == "training.model_import_relay_file_status":
        try:
            result = await asyncio.to_thread(_training_model_import_relay_file_status, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model import relay file status failed"}
        return True, result, None
    if op in {"training.model_import_relay_chunk", "training.model_stream_write"}:
        try:
            result = await asyncio.to_thread(_training_model_import_relay_chunk, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model import relay chunk failed"}
        return True, result, None
    if op in {"training.model_import_relay_commit", "training.model_stream_commit"}:
        try:
            result = await asyncio.to_thread(_training_model_import_relay_commit, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "model import relay commit failed"}
        return True, result, None
    if op == "training.register_openwebui_model":
        try:
            result = await asyncio.to_thread(_register_openwebui_model, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("registered") is not True:
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "register failed"}
        return True, result, None
    if op == "training.openwebui_status":
        try:
            result = await asyncio.to_thread(_openwebui_status_sync)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        return True, result if isinstance(result, dict) else {"enabled": False}, None
    if op == "training.openwebui_chat_test":
        try:
            result = await asyncio.to_thread(_openwebui_chat_test_sync, payload or {})
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        if not isinstance(result, dict) or result.get("ok") is not True:
            return False, result if isinstance(result, dict) else {}, {"message": (result or {}).get("error") or "OpenWebUI chat test failed"}
        return True, result, None
    if op == "intelligence.analyze":
        if not isinstance(payload, dict):
            return False, None, {"message": "payload must be object"}
        timeout = payload.get("timeout", 120)
        try:
            timeout = max(1, min(int(timeout), 300))
        except Exception:
            timeout = 120
        gateway_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"timeout", "callback_token", "authorization", "api_key"}
        }
        gateway_payload.setdefault("bridge_instance_id", INSTANCE_ID)
        try:
            if _intelligence_gateway_base_url():
                result = await _call_intelligence_gateway(gateway_payload, timeout=timeout)
            else:
                result = await asyncio.to_thread(_bridge_local_intelligence_analyze, gateway_payload)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=1000)}
        safe_result = _redact_training_value(result)
        return True, safe_result if isinstance(safe_result, dict) else {"output": safe_result}, None
    if op == "training.inference":
        normalized, error = _normalize_training_inference_payload(payload or {})
        if error:
            return False, None, {"message": error}
        try:
            result = await asyncio.to_thread(_run_local_training_inference, normalized)
        except Exception as exc:
            return False, None, {"message": _redact_training_text(exc, limit=12000)}
        return True, result if isinstance(result, dict) else {"output": result}, None
    if op == "training.submit_job":
        normalized, error = _normalize_training_job_payload(payload)
        if error:
            return False, None, {"message": error}
        now_iso = datetime.utcnow().isoformat() + "Z"
        record = {
            "job_id": normalized["job_id"],
            "status": "running",
            "accepted_at": now_iso,
            "updated_at": now_iso,
            "bridge_instance_id": INSTANCE_ID,
            "payload": _training_payload_for_record(normalized),
            "events": [
                {"ts": now_iso, "type": "accepted", "message": "training job accepted by bridge"}
            ],
        }
        try:
            _write_training_job_record(normalized["job_id"], record)
        except Exception as exc:
            return False, None, {"message": str(exc)}
        gateway_job_id = f"{INSTANCE_ID or 'bridge'}:{normalized['job_id']}"
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway("POST", "/training/jobs", normalized, timeout=30)
            except Exception as exc:
                now_iso = datetime.utcnow().isoformat() + "Z"
                record["status"] = "failed"
                record["updated_at"] = now_iso
                events = record.get("events") if isinstance(record.get("events"), list) else []
                events.append({
                    "ts": now_iso,
                    "type": "gateway_submit_failed",
                    "message": _redact_training_text(exc, limit=1000),
                })
                record["events"] = events[-200:]
                _write_training_job_record(normalized["job_id"], record)
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
            safe_gateway_result = _redact_training_value(gateway_result)
            accepted = bool(gateway_result.get("accepted", True)) if isinstance(gateway_result, dict) else True
            gateway_status = str(
                (gateway_result.get("status") if isinstance(gateway_result, dict) else "")
                or ("running" if accepted else "failed")
            )[:30]
            gateway_job_id = str(
                (gateway_result.get("gateway_job_id") if isinstance(gateway_result, dict) else "")
                or gateway_job_id
            )[:120]
            worker_id = str(
                (gateway_result.get("worker_id") if isinstance(gateway_result, dict) else "")
                or INSTANCE_ID
                or "local-training-gateway"
            )[:120]
            now_iso = datetime.utcnow().isoformat() + "Z"
            record["status"] = gateway_status
            record["updated_at"] = now_iso
            record["gateway_result"] = safe_gateway_result if isinstance(safe_gateway_result, dict) else {}
            events = record.get("events") if isinstance(record.get("events"), list) else []
            events.append({
                "ts": now_iso,
                "type": "gateway_accepted" if accepted else "gateway_rejected",
                "message": "training job forwarded to local training gateway" if accepted else "training gateway rejected job",
            })
            record["events"] = events[-200:]
            _write_training_job_record(normalized["job_id"], record)
            return True, {
                "accepted": accepted,
                "status": gateway_status,
                "gateway_job_id": gateway_job_id,
                "worker_id": worker_id,
                "job_id": normalized["job_id"],
                "state_stored": True,
                "mode": "training_gateway",
                "result": safe_gateway_result,
            }, None
        if _should_run_local_training_job(normalized):
            try:
                _append_training_job_event(record, "local_runner_queued", "training job queued for local QLoRA runner")
                record["status"] = "running"
                record["worker_id"] = INSTANCE_ID or "local-bridge"
                record["mode"] = "bridge_local_qlora"
                record["dataset_package"] = _training_dataset_package_summary(normalized.get("dataset_package"))
                _write_training_job_record(normalized["job_id"], record)
                _start_local_training_job(normalized)
            except Exception as exc:
                now_iso = datetime.utcnow().isoformat() + "Z"
                record["status"] = "failed"
                record["updated_at"] = now_iso
                record["error"] = _redact_training_text(exc, limit=1000)
                _append_training_job_event(record, "local_runner_start_failed", record["error"])
                _write_training_job_record(normalized["job_id"], record)
                return False, None, {"message": record["error"]}
            return True, {
                "accepted": True,
                "status": "running",
                "gateway_job_id": gateway_job_id,
                "worker_id": INSTANCE_ID or "local-bridge",
                "job_id": normalized["job_id"],
                "state_stored": True,
                "mode": "bridge_local_qlora",
                "dataset_package": _training_dataset_package_summary(normalized.get("dataset_package")),
            }, None
        return True, {
            "accepted": True,
            "status": "running",
            "gateway_job_id": gateway_job_id,
            "worker_id": INSTANCE_ID or "local-bridge",
            "job_id": normalized["job_id"],
            "state_stored": True,
            "mode": "bridge_manifest",
        }, None
    if op == "training.get_job":
        job_id = payload.get("job_id")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        record = _load_training_job_record(job_id)
        if not record:
            return False, None, {"message": "training job not found"}
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway(
                    "GET",
                    f"/training/jobs/{urllib.parse.quote(job_id, safe='')}",
                    timeout=15,
                )
            except Exception as exc:
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
            safe_gateway_result = _redact_training_value(gateway_result)
            if isinstance(safe_gateway_result, dict):
                return True, {"job_id": job_id, **safe_gateway_result}, None
        return True, record, None
    if op == "training.cancel_job":
        job_id = payload.get("job_id")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        record = _load_training_job_record(job_id)
        if not record:
            return False, None, {"message": "training job not found"}
        cancel_result = None
        if _training_gateway_base_url():
            try:
                cancel_result = await _call_training_gateway(
                    "POST",
                    f"/training/jobs/{urllib.parse.quote(job_id, safe='')}/cancel",
                    payload,
                    timeout=15,
                )
            except Exception as exc:
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
        now_iso = datetime.utcnow().isoformat() + "Z"
        safe_cancel_result = _redact_training_value(cancel_result) if cancel_result is not None else {}
        status = str(
            (cancel_result.get("status") if isinstance(cancel_result, dict) else "")
            or "cancelled"
        )[:30]
        record["status"] = status
        record["updated_at"] = now_iso
        if isinstance(safe_cancel_result, dict) and safe_cancel_result:
            record["cancel_result"] = safe_cancel_result
        events = record.get("events") if isinstance(record.get("events"), list) else []
        events.append({"ts": now_iso, "type": "cancelled", "message": "training job cancelled by bridge"})
        record["events"] = events[-200:]
        try:
            _write_training_job_record(job_id, record)
        except Exception as exc:
            return False, None, {"message": str(exc)}
        result = {"cancelled": True, "job_id": job_id, "status": status}
        if isinstance(safe_cancel_result, dict):
            result.update(safe_cancel_result)
            result.setdefault("cancelled", True)
            result.setdefault("job_id", job_id)
            result.setdefault("status", status)
        return True, result, None
    if op == "training.stream_logs":
        job_id = payload.get("job_id")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway(
                    "GET",
                    f"/training/jobs/{urllib.parse.quote(job_id, safe='')}/logs",
                    timeout=15,
                )
            except Exception as exc:
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
            safe_gateway_result = _redact_training_value(gateway_result)
            if isinstance(safe_gateway_result, dict):
                return True, {"job_id": job_id, **safe_gateway_result}, None
        record = _load_training_job_record(job_id) or {}
        events = record.get("events") if isinstance(record.get("events"), list) else []
        return True, {"job_id": job_id, "status": record.get("status", "unknown"), "lines": events[-100:]}, None
    if op == "training.collect_result":
        job_id = payload.get("job_id")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        record = _load_training_job_record(job_id)
        if not record:
            return False, None, {"message": "training job not found"}
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway(
                    "GET",
                    f"/training/jobs/{urllib.parse.quote(job_id, safe='')}/result",
                    timeout=15,
                )
            except Exception as exc:
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
            safe_gateway_result = _redact_training_value(gateway_result)
            if isinstance(safe_gateway_result, dict):
                return True, {"job_id": job_id, **safe_gateway_result}, None
        return True, {
            "job_id": job_id,
            "status": record.get("status", "unknown"),
            "progress": record.get("progress"),
            "worker_id": record.get("worker_id") or INSTANCE_ID or "local-bridge",
            "metrics": record.get("metrics") if isinstance(record.get("metrics"), dict) else {},
            "artifacts": record.get("artifacts") if isinstance(record.get("artifacts"), list) else [],
            "error": record.get("error"),
            "failure_stage": record.get("failure_stage"),
        }, None
    if op == "training.download_artifact":
        job_id = payload.get("job_id")
        artifact_id = payload.get("artifact_id")
        artifact_name = payload.get("artifact_name") or payload.get("name")
        expected_sha = payload.get("sha256") or payload.get("artifact_sha256")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        if not _valid_training_artifact_id(artifact_id):
            return False, None, {"message": "invalid artifact_id"}
        if _training_gateway_base_url():
            try:
                gateway_result = await _call_training_gateway_download(
                    "GET",
                    (
                        f"/training/jobs/{urllib.parse.quote(job_id, safe='')}"
                        f"/artifacts/{urllib.parse.quote(artifact_id, safe='')}/download"
                    ),
                    timeout=60,
                )
            except Exception as exc:
                return False, None, {"message": _redact_training_text(exc, limit=1000)}
            safe_gateway_result = _redact_training_value(gateway_result)
            if isinstance(safe_gateway_result, dict):
                safe_gateway_result.setdefault("job_id", job_id)
                safe_gateway_result.setdefault("artifact_id", artifact_id)
                return True, safe_gateway_result, None
        record = _load_training_job_record(job_id) or {}
        item = _training_artifact_find_record(
            record,
            artifact_id,
            artifact_name=artifact_name,
            sha256=expected_sha,
        )
        if isinstance(item, dict):
            safe_item = _redact_training_value(item)
            content_b64 = safe_item.get("content_base64") or safe_item.get("base64")
            if content_b64:
                return True, {
                    "job_id": job_id,
                    "artifact_id": artifact_id,
                    "filename": safe_item.get("name") or artifact_id,
                    "content_type": safe_item.get("content_type") or safe_item.get("mime_type") or "application/octet-stream",
                    "content_base64": content_b64,
                    "sha256": safe_item.get("sha256") or "",
                    "size_bytes": safe_item.get("size_bytes") or safe_item.get("bytes") or 0,
                }, None
            file_payload, file_error = _training_artifact_file_payload(job_id, artifact_id, item, include_content=True)
            if isinstance(file_payload, dict):
                return True, file_payload, None
            return False, None, {"message": file_error or "training artifact download not available"}
        return False, None, {"message": "training artifact download not available"}
    if op == "training.artifact_status":
        job_id = payload.get("job_id")
        artifact_id = payload.get("artifact_id")
        artifact_name = payload.get("artifact_name") or payload.get("name")
        expected_sha = payload.get("sha256") or payload.get("artifact_sha256")
        if not _valid_training_job_id(job_id):
            return False, None, {"message": "invalid job_id"}
        if not _valid_training_artifact_id(artifact_id):
            return False, None, {"message": "invalid artifact_id"}
        record = _load_training_job_record(job_id) or {}
        item = _training_artifact_find_record(
            record,
            artifact_id,
            artifact_name=artifact_name,
            sha256=expected_sha,
        )
        if not isinstance(item, dict):
            return True, {
                "job_id": job_id,
                "artifact_id": artifact_id,
                "exists": False,
                "reason": "training artifact record not found",
            }, None
        file_payload, file_error = _training_artifact_file_payload(job_id, artifact_id, item, include_content=False)
        if isinstance(file_payload, dict):
            return True, file_payload, None
        return True, {
            "job_id": job_id,
            "artifact_id": artifact_id,
            "exists": False,
            "reason": file_error or "training artifact file not found",
        }, None
    if op == "notify_decision":
        # SkillForge 决策完成 → 写 decision JSON 到 ~/.GATEWAY/decisions/RUNID/KEY.json
        skill_run_id = payload.get("run_id") or "unknown"
        request_id = payload.get("request_id") or ""
        next_step = payload.get("next_step") or ""
        cap = discover_local_capabilities()
        gateway_kind = cap.get("gateway_kind", "aiclaw")
        base_dir = Path.home() / f".{gateway_kind}" / "decisions" / skill_run_id
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            key = next_step or request_id or "decision"
            safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")
            if not safe_key:
                safe_key = "decision"
            target = base_dir / (safe_key + ".json")
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return True, {"path": str(target), "key": safe_key}, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "read_skill":
        skill_id = payload.get("skill_id")
        # 校验 skill_id 不含路径分隔符 / .. 等危险字符（防 path traversal）
        if not _valid_skill_id(skill_id):
            return False, None, {"message": "invalid skill_id"}
        seen_path = None
        seen_root = None  # resolve 后的 skill 根目录，用于逃逸校验
        for d in _candidate_skill_dirs(payload):
            try:
                base = Path(d).resolve(strict=False)
                p = base / skill_id
                if p.exists() and p.is_dir() and not p.is_symlink():
                    resolved = p.resolve(strict=False)
                    # 确保 resolved 仍在 base 之下（防 base/skill_id 本身是 symlink 跳出）
                    try:
                        resolved.relative_to(base)
                    except ValueError:
                        continue
                    seen_path = p
                    seen_root = resolved
                    break
            except Exception:
                continue
        if not seen_path or not seen_root:
            return False, None, {"message": f"skill {skill_id} not found in any local skills dir"}
        try:
            files = []
            total_bytes = 0
            MAX = 5 * 1024 * 1024
            # followlinks=False 防止 walk 跟随子目录 symlink 逃逸
            for root, dirs, fnames in os.walk(seen_root, followlinks=False):
                # 过滤 symlink 子目录（os.walk 默认就不进入，但显式 dirs 过滤更稳）
                dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
                for fname in fnames:
                    if fname.startswith(".git"):
                        continue
                    full = Path(root) / fname
                    # 跳过 symlink 文件，防止跟随读到 skill 目录外
                    if full.is_symlink():
                        continue
                    # 双保险：resolve 后必须仍在 seen_root 之下
                    try:
                        full_resolved = full.resolve(strict=True)
                        full_resolved.relative_to(seen_root)
                    except (ValueError, OSError):
                        continue
                    rel = str(full.relative_to(seen_root)).replace(os.sep, "/")
                    try:
                        content = full.read_bytes()
                    except Exception:
                        continue
                    total_bytes += len(content)
                    if total_bytes > MAX:
                        return False, None, {"message": f"skill 总大小超过 {MAX} 字节"}
                    files.append({"path": rel, "content_b64": base64.b64encode(content).decode("ascii")})
            return True, {"skill_id": skill_id, "source_dir": str(seen_root), "files": files, "count": len(files)}, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "sync_schedules":
        try:
            current = _load_schedules()
            data = {
                "schedules": payload.get("schedules") or [],
                "submit_token": payload.get("submit_token", ""),
                "submit_url": payload.get("submit_url", ""),
                "config_version": payload.get("config_version", 0),
                "updated_at": time.time(),
            }
            if _schedule_snapshot_equivalent(current, data):
                data["config_version"] = current.get("config_version", data["config_version"])
                data["updated_at"] = current.get("updated_at", data["updated_at"])
            _save_schedules(data)
            _get_schedule_reload_event().set()
            return True, {"accepted": len(data["schedules"]),
                          "config_version": data["config_version"]}, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "force_update":
        try:
            check_and_apply_update(force_restart=True)
            return True, {"message": "update triggered"}, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    if op == "debug_capabilities":
        try:
            cap = discover_local_capabilities()
            # 附加调试信息：HOME / 用户 / 候选目录探测详情
            home = str(Path.home())
            import getpass as _getpass
            user = _getpass.getuser()
            probe = {"home": home, "user": user, "uid": os.getuid(), "gid": os.getgid()}
            for p in cap.get("skills_dirs", []):
                probe[f"exists:{p}"] = Path(p).exists()
                probe[f"write:{p}"] = os.access(p, os.W_OK) if Path(p).exists() else False
            # 默认候选
            for name, p in [("aiclaw", str(Path.home() / ".aiclaw" / "skills")),
                            ("openclaw", str(Path.home() / ".openclaw" / "skills")),
                            ("agents", str(Path.home() / ".agents" / "skills"))]:
                probe[f"exists:{p}"] = Path(p).exists()
            # 配置文件
            for name, p in [("aiclaw_json", str(Path.home() / ".aiclaw" / "aiclaw.json")),
                            ("openclaw_json", str(Path.home() / ".openclaw" / "openclaw.json"))]:
                probe[f"cfg:{name}"] = Path(p).exists()
            # 自动更新调试
            probe["SKILLFORGE_HTTP_BASE"] = SKILLFORGE_HTTP_BASE
            probe["SKILLFORGE_WS_URL"] = SKILLFORGE_WS_URL
            probe["installed_hash"] = _installed_hash()
            probe["server_hash"] = fetch_server_script_hash() or "(fetch failed)"
            try:
                probe["mlx_python_candidates"] = [
                    {
                        "python": str(candidate),
                        "has_mlx_lm": _python_has_module(candidate, "mlx_lm"),
                        "mlx_lm": _python_module_probe(candidate, "mlx_lm"),
                        "qwen3_5_moe": _python_module_probe(candidate, "mlx_lm.models.qwen3_5_moe"),
                    }
                    for candidate in _mlx_python_candidates(sys.executable)[:12]
                ]
            except Exception as exc:  # noqa: BLE001
                probe["mlx_python_candidates_error"] = _redact_training_text(exc, limit=500)
            probe["pid"] = os.getpid()
            probe["pid_path"] = str(PID_PATH)
            probe["lock_path"] = str(LOCK_PATH)
            try:
                probe["pid_file"] = PID_PATH.read_text(encoding="utf-8").strip() if PID_PATH.exists() else ""
            except Exception:
                probe["pid_file"] = ""
            try:
                schedules = _load_schedules()
                probe["schedules_config_version"] = schedules.get("config_version")
                probe["schedules_updated_at"] = schedules.get("updated_at")
                probe["schedules_count"] = len(schedules.get("schedules") or [])
                probe["schedules"] = schedules.get("schedules") or []
            except Exception as exc:  # noqa: BLE001
                probe["schedules_error"] = str(exc)
            probe["scheduler_state"] = _scheduler_state
            cap["_debug"] = probe
            return True, cap, None
        except Exception as exc:
            return False, None, {"message": str(exc)}
    return False, None, {"message": f"unknown op: {op}"}


async def auth_skillforge(sf_ws, enrollment_token):
    private_key = load_or_create_key()
    pubkey = export_pubkey(private_key)
    await sf_ws.send(
        json.dumps(
            {
                "type": "auth_init",
                "instance_id": INSTANCE_ID,
                "pubkey": pubkey,
                "enrollment_token": enrollment_token,
                "fingerprint": _bridge_fingerprint(),
                "platform": os.uname().sysname,
            }
        )
    )
    challenge = json.loads(await asyncio.wait_for(sf_ws.recv(), timeout=10))
    if challenge.get("type") != "auth_challenge":
        raise RuntimeError(f"auth init failed: {challenge}")
    signature = base64.b64encode(
        private_key.sign(challenge["nonce"].encode("utf-8"))
    ).decode("utf-8")
    await sf_ws.send(
        json.dumps(
            {
                "type": "auth_response",
                "instance_id": INSTANCE_ID,
                "signature": signature,
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(sf_ws.recv(), timeout=10))
    if ack.get("type") not in {"auth_ok", "registered", "register_ok"}:
        raise RuntimeError(f"auth failed: {ack}")
    epoch_msg = json.loads(await asyncio.wait_for(sf_ws.recv(), timeout=10))
    if epoch_msg.get("type") != "epoch_bound":
        raise RuntimeError(f"epoch bind failed: {epoch_msg}")
    # auth 成功后立即上报本机能力
    try:
        await sf_ws.send(json.dumps(discover_local_capabilities()))
    except Exception:
        pass
    return epoch_msg.get("epoch", 1)


async def run_bridge():
    current_token = ENROLLMENT_TOKEN
    _reconnect_delay = 3  # 初始重连延迟秒数
    _max_reconnect_delay = 120  # 最大延迟
    while True:
        local_ws = None
        try:
            # 先连 SkillForge server 注册在线；本地 Gateway 不可达时仍需在线以便 server 下发指令
            logger = setup_logging()
            logger.info("bridge 正在连接 %s ...", SKILLFORGE_WS_URL)
            async with websockets.connect(
                SKILLFORGE_WS_URL,
                open_timeout=10,
                close_timeout=5,
                max_size=64 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            ) as sf_ws:
                current_epoch = await auth_skillforge(sf_ws, current_token)
                request_epochs = {}
                request_methods = {}
                # 连接认证成功，重置重连退避延迟
                _reconnect_delay = 3
                logger = setup_logging()
                logger.info("bridge 已连接 (epoch=%s)", current_epoch)
                send_lock = asyncio.Lock()
                bridge_tasks = set()
                bridge_op_chunks = {}
                local_state = {"ws": None}

                async def send_skillforge(payload: dict):
                    async with send_lock:
                        await sf_ws.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

                async def send_bridge_op_response(request_id: str, epoch: int, ok: bool, result: dict | None, error: dict | None):
                    result = result or {}
                    if ok:
                        encoding, chunks = _bridge_transport_chunks(result)
                        if len(chunks) == 1 and encoding == "json":
                            await send_skillforge({
                                "type": "bridge_op_response",
                                "request_id": request_id,
                                "epoch": epoch,
                                "ok": True,
                                "result": result,
                                "error": error,
                            })
                            return
                        chunk_count = len(chunks)
                        for chunk_index, chunk in enumerate(chunks):
                            await send_skillforge({
                                "type": "bridge_op_response_chunk",
                                "request_id": request_id,
                                "epoch": epoch,
                                "encoding": encoding,
                                "chunk_index": chunk_index,
                                "chunk_count": chunk_count,
                                "data": chunk,
                            })
                        return
                    await send_skillforge({
                        "type": "bridge_op_response",
                        "request_id": request_id,
                        "epoch": epoch,
                        "ok": False,
                        "result": result,
                        "error": error,
                    })

                def schedule_bridge_op(op_msg: dict):
                    async def _run_bridge_op(inner_msg: dict):
                        ok, result, error = await handle_bridge_op(
                            inner_msg.get("op"),
                            inner_msg.get("payload") or {},
                        )
                        await send_bridge_op_response(
                            request_id=inner_msg.get("request_id"),
                            epoch=inner_msg.get("epoch"),
                            ok=ok,
                            result=result or {},
                            error=error,
                        )

                    task = asyncio.create_task(_run_bridge_op(dict(op_msg)))
                    bridge_tasks.add(task)
                    task.add_done_callback(bridge_tasks.discard)

                async def forward_local():
                    local_delay = 3
                    while True:
                        ws = None
                        try:
                            ws = await connect_local()
                            local_state["ws"] = ws
                            local_delay = 3
                            async for raw in ws:
                                msg = json.loads(raw)
                                if msg.get("type") == "res":
                                    req_id = msg.get("id")
                                    payload = msg.get("result", msg.get("payload", {}))
                                    if request_methods.get(req_id) == "skills.status":
                                        try:
                                            payload = _merge_skills_status_payload(payload)
                                        except Exception as exc:
                                            logger = setup_logging()
                                            logger.warning("合并本地 managed skills 失败: %s", exc)
                                    await send_skillforge({
                                        "type": "forward_response",
                                        "request_id": req_id,
                                        "epoch": request_epochs.get(req_id),
                                        "ok": msg.get("ok", msg.get("error") is None),
                                        "payload": payload,
                                        "error": msg.get("error"),
                                    })
                                elif msg.get("type") == "event":
                                    await send_skillforge({
                                        "type": "forward_event",
                                        "epoch": request_epochs.get(msg.get("request_id")) or current_epoch,
                                        "event": msg.get("event"),
                                        "payload": msg.get("payload", {}),
                                    })
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger = setup_logging()
                            logger.warning("本地 Gateway 暂不可达 (%s)，%ds 后重试", exc, local_delay)
                            await asyncio.sleep(local_delay)
                            local_delay = min(local_delay * 2, 60)
                        finally:
                            if local_state.get("ws") is ws:
                                local_state["ws"] = None
                            if ws is not None:
                                try:
                                    await ws.close()
                                except Exception:
                                    pass

                async def forward_skillforge():
                    last_cap_hash = ""

                    async def _maybe_re_report_capabilities():
                        """重新探测本机能力，有变化时上报 server。"""
                        nonlocal last_cap_hash
                        try:
                            cap = discover_local_capabilities()
                            cap_json = json.dumps(cap, sort_keys=True)
                            cap_hash = hashlib.md5(cap_json.encode()).hexdigest()
                            if cap_hash != last_cap_hash:
                                last_cap_hash = cap_hash
                                await send_skillforge(cap)
                        except Exception:
                            pass

                    async def _cap_report_loop():
                        while True:
                            await asyncio.sleep(60)
                            await _maybe_re_report_capabilities()

                    async def _update_check_loop():
                        """定期检查更新，有新版本时 force_restart 自动重启进程。"""
                        while True:
                            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
                            try:
                                loop = asyncio.get_running_loop()
                                await loop.run_in_executor(
                                    None, check_and_apply_update, True,
                                )
                            except SystemExit:
                                raise
                            except Exception as exc:  # noqa: BLE001
                                logger = setup_logging()
                                logger.warning("periodic update check 失败: %s", exc)

                    cap_task = asyncio.create_task(_cap_report_loop())
                    update_task = asyncio.create_task(_update_check_loop())
                    try:
                        async for raw in sf_ws:
                            msg = json.loads(raw)
                            if msg.get("type") == "forward_request":
                                method = msg.get("method", "")
                                req_id = msg.get("request_id")
                                request_epochs[req_id] = msg.get("epoch")
                                request_methods[req_id] = method

                                # skills.reload 由 bridge 本地处理 — 文件已被
                                # install_skill 写入磁盘，无需 gateway 支持热重载。
                                if method == "skills.reload":
                                    _reload_ok = False
                                    try:
                                        cap = discover_local_capabilities()
                                        sd = cap.get("skills_dir_default", "")
                                        _reload_ok = bool(sd) and Path(sd).exists()
                                    except Exception:
                                        pass
                                    await send_skillforge({
                                        "type": "forward_response",
                                        "request_id": msg.get("request_id"),
                                        "epoch": msg.get("epoch"),
                                        "ok": _reload_ok,
                                        "payload": {
                                            "status": "ok" if _reload_ok else "failed",
                                            "ok": _reload_ok,
                                        },
                                        "error": None if _reload_ok else "skills directory not found",
                                    })
                                    continue

                                active_local_ws = local_state.get("ws")
                                if active_local_ws is not None:
                                    try:
                                        await active_local_ws.send(
                                            json.dumps(
                                                {
                                                    "type": "req",
                                                    "method": method,
                                                    "id": msg.get("request_id"),
                                                    "params": msg.get("params", {}),
                                                }
                                            )
                                        )
                                    except Exception:
                                        local_state["ws"] = None
                                        await send_skillforge({
                                            "type": "forward_response",
                                            "request_id": req_id,
                                            "epoch": msg.get("epoch"),
                                            "ok": False,
                                            "payload": {},
                                            "error": "local gateway offline",
                                        })
                                elif method == "skills.status":
                                    await send_skillforge({
                                        "type": "forward_response",
                                        "request_id": req_id,
                                        "epoch": msg.get("epoch"),
                                        "ok": True,
                                        "payload": _merge_skills_status_payload({"skills": []}),
                                        "error": None,
                                    })
                                else:
                                    await send_skillforge({
                                        "type": "forward_response",
                                        "request_id": req_id,
                                        "epoch": msg.get("epoch"),
                                        "ok": False,
                                        "payload": {},
                                        "error": "local gateway offline",
                                    })
                            elif msg.get("type") == "bridge_op":
                                schedule_bridge_op(msg)
                            elif msg.get("type") == "bridge_op_chunk":
                                request_id = msg.get("request_id") or ""
                                chunk_count = int(msg.get("chunk_count") or 0)
                                chunk_index = int(msg.get("chunk_index") or 0)
                                state = bridge_op_chunks.get(request_id)
                                if state is None:
                                    state = {
                                        "epoch": msg.get("epoch"),
                                        "op": msg.get("op"),
                                        "encoding": msg.get("encoding") or "json",
                                        "chunk_count": chunk_count,
                                        "parts": {},
                                    }
                                    bridge_op_chunks[request_id] = state
                                if (
                                    state.get("epoch") != msg.get("epoch")
                                    or state.get("op") != msg.get("op")
                                    or state.get("encoding") != (msg.get("encoding") or "json")
                                    or state.get("chunk_count") != chunk_count
                                ):
                                    bridge_op_chunks.pop(request_id, None)
                                    await send_bridge_op_response(
                                        request_id=request_id,
                                        epoch=msg.get("epoch"),
                                        ok=False,
                                        result={},
                                        error={"message": "bridge_op chunk metadata mismatch"},
                                    )
                                    continue
                                state["parts"][chunk_index] = msg.get("data") or ""
                                if len(state["parts"]) < chunk_count:
                                    continue
                                try:
                                    payload = _bridge_transport_decode(
                                        state["parts"],
                                        chunk_count=state["chunk_count"],
                                        encoding=state["encoding"],
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    await send_bridge_op_response(
                                        request_id=request_id,
                                        epoch=msg.get("epoch"),
                                        ok=False,
                                        result={},
                                        error={"message": f"bridge_op chunk decode failed: {exc}"},
                                    )
                                else:
                                    schedule_bridge_op({
                                        "request_id": request_id,
                                        "epoch": state["epoch"],
                                        "op": state["op"],
                                        "payload": payload,
                                    })
                                finally:
                                    bridge_op_chunks.pop(request_id, None)
                            elif msg.get("type") == "ping":
                                if msg.get("rotate_required") and msg.get("rotation_token"):
                                    rotate_key_material()
                                    current_token = msg["rotation_token"]
                                    await sf_ws.close()
                                    return
                                await send_skillforge({"type": "pong"})
                    finally:
                        cap_task.cancel()
                        update_task.cancel()
                        for task in list(bridge_tasks):
                            task.cancel()

                await asyncio.gather(forward_local(), forward_skillforge())
                if getattr(sf_ws, "close_code", None) == 1000 and getattr(sf_ws, "close_reason", "") == "replaced":
                    logger.warning("bridge 连接已被同 instance 新连接替换，当前进程退出 instance=%s", INSTANCE_ID)
                    return
        except Exception as exc:
            logger = setup_logging()
            close_reason = str(getattr(exc, "reason", "") or "")
            close_code = getattr(exc, "code", None)
            if not close_code:
                rcvd = getattr(exc, "rcvd", None)
                close_code = getattr(rcvd, "code", None)
                close_reason = close_reason or str(getattr(rcvd, "reason", "") or "")
            if close_code == 1000 and close_reason == "replaced":
                logger.warning("bridge 连接已被同 instance 新连接替换，当前进程退出 instance=%s", INSTANCE_ID)
                return
            logger.warning("bridge reconnect after error: %s (下次重连 %ds 后)", exc, _reconnect_delay)
            await asyncio.sleep(_reconnect_delay)
            _reconnect_delay = min(_reconnect_delay * 2, _max_reconnect_delay)
        finally:
            if local_ws is not None:
                try:
                    await local_ws.close()
                except Exception:
                    pass


# ═════════════════════════════════════════════════════════════════════════════
#  日志
# ═════════════════════════════════════════════════════════════════════════════

_logger: logging.Logger | None = None


def setup_logging(to_file: bool = True) -> logging.Logger:
    """初始化 logging。file handler 到 STATE_DIR/bridge.log + console。"""
    global _logger
    if _logger is not None:
        return _logger
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("skillforge-bridge")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if to_file:
        try:
            fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:  # noqa: BLE001
            pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False
    _logger = logger
    return logger


# ═════════════════════════════════════════════════════════════════════════════
#  依赖引导（首次运行时 pip install + re-exec）
# ═════════════════════════════════════════════════════════════════════════════

CORE_DEPS = ["websockets", "cryptography"]
TRAY_DEPS = ["pystray", "Pillow"]


def _venv_python() -> Path:
    vp = STATE_DIR / "venv" / "bin" / "python3"
    if not vp.exists():
        vp = STATE_DIR / "venv" / "bin" / "python"
    return vp


def _ensure_venv() -> Path:
    vp = _venv_python()
    if vp.exists():
        return vp
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    venv_dir = STATE_DIR / "venv"
    logger = setup_logging()
    logger.info("创建 venv: %s", venv_dir)
    # 先尝试带 ensurepip 创建（大多数系统可用）
    result = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], capture_output=True, text=True)
    if result.returncode != 0 or not vp.exists():
        # ensurepip 不可用（精简 Python / Ubuntu 最小安装），用 --without-pip 后手动装 pip
        logger.info("venv --without-pip（系统无 ensurepip），将用 get-pip.py 安装 pip")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv_dir)], check=True)
    vp = _venv_python()
    if not vp.exists():
        raise RuntimeError(f"venv 创建失败: {venv_dir}")
    return vp


def _ensure_pip_in_venv(vp: Path) -> None:
    """确保 venv 里有 pip，没有就用 get-pip.py 装入。"""
    pip_check = subprocess.run([str(vp), "-m", "pip", "--version"], capture_output=True, text=True)
    if pip_check.returncode == 0:
        return
    logger = setup_logging()
    logger.info("venv 中无 pip，用 get-pip.py 安装...")
    # 用当前系统 Python 下载并执行 get-pip.py 到 venv 中
    get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
    get_pip_script = STATE_DIR / "get-pip.py"
    try:
        urllib.request.urlretrieve(get_pip_url, str(get_pip_script))
    except Exception:
        # 网络不通时用系统 pip 的 wheel 作为后备（极少见）
        logger.warning("下载 get-pip.py 失败，尝试用系统 pip 直接安装...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--target",
                         str(STATE_DIR / "venv" / "lib"), "pip"], capture_output=True, text=True)
        return
    subprocess.run([str(vp), str(get_pip_script)], check=True, capture_output=True, text=True, timeout=120)
    logger.info("pip 安装完成")


def bootstrap_dependencies(need_tray: bool = False) -> bool:
    """检查 + 安装依赖；如果刚装了东西会 os.execv 重启自己（不会返回 True）。"""
    logger = setup_logging()
    missing: list[str] = []
    try:
        import websockets as _  # noqa: F401
        import cryptography as _c  # noqa: F401
    except ImportError:
        missing.extend(CORE_DEPS)
    if need_tray:
        try:
            import pystray as _p  # noqa: F401
            import PIL as _pil  # noqa: F401
        except ImportError:
            missing.extend(TRAY_DEPS)
    if not missing:
        return False
    logger.info("缺少依赖: %s, 开始安装...", ", ".join(missing))
    vp = _ensure_venv()
    _ensure_pip_in_venv(vp)
    try:
        subprocess.run([str(vp), "-m", "pip", "install", *missing], check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        logger.error("pip install 失败: %s\n%s", exc, (exc.stderr or b"").decode("utf-8", errors="replace"))
        raise
    logger.info("依赖安装完成，切换到 venv 重启: %s", vp)
    # 不要用 .resolve() 比较：macOS 上 venv 的 bin/python3 是到系统 python 的 symlink，
    # resolve 后会跟系统 python 路径相等 → execv 被跳过 → 当前进程仍然用系统 python、
    # 看不到 venv 的 site-packages，import cryptography 仍然失败。
    # 改成字面路径比较：只要可执行路径字符串不同就切。
    if str(sys.executable) != str(vp):
        os.execv(str(vp), [str(vp), *sys.argv])
    return True


def _restart_python_executable() -> str:
    """Return a usable Python executable for self re-exec.

    Some old installs were launched through a project venv path that can later
    disappear while the process is still alive. In that case sys.executable can
    point at a dead path and self-update writes the new script but fails to
    restart into it. Fall back to a stable interpreter on PATH.
    """
    candidates = [
        sys.executable,
        os.environ.get("PYTHON"),
        shutil.which("python3"),
        shutil.which("python"),
        "/usr/bin/python3",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if Path(candidate).exists():
                return str(candidate)
        except Exception:
            continue
    return sys.executable


def _machine_id_hash() -> str:
    candidates = [
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
        Path.home() / ".skillforge_bridge" / "machine-id",
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if value:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    seed = f"{platform.node()}:{Path.home()}:{os.getuid()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _bridge_fingerprint() -> str:
    return f"host:{socket.gethostname()}|machine:{_machine_id_hash()}"


def _exec_self() -> None:
    python = _restart_python_executable()
    os.execv(python, [python, *sys.argv])


def _repair_systemd_unit_execstart() -> None:
    """Self-heal Linux units whose Python path disappeared after install."""
    if not sys.platform.startswith("linux") or not INSTANCE_ID:
        return
    unit_slug = hashlib.md5((INSTANCE_ID or "default").encode()).hexdigest()[:8]
    unit_path = Path.home() / ".config" / "systemd" / "user" / f"skillforge-bridge-{unit_slug}.service"
    if not unit_path.exists():
        return
    python = _restart_python_executable()
    expected = f"ExecStart={shlex.quote(python)} {shlex.quote(str(SCRIPT_CACHE_PATH))} --daemon"
    try:
        lines = unit_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    changed = False
    next_lines = []
    for line in lines:
        if line.startswith("ExecStart="):
            if line.strip() != expected:
                next_lines.append(expected)
                changed = True
            else:
                next_lines.append(line)
        else:
            next_lines.append(line)
    if not changed:
        return
    logger = setup_logging()
    try:
        unit_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
        _run_systemctl_user("daemon-reload")
        logger.info("systemd unit ExecStart 已修复为: %s", expected)
    except Exception as exc:  # noqa: BLE001
        logger.warning("systemd unit ExecStart 修复失败: %s", exc)


def _linux_unit_paths(instance_id: str) -> tuple[str, Path]:
    unit_slug = hashlib.md5((instance_id or "default").encode()).hexdigest()[:8]
    unit_name = f"skillforge-bridge-{unit_slug}.service"
    unit_path = Path.home() / ".config" / "systemd" / "user" / unit_name
    return unit_name, unit_path


def _linux_systemd_unit_text(instance_id: str, python: str) -> str:
    return SYSTEMD_UNIT_TEMPLATE.format(
        instance_id=instance_id or "default",
        python_quoted=shlex.quote(python),
        script_quoted=shlex.quote(str(SCRIPT_CACHE_PATH)),
        log=str(LOG_PATH),
    )


def _ensure_linux_autostart_from_daemon() -> None:
    """Repair boot autostart after online auto-update without requiring --install."""
    if not sys.platform.startswith("linux") or not INSTANCE_ID:
        return
    logger = setup_logging()
    python = _restart_python_executable()
    unit_name, unit_path = _linux_unit_paths(INSTANCE_ID or "default")
    try:
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        desired = _linux_systemd_unit_text(INSTANCE_ID or "default", python)
        current = unit_path.read_text(encoding="utf-8") if unit_path.exists() else ""
        if current != desired:
            unit_path.write_text(desired, encoding="utf-8")
            logger.info("daemon: systemd unit 已刷新: %s", unit_path)
        ok, detail = _run_systemctl_user_checked("daemon-reload")
        if not ok:
            raise RuntimeError(detail)
        ok, detail = _run_systemctl_user_checked("enable", unit_name)
        if not ok:
            raise RuntimeError(detail)
        linger_ok, linger_detail = _ensure_linger_enabled()
        if not linger_ok:
            logger.warning("daemon: linger 不可用，增加 crontab @reboot fallback: %s", linger_detail)
            _install_crontab_fallback(INSTANCE_ID or "default", python)
            save_installed_config(extra={
                "AUTOSTART_MODE": "systemd+crontab",
                "LAST_SYSTEMD_ERROR": None,
                "LAST_SYSTEMD_LINGER": linger_detail,
            })
        else:
            _remove_crontab_fallback(INSTANCE_ID or "default")
            save_installed_config(extra={
                "AUTOSTART_MODE": "systemd",
                "LAST_SYSTEMD_ERROR": None,
                "LAST_SYSTEMD_LINGER": linger_detail,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon: autostart repair failed: %s", exc)
        try:
            _install_crontab_fallback(INSTANCE_ID or "default", python)
            save_installed_config(extra={
                "AUTOSTART_MODE": "crontab",
                "LAST_SYSTEMD_ERROR": str(exc),
            })
        except Exception as fallback_exc:  # noqa: BLE001
            logger.warning("daemon: crontab fallback repair failed: %s", fallback_exc)


# ═════════════════════════════════════════════════════════════════════════════
#  配置持久化（env.json）
# ═════════════════════════════════════════════════════════════════════════════

def save_installed_config(server_hash: str | None = None, extra: dict | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # 保留既有值，仅覆盖变化的字段
    existing: dict = {}
    if ENV_PATH.exists():
        try:
            existing = json.loads(ENV_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}
    payload = {
        **existing,
        "INSTANCE_ID": INSTANCE_ID,
        "ENROLLMENT_TOKEN": ENROLLMENT_TOKEN,
        "SKILLFORGE_WS_URL": SKILLFORGE_WS_URL,
        "SKILLFORGE_HTTP_BASE": SKILLFORGE_HTTP_BASE,
        "LOCAL_AICLAW_URL": LOCAL_AICLAW_URL,
        "LOCAL_AICLAW_TOKEN_FALLBACK": LOCAL_AICLAW_TOKEN_FALLBACK,
        "LOCAL_TRAINING_GATEWAY_URL": LOCAL_TRAINING_GATEWAY_URL,
        "LOCAL_TRAINING_GATEWAY_TOKEN": LOCAL_TRAINING_GATEWAY_TOKEN,
        "LOCAL_INTELLIGENCE_GATEWAY_URL": LOCAL_INTELLIGENCE_GATEWAY_URL,
        "LOCAL_INTELLIGENCE_GATEWAY_TOKEN": LOCAL_INTELLIGENCE_GATEWAY_TOKEN,
        "BRIDGE_PLATFORM_TARGET": BRIDGE_PLATFORM_TARGET,
        "BRIDGE_VERSION_INSTALLED": BRIDGE_VERSION,
    }
    if server_hash:
        payload["BRIDGE_SCRIPT_HASH"] = server_hash
    if extra:
        for k, v in extra.items():
            if v is None:
                payload.pop(k, None)
            else:
                payload[k] = v
    ENV_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)


def _installed_hash() -> str:
    """从 env.json 读上次下载应用的 server hash，没有则返回空。"""
    if not ENV_PATH.exists():
        return ""
    try:
        return (json.loads(ENV_PATH.read_text(encoding="utf-8")) or {}).get("BRIDGE_SCRIPT_HASH", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def load_installed_config() -> dict:
    if not ENV_PATH.exists():
        return {}
    try:
        data = json.loads(ENV_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    for k, v in data.items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)
    return data


# ═════════════════════════════════════════════════════════════════════════════
#  自动更新（启动时 + 运行中周期性）
# ═════════════════════════════════════════════════════════════════════════════

def _current_script_hash() -> str:
    try:
        return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def _platform_query() -> str | None:
    """取目标平台查询参数值。server 侧的 /bridge/script* 端点必须带 platform，
    没有就说明本地 BRIDGE_PLATFORM_TARGET 丢了（老版本脚本 env.json 没写），
    主动返回 None 让调用方跳过更新，不走错误流程。
    """
    p = (BRIDGE_PLATFORM_TARGET or "").strip().lower()
    if p in ("linux", "darwin"):
        return p
    return None


def _sign_self_update_challenge(platform: str) -> tuple[str, str] | None:
    """生成自更新请求的 (ts, sig) 用 device_privkey 对 `{instance}:{plat}:{ts}` 签名。

    加密库未就绪时返回 None，上层调用会跳过更新循环（不会误阻塞 bridge 主流程）。
    """
    try:
        private_key = load_or_create_key()
    except Exception:
        return None
    ts = str(int(time.time()))
    challenge = f"{INSTANCE_ID}:{platform}:{ts}"
    try:
        sig = base64.b64encode(
            private_key.sign(challenge.encode("utf-8"))
        ).decode("ascii")
    except Exception:
        return None
    return ts, sig


def fetch_server_script_hash() -> str | None:
    if not SKILLFORGE_HTTP_BASE:
        return None
    plat = _platform_query()
    if not plat:
        return None
    signed = _sign_self_update_challenge(plat)
    if not signed:
        return None
    ts, sig = signed
    url = (
        f"{SKILLFORGE_HTTP_BASE.rstrip('/')}/api/aiclaw/bridge/script-hash"
        f"?platform={plat}"
        f"&instance_id={urllib.parse.quote(INSTANCE_ID, safe='')}"
        f"&ts={ts}"
        f"&sig={urllib.parse.quote(sig, safe='')}"
    )
    try:
        with urllib.request.urlopen(url, timeout=UPDATE_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("hash")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def fetch_server_script() -> bytes | None:
    if not SKILLFORGE_HTTP_BASE:
        return None
    plat = _platform_query()
    if not plat:
        return None
    signed = _sign_self_update_challenge(plat)
    if not signed:
        return None
    ts, sig = signed
    url = (
        f"{SKILLFORGE_HTTP_BASE.rstrip('/')}/api/aiclaw/bridge/script"
        f"?instance_id={urllib.parse.quote(INSTANCE_ID, safe='')}"
        f"&platform={plat}"
        f"&ts={ts}"
        f"&sig={urllib.parse.quote(sig, safe='')}"
    )
    try:
        with urllib.request.urlopen(url, timeout=UPDATE_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError):
        return None


def _preserved_update_header(current_lines: list[str]) -> str:
    """保留 server render 的配置块，忽略源码里的 marker 字符串字面量。"""
    header_idx = next(
        (
            i for i, line in enumerate(current_lines)
            if line.lstrip().startswith(UPDATE_HEADER_MARKER)
        ),
        -1,
    )
    return "".join(current_lines[: header_idx + 1]) + "\n" if header_idx >= 0 else ""


def check_and_apply_update(force_restart: bool = True) -> bool:
    """查 server hash，与 env.json 里持久化的 hash 不一致就下载新版覆盖自身。

    对比逻辑用 env.json 里的 BRIDGE_SCRIPT_HASH（上次成功下载时写入），不用本地文件
    hash —— 因为渲染后的脚本顶部有 `_BRIDGE_CONFIG` 配置块，文件 hash 永远 ≠ server 端
    纯源码 hash，否则会进入无限更新循环。

    渲染后的脚本顶部有配置块（_BRIDGE_CONFIG），不能直接用 server 的纯源码覆盖；
    本函数保留当前文件的配置块（到 '=== bridge 主程序源码' 标志为止），然后把 server
    返回的主程序源码（去掉 shebang）拼到配置块之后，原子写回。
    """
    logger = setup_logging()
    server_hash = fetch_server_script_hash()
    if not server_hash:
        return False
    installed_hash = _installed_hash()
    if server_hash == installed_hash:
        # 即使脚本无需更新，也刷新 env.json 里的运行版本，避免历史本地试验版本残留误导 UI/排障。
        try:
            save_installed_config(server_hash=server_hash)
        except Exception as exc:  # noqa: BLE001
            logger.debug("update: refresh installed config skipped: %s", exc)
        return False
    logger.info("update: 发现新版本 server=%s installed=%s", server_hash, installed_hash or "(首次)")
    new_body = fetch_server_script()
    if not new_body:
        logger.warning("update: 下载失败")
        return False

    self_path = Path(__file__).resolve()
    try:
        current = self_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("update: 读取当前脚本失败: %s", exc)
        return False
    header = _preserved_update_header(current)

    new_text = new_body.decode("utf-8")
    new_lines = new_text.splitlines(keepends=True)
    if new_lines and new_lines[0].startswith("#!"):
        new_lines = new_lines[1:]
    # 如果 header 里已经有 `from __future__ import annotations`（server render 注入的），
    # body 里 bridge 源的同语句必须去掉 —— Python 规定 future import 全文件只能一次，
    # 否则拼接出来的脚本报 SyntaxError。
    if header and "from __future__ import annotations" in header:
        new_lines = [
            ln for ln in new_lines
            if ln.strip() != "from __future__ import annotations"
        ]
    new_text = "".join(new_lines)
    combined = (header + new_text) if header else new_text

    tmp_fd, tmp_path = tempfile.mkstemp(prefix=self_path.name + ".", dir=str(self_path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(combined)
        os.chmod(tmp_path, 0o755)
        shutil.move(tmp_path, self_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("update: 原子写失败: %s", exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False

    logger.info("update: 已覆盖 %s → %s", self_path, server_hash)
    # 持久化新的 server hash，避免下次启动时又触发更新。失败时重试一次再放弃；
    # 彻底失败时也不阻塞 re-exec（下次启动会重新下载同一版本，虽浪费但不致命）。
    for attempt in (1, 2):
        try:
            save_installed_config(server_hash=server_hash)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("update: save_installed_config 第 %d 次失败: %s", attempt, exc)
            if attempt == 2:
                logger.error("update: save_installed_config 已放弃，下次启动可能重复下载")
    if force_restart:
        logger.info("update: re-exec 加载新版本")
        _exec_self()
    return True


# ═════════════════════════════════════════════════════════════════════════════
#  服务安装（systemd / launchd）
# ═════════════════════════════════════════════════════════════════════════════

def _install_script_to_state() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve()
    if SCRIPT_CACHE_PATH.resolve() == src:
        return SCRIPT_CACHE_PATH
    shutil.copy2(src, SCRIPT_CACHE_PATH)
    os.chmod(SCRIPT_CACHE_PATH, 0o755)
    return SCRIPT_CACHE_PATH


# =BEGIN-LINUX=
def _systemctl_user_env() -> dict[str, str]:
    """补全 systemctl --user 所需的环境变量。

    sudo / SSH 运行时 XDG_RUNTIME_DIR 可能不存在，导致
    systemctl --user 报 "Failed to connect to bus"。
    """
    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env:
        uid = os.getuid()
        candidate = f"/run/user/{uid}"
        if Path(candidate).exists():
            env["XDG_RUNTIME_DIR"] = candidate
    return env


def _run_systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True,
        env=_systemctl_user_env(),
    )


def _try_enable_linger() -> None:
    """Linux: enable-linger 让 user service 不依赖登录会话（开机自启关键）。"""
    try:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if user:
            subprocess.run(["loginctl", "enable-linger", user], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _systemctl_detail(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "").strip()


def _run_systemctl_user_checked(*args: str) -> tuple[bool, str]:
    try:
        result = _run_systemctl_user(*args)
    except FileNotFoundError:
        return False, "systemctl not found"
    detail = _systemctl_detail(result)
    if result.returncode == 0:
        return True, detail
    return False, f"systemctl --user {' '.join(args)} rc={result.returncode}: {detail}"


def _linger_enabled(user: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["loginctl", "show-user", user, "-p", "Linger", "--value"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return False, "loginctl not found"
    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode == 0 and result.stdout.strip().lower() == "yes":
        return True, detail
    return False, detail or f"loginctl show-user rc={result.returncode}"


def _ensure_linger_enabled() -> tuple[bool, str]:
    """Enable and verify user linger, otherwise boot-time user services won't run."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return False, "USER/LOGNAME is empty"
    ok, detail = _linger_enabled(user)
    if ok:
        return True, "linger already enabled"
    try:
        result = subprocess.run(
            ["loginctl", "enable-linger", user],
            capture_output=True, text=True, timeout=8,
        )
    except FileNotFoundError:
        return False, "loginctl not found"
    enable_detail = (result.stderr or result.stdout or "").strip()
    if result.returncode != 0:
        return False, f"loginctl enable-linger rc={result.returncode}: {enable_detail or detail}"
    ok, verify_detail = _linger_enabled(user)
    if ok:
        return True, "linger enabled"
    return False, f"linger verify failed: {verify_detail or enable_detail or detail}"


def _install_linux_systemd(unit_name: str, unit_path: Path, python: str) -> dict:
    logger = setup_logging()
    details: dict[str, str | bool] = {}
    _remove_crontab_fallback(INSTANCE_ID or "default")

    for args in (("daemon-reload",), ("enable", unit_name), ("restart", unit_name)):
        ok, detail = _run_systemctl_user_checked(*args)
        details["systemctl_" + "_".join(args)] = detail
        if not ok:
            raise RuntimeError(detail)

    active_ok, active_detail = _run_systemctl_user_checked("is-active", "--quiet", unit_name)
    details["systemctl_is_active"] = active_detail
    if not active_ok:
        status = _run_systemctl_user("status", "--no-pager", unit_name)
        raise RuntimeError(_systemctl_detail(status) or active_detail or "systemd service not active")

    linger_ok, linger_detail = _ensure_linger_enabled()
    details["linger"] = linger_detail
    if not linger_ok:
        logger.warning("install: user systemd linger 不可用，增加 crontab @reboot fallback: %s", linger_detail)
        _install_crontab_fallback(INSTANCE_ID or "default", python)
        details["crontab_fallback"] = True
    else:
        details["crontab_fallback"] = False

    pid = _wait_for_bridge_pid(timeout_seconds=10.0)
    if not pid:
        raise RuntimeError(f"{unit_name} active but bridge pid not found; unit={unit_path}")
    logger.info("install: systemd user service 已启动 pid=%s unit=%s", pid, unit_name)
    return details
# =END-LINUX=


# =BEGIN-MACOS=
def _launchd_domains() -> list[str]:
    """LaunchAgent 有效的 domain 候选，按推荐顺序试。

    - `user/UID`：LaunchAgents 的标准 domain（headless / SSH 也能用）
    - `gui/UID`：有 aqua 会话时可用；老 macOS 不支持 bootstrap，会返回 "Domain does not support specified action"
    """
    uid = os.getuid()
    return [f"user/{uid}", f"gui/{uid}"]


def _launchd_targets(label: str) -> list[str]:
    return [f"{domain}/{label}" for domain in _launchd_domains()]


def _launchd_find_loaded_target(label: str) -> tuple[str | None, str]:
    """看哪个 domain 里实际 load 了该 service。返回第一个 `launchctl print` 成功的 target。"""
    last_detail = ""
    for target in _launchd_targets(label):
        result = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return target, result.stdout or ""
        last_detail = (result.stderr or result.stdout or "").strip()
    return None, last_detail


def _launchd_enable(label: str) -> None:
    """两个 domain 都 enable 一下，防止 service 之前被 disable 导致 bootstrap 后仍不起来。"""
    for target in _launchd_targets(label):
        subprocess.run(
            ["launchctl", "enable", target],
            capture_output=True, text=True,
        )


def _launchd_kickstart_loaded(label: str) -> tuple[str | None, str]:
    """对已 load 的 service 主动 kickstart 一次，等 PID 文件出现再返回。"""
    logger = setup_logging()
    target, detail = _launchd_find_loaded_target(label)
    if not target:
        return None, detail
    kick = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True, text=True,
    )
    if kick.returncode != 0:
        logger.warning(
            "install: kickstart %s 失败（rc=%s）: %s",
            target, kick.returncode, (kick.stderr or kick.stdout or "").strip(),
        )
    pid = _wait_for_bridge_pid(timeout_seconds=8.0)
    if pid:
        logger.info("install: launchd 已启动 bridge pid=%s target=%s", pid, target)
        return target, detail
    return target, detail


def _launchd_stop(label: str, plist_path: Path) -> None:
    """停 launchd service。全路径清理：bootout 两个 domain + unload + remove（老 API 强删 registration）。

    所有调用吞错 —— service 不在 / label 未注册都返回非 0，对我们是正常路径。
    末尾再 enable 一下，给下一次 bootstrap 扫除潜在 disabled 状态。
    """
    for domain in _launchd_domains():
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"],
            capture_output=True,
        )
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )
    subprocess.run(
        ["launchctl", "remove", label],
        capture_output=True,
    )
    _launchd_enable(label)


def _clear_plist_quarantine(plist_path: Path) -> None:
    """新下载的脚本写出的 plist 可能带 com.apple.quarantine，部分 macOS 会让 launchctl
    报 `Bootstrap failed: 5: Input/output error`。主动去掉（失败忽略）。
    """
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(plist_path)],
        capture_output=True,
    )


def _launchd_reload(label: str, plist_path: Path) -> None:
    """重新加载 launchd plist —— 先停再启，等效 "重装并重启"。

    每一步都以 `launchctl print + kickstart + PID 文件` 验证实际 daemon 是否启动,
    而不是只看返回码。返回码 0 不代表真的在跑；同理 bootstrap 返回非 0 也不代表
    service 没加载（有些 macOS 版本在 Already bootstrapped 时会返回非 0，但其实
    service 已经在跑）。失败再退老 `load -w`。
    """
    logger = setup_logging()

    _launchd_stop(label, plist_path)
    _clear_plist_quarantine(plist_path)
    _launchd_enable(label)

    attempts: list[tuple[str, int, str]] = []
    for domain in _launchd_domains():
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            capture_output=True, text=True,
        )
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode == 0:
            logger.info("install: bootstrap %s 成功", domain)
        else:
            attempts.append((domain, result.returncode, detail))
            logger.warning(
                "install: bootstrap %s 失败（rc=%s）: %s",
                domain, result.returncode, detail,
            )
        _launchd_enable(label)
        target, _ = _launchd_kickstart_loaded(label)
        if target:
            return

    # 两个 domain 都没把 service 启起来 —— 退回老命令
    load = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        capture_output=True, text=True,
    )
    if load.returncode != 0:
        logger.warning(
            "install: load -w 失败（rc=%s）: %s",
            load.returncode, (load.stderr or load.stdout or "").strip(),
        )
    _launchd_enable(label)
    target, _ = _launchd_kickstart_loaded(label)
    if target:
        return

    detail = "; ".join(f"{d} rc={rc} {err}" for d, rc, err in attempts)
    raise RuntimeError(
        f"launchctl 无法加载 {plist_path}: {detail}; "
        f"load rc={load.returncode} stderr={(load.stderr or load.stdout or '').strip()}"
    )


# =END-MACOS=


# ─── crontab @reboot fallback：systemd/launchd 自启动不可靠时用 ─────────────
def _crontab_marker(instance_id: str) -> tuple[str, str]:
    return (
        f"# >>> skillforge.bridge.{instance_id} >>>",
        f"# <<< skillforge.bridge.{instance_id} <<<",
    )


def _read_user_crontab() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("系统没有 crontab 命令") from exc
    if result.returncode == 0:
        return result.stdout
    stderr = (result.stderr or "").lower()
    if "no crontab" in stderr:
        return ""
    raise RuntimeError(
        f"crontab -l 失败 rc={result.returncode}: {(result.stderr or result.stdout).strip()}"
    )


def _write_user_crontab(content: str) -> None:
    result = subprocess.run(
        ["crontab", "-"],
        input=content,
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crontab 写入失败 rc={result.returncode}: {(result.stderr or result.stdout).strip()}"
        )


def _remove_crontab_fallback(instance_id: str) -> None:
    begin, end = _crontab_marker(instance_id)
    try:
        content = _read_user_crontab()
    except RuntimeError:
        return
    lines = content.splitlines()
    cleaned: list[str] = []
    skip = False
    for line in lines:
        if line.strip() == begin:
            skip = True
            continue
        if line.strip() == end:
            skip = False
            continue
        if not skip:
            cleaned.append(line)
    new_content = "\n".join(cleaned).strip()
    if new_content:
        _write_user_crontab(new_content + "\n")
    else:
        try:
            result = subprocess.run(
                ["crontab", "-r"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                _write_user_crontab("")
        except Exception:  # noqa: BLE001
            pass
    try:
        AUTOSTART_LAUNCHER_PATH.unlink()
    except OSError:
        pass


def _install_crontab_fallback(instance_id: str, python: str) -> int:
    """自启动路径走不通时的退路：写一个 @reboot crontab + 必要时立即 fork daemon。

    返回立即启动的 daemon pid（可能不是 daemon 自身写 PID_PATH 的值，但等 PID 文件
    后能确认进程就位）。
    """
    logger = setup_logging()
    quoted_python = shlex.quote(python)
    quoted_script = shlex.quote(str(SCRIPT_CACHE_PATH))
    quoted_log = shlex.quote(str(LOG_PATH))
    quoted_pid = shlex.quote(str(PID_PATH))
    launcher = (
        "#!/bin/sh\n"
        "export SKILLFORGE_BRIDGE_DAEMON=1\n"
        f"PID_FILE={quoted_pid}\n"
        "if [ -f \"$PID_FILE\" ]; then\n"
        "  pid=$(cat \"$PID_FILE\" 2>/dev/null || true)\n"
        "  if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then exit 0; fi\n"
        "fi\n"
        f"exec {quoted_python} {quoted_script} --daemon >>{quoted_log} 2>&1\n"
    )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUTOSTART_LAUNCHER_PATH.write_text(launcher, encoding="utf-8")
    os.chmod(AUTOSTART_LAUNCHER_PATH, 0o755)

    begin, end = _crontab_marker(instance_id)
    content = _read_user_crontab()
    lines = content.splitlines()
    cleaned: list[str] = []
    skip = False
    for line in lines:
        if line.strip() == begin:
            skip = True
            continue
        if line.strip() == end:
            skip = False
            continue
        if not skip:
            cleaned.append(line)
    cleaned.extend([
        begin,
        f"@reboot /bin/sh {shlex.quote(str(AUTOSTART_LAUNCHER_PATH))}",
        end,
    ])
    _write_user_crontab("\n".join(cleaned).strip() + "\n")

    pid = _read_running_pid()
    if not pid:
        with open(LOG_PATH, "a", encoding="utf-8") as logf:
            subprocess.Popen(  # noqa: S603
                [python, str(SCRIPT_CACHE_PATH), "--daemon"],
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=logf,
                start_new_session=True,
                env={**os.environ, "SKILLFORGE_BRIDGE_DAEMON": "1"},
            )
        pid = _wait_for_bridge_pid(timeout_seconds=10.0)
    if not pid:
        raise RuntimeError("fallback daemon 已拉起，但未在超时内写出 PID")
    logger.warning(
        "install: 自启动已增加 crontab @reboot fallback，当前 daemon pid=%s", pid,
    )
    return pid


# ─── 进程识别 helpers（公用，不限平台） ──────────────────────────────────
def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_running_pid() -> int | None:
    if PID_PATH.exists():
        try:
            pid = int((PID_PATH.read_text(encoding="utf-8") or "0").strip() or 0)
        except ValueError:
            pid = 0
        if _pid_is_running(pid):
            return pid
        try:
            PID_PATH.unlink()
        except OSError:
            pass
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"skillforgebridge|openclaw_bridge|\\.skillforge_bridge/{_INSTANCE_SLUG}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if _pid_is_running(pid) and pid != os.getpid():
                return pid
    except Exception:  # noqa: BLE001
        pass
    return None


def _acquire_process_lock() -> bool:
    """Keep only one daemon/tray process for this instance alive."""
    global _PROCESS_LOCK_FH
    if _PROCESS_LOCK_FH is not None:
        return True
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        if os.name == "posix":
            import fcntl

            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
        else:
            running_pid = _read_running_pid()
            if running_pid and running_pid != os.getpid():
                return False
        lock_fh.seek(0)
        lock_fh.truncate()
        lock_fh.write(str(os.getpid()))
        lock_fh.flush()
        _PROCESS_LOCK_FH = lock_fh
        return True
    except Exception:
        try:
            lock_fh.close()
        except Exception:
            pass
        raise


def _wait_for_bridge_pid(timeout_seconds: float = 10.0) -> int | None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        pid = _read_running_pid()
        if pid:
            return pid
        time.sleep(0.25)
    return _read_running_pid()


def _stop_running_bridge() -> None:
    """install 前先停掉同 instance 已在跑的 bridge 进程，避免新老并存。

    覆盖三种来源：launchd/systemd 已注册的服务、daemon 模式写的 PID 文件、
    以及前台 tray 没写 PID 文件的进程（用 pgrep 按命令行匹配）。全部 SIGTERM，
    保护当前进程自己不被 kill。
    """
    logger = setup_logging()
    instance = INSTANCE_ID or "default"
    _slug = hashlib.md5(instance.encode()).hexdigest()[:8]

    # 1. launchd（macOS）/ systemd（Linux）管理的先走原生工具停
    # =BEGIN-MACOS=
    label = f"com.skillforge.bridge.{instance}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if plist.exists():
        _launchd_stop(label, plist)
    # =END-MACOS=
    # =BEGIN-LINUX=
    _run_systemctl_user("stop", f"skillforge-bridge-{_slug}.service")
    # =END-LINUX=

    self_pid = os.getpid()

    def _term(pid: int) -> None:
        if pid <= 0 or pid == self_pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("install: 已向旧 bridge 进程发送 SIGTERM pid=%s", pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("install: 无权限 kill pid=%s，请手动停", pid)

    # 2. PID 文件（daemon 模式写的）
    if PID_PATH.exists():
        try:
            _term(int((PID_PATH.read_text(encoding="utf-8") or "0").strip() or 0))
        except ValueError:
            pass

    # 3. pgrep 兜底：匹配命令行里的 bridge 关键词（脚本名可能被用户重命名，不强制依赖）
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"skillforgebridge|openclaw_bridge|\\.skillforge_bridge/{instance}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.split():
            try:
                _term(int(line))
            except ValueError:
                continue
    except FileNotFoundError:
        pass  # 某些极简环境没有 pgrep
    except Exception as exc:  # noqa: BLE001
        logger.warning("install: pgrep 匹配旧进程失败: %s", exc)


def install_service() -> None:
    bootstrap_dependencies(need_tray=False)
    logger = setup_logging()
    if not INSTANCE_ID or not ENROLLMENT_TOKEN:
        logger.error("install: INSTANCE_ID / ENROLLMENT_TOKEN 缺失，无法安装")
        sys.exit(2)
    # install 前先自我升级到最新版本，确保修过的 launchd / 依赖逻辑生效
    # （check_and_apply_update 在有更新时会 os.execv 重启；无更新则直接返回 False）
    try:
        check_and_apply_update(force_restart=True)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("install: 预检更新失败（继续执行 install）: %s", exc)
    # 先把已在跑的老 bridge 全部停掉（前台 tray / 手动 daemon / 旧 launchd）
    _stop_running_bridge()
    logger.info("install: 复制脚本到 %s", SCRIPT_CACHE_PATH)
    _install_script_to_state()
    # 安装时查一次 server hash 并持久化，后续启动比对用
    save_installed_config(server_hash=fetch_server_script_hash())
    _write_icon_to_cache()

    python = str(_venv_python()) if _venv_python().exists() else _restart_python_executable()

    # =BEGIN-LINUX=
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_name, unit_path = _linux_unit_paths(INSTANCE_ID or "default")
    content = _linux_systemd_unit_text(INSTANCE_ID or "default", python)
    unit_path.write_text(content, encoding="utf-8")
    logger.info("install: 生成 systemd user unit: %s", unit_path)
    try:
        systemd_details = _install_linux_systemd(unit_name, unit_path, python)
        save_installed_config(extra={
            "AUTOSTART_MODE": "systemd+crontab" if systemd_details.get("crontab_fallback") else "systemd",
            "LAST_SYSTEMD_ERROR": None,
            "LAST_SYSTEMD_LINGER": systemd_details.get("linger"),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("install: systemd user 安装失败，准备 crontab fallback: %s", exc)
        pid = _install_crontab_fallback(INSTANCE_ID or "default", python)
        save_installed_config(extra={
            "AUTOSTART_MODE": "crontab",
            "LAST_SYSTEMD_ERROR": str(exc),
        })
        logger.info("install: Linux fallback 完成，后台 pid=%s", pid)
    # =END-LINUX=

    # =BEGIN-MACOS=
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    label = f"com.skillforge.bridge.{INSTANCE_ID or 'default'}"
    plist_path = plist_dir / f"{label}.plist"
    content = LAUNCHD_PLIST_TEMPLATE.format(
        instance_id=INSTANCE_ID or "default",
        python=python,
        script=str(SCRIPT_CACHE_PATH),
        log=str(LOG_PATH),
    )
    plist_path.write_text(content, encoding="utf-8")
    logger.info("install: 生成 launchd plist: %s", plist_path)
    try:
        _launchd_reload(label, plist_path)
        _remove_crontab_fallback(INSTANCE_ID or "default")
        save_installed_config(extra={"AUTOSTART_MODE": "launchd", "LAST_LAUNCHD_ERROR": None})
    except Exception as exc:  # noqa: BLE001
        logger.warning("install: launchd 安装失败，准备 crontab fallback: %s", exc)
        pid = _install_crontab_fallback(INSTANCE_ID or "default", python)
        save_installed_config(extra={"AUTOSTART_MODE": "crontab", "LAST_LAUNCHD_ERROR": str(exc)})
        logger.info("install: fallback 完成，后台 pid=%s", pid)
    # =END-MACOS=

    logger.info("install: 完成 ✅ — service 已启动；日志: tail -f %s", LOG_PATH)


def uninstall_service() -> None:
    logger = setup_logging()
    # =BEGIN-LINUX=
    unit_name, unit_path = _linux_unit_paths(INSTANCE_ID or "default")
    _run_systemctl_user("stop", unit_name)
    _run_systemctl_user("disable", unit_name)
    if unit_path.exists():
        unit_path.unlink()
    _run_systemctl_user("daemon-reload")
    _remove_crontab_fallback(INSTANCE_ID or "default")
    # =END-LINUX=
    # =BEGIN-MACOS=
    label = f"com.skillforge.bridge.{INSTANCE_ID or 'default'}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if plist_path.exists():
        _launchd_stop(label, plist_path)
        plist_path.unlink()
    _remove_crontab_fallback(INSTANCE_ID or "default")
    _stop_running_bridge()
    save_installed_config(extra={"AUTOSTART_MODE": None, "LAST_LAUNCHD_ERROR": None})
    # =END-MACOS=
    logger.info("uninstall: 完成（state 目录保留在 %s）", STATE_DIR)


# ═════════════════════════════════════════════════════════════════════════════
#  运行模式
# ═════════════════════════════════════════════════════════════════════════════

_runtime_state: dict = {"connected": False, "last_event": "starting"}


async def _bridge_loop_with_updates() -> None:
    # 定期更新检查已移入 forward_skillforge() 内的 _update_check_loop，
    # 只在 WS 连接有效时运行，且用 run_in_executor 避免阻塞事件循环。
    # 节点调度器独立于 bridge WS 连接，读 schedules.json 驱动，桥重连不影响
    scheduler_task = asyncio.create_task(_node_scheduler_loop())
    openwebui_server = _start_openwebui_server_if_enabled()
    try:
        await run_bridge()
    finally:
        scheduler_task.cancel()
        if openwebui_server is not None:
            openwebui_server.shutdown()
            openwebui_server.server_close()


def daemon_mode() -> None:
    bootstrap_dependencies(need_tray=False)
    logger = setup_logging()
    if not _acquire_process_lock():
        logger.warning("daemon: 已有同 instance bridge 进程在运行，当前进程退出 instance=%s", INSTANCE_ID)
        return
    logger.info("bridge daemon 启动 version=%s instance=%s", BRIDGE_VERSION, INSTANCE_ID)
    _repair_systemd_unit_execstart()
    _ensure_linux_autostart_from_daemon()
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    try:
        save_installed_config()
    except Exception as exc:  # noqa: BLE001
        logger.debug("daemon: refresh installed config skipped: %s", exc)
    check_and_apply_update(force_restart=True)
    try:
        if _resume_media_bootstrap_if_needed():
            logger.info("daemon: resumed interrupted governed H3 bootstrap")
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon: failed to resume governed H3 bootstrap: %s", exc)
    try:
        asyncio.run(_bridge_loop_with_updates())
    except KeyboardInterrupt:
        logger.info("daemon: SIGINT 退出")
    finally:
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass


def tray_mode() -> None:
    bootstrap_dependencies(need_tray=True)
    logger = setup_logging()
    if not _acquire_process_lock():
        logger.warning("tray: 已有同 instance bridge 进程在运行，当前进程退出 instance=%s", INSTANCE_ID)
        return
    logger.info("bridge tray 启动 version=%s instance=%s", BRIDGE_VERSION, INSTANCE_ID)
    try:
        save_installed_config()
    except Exception as exc:  # noqa: BLE001
        logger.debug("tray: refresh installed config skipped: %s", exc)

    try:
        import pystray
        from PIL import Image
    except ImportError as exc:
        logger.error("无法 import pystray/Pillow: %s", exc)
        sys.exit(3)

    _write_icon_to_cache()
    icon_image = Image.open(ICON_CACHE_PATH)

    def _bridge_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            check_and_apply_update(force_restart=True)
            loop.run_until_complete(_bridge_loop_with_updates())
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("bridge loop 异常: %s", exc)
        finally:
            loop.close()

    threading.Thread(target=_bridge_thread, daemon=True, name="bridge-loop").start()

    def _status_title():
        return f"SkillForge Bridge · {'在线' if _runtime_state['connected'] else '离线'}"

    def _open_log(icon, item):  # noqa: ARG001
        # =BEGIN-MACOS=
        subprocess.Popen(["open", str(LOG_PATH)])
        # =END-MACOS=
        # =BEGIN-LINUX=
        subprocess.Popen(["xdg-open", str(LOG_PATH)])
        # =END-LINUX=

    def _force_update(icon, item):  # noqa: ARG001
        try:
            changed = check_and_apply_update(force_restart=False)
            icon.notify("已下载新版本，下次重启生效" if changed else "当前已是最新版本", "SkillForge Bridge")
        except Exception as exc:  # noqa: BLE001
            icon.notify(f"更新失败: {exc}", "SkillForge Bridge")

    def _restart(icon, item):  # noqa: ARG001
        icon.stop()
        _exec_self()

    def _quit(icon, item):  # noqa: ARG001
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: _status_title(), lambda i, it: None, enabled=False),
        pystray.MenuItem(f"实例 {INSTANCE_ID}", lambda i, it: None, enabled=False),
        pystray.MenuItem(f"版本 {BRIDGE_VERSION}", lambda i, it: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("检查更新", _force_update),
        pystray.MenuItem("打开日志", _open_log),
        pystray.MenuItem("重启", _restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _quit),
    )
    icon = pystray.Icon("skillforge-bridge", icon_image, "SkillForge Bridge", menu)
    try:
        icon.run()
    finally:
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass


def print_status() -> None:
    logger = setup_logging(to_file=False)
    server_hash = fetch_server_script_hash() or "(查询失败)"
    installed = _installed_hash() or "(未安装)"
    installed_cfg = load_installed_config()
    logger.info("version         = %s", BRIDGE_VERSION)
    logger.info("instance_id     = %s", INSTANCE_ID or "(空)")
    logger.info("state_dir       = %s", STATE_DIR)
    logger.info("installed_env   = %s", "存在" if ENV_PATH.exists() else "缺失")
    logger.info("installed_hash  = %s", installed)
    logger.info("server_hash     = %s", server_hash)
    logger.info("update_needed   = %s", "是" if server_hash not in ("(查询失败)", "", installed) else "否")
    logger.info("running pid     = %s", PID_PATH.read_text() if PID_PATH.exists() else "(无)")
    logger.info("autostart_mode  = %s", installed_cfg.get("AUTOSTART_MODE", "(未知)"))
    logger.info("platform_target = %s", BRIDGE_PLATFORM_TARGET or "(空)")
    logger.info("skillforge_ws   = %s", SKILLFORGE_WS_URL)
    logger.info("http_base       = %s", SKILLFORGE_HTTP_BASE)


# ═════════════════════════════════════════════════════════════════════════════
#  入口：CLI 分发
# ═════════════════════════════════════════════════════════════════════════════

def _detect_display() -> bool:
    if os.environ.get("SKILLFORGE_BRIDGE_DAEMON") == "1":
        return False
    # =BEGIN-MACOS=
    return True
    # =END-MACOS=
    # =BEGIN-LINUX=
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # =END-LINUX=


def main() -> None:
    load_installed_config()
    # load_installed_config() 将 env.json 写入 os.environ，但模块顶层变量
    # 在 import 时已缓存了旧值（可能为空）。这里重新同步关键配置变量。
    global INSTANCE_ID, ENROLLMENT_TOKEN, SKILLFORGE_WS_URL, SKILLFORGE_HTTP_BASE
    global LOCAL_AICLAW_URL, LOCAL_AICLAW_TOKEN_FALLBACK, LOCAL_TRAINING_GATEWAY_URL, LOCAL_TRAINING_GATEWAY_TOKEN
    global LOCAL_INTELLIGENCE_GATEWAY_URL, LOCAL_INTELLIGENCE_GATEWAY_TOKEN
    global BRIDGE_PLATFORM_TARGET
    global _INSTANCE_SLUG, STATE_DIR, LEGACY_STATE_DIR
    global KEY_PATH, KEY_OLD_PATH, ENV_PATH, LOG_PATH, PID_PATH, LOCK_PATH, SCHEDULES_PATH
    global SCRIPT_CACHE_PATH, ICON_CACHE_PATH, AUTOSTART_LAUNCHER_PATH
    global MEDIA_BOOTSTRAP_ROOT, MEDIA_BOOTSTRAP_STATUS_PATH, MEDIA_BOOTSTRAP_COMFY_DIR
    global MEDIA_BOOTSTRAP_VENV_DIR, MEDIA_BOOTSTRAP_MODEL_DIR, MEDIA_BOOTSTRAP_INPUT_DIR, MEDIA_BOOTSTRAP_OUTPUT_DIR
    INSTANCE_ID = os.getenv("INSTANCE_ID", INSTANCE_ID)
    ENROLLMENT_TOKEN = os.getenv("ENROLLMENT_TOKEN", ENROLLMENT_TOKEN)
    SKILLFORGE_WS_URL = os.getenv("SKILLFORGE_WS_URL", SKILLFORGE_WS_URL)
    SKILLFORGE_HTTP_BASE = os.getenv("SKILLFORGE_HTTP_BASE", SKILLFORGE_HTTP_BASE)
    LOCAL_AICLAW_URL = os.getenv("LOCAL_AICLAW_URL", LOCAL_AICLAW_URL)
    LOCAL_AICLAW_TOKEN_FALLBACK = os.getenv("LOCAL_AICLAW_TOKEN_FALLBACK", LOCAL_AICLAW_TOKEN_FALLBACK)
    LOCAL_TRAINING_GATEWAY_URL = os.getenv("LOCAL_TRAINING_GATEWAY_URL", LOCAL_TRAINING_GATEWAY_URL)
    LOCAL_TRAINING_GATEWAY_TOKEN = os.getenv("LOCAL_TRAINING_GATEWAY_TOKEN", LOCAL_TRAINING_GATEWAY_TOKEN)
    LOCAL_INTELLIGENCE_GATEWAY_URL = os.getenv("LOCAL_INTELLIGENCE_GATEWAY_URL", LOCAL_INTELLIGENCE_GATEWAY_URL)
    LOCAL_INTELLIGENCE_GATEWAY_TOKEN = os.getenv("LOCAL_INTELLIGENCE_GATEWAY_TOKEN", LOCAL_INTELLIGENCE_GATEWAY_TOKEN)
    BRIDGE_PLATFORM_TARGET = os.getenv("BRIDGE_PLATFORM_TARGET", BRIDGE_PLATFORM_TARGET)
    # 重新计算 STATE_DIR 系列（依赖 INSTANCE_ID）
    _INSTANCE_SLUG = hashlib.md5((INSTANCE_ID or "default").encode()).hexdigest()[:8]
    STATE_DIR = Path.home() / ".skillforge_bridge" / _INSTANCE_SLUG
    LEGACY_STATE_DIR = Path.home() / ".skillforge_bridge" / (INSTANCE_ID or "default")
    KEY_PATH = STATE_DIR / "device.key"
    KEY_OLD_PATH = STATE_DIR / "device.key.old"
    ENV_PATH = STATE_DIR / "env.json"
    LOG_PATH = STATE_DIR / "bridge.log"
    PID_PATH = STATE_DIR / "bridge.pid"
    LOCK_PATH = STATE_DIR / "bridge.lock"
    SCHEDULES_PATH = STATE_DIR / "schedules.json"
    SCRIPT_CACHE_PATH = STATE_DIR / "bridge.py"
    ICON_CACHE_PATH = STATE_DIR / "icon.png"
    AUTOSTART_LAUNCHER_PATH = STATE_DIR / "bridge_autostart.sh"
    old_bootstrap_model_dir = MEDIA_BOOTSTRAP_MODEL_DIR
    MEDIA_BOOTSTRAP_ROOT = STATE_DIR / "media"
    MEDIA_BOOTSTRAP_STATUS_PATH = MEDIA_BOOTSTRAP_ROOT / "bootstrap.json"
    MEDIA_BOOTSTRAP_COMFY_DIR = MEDIA_BOOTSTRAP_ROOT / "ComfyUI"
    MEDIA_BOOTSTRAP_VENV_DIR = MEDIA_BOOTSTRAP_ROOT / "venv"
    MEDIA_BOOTSTRAP_MODEL_DIR = MEDIA_BOOTSTRAP_ROOT / "models"
    MEDIA_BOOTSTRAP_INPUT_DIR = MEDIA_BOOTSTRAP_COMFY_DIR / "input"
    MEDIA_BOOTSTRAP_OUTPUT_DIR = MEDIA_BOOTSTRAP_COMFY_DIR / "output"
    if old_bootstrap_model_dir in MEDIA_MODEL_ROOTS:
        MEDIA_MODEL_ROOTS.remove(old_bootstrap_model_dir)
    if MEDIA_BOOTSTRAP_MODEL_DIR not in MEDIA_MODEL_ROOTS:
        MEDIA_MODEL_ROOTS.append(MEDIA_BOOTSTRAP_MODEL_DIR)
    _migrate_legacy_state_dir()
    bootstrap_dependencies(need_tray=False)
    # bootstrap 之后真正 import 核心依赖
    global serialization, Ed25519PrivateKey, load_pem_private_key, websockets, _CORE_DEPS_OK
    if not _CORE_DEPS_OK:
        from cryptography.hazmat.primitives import serialization as _s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _pk
        from cryptography.hazmat.primitives.serialization import load_pem_private_key as _lpk
        import websockets as _ws
        serialization = _s
        Ed25519PrivateKey = _pk
        load_pem_private_key = _lpk
        websockets = _ws
        _CORE_DEPS_OK = True

    args = set(sys.argv[1:])
    if "--help" in args or "-h" in args:
        print(__doc__)
        return
    if "--install" in args:
        install_service()
        return
    if "--uninstall" in args:
        uninstall_service()
        return
    if "--update" in args:
        setup_logging()
        check_and_apply_update(force_restart=False)
        return
    if "--status" in args:
        print_status()
        return
    if "--daemon" in args:
        daemon_mode()
        return
    if "--tray" in args:
        tray_mode()
        return
    if os.environ.get("SKILLFORGE_BRIDGE_DAEMON") == "1":
        daemon_mode()
        return
    # 默认：一键 install —— 写 launchd/systemd unit 并让它把 bridge 跑为后台服务。
    # 已 install 过也幂等（unload + load），等价于"一键重装并重启"。
    # 想前台调试用 `--tray` 或 `--daemon`。
    install_service()


if __name__ == "__main__":
    main()
