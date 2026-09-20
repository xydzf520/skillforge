"""北京时间工具 — 项目统一使用 Asia/Shanghai 时区。"""
from datetime import datetime
from zoneinfo import ZoneInfo

BJT = ZoneInfo("Asia/Shanghai")


def now_bjt() -> datetime:
    """返回当前北京时间（naive datetime，无时区标记）。

    API 序列化时由 main.py 的 _patch_fastapi_datetime_encoder 自动附加 +08:00。
    DB 列是 timestamp without time zone，naive 存储保持兼容。
    """
    return datetime.now(BJT).replace(tzinfo=None)


def to_bjt_naive(value: datetime | None) -> datetime | None:
    """把 aware datetime 转成北京时间 naive；naive 输入视为已是北京时间。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(BJT).replace(tzinfo=None)


def parse_bjt_datetime(value: str) -> datetime:
    """解析 ISO 时间；带时区输入转北京时间，naive 输入按北京时间保留。"""
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = f"{raw[:-1]}+00:00"
    parsed = to_bjt_naive(datetime.fromisoformat(raw))
    assert parsed is not None
    return parsed


def isoformat_bjt(value: datetime | None) -> str | None:
    """输出带 +08:00 的 ISO 字符串，供手写 dict 响应使用。"""
    if value is None:
        return None
    normalized = to_bjt_naive(value)
    if normalized is None:
        return None
    return normalized.replace(tzinfo=BJT).isoformat()
