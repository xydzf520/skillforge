"""Schema 校验 (app.browser.schemas) 单元测试。"""
import pytest

from app.browser.schemas import (
    CollectionValidationError,
    SycmDashboardResult,
    SycmLoginResult,
    get_schema,
    validate_collection_result,
)


def test_dashboard_schema_ok():
    data = {
        "metrics": [
            {"label": "销售额", "value": "¥3,540,000"},
            {"label": "订单数", "value": "1234", "unit": "单"},
        ],
        "source": "api",
        "page_url": "https://sycm.taobao.com/portal/home.htm",
    }
    out = validate_collection_result("sycm", "dashboard", data)
    assert out["source"] == "api"
    assert len(out["metrics"]) == 2
    assert out["metrics"][1]["unit"] == "单"


def test_dashboard_schema_rejects_empty_metrics():
    with pytest.raises(CollectionValidationError) as exc:
        validate_collection_result(
            "sycm", "dashboard",
            {"metrics": [], "source": "dom"},
        )
    # detail 应该提到 metrics 字段
    assert "metrics" in exc.value.detail


def test_dashboard_schema_rejects_bad_source():
    with pytest.raises(CollectionValidationError):
        validate_collection_result(
            "sycm", "dashboard",
            {"metrics": [{"label": "x", "value": "1"}], "source": "fallback"},
        )


def test_dashboard_schema_rejects_oversized_label():
    with pytest.raises(CollectionValidationError):
        validate_collection_result(
            "sycm", "dashboard",
            {
                "metrics": [{"label": "a" * 50, "value": "1"}],
                "source": "dom",
            },
        )


def test_login_schema_ok():
    out = validate_collection_result(
        "sycm", "check_login",
        {"logged_in": True, "current_url": "https://sycm.taobao.com/portal/home.htm"},
    )
    assert out["logged_in"] is True
    assert "current_url" in out


def test_login_schema_requires_url():
    with pytest.raises(CollectionValidationError):
        validate_collection_result(
            "sycm", "check_login",
            {"logged_in": False, "current_url": ""},
        )


def test_unregistered_platform_passes_through():
    """未注册的 (platform, task_type) 不做校验。"""
    out = validate_collection_result(
        "douyin", "live_data",
        {"foo": "bar"},
    )
    assert out == {"foo": "bar"}


def test_unregistered_platform_with_non_dict():
    out = validate_collection_result("foo", "bar", "raw_string")
    assert out == {"data": "raw_string"}


def test_get_schema_lookup():
    assert get_schema("sycm", "dashboard") is SycmDashboardResult
    assert get_schema("sycm", "check_login") is SycmLoginResult
    assert get_schema("unknown", "x") is None
