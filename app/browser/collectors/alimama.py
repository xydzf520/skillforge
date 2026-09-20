"""阿里妈妈采集器：登录态探测与账号报表页轻量检查。"""

from app.browser.collector_base import BaseCollector, register_collector


ALIMAMA_ACCOUNT_URL = "https://one.alimama.com/index.html#!/report/account?rptType=account"
# checkAccess 必须带一个有效业务线 bizCode；不带时会返回「bizCode未找到」，
# 但这不能代表登录态失效。universalBP 在搜索/展示推广页都可用作登录态探针。
ALIMAMA_CHECK_ACCESS_URL = "https://one.alimama.com/member/checkAccess.json?bizCode=universalBP"


@register_collector
class AlimamaCollector(BaseCollector):
    """阿里妈妈平台采集器。"""

    platform = "alimama"

    async def collect(self, task_type: str, params: dict) -> dict:
        dispatch = {
            "check_login": self._check_login,
            "dashboard": self._check_login,
        }
        fn = dispatch.get(task_type)
        if not fn:
            raise ValueError(f"alimama 不支持: {task_type}，可用: {list(dispatch.keys())}")
        return await fn(params)

    async def _check_login(self, params: dict) -> dict:
        url = params.get("url") or ALIMAMA_ACCOUNT_URL
        wait_ms = int(params.get("wait_ms") or 4000)

        await self.enable_resource_blocking()
        await self.navigate(url, wait_ms=wait_ms)
        access = await self.fetch_json(ALIMAMA_CHECK_ACCESS_URL, method="POST")

        data = access.get("data") if isinstance(access, dict) else None
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            top_info = access.get("info") if isinstance(access, dict) else None
            info = top_info if isinstance(top_info, dict) else {}

        page = await self.evaluate("""
        (() => ({
          url: location.href,
          title: document.title,
          bodyText: (document.body && document.body.innerText || '').slice(0, 300)
        }))()
        """)
        current_url = (page or {}).get("url") or url
        detail = info.get("message") or "阿里妈妈 checkAccess 未返回登录态信息"
        is_login_route = "#!/login" in current_url or "/login/" in current_url
        logged_in = bool(info.get("ok")) and not is_login_route
        if logged_in:
            status = "valid"
            meta = data.get("meta") if isinstance(data, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            nick = meta.get("nickName") if isinstance(meta, dict) else ""
            detail = f"登录有效：{nick}" if nick else "登录有效"
        elif is_login_route or "未登录" in detail:
            status = "expired"
            detail = "未登录，请重新登录"
        else:
            status = "unknown"

        return {
            "logged_in": logged_in,
            "status": status,
            "detail": detail,
            "current_url": current_url,
            "check_url": ALIMAMA_CHECK_ACCESS_URL,
            "page": page or {"url": url},
            "access": access,
        }
