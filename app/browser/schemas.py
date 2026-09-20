"""浏览器采集结果的 Pydantic schema 校验。

每个 (platform, task_type) 二元组对应一个 schema；采集器返回结果必须通过校验
才视为成功。校验失败抛 CollectionValidationError，由上层捕获并写入 task.error_message。
未注册的 (platform, task_type) 不做校验，直接透传，保持向后兼容。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CollectionValidationError(Exception):
    """采集结果未通过 schema 校验。不应被 retry 重试。"""

    def __init__(self, platform: str, task_type: str, detail: str):
        self.platform = platform
        self.task_type = task_type
        self.detail = detail
        super().__init__(f"{platform}.{task_type} 校验失败: {detail}")


class SycmMetric(BaseModel):
    model_config = ConfigDict(extra="allow")
    label: str = Field(..., min_length=1, max_length=40)
    value: str = Field(..., min_length=1, max_length=200)
    unit: str | None = Field(None, max_length=20)


class SycmDashboardResult(BaseModel):
    """生意参谋经营概览采集结果。"""
    model_config = ConfigDict(extra="allow")
    metrics: list[SycmMetric] = Field(..., min_length=1, description="至少返回 1 条指标")
    source: Literal["api", "dom"] = Field(..., description="数据来源标记")
    page_url: str | None = None


class SycmLoginResult(BaseModel):
    """生意参谋登录态检测结果。

    三态 status：
    - valid：URL 在 dashboard + 业务 API 明确成功
    - expired：URL 跳登录 或 业务 API 明确 401/403/NOT_LOGIN
    - unknown：探测异常 / 非常规状态，不要回退成 valid（避免心跳假阳）
    """
    model_config = ConfigDict(extra="allow")
    logged_in: bool
    status: Literal["valid", "expired", "unknown"] = "unknown"
    current_url: str = Field(..., min_length=1)
    detail: str | None = None


_SCHEMA_REGISTRY: dict[tuple[str, str], type[BaseModel]] = {
    ("sycm", "dashboard"): SycmDashboardResult,
    ("sycm", "check_login"): SycmLoginResult,
}


def get_schema(platform: str, task_type: str) -> type[BaseModel] | None:
    return _SCHEMA_REGISTRY.get((platform, task_type))


def validate_collection_result(platform: str, task_type: str, data: Any) -> dict:
    """校验 (platform, task_type) 采集结果。

    - 已注册 schema 且校验通过 → 返回 model_dump（剥离 None 字段）
    - 已注册 schema 但校验失败 → 抛 CollectionValidationError
    - 未注册 schema → 透传 dict 或包成 {"data": data}
    """
    schema_cls = get_schema(platform, task_type)
    if schema_cls is None:
        return data if isinstance(data, dict) else {"data": data}
    try:
        validated = schema_cls.model_validate(data)
    except ValidationError as exc:
        # 只取前 3 个错误，避免日志爆炸
        errs = exc.errors()[:3]
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs)
        raise CollectionValidationError(platform, task_type, detail) from exc
    return validated.model_dump(exclude_none=True)
