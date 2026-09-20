from app.qianchuan.video_content_analysis import collect_qianchuan_video_content_analysis


def _fetch_result(payload, *, url="https://qianchuan.jinritemai.example.test/api"):
    return {
        "success": True,
        "data": {"url": url, "status": 200, "ok": True, "data": payload},
        "proof": {"response_hash": "hash"},
    }


def test_collect_qianchuan_video_content_analysis_parses_click_lifecycle():
    calls = []

    def fake_fetch(spec):
        calls.append(spec)
        url = spec["url"]
        if "statQuery" in url:
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "Rows": [
                        {
                            "Dimensions": {"duration": {"Value": "0"}},
                            "Metrics": {
                                "live_watch_count_for_roi2_v2": {"Value": "1623"},
                                "video_lose_count_for_roi2": {"Value": "10"},
                            },
                        },
                        {
                            "Dimensions": {"duration": {"Value": "1"}},
                            "Metrics": {
                                "live_watch_count_for_roi2_v2": {"Value": "3187"},
                                "video_lose_count_for_roi2": {"Value": "20"},
                            },
                        },
                    ],
                    "Totals": [{
                        "Metrics": {
                            "live_watch_count_for_roi2_v2": {"Value": "43719"},
                            "video_lose_count_for_roi2": {"Value": "6499006"},
                        },
                    }],
                },
            }, url=url)
        if "getContentFormulaAndScript" in url:
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "script": "以后他再说状态不好，你就给他拿出这个示例品牌的战甲延时套。",
                    "formula": ["商品信息", "行动号召"],
                },
            }, url=url)
        if "getContentMaterialAnalysisInfo" in url:
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "material_id": "7639640154815758378",
                    "title": "反正都要买套套",
                    "cost": 1015807.92,
                    "ctr": 0.0093,
                    "my_tag_entry": [
                        {"tag_label": "优惠活动", "tag_name_list": [{"text": "限时优惠"}]},
                    ],
                    "bench_tag_entry": [
                        {"tag_label": "优惠活动", "tag_name_list": [{"text": "限时大促"}, {"text": "限时优惠"}]},
                        {"tag_label": "用户痛点", "tag_name_list": [{"text": "库存有限"}]},
                    ],
                },
            }, url=url)
        return _fetch_result({"status_code": 0, "data": {"list": []}}, url=url)

    result = collect_qianchuan_video_content_analysis(
        {
            "aavid": "1855723231649801",
            "material_id": "7639640154815758378",
            "vid": "v28033gi0000d83b7mfog65kbon4tn7g",
            "start_date": "2026-06-08",
            "end_date": "2026-06-14",
        },
        fake_fetch,
    )

    assert result["ok"] is True
    assert result["summary"]["click_total"] == 43719
    assert result["summary"]["zero_second_click"] == 1623
    assert result["summary"]["click_peak"]["duration"] == 1
    assert result["interaction_lifecycle"]["peaks"]["click"]["value"] == 3187
    assert result["content_analysis"]["script"]["available"] is True
    assert "用户痛点" in result["content_analysis"]["creative_gap"]["missing_tag_labels"]
    assert any("statQuery" in call["url"] for call in calls)
    stat_call = next(call for call in calls if "statQuery" in call["url"])
    assert stat_call["body"]["Filters"]["Conditions"][2]["Values"] == ["1"]


