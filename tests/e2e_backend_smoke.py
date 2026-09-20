"""E2E smoke tests for SSRF/resume/login-state endpoints.

Runs against a LIVE backend at http://localhost:8000.
Use: ./venv/bin/python tests/e2e_backend_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field

import httpx
import websockets


BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
USERNAME = "e2e_admin"
PASSWORD = "E2E2026!"

# ── 测试样本 ──────────────────────────────────────────────

MALICIOUS_URLS: list[str] = [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254:80/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://localhost/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://test.local/",
    "http://foo.internal/",
    "http://bar.intranet/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "ftp://example.com",
    # 长 URL
    "https://example.com/" + ("a" * 2049),
    # 空 / 空白
    "",
    "   ",
]

LEGIT_URLS: list[tuple[str, str]] = [
    ("https://sycm.taobao.com/portal/home.htm", "sycm"),
    ("https://www.example.com", "generic"),
]


@dataclass
class TallyReport:
    category: str
    passed: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)

    def record(self, ok: bool, sample: dict) -> None:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(sample)

    def print(self) -> None:
        print(f"\n── {self.category}: PASS={self.passed}, FAIL={self.failed}")
        for f in self.failures:
            print(f"  FAIL: {json.dumps(f, ensure_ascii=False)[:400]}")


# ── Helpers ───────────────────────────────────────────────

async def login(client: httpx.AsyncClient) -> None:
    r = await client.post(
        f"{BASE}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert r.status_code == 200, f"login failed: {r.text}"


def _expect_param_invalid(resp: httpx.Response, url: str) -> tuple[bool, dict]:
    """合法期望：status 400 且 payload.code == PARAM_INVALID"""
    sample: dict = {"url": url, "status": resp.status_code, "body": resp.text[:300]}
    if resp.status_code not in (400, 422):
        return False, {**sample, "why": f"expected 400/422, got {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:
        return False, {**sample, "why": "not json"}
    # FastAPI validation 422 也可以；但 guard 应翻译成 400 PARAM_INVALID
    if resp.status_code == 400:
        # 后端把 AppError 统一包成 {"error": {"code", "message", "detail"}}
        err = data.get("error") if isinstance(data, dict) else None
        code = err.get("code") if isinstance(err, dict) else data.get("code") if isinstance(data, dict) else None
        if code != "PARAM_INVALID":
            return False, {**sample, "why": f"code={code!r}"}
        return True, sample
    # 422 —— pydantic 层拒了 empty/whitespace 之类，也算合理
    return True, sample


async def test_discover_ssrf(client: httpx.AsyncClient) -> TallyReport:
    rep = TallyReport("1. /api/browser/discover SSRF")
    for url in MALICIOUS_URLS:
        r = await client.post(
            f"{BASE}/api/browser/discover",
            json={"url": url, "platform": "generic"},
        )
        ok, sample = _expect_param_invalid(r, url)
        rep.record(ok, sample)
    # 合法 URL：不应返回 400 PARAM_INVALID（可能 502/200/其它业务错误）
    for url, platform in LEGIT_URLS:
        r = await client.post(
            f"{BASE}/api/browser/discover",
            json={"url": url, "platform": platform, "wait_ms": 1000},
            timeout=30.0,
        )
        sample = {"url": url, "status": r.status_code, "body": r.text[:300]}
        if r.status_code == 400:
            try:
                data = r.json()
                err = data.get("error") if isinstance(data, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                if code == "PARAM_INVALID":
                    rep.record(False, {**sample, "why": "legit URL rejected by guard"})
                    continue
            except Exception:
                pass
        rep.record(True, sample)
    return rep


async def test_fetch_json_local_ssrf(client: httpx.AsyncClient) -> TallyReport:
    """fetch-json-local 只接受本地调用；localhost 上 httpx 是本地，所以能进到 SSRF 检查"""
    rep = TallyReport("2. /api/browser/fetch-json-local SSRF")
    for url in MALICIOUS_URLS:
        r = await client.post(
            f"{BASE}/api/browser/fetch-json-local",
            json={"url": url, "method": "GET"},
        )
        # 先确认进来了（非 403）
        if r.status_code == 403:
            rep.record(False, {"url": url, "status": 403, "why": "blocked as non-local"})
            continue
        ok, sample = _expect_param_invalid(r, url)
        rep.record(ok, sample)
    # page_url 字段
    r = await client.post(
        f"{BASE}/api/browser/fetch-json-local",
        json={
            "url": "https://sycm.taobao.com/api",
            "method": "GET",
            "page_url": "http://127.0.0.1/x",
        },
    )
    sample = {"url": "page_url=127.0.0.1", "status": r.status_code, "body": r.text[:300]}
    ok, sample2 = _expect_param_invalid(r, "page_url=127.0.0.1")
    rep.record(ok, sample2)
    return rep


async def get_skill_id(client: httpx.AsyncClient) -> str | None:
    r = await client.get(f"{BASE}/api/skills/", params={"page_size": 1})
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    items = data.get("items") or data.get("data") or []
    if not items:
        return None
    return items[0].get("id") or items[0].get("skill_id")


async def test_resume_protocol(client: httpx.AsyncClient, skill_id: str) -> TallyReport:
    rep = TallyReport("3. Resume 协议（无 session）")
    # 用 httpx 的 cookie jar 提取 cookie 头
    cookies = client.cookies
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    ws_url = f"{WS_BASE}/api/skills/{skill_id}/workbench/coding/stream"
    extra_headers = {"Cookie": cookie_header} if cookie_header else None

    for attempt in (1, 2):
        sample: dict = {"attempt": attempt}
        try:
            async with websockets.connect(ws_url, extra_headers=extra_headers, open_timeout=10) as ws:
                await ws.send(json.dumps({"type": "resume", "last_seq": 0}))
                # 收第一条消息
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                sample["first_msg"] = msg[:400]
                try:
                    data = json.loads(msg)
                except Exception:
                    rep.record(False, {**sample, "why": "first msg not json"})
                    continue
                # 期望: {"type":"error", "code":"RESUME_NO_SESSION", ...} 后 WS close
                if data.get("type") == "error" and data.get("code") == "RESUME_NO_SESSION":
                    rep.record(True, sample)
                else:
                    # BUG: 如果 server 回的是 INVALID_MESSAGE_TYPE，就是 resume protocol 没接进去
                    rep.record(False, {**sample, "why": f"expected RESUME_NO_SESSION, got type={data.get('type')!r} code={data.get('code')!r}"})
        except Exception as e:
            rep.record(False, {**sample, "why": f"exception: {e!r}"})
    return rep


async def test_verify_login_unknown(client: httpx.AsyncClient) -> TallyReport:
    """三态 contract：unsupported / valid / expired / unknown；不应 fake-valid，也不应 500"""
    rep = TallyReport("4. verify-login 三态")
    # unsupported source
    r = await client.post(
        f"{BASE}/api/browser/verify-login",
        json={"source_id": "platform-not-exists-xyz"},
    )
    sample = {"case": "unsupported", "status_code": r.status_code, "body": r.text[:300]}
    if r.status_code == 200:
        try:
            data = r.json()
            if data.get("status") == "unsupported" and data.get("verified") is False:
                rep.record(True, {**sample, "payload": data})
            else:
                rep.record(False, {**sample, "why": f"expected status=unsupported, got {data}"})
        except Exception as e:
            rep.record(False, {**sample, "why": f"not json: {e}"})
    else:
        rep.record(False, {**sample, "why": "non-200"})
    # supported source (sycm)
    r = await client.post(
        f"{BASE}/api/browser/verify-login",
        json={"source_id": "platform-sycm"},
        timeout=45.0,
    )
    sample2 = {"case": "supported", "status_code": r.status_code, "body": r.text[:400]}
    if r.status_code != 200:
        rep.record(False, {**sample2, "why": "non-200"})
        return rep
    try:
        data = r.json()
    except Exception:
        rep.record(False, {**sample2, "why": "not json"})
        return rep
    status = data.get("status")
    if status in ("valid", "expired", "unknown"):
        verified = data.get("verified")
        if status == "valid" and verified is not True:
            rep.record(False, {**sample2, "payload": data, "why": "valid但verified!=True"})
        elif status in ("expired", "unknown") and verified is True:
            rep.record(False, {**sample2, "payload": data, "why": f"{status}但verified=True"})
        else:
            rep.record(True, {**sample2, "payload": data})
    else:
        rep.record(False, {**sample2, "payload": data, "why": f"unexpected status={status!r}"})
    return rep


async def main() -> int:
    async with httpx.AsyncClient(timeout=15.0) as client:
        await login(client)
        reports: list[TallyReport] = []
        reports.append(await test_discover_ssrf(client))
        reports.append(await test_fetch_json_local_ssrf(client))
        reports.append(await test_verify_login_unknown(client))
        skill_id = await get_skill_id(client)
        if skill_id:
            reports.append(await test_resume_protocol(client, skill_id))
        else:
            rep = TallyReport("3. Resume 协议（无 session）")
            rep.record(False, {"why": "no skill in DB, skip"})
            reports.append(rep)

    total_fail = 0
    for r in reports:
        r.print()
        total_fail += r.failed
    print(f"\n=== 总失败: {total_fail} ===")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
