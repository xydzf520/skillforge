from __future__ import annotations

import requests

from smoke_stack import smoke_env


def verify_bridge_script(session: requests.Session) -> None:
    instance_id = "smoke-bridge-inst"
    create_resp = session.post(
        f"{smoke_env.BASE_URL}/api/aiclaw/instances",
        json={
            "id": instance_id,
            "name": "Smoke Bridge",
            "department": "AI",
            "gateway_url": "ws://127.0.0.1:18789",
        },
        timeout=10,
    )
    if create_resp.status_code not in (200, 409):
        smoke_env.fail(f"创建 AIClaw instance 失败: {create_resp.status_code} {create_resp.text[:200]}")

    if create_resp.status_code == 200:
        payload = create_resp.json()
    else:
        regen = session.post(f"{smoke_env.BASE_URL}/api/aiclaw/instances/{instance_id}/regenerate-enrollment", timeout=10)
        if regen.status_code != 200:
            smoke_env.fail(f"重发 enrollment 失败: {regen.status_code} {regen.text[:200]}")
        payload = regen.json()

    token = payload.get("enrollment_token")
    if not token:
        smoke_env.fail("AIClaw instance 未返回 enrollment_token")

    script_resp = session.get(
        f"{smoke_env.BASE_URL}/api/aiclaw/instances/{instance_id}/bridge-script",
        params={"one_time_token": token, "platform": "linux"},
        timeout=10,
    )
    if script_resp.status_code != 200:
        smoke_env.fail(f"bridge-script 下载失败: {script_resp.status_code} {script_resp.text[:200]}")
    if "ENROLLMENT_TOKEN" not in script_resp.text or token not in script_resp.text:
        smoke_env.fail("bridge-script 内容不完整")
    smoke_env.ok("bridge-script 下载链路正常")
