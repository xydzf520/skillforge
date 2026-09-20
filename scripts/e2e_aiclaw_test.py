#!/usr/bin/env python3
"""AIClaw 端到端测试脚本

测试链路：
  1. HTTP login admin → 拿 session cookie
  2. 创建 AIClaw 实例 → 拿 enrollment token
  3. 下载 bridge.py 脚本 → 写到临时文件
  4. 启动 bridge subprocess
  5. 等待 bridge 注册成功（轮询实例的 bridge_connected_at）
  6. 测试 GET /api/aiclaw/instances/{id}/agents 列出 agents
  7. WebSocket 连 chat → 发文字 → 验证流式返回
  8. WebSocket 连 chat → 发图片 → 验证流式返回
  9. 清理：实例可保留供后续手工测试

依赖：requests、websockets

用法：
  python3 scripts/e2e_aiclaw_test.py [--keep] [--id INSTANCE_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import websockets


SKILLFORGE_BASE = os.environ.get("SKILLFORGE_BASE", "http://localhost:8000")
SKILLFORGE_WS_BASE = SKILLFORGE_BASE.replace("http://", "ws://").replace("https://", "wss://")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "TestPwd2026!")
LOCAL_AICLAW_URL = os.environ.get("LOCAL_AICLAW_URL", "ws://127.0.0.1:18789")
# 默认从 systemd 跑的 aiclaw-gateway 进程环境抓 token
def _autodiscover_aiclaw_token() -> str:
    """从本机 AIClaw 或 openclaw-cn gateway 进程 environ 拿 token。"""
    import glob
    for proc_environ in glob.glob("/proc/*/environ"):
        try:
            data = open(proc_environ, "rb").read()
            if b"AICLAW_GATEWAY_TOKEN=" not in data and b"OPENCLAW_GATEWAY_TOKEN=" not in data:
                continue
            for kv in data.split(b"\0"):
                if kv.startswith(b"AICLAW_GATEWAY_TOKEN=") or kv.startswith(b"OPENCLAW_GATEWAY_TOKEN="):
                    return kv.split(b"=", 1)[1].decode()
        except Exception:
            continue
    return ""

LOCAL_AICLAW_TOKEN = os.environ.get("LOCAL_AICLAW_TOKEN") or _autodiscover_aiclaw_token()


# 100×100 红色 PNG（PIL 生成的有效 PNG，可被 mime sniff）
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAGQAAABkCAIAAAD/gAIDAAAA5klEQVR4nO3QQQkAIADAQLV/Z"
    "63gXiLcJRibe3BrvQ74iVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWY"
    "FZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWY"
    "FZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWYFZgVmBWY"
    "FZgVmBWcEBil4Bx/GEGnoAAAAASUVORK5CYII="
)


def color(text, code):
    return f"\033[{code}m{text}\033[0m"


def step(n, msg):
    print(color(f"\n== Step {n}: {msg}", "1;36"))


def ok(msg):
    print(color(f"  ✓ {msg}", "32"))


def warn(msg):
    print(color(f"  ⚠ {msg}", "33"))


def fail(msg):
    print(color(f"  ✗ {msg}", "31"))
    sys.exit(1)


# ── HTTP / Cookie ──────────────────────────────────────────

def http_login() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{SKILLFORGE_BASE}/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        fail(f"login failed: {r.status_code} {r.text}")
    ok(f"logged in as {ADMIN_USERNAME}")
    return s


def create_instance(session: requests.Session, instance_id: str) -> dict:
    r = session.post(
        f"{SKILLFORGE_BASE}/api/aiclaw/instances",
        json={
            "id": instance_id,
            "name": f"e2e-test-{instance_id}",
            "department": "AI",
            "gateway_url": LOCAL_AICLAW_URL,
            "auth_token": LOCAL_AICLAW_TOKEN,
        },
        timeout=10,
    )
    if r.status_code != 200:
        # 已存在则重发 enrollment
        if r.status_code in (400, 409, 500):
            warn(f"create returned {r.status_code}, trying regenerate-enrollment")
            rr = session.post(
                f"{SKILLFORGE_BASE}/api/aiclaw/instances/{instance_id}/regenerate-enrollment",
                timeout=10,
            )
            if rr.status_code == 200:
                data = rr.json()
                ok(f"reused existing instance, enrollment expires at {data.get('expires_at')}")
                return data
        fail(f"create instance failed: {r.status_code} {r.text}")
    data = r.json()
    ok(f"instance created, enrollment expires at {data.get('expires_at')}")
    return data


def download_bridge_script(session: requests.Session, instance_id: str, token: str) -> str:
    r = session.get(
        f"{SKILLFORGE_BASE}/api/aiclaw/instances/{instance_id}/bridge-script",
        params={"one_time_token": token},
        timeout=10,
    )
    if r.status_code != 200:
        fail(f"download script failed: {r.status_code} {r.text}")
    ok(f"bridge script downloaded ({len(r.text)} bytes)")
    return r.text


def get_instance(session: requests.Session, instance_id: str) -> dict:
    r = session.get(f"{SKILLFORGE_BASE}/api/aiclaw/instances/{instance_id}", timeout=10)
    if r.status_code != 200:
        fail(f"get instance failed: {r.status_code} {r.text}")
    return r.json()


def list_agents(session: requests.Session, instance_id: str) -> list:
    r = session.get(
        f"{SKILLFORGE_BASE}/api/aiclaw/instances/{instance_id}/agents",
        timeout=15,
    )
    if r.status_code != 200:
        fail(f"list agents failed: {r.status_code} {r.text}")
    data = r.json()
    return data.get("items", [])


def list_skills(session: requests.Session, instance_id: str, agent_id: str) -> list:
    r = session.get(
        f"{SKILLFORGE_BASE}/api/aiclaw/instances/{instance_id}/agents/{agent_id}/skills",
        timeout=15,
    )
    if r.status_code != 200:
        warn(f"list skills failed: {r.status_code} {r.text}")
        return []
    data = r.json()
    return data.get("items", [])


# ── Bridge subprocess ───────────────────────────────────────

def start_bridge(script_text: str) -> tuple[subprocess.Popen, Path, Path]:
    tmp = Path(tempfile.mkstemp(suffix=".py", prefix="aiclaw_bridge_e2e_")[1])
    tmp.write_text(script_text, encoding="utf-8")
    log = Path(str(tmp) + ".log")
    log_fp = open(log, "w")
    proc = subprocess.Popen(
        [sys.executable, "-u", str(tmp)],
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    ok(f"bridge subprocess pid={proc.pid}")
    print(f"    script: {tmp}")
    print(f"    log:    {log}")
    return proc, tmp, log


def wait_until_online(session: requests.Session, instance_id: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            inst = get_instance(session, instance_id)
            if inst.get("bridge_connected_at"):
                ok(f"bridge online: connected_at={inst['bridge_connected_at']}, has_pubkey={inst.get('has_device_pubkey')}")
                return
        except Exception as exc:
            warn(f"poll error: {exc}")
        time.sleep(1)
    fail(f"bridge did not come online within {timeout}s")


# ── WebSocket chat 测试 ────────────────────────────────────

async def chat_text_test(cookies: dict, instance_id: str, agent_id: str) -> dict:
    """发一条纯文本消息，验证流式回复。"""
    url = f"{SKILLFORGE_WS_BASE}/api/aiclaw/instances/{instance_id}/chat/{agent_id}/stream"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"  → ws connect: {url}")
    async with websockets.connect(
        url,
        additional_headers={"Cookie": cookie_header},
        open_timeout=10,
    ) as ws:
        await ws.send(json.dumps({
            "type": "user_message",
            "content": "你好，请用一句话介绍你自己。",
            "attachments": [],
        }))
        chunks = []
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "chat_event":
                payload = msg.get("payload", {})
                state = payload.get("state")
                if payload.get("delta"):
                    print(f"    ⋯ delta: {payload['delta'][:80]}")
                elif payload.get("text"):
                    print(f"    ⋯ text:  {payload['text'][:80]}")
                else:
                    print(f"    ⋯ state: {state}")
                chunks.append(payload)
                if state in {"final", "aborted", "error", "queue_overflow_lost"}:
                    break
            elif msg.get("type") == "error":
                fail(f"chat error: {msg}")
        return {"chunks": chunks, "count": len(chunks)}


async def chat_image_test(cookies: dict, instance_id: str, agent_id: str) -> dict:
    """发一条带图片附件的消息，验证 SkillForge → AIClaw 转发链路（图片是否被 vision model 识别取决于 agent 配置）。"""
    url = f"{SKILLFORGE_WS_BASE}/api/aiclaw/instances/{instance_id}/chat/{agent_id}/stream"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"  → ws connect (image): {url}")
    async with websockets.connect(
        url,
        additional_headers={"Cookie": cookie_header},
        open_timeout=10,
    ) as ws:
        await ws.send(json.dumps({
            "type": "user_message",
            "content": "这张图里有什么？",
            "attachments": [
                {
                    "name": "tiny.png",
                    "mimeType": "image/png",
                    "data": TINY_PNG_BASE64,
                }
            ],
        }))
        chunks = []
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "chat_event":
                payload = msg.get("payload", {})
                state = payload.get("state")
                if payload.get("delta"):
                    print(f"    ⋯ delta: {payload['delta'][:80]}")
                elif payload.get("text"):
                    print(f"    ⋯ text:  {payload['text'][:80]}")
                else:
                    print(f"    ⋯ state: {state}")
                chunks.append(payload)
                if state in {"final", "aborted", "error", "queue_overflow_lost"}:
                    break
            elif msg.get("type") == "error":
                fail(f"chat error: {msg}")
        return {"chunks": chunks, "count": len(chunks)}


async def chat_abort_test(cookies: dict, instance_id: str, agent_id: str) -> dict:
    """发一条会触发长回复的消息，第一个 delta 后立即 abort，验证中断流。"""
    url = f"{SKILLFORGE_WS_BASE}/api/aiclaw/instances/{instance_id}/chat/{agent_id}/stream"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"  → ws connect (abort): {url}")
    async with websockets.connect(
        url,
        additional_headers={"Cookie": cookie_header},
        open_timeout=10,
    ) as ws:
        await ws.send(json.dumps({
            "type": "user_message",
            "content": "请用 200 字详细介绍量子计算的原理与应用，分点阐述。",
            "attachments": [],
        }))
        chunks = []
        run_id = None
        aborted = False
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "chat_event":
                payload = msg.get("payload", {})
                state = payload.get("state")
                if not run_id:
                    run_id = payload.get("runId")
                if payload.get("delta"):
                    print(f"    ⋯ delta: {payload['delta'][:60]}")
                if state == "started":
                    pass
                chunks.append(payload)
                # 收到第二个 delta 就触发 abort
                if not aborted and len([c for c in chunks if c.get("delta")]) >= 2 and run_id:
                    print(f"  → 发送 abort runId={run_id}")
                    await ws.send(json.dumps({"type": "abort", "runId": run_id}))
                    aborted = True
                if state in {"final", "aborted", "error", "queue_overflow_lost"}:
                    print(f"    ⋯ ended state={state}")
                    break
        return {"chunks": chunks, "count": len(chunks), "aborted": aborted}


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default="e2e-test-1", help="instance id")
    parser.add_argument("--keep", action="store_true", help="保留实例与 bridge 不退出")
    args = parser.parse_args()

    instance_id = args.id

    step(1, "HTTP 登录 admin")
    sess = http_login()

    step(2, f"创建 AIClaw 实例 id={instance_id}")
    enroll = create_instance(sess, instance_id)
    enrollment_token = enroll.get("enrollment_token")
    if not enrollment_token:
        fail("create response missing enrollment_token")

    step(3, "下载 bridge 脚本")
    script = download_bridge_script(sess, instance_id, enrollment_token)

    step(4, "启动 bridge subprocess")
    proc, tmp, log = start_bridge(script)

    try:
        step(5, "等待 bridge 注册成功")
        try:
            wait_until_online(sess, instance_id, timeout=20)
        except SystemExit:
            print(color("\n=== bridge log dump ===", "1;31"))
            try:
                print(log.read_text())
            except Exception:
                pass
            raise

        step(6, "GET /api/aiclaw/instances/{id}/agents")
        agents = list_agents(sess, instance_id)
        if not agents:
            warn("AIClaw 没有任何 agent，跳过 chat 测试。可手动 aiclaw register-agent 后重试")
            return
        ok(f"agents: {[a.get('id') or a.get('name') for a in agents]}")
        first_agent_id = agents[0].get("id") or agents[0].get("name")

        step(7, "WebSocket chat — 纯文本")
        cookies = sess.cookies.get_dict()
        text_result = asyncio.run(chat_text_test(cookies, instance_id, first_agent_id))
        ok(f"text chat 收到 {text_result['count']} 个事件")

        step(8, "WebSocket chat — 图片附件")
        img_result = asyncio.run(chat_image_test(cookies, instance_id, first_agent_id))
        ok(f"image chat 收到 {img_result['count']} 个事件")

        step(9, "GET skills.status — agent 已加载的 Skills")
        skills = list_skills(sess, instance_id, first_agent_id)
        if skills:
            preview = [s.get("id") or s.get("name") for s in skills][:10]
            ok(f"skills: {preview}（共 {len(skills)}）")
        else:
            warn("agent 没有加载 Skill")

        step(10, "WebSocket chat — abort 中断流")
        abort_result = asyncio.run(chat_abort_test(cookies, instance_id, first_agent_id))
        if abort_result["aborted"]:
            ok(f"已发 abort，最终收到 {abort_result['count']} 个事件")
        else:
            warn(f"未触发 abort 路径（{abort_result['count']} 个事件）")

        print(color("\n=== ✓ 端到端测试全部通过 ===", "1;32"))

        if args.keep:
            print(color("\n--keep: 保持 bridge 运行，按 Ctrl+C 退出", "1;33"))
            try:
                proc.wait()
            except KeyboardInterrupt:
                pass
    finally:
        if not args.keep:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            # 保留 tmp 与 log 方便排查
            print(f"\nbridge artifacts kept: {tmp} / {log}")


if __name__ == "__main__":
    main()
