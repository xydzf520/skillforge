"""GenericCollector 抓包辅助逻辑测试。"""

from __future__ import annotations

import pytest

from app.browser.collectors.generic import GenericCollector


class _FakeGeneric(GenericCollector):
    def __init__(self, body: str):
        super().__init__()
        self.body = body

    async def _send(self, method, params=None):
        assert method == "Network.getResponseBody"
        return {"body": self.body, "base64Encoded": False}


@pytest.mark.asyncio
async def test_collect_cdp_response_bodies_keeps_api_body():
    coll = _FakeGeneric('{"data":{"rows":[{"id":1}],"total":1}}')
    events = [
        {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "r1",
                "request": {
                    "url": "https://one.alimama.com/campaign/horizontal/findPage.json",
                    "method": "POST",
                    "postData": '{"page":1,"pageSize":20}',
                },
            },
        },
        {
            "method": "Network.responseReceived",
            "params": {
                "requestId": "r1",
                "type": "XHR",
                "response": {
                    "url": "https://one.alimama.com/campaign/horizontal/findPage.json",
                    "status": 200,
                    "mimeType": "application/json",
                },
            },
        },
    ]

    captured = await coll._collect_cdp_response_bodies(events)

    assert captured[0]["source"] == "cdp_network"
    assert captured[0]["method"] == "POST"
    assert captured[0]["request_body_preview"] == '{"page":1,"pageSize":20}'
    assert captured[0]["body_preview"].startswith('{"data"')
    entry = GenericCollector._make_api_entry(captured[0], (".js", ".css"))
    assert entry is not None
    assert entry["response_keys"] == ["data"]
    assert entry["data_keys"] == ["rows", "total"]
    assert entry["request_body_length"] == 24


def test_looks_like_data_api_for_alimama_script_json():
    assert GenericCollector._looks_like_data_api(
        "https://one.alimama.com/report/query.json?bizCode=onebpSearch",
        "script",
    )
    assert not GenericCollector._looks_like_data_api(
        "https://one.alimama.com/assets/app.js",
        "script",
    )