def test_collect_qianchuan_video_content_analysis_resolves_short_cloud_video_id():
    calls = []

    def fake_fetch(spec):
        calls.append(spec)
        url = spec["url"]
        if "roi2_material_list" in url:
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "Rows": [
                        {
                            "Dimensions": {
                                "material_name_v2": {"Value": "100329907_原片_示例企业一组_20260604-LWL-HJW_铂金DD_反正都要买套套的_其他_10"},
                                "material_id": {"Value": "7648885564020555795"},
                                "material_content_v2": {"Value": '{"vid":"v28033gi0000d8j4pt7og65matnlah6g"}'},
                                "material_duration_v2": {"Value": "39"},
                            },
                            "Metrics": {
                                "stat_cost_for_roi2": {"Value": "1695.16"},
                                "total_prepay_and_pay_order_roi2": {"Value": "1.36"},
                                "total_pay_order_gmv_include_coupon_for_roi2": {"Value": "2307.90"},
                                "product_click_count_for_roi2": {"Value": "199"},
                            },
                        },
                        {
                            "Dimensions": {
                                "material_name_v2": {"Value": "100000000_原片_其他视频"},
                                "material_id": {"Value": "other-material"},
                            },
                            "Metrics": {"stat_cost_for_roi2": {"Value": "9999"}},
                        },
                    ]
                },
            }, url=url)
        if "roi2_video_material_analysis_insight" in str(spec.get("body")):
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "Rows": [
                        {
                            "Dimensions": {"duration": {"Value": "3"}},
                            "Metrics": {"live_watch_count_for_roi2_v2": {"Value": "19"}},
                        },
                    ],
                    "Totals": [{"Metrics": {"live_watch_count_for_roi2_v2": {"Value": "199"}}}],
                },
            }, url=url)
        if "getContentFormulaAndScript" in url:
            assert "v28033gi0000d8j4pt7og65matnlah6g" in url
            return _fetch_result({"status_code": 0, "data": {"script": "反正都要买套套，为什么不能是示例品牌。"}}, url=url)
        return _fetch_result({"status_code": 0, "data": {}}, url=url)

    result = collect_qianchuan_video_content_analysis(
        {
            "aavid": "1855723231649801",
            "cloud_video_id": "100329907",
            "material_name": "示例企业一组_20260604-LWL-HJW_铂金DD_反正都要买套套的_其他_10",
            "start_date": "2026-06-08",
            "end_date": "2026-06-14",
            "include_top_videos": False,
        },
        fake_fetch,
    )

    assert result["ok"] is True
    assert result["summary"]["material_id"] == "7648885564020555795"
    assert result["summary"]["cloud_video_id"] == "100329907"
    assert result["summary"]["click_total"] == 199
    assert result["resolved_material"]["vid"] == "v28033gi0000d8j4pt7og65matnlah6g"
    assert result["resolved_material"]["cost"] == 1695.16
    assert result["content_analysis"]["script"]["available"] is True
    search_call = next(call for call in calls if "roi2_material_list" in call["url"])
    assert search_call["body"]["DataSetKey"] == "roi2_video_material_analysis"
    assert search_call["body"]["Filters"]["Conditions"][3]["Field"] == "material_type"
    assert {"Field": "material_name_v2", "Values": ["100329907"], "Operator": 7} in search_call["body"]["Filters"]["Conditions"]
    stat_call = next(call for call in calls if "roi2_video_material_analysis_insight" in str(call.get("body")))
    conditions = stat_call["body"]["Filters"]["Conditions"]
    assert {"Field": "material_id", "Values": ["7648885564020555795"], "Operator": 7} in conditions


def test_collect_qianchuan_video_content_analysis_prefers_high_cost_same_short_id_candidate():
    calls = []

    def fake_fetch(spec):
        calls.append(spec)
        url = spec["url"]
        if "roi2_material_list" in url:
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "Rows": [
                        {
                            "Dimensions": {
                                "material_name_v2": {"Value": "96289777_衍生74017535_示例企业一组_20260513-LWL-MJL_战甲AA_以后他在说状态不好_其他_03_新"},
                                "material_id": {"Value": "low-derived"},
                                "material_content_v2": {"Value": '{"vid":"low-vid"}'},
                            },
                            "Metrics": {
                                "stat_cost_for_roi2": {"Value": "56.46"},
                                "total_pay_order_gmv_include_coupon_for_roi2": {"Value": "89.90"},
                                "product_click_count_for_roi2": {"Value": "15"},
                            },
                        },
                        {
                            "Dimensions": {
                                "material_name_v2": {"Value": "96289777_原片_示例企业一组_20260513-LWL-MJL_战甲持久_以后他在说状态不好_其他_03_新"},
                                "material_id": {"Value": "high-original"},
                                "material_content_v2": {"Value": '{"vid":"high-vid"}'},
                            },
                            "Metrics": {
                                "stat_cost_for_roi2": {"Value": "293217.39"},
                                "total_pay_order_gmv_include_coupon_for_roi2": {"Value": "329976.83"},
                                "product_click_count_for_roi2": {"Value": "58838"},
                            },
                        },
                    ]
                },
            }, url=url)
        if "roi2_video_material_analysis_insight" in str(spec.get("body")):
            return _fetch_result({
                "status_code": 0,
                "data": {
                    "Rows": [
                        {
                            "Dimensions": {"duration": {"Value": "1"}},
                            "Metrics": {"live_watch_count_for_roi2_v2": {"Value": "3187"}},
                        },
                    ],
                    "Totals": [{"Metrics": {"live_watch_count_for_roi2_v2": {"Value": "43719"}}}],
                },
            }, url=url)
        if "getContentFormulaAndScript" in url:
            assert "high-vid" in url
            return _fetch_result({"status_code": 0, "data": {"script": "以后他再说状态不好。"}}, url=url)
        return _fetch_result({"status_code": 0, "data": {}}, url=url)

    result = collect_qianchuan_video_content_analysis(
        {
            "aavid": "1855723231649801",
            "cloud_video_id": "96289777",
            "material_name": "示例企业一组_20260513-LWL-MJL_战甲AA_以后他在说状态不好_其他_03_新",
            "start_date": "2026-06-08",
            "end_date": "2026-06-14",
            "include_top_videos": False,
        },
        fake_fetch,
    )

    assert result["ok"] is True
    assert result["summary"]["material_id"] == "high-original"
    assert result["resolved_material"]["vid"] == "high-vid"
    assert result["resolved_material"]["cost"] == 293217.39
    stat_call = next(call for call in calls if "roi2_video_material_analysis_insight" in str(call.get("body")))
    assert {"Field": "material_id", "Values": ["high-original"], "Operator": 7} in stat_call["body"]["Filters"]["Conditions"]
