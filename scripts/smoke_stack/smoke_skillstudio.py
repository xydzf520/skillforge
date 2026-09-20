from __future__ import annotations

import time

import requests

from smoke_stack import smoke_env


def verify_explain_pack(session: requests.Session) -> None:
    skill_id = f"smoke-explain-{int(time.time())}"
    create_resp = session.post(
        f"{smoke_env.BASE_URL}/api/skills/",
        json={
            "skill_id": skill_id,
            "name": skill_id,
            "department": "AI",
            "trigger_type": "manual",
            "risk_level": "R2",
            "skill_md": (
                "---\n"
                f"name: {skill_id}\n"
                "description: smoke explain skill\n"
                "department: AI\n"
                "trigger_type: manual\n"
                "risk_level: R2\n"
                "---\n\n"
                f"# {skill_id}\n\n"
                "## 目的\n\n"
                "根据 ROI 判断是否加预算\n\n"
                "## 执行步骤\n\n"
                "### step_1: 判断 ROI\n\n"
                "- 条件: ROI > 1.2\n"
            ),
            "policy_pack": {"roi_threshold": 1.2},
        },
        timeout=10,
    )
    if create_resp.status_code != 200:
        smoke_env.fail(f"创建 explain smoke skill 失败: {create_resp.status_code} {create_resp.text[:200]}")

    explain_resp = session.get(f"{smoke_env.BASE_URL}/api/skills/{skill_id}/explain-pack", timeout=10)
    if explain_resp.status_code != 200:
        smoke_env.fail(f"explain-pack 失败: {explain_resp.status_code} {explain_resp.text[:200]}")
    payload = explain_resp.json()
    if not payload.get("executive_summary"):
        smoke_env.fail("explain-pack 缺少 executive_summary")
    smoke_env.ok("explain-pack 返回正常")


def verify_review_api(session: requests.Session) -> None:
    for path in ("/api/reviews/", "/api/reviews/?reviewer=me&page_size=1"):
        resp = session.get(f"{smoke_env.BASE_URL}{path}", timeout=10)
        if resp.status_code != 200:
            smoke_env.fail(f"review api 失败: {path} -> {resp.status_code}")
    smoke_env.ok("review API 返回正常")
