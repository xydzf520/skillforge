"""环境属性收集器。

为 ABAC 策略评估提供当前请求的环境上下文（时间、来源 IP、是否生产环境等）。
"""

from __future__ import annotations

from starlette.requests import Request

from app.config import settings
from app.common.time_utils import isoformat_bjt, now_bjt


def collect_environment(request: Request | None = None) -> dict:
    """收集当前请求的环境属性。

    返回标准化的环境 dict，供 ABAC 策略引擎的 environment_condition 评估使用。
    """
    now = now_bjt()
    env: dict = {
        "is_production": not settings.DEBUG,
        "timestamp": isoformat_bjt(now),
        "hour": now.hour,
        "weekday": now.weekday(),  # 0=周一, 6=周日
        "is_business_hours": 9 <= now.hour <= 18,
    }

    if request:
        env["ip"] = request.client.host if request.client else None
        env["is_export"] = request.url.path.endswith("/export")
        env["method"] = request.method
        env["path"] = request.url.path

    return env
