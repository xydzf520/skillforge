import pytest

from app.dingtalk import client as client_mod
from app.dingtalk.client import DingTalkClient


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self.payload


def test_work_notice_without_buttons_uses_markdown_message():
    msg = DingTalkClient._build_work_notice_msg({"title": "Alert", "markdown": "**Alert**"})

    assert msg == {
        "msgtype": "markdown",
        "markdown": {"title": "Alert", "text": "**Alert**"},
    }


def test_work_notice_with_buttons_uses_action_card(monkeypatch):
    monkeypatch.setattr(client_mod.settings, "PUBLIC_BASE_URL", "http://skillforge.example.com")

    msg = DingTalkClient._build_work_notice_msg({
        "title": "Task",
        "markdown": "Open task",
        "buttons": [
            {"title": "Open", "action_url": "/todos/1"},
            {"title": "External", "url": "https://example.com/path"},
        ],
    })

    assert msg["msgtype"] == "action_card"
    assert msg["action_card"]["title"] == "Task"
    assert msg["action_card"]["markdown"] == "Open task"
    assert msg["action_card"]["btn_orientation"] == "1"
    assert msg["action_card"]["btn_json_list"] == [
        {"title": "Open", "action_url": "http://skillforge.example.com/todos/1"},
        {"title": "External", "action_url": "https://example.com/path"},
    ]


@pytest.mark.asyncio
async def test_get_user_id_by_auth_code_normalizes_topapi_result(monkeypatch):
    client = DingTalkClient()

    async def fake_request(method, url, *, params=None, json_body=None, headers=None):
        assert method == "POST"
        assert url.endswith("/topapi/v2/user/getuserinfo")
        assert json_body == {"code": "auth-code"}
        return {
            "ok": True,
            "data": {
                "result": {
                    "userid": "ding-real-user",
                    "unionid": "union-real-user",
                    "name": "真实钉钉用户",
                }
            },
        }

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = await client.get_user_id_by_auth_code(" auth-code ")

    assert result == {
        "ok": True,
        "data": {
            "user_id": "ding-real-user",
            "union_id": "union-real-user",
            "name": "真实钉钉用户",
            "raw": {
                "userid": "ding-real-user",
                "unionid": "union-real-user",
                "name": "真实钉钉用户",
            },
        },
    }


def test_normalize_user_preserves_union_id_for_identity_linking():
    assert DingTalkClient._normalize_user(
        {
            "userid": "16987446933259877",
            "unionid": "union-real-user",
            "name": "童小琳",
        }
    )["union_id"] == "union-real-user"


@pytest.mark.asyncio
async def test_get_user_id_by_union_id_normalizes_enterprise_userid(monkeypatch):
    client = DingTalkClient()

    async def fake_request(method, url, *, params=None, json_body=None, headers=None):
        assert method == "POST"
        assert url.endswith("/topapi/user/getbyunionid")
        assert json_body == {"unionid": "union-real-user"}
        return {"ok": True, "data": {"result": {"userid": "16987446933259877"}}}

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = await client.get_user_id_by_union_id(" union-real-user ")

    assert result == {
        "ok": True,
        "data": {
            "user_id": "16987446933259877",
            "union_id": "union-real-user",
            "raw": {"userid": "16987446933259877"},
        },
    }


@pytest.mark.asyncio
async def test_get_access_token_keeps_legacy_oapi_token_when_corp_id_is_set(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            calls.append(("GET", url))
            assert url.endswith("/gettoken")
            return _FakeResponse({"errcode": 0, "access_token": "old-oapi-token", "expires_in": 7200})

        async def post(self, url, json=None):
            calls.append(("POST", url))
            raise AssertionError("get_access_token must not request v1 token")

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeHttpxClient)
    monkeypatch.setattr(client_mod.settings, "DINGTALK_APP_KEY", "appkey")
    monkeypatch.setattr(client_mod.settings, "DINGTALK_APP_SECRET", "appsecret")
    monkeypatch.setattr(client_mod.settings, "DINGTALK_CORP_ID", "corp-id")

    token = await DingTalkClient().get_access_token()

    assert token == "old-oapi-token"
    assert calls == [("GET", "https://oapi.dingtalk.com/gettoken")]
