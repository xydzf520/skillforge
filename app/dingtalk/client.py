"""
钉钉API客户端：token管理 + 工作通知发送 + 互动卡片 + 审批。
所有钉钉消息先通过outbox入队，由后台协程消费发送。
"""

import time
from typing import Any

import httpx
from loguru import logger

from app.config import settings


class DingTalkClient:
    """钉钉企业内部应用API封装"""

    BASE_URL = "https://oapi.dingtalk.com"

    def __init__(self):
        self._token: str | None = None
        self._token_expires_at: float = 0
        self._v1_token: str | None = None
        self._v1_token_expires_at: float = 0

    @staticmethod
    def _normalize_department(item: dict[str, Any]) -> dict[str, Any]:
        dept_id = item.get("dept_id") or item.get("id") or item.get("department_id") or item.get("deptId")
        parent_dept_id = item.get("parent_dept_id") or item.get("parent_id") or item.get("parentDeptId")
        return {
            "dept_id": str(dept_id) if dept_id is not None else "",
            "name": item.get("name") or item.get("dept_name") or item.get("deptName") or "",
            "parent_dept_id": str(parent_dept_id) if parent_dept_id not in (None, "") else None,
            "order": item.get("order") or item.get("sort") or item.get("sort_order") or 0,
            "manager_user_id": item.get("manager_userid") or item.get("manager_user_id") or item.get("leader_userid"),
            "raw": item,
        }

    @staticmethod
    def _normalize_user(item: dict[str, Any]) -> dict[str, Any]:
        user_id = item.get("userid") or item.get("user_id") or item.get("id") or item.get("userId")
        return {
            "user_id": str(user_id) if user_id is not None else "",
            "union_id": item.get("unionid") or item.get("unionId") or item.get("union_id") or "",
            "name": item.get("name") or item.get("user_name") or "",
            "avatar": item.get("avatar") or item.get("avatarUrl") or item.get("avatar_url") or "",
            "email": item.get("email") or "",
            "mobile": item.get("mobile") or item.get("phone") or "",
            "is_manager": bool(item.get("is_manager") or item.get("leader") or item.get("is_main_department")),
            "raw": item,
        }

    async def get_access_token(self) -> str:
        """获取旧版 oapi access_token（自动缓存，过期前10分钟刷新）。"""
        if self._token and time.time() < self._token_expires_at - 600:
            return self._token

        if not settings.DINGTALK_APP_KEY or not settings.DINGTALK_APP_SECRET:
            logger.warning("钉钉AppKey/Secret未配置，跳过token获取")
            return ""

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.BASE_URL}/gettoken",
                params={
                    "appkey": settings.DINGTALK_APP_KEY,
                    "appsecret": settings.DINGTALK_APP_SECRET,
                },
            )
            data = resp.json()

        if data.get("errcode") != 0:
            safe_data = {"errcode": data.get("errcode"), "errmsg": data.get("errmsg")}
            logger.error(f"获取钉钉token失败: {safe_data}")
            return ""

        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 7200)
        return self._token

    async def get_v1_access_token(self) -> str:
        """获取新版 v1.0 API app token，仅用于明确调用 api.dingtalk.com/v1.0 的兜底路径。"""
        if self._v1_token and time.time() < self._v1_token_expires_at - 600:
            return self._v1_token

        if not settings.DINGTALK_APP_KEY or not settings.DINGTALK_APP_SECRET:
            logger.warning("钉钉AppKey/Secret未配置，跳过v1 token获取")
            return ""
        if not settings.DINGTALK_CORP_ID:
            return ""

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.dingtalk.com/v1.0/oauth2/{settings.DINGTALK_CORP_ID}/token",
                    json={
                        "client_id": settings.DINGTALK_APP_KEY,
                        "client_secret": settings.DINGTALK_APP_SECRET,
                        "grant_type": "client_credentials",
                    },
                )
                data = resp.json()
        except Exception as e:
            logger.warning(f"新版钉钉token接口异常: {e}")
            return ""

        if data.get("access_token"):
            self._v1_token = data["access_token"]
            self._v1_token_expires_at = time.time() + data.get("expires_in", 7200)
            return self._v1_token

        logger.warning(f"新版钉钉token获取失败: {data.get('message', data.get('errmsg', ''))}")
        return ""

    async def _request_json(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                            json_body: dict[str, Any] | None = None,
                            headers: dict[str, str] | None = None) -> dict[str, Any]:
        token = await self.get_access_token()
        if not token:
            return {"ok": False, "error": "无token"}

        request_headers = dict(headers or {})
        request_params = dict(params or {})
        request_params.setdefault("access_token", token)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, url, params=request_params, json=json_body, headers=request_headers)
            try:
                data = resp.json()
            except Exception:
                return {"ok": False, "error": resp.text or "响应不是JSON", "status_code": resp.status_code}

        if resp.status_code >= 400:
            return {"ok": False, "error": resp.text, "status_code": resp.status_code, "data": data}

        if isinstance(data, dict) and data.get("errcode") not in (None, 0):
            return {"ok": False, "error": data.get("errmsg", "未知错误"), "data": data}

        return {"ok": True, "data": data}

    async def list_sub_departments(self, dept_id: str | int = 1) -> dict[str, Any]:
        """拉取某部门下的直接子部门。"""
        result = await self._request_json(
            "POST",
            f"{self.BASE_URL}/topapi/v2/department/listsub",
            json_body={"dept_id": dept_id},
        )
        if not result["ok"]:
            return result

        payload = result["data"] or {}
        raw_departments = payload.get("result") or []
        if isinstance(raw_departments, dict):
            raw_departments = raw_departments.get("dept_list") or raw_departments.get("list") or []
        departments = [
            self._normalize_department(item)
            for item in raw_departments
            if isinstance(item, dict)
        ]
        return {"ok": True, "data": departments}

    async def get_department_tree(self, root_dept_id: str | int = 1) -> dict[str, Any]:
        """递归拉取完整部门树，返回 root 下的所有部门节点。"""
        result = await self.list_sub_departments(root_dept_id)
        if not result["ok"]:
            return result

        async def _walk(nodes: list[dict[str, Any]]) -> dict[str, Any]:
            tree: list[dict[str, Any]] = []
            for node in nodes:
                child_result = await self.list_sub_departments(node["dept_id"])
                if not child_result["ok"]:
                    return child_result
                node_with_children = dict(node)
                subtree = await _walk(child_result["data"])
                if not subtree["ok"]:
                    return subtree
                node_with_children["children"] = subtree["data"]
                tree.append(node_with_children)
            return {"ok": True, "data": tree}

        return await _walk(result["data"])

    async def list_department_users(self, dept_id: str | int, size: int = 100) -> dict[str, Any]:
        """分页拉取部门成员并做一次归一化。"""
        users: list[dict[str, Any]] = []
        cursor: str | int = 0

        while True:
            result = await self._request_json(
                "POST",
                f"{self.BASE_URL}/topapi/v2/user/list",
                json_body={"dept_id": dept_id, "cursor": cursor, "size": size},
            )
            if not result["ok"]:
                return result

            payload = result["data"] or {}
            raw_result = payload.get("result") or {}
            raw_users = raw_result.get("list") or raw_result.get("users") or []
            users.extend(
                self._normalize_user(item)
                for item in raw_users
                if isinstance(item, dict)
            )

            has_more = raw_result.get("has_more")
            next_cursor = raw_result.get("next_cursor") or raw_result.get("nextCursor") or payload.get("next_cursor")
            if not has_more:
                break
            if next_cursor in (None, ""):
                break
            cursor = next_cursor

        return {"ok": True, "data": users}

    async def get_user_detail(self, user_id: str) -> dict[str, Any]:
        """查询企业通讯录内的用户。

        OAuth 登录拿到的 openId/userId 只有通过企业应用 token 再查一次通讯录，
        才能确认该扫码人确实属于当前企业。
        """
        user_id = str(user_id or "").strip()
        if not user_id:
            return {"ok": False, "error": "empty_user_id"}

        # 1. 旧 API：兼容传统 userId 格式
        result = await self._request_json(
            "POST",
            f"{self.BASE_URL}/topapi/v2/user/get",
            json_body={"userid": user_id, "language": "zh_CN"},
        )
        if result["ok"]:
            payload = result["data"] or {}
            raw = payload.get("result") or payload.get("user_info") or payload
            if isinstance(raw, dict):
                normalized = self._normalize_user(raw)
                if not normalized["user_id"]:
                    normalized["user_id"] = user_id
                dept_ids = (
                    raw.get("dept_id_list")
                    or raw.get("deptIdList")
                    or raw.get("department")
                    or raw.get("dept_ids")
                    or []
                )
                if isinstance(dept_ids, (str, int)):
                    dept_ids = [dept_ids]
                normalized["dept_id_list"] = [str(item) for item in dept_ids if item not in (None, "")]
                normalized["raw"] = raw
                return {"ok": True, "data": normalized}

        # 2. 新式应用回退：旧 API 找不到用户时，用新 v1.0 API 重试真实 userid。
        app_token = await self.get_v1_access_token()
        if settings.DINGTALK_CORP_ID and app_token:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"https://api.dingtalk.com/v1.0/contact/users/{user_id}",
                        headers={"x-acs-dingtalk-access-token": app_token},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = data or {}
                        if isinstance(raw, dict) and (raw.get("userId") or raw.get("userid")):
                            normalized = self._normalize_user(raw)
                            if not normalized["user_id"]:
                                normalized["user_id"] = (
                                    raw.get("userId") or raw.get("userid") or user_id
                                )
                            dept_ids = raw.get("deptIdList") or raw.get("dept_id_list") or []
                            if isinstance(dept_ids, (str, int)):
                                dept_ids = [dept_ids]
                            normalized["dept_id_list"] = [str(i) for i in dept_ids if i not in (None, "")]
                            normalized["raw"] = raw
                            return {"ok": True, "data": normalized}
                        logger.info(f"钉钉新API用户查询返回空: user_id={user_id} status={resp.status_code}")
                    elif resp.status_code == 403:
                        logger.info(f"钉钉新API Contact.User.Read 权限未开通，回退失败: user_id={user_id}")
                    else:
                        logger.warning(f"钉钉新API用户查询失败: user_id={user_id} status={resp.status_code} body={resp.text[:200]}")
            except Exception as e:
                logger.warning(f"钉钉新API用户查询异常: user_id={user_id} error={e}")

        # 旧 API 失败且新 API 不可用 → 返回旧 API 的错误信息
        return result

    async def get_user_id_by_auth_code(self, auth_code: str) -> dict[str, Any]:
        """用钉钉免登码换企业通讯录 userid。

        新 OAuth 的 ``contact/users/me`` 在权限不足时可能只返回 openId。
        ``topapi/v2/user/getuserinfo`` 是企业内部应用默认可用的免登接口，
        能用同一个回调 code 补出真实 userid，供后续通讯录校验和工作通知使用。
        """
        code = str(auth_code or "").strip()
        if not code:
            return {"ok": False, "error": "empty_auth_code"}

        result = await self._request_json(
            "POST",
            f"{self.BASE_URL}/topapi/v2/user/getuserinfo",
            json_body={"code": code},
        )
        if not result["ok"]:
            return result

        payload = result["data"] or {}
        raw = payload.get("result") or payload.get("user_info") or payload
        if not isinstance(raw, dict):
            return {"ok": False, "error": "invalid_userinfo_payload", "data": payload}

        user_id = raw.get("userid") or raw.get("userId") or raw.get("user_id")
        if not user_id:
            return {"ok": False, "error": "missing_userid", "data": payload}

        return {
            "ok": True,
            "data": {
                "user_id": str(user_id),
                "union_id": raw.get("unionid") or raw.get("unionId") or raw.get("union_id") or "",
                "name": raw.get("name") or raw.get("nick") or "",
                "raw": raw,
            },
        }

    async def get_user_id_by_union_id(self, union_id: str) -> dict[str, Any]:
        """Resolve an OAuth unionId to the enterprise address-book userid.

        DingTalk OAuth identities and enterprise directory identities are
        different namespaces.  ``contact/users/me`` may only expose openId and
        unionId, while organization membership, work notices and SkillForge
        department permissions must use the enterprise userid.  This official
        conversion is therefore the safe bridge between both namespaces.
        """
        normalized_union_id = str(union_id or "").strip()
        if not normalized_union_id:
            return {"ok": False, "error": "empty_union_id"}

        result = await self._request_json(
            "POST",
            f"{self.BASE_URL}/topapi/user/getbyunionid",
            json_body={"unionid": normalized_union_id},
        )
        if not result["ok"]:
            return result

        payload = result["data"] or {}
        raw = payload.get("result") or payload.get("user_info") or payload
        if not isinstance(raw, dict):
            return {"ok": False, "error": "invalid_union_user_payload", "data": payload}

        user_id = raw.get("userid") or raw.get("userId") or raw.get("user_id")
        if not user_id:
            return {"ok": False, "error": "missing_userid", "data": payload}

        return {
            "ok": True,
            "data": {
                "user_id": str(user_id),
                "union_id": normalized_union_id,
                "raw": raw,
            },
        }

    @staticmethod
    def _build_work_notice_msg(content: dict) -> dict[str, Any]:
        """Build a DingTalk asyncsend_v2 message body for work notices."""
        title = content.get("title") or "SkillForge通知"
        markdown = content.get("markdown") or title
        buttons = content.get("buttons") or []

        if not buttons:
            return {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": markdown,
                },
            }

        action_card: dict[str, Any] = {
            "title": title,
            "markdown": markdown,
        }

        # 按钮（可选）— 转换为钉钉 btn_json_list 格式。钉钉字段名是 snake_case
        # `action_url`, 这里允许 caller 传 `action_url` / `actionURL` / `url` 任意之一;
        # URL 必须是绝对地址, 相对路径兜底到 `PUBLIC_BASE_URL + /todos`（钉钉只渲染绝对 URL）。
        base = (settings.PUBLIC_BASE_URL or "").rstrip("/")

        def _to_absolute(url: str | None) -> str:
            if not url:
                return f"{base}/todos" if base else "/todos"
            if url.startswith(("http://", "https://")):
                return url
            # 相对路径: 如果 base 可用则拼成绝对, 否则原样返回(由钉钉判断)
            return f"{base}{url}" if base and url.startswith("/") else url

        btn_list = []
        for btn in buttons:
            url = _to_absolute(
                btn.get("action_url") or btn.get("actionURL") or btn.get("url")
            )
            btn_list.append({"title": btn.get("title", ""), "action_url": url})
        action_card["btn_orientation"] = "1"
        action_card["btn_json_list"] = btn_list

        return {
            "msgtype": "action_card",
            "action_card": action_card,
        }

    async def send_work_notice(self, user_id: str, content: dict) -> dict:
        """
        发送工作通知到个人钉钉。
        content: {"title": "...", "markdown": "...", "buttons": [...]}
        """
        token = await self.get_access_token()
        if not token:
            return {"ok": False, "error": "无token"}

        msg = self._build_work_notice_msg(content)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.BASE_URL}/topapi/message/corpconversation/asyncsend_v2",
                params={"access_token": token},
                json={
                    "agent_id": settings.DINGTALK_AGENT_ID,
                    "userid_list": user_id,
                    "msg": msg,
                },
            )
            data = resp.json()

        if data.get("errcode") != 0:
            logger.error(f"发送工作通知失败: {data}")
            return {"ok": False, "error": data.get("errmsg", "未知错误")}

        return {"ok": True, "task_id": data.get("task_id")}

    async def send_interactive_card(self, user_id: str, card_data: dict) -> dict:
        """发送互动卡片（带回调按钮）"""
        token = await self.get_access_token()
        if not token:
            return {"ok": False, "error": "无token"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver",
                headers={
                    "x-acs-dingtalk-access-token": token,
                    "Content-Type": "application/json",
                },
                json=card_data,
            )

        if resp.status_code != 200:
            logger.error(f"发送互动卡片失败: {resp.text}")
            return {"ok": False, "error": resp.text}

        return {"ok": True, "data": resp.json()}

    async def update_interactive_card(self, out_track_id: str, card_data: dict) -> dict:
        """更新已发送的互动卡片（T+1效果回更）"""
        token = await self.get_access_token()
        if not token:
            return {"ok": False, "error": "无token"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                "https://api.dingtalk.com/v1.0/card/instances",
                headers={
                    "x-acs-dingtalk-access-token": token,
                    "Content-Type": "application/json",
                },
                json={
                    "outTrackId": out_track_id,
                    "cardData": card_data,
                },
            )

        if resp.status_code != 200:
            logger.warning(f"更新互动卡片失败: {resp.text}")
            return {"ok": False, "error": resp.text}

        return {"ok": True}

    async def create_approval(self, process_code: str, originator_id: str,
                               approver_id: str, form_data: list) -> dict:
        """发起审批流（L2/L3）"""
        token = await self.get_access_token()
        if not token:
            return {"ok": False, "error": "无token"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.BASE_URL}/topapi/processinstance/create",
                params={"access_token": token},
                json={
                    "process_code": process_code,
                    "originator_user_id": originator_id,
                    "approvers": approver_id,
                    "form_component_values": form_data,
                },
            )
            data = resp.json()

        if data.get("errcode") != 0:
            logger.error(f"创建审批失败: {data}")
            return {"ok": False, "error": data.get("errmsg", "")}

        return {"ok": True, "instance_id": data.get("process_instance_id")}

    async def send(self, msg) -> dict:
        """统一发送入口，根据message_type分发"""
        msg_type = msg.message_type if hasattr(msg, "message_type") else msg.get("message_type", "")
        payload = msg.payload if hasattr(msg, "payload") else msg.get("payload", {})
        recipient = msg.recipient_user_id if hasattr(msg, "recipient_user_id") else msg.get("recipient_user_id", "")

        if msg_type == "work_notice":
            return await self.send_work_notice(recipient, payload)
        elif msg_type == "interactive_card":
            return await self.send_interactive_card(recipient, payload)
        elif msg_type == "approval":
            return await self.create_approval(
                payload.get("process_code", ""),
                payload.get("originator_id", "system"),
                recipient,
                payload.get("form_data", []),
            )
        else:
            return {"ok": False, "error": f"未知消息类型: {msg_type}"}


# 全局实例
dingtalk_client = DingTalkClient()
