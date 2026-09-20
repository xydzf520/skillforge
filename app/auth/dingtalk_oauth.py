"""
钉钉扫码登录OAuth2.0流程。
用户扫码 → 钉钉回调携带authCode → 换取用户信息 → 创建session。
"""

import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.audit import audit
from app.common.cache import _get_pool
from app.common.time_utils import now_bjt
from app.config import settings
from app.dingtalk.client import dingtalk_client
from app.org.models import OrgUnit, UserOrgMembership

_STATE_TTL_SECONDS = 600
_state_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="dingtalk-oauth-state")


def _state_replay_key(nonce: str) -> str:
    digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    return f"sf:auth:dingtalk:state:{digest}"
DINGTALK_STATE_COOKIE = "skillforge_dingtalk_state"

def _create_state_token() -> str:
    """生成签名state，支持多worker共享SECRET_KEY的部署场景。"""
    payload = {
        "purpose": "dingtalk_oauth",
        "nonce": secrets.token_urlsafe(16),
    }
    return _state_signer.dumps(payload)


def get_dingtalk_login_url() -> tuple[str, str]:
    """生成钉钉扫码登录页面URL，返回 (url, state)"""
    state = _create_state_token()
    params = {
        "client_id": settings.DINGTALK_APP_KEY,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": settings.DINGTALK_LOGIN_REDIRECT,
        "state": state,
        "prompt": "consent",
    }
    return f"https://login.dingtalk.com/oauth2/auth?{urlencode(params)}", state


async def verify_state(state: str) -> bool:
    """校验签名state，并通过 Redis 做一次性消费，防止 TTL 窗口内重放。"""
    try:
        payload = _state_signer.loads(state, max_age=_STATE_TTL_SECONDS)
    except SignatureExpired:
        logger.warning("钉钉OAuth state已过期")
        return False
    except BadSignature:
        logger.warning("钉钉OAuth state签名非法")
        return False

    if not isinstance(payload, dict) or payload.get("purpose") != "dingtalk_oauth":
        logger.warning("钉钉OAuth state载荷非法")
        return False

    nonce = payload.get("nonce")
    if not nonce or not isinstance(nonce, str):
        logger.warning("钉钉OAuth state缺少nonce")
        return False

    try:
        pool = _get_pool()
        consumed = await pool.set(_state_replay_key(nonce), "1", ex=_STATE_TTL_SECONDS, nx=True)
    except Exception as e:
        logger.error(f"钉钉OAuth state防重放校验失败: {e}")
        return False

    if not consumed:
        logger.warning("钉钉OAuth state已被消费，疑似重放")
        return False

    return True


async def handle_dingtalk_callback(
    db: AsyncSession,
    auth_code: str,
    ip_address: str | None = None,
) -> User | None:
    """
    处理钉钉扫码回调：
    1. 用authCode换取access_token
    2. 用token获取用户信息（userId, name, dept）
    3. 查找或创建本地用户
    4. 返回User对象
    """
    if not settings.DINGTALK_APP_KEY:
        logger.error("钉钉AppKey未配置")
        return None

    try:
        # 1. 用authCode换token
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                json={
                    "clientId": settings.DINGTALK_APP_KEY,
                    "clientSecret": settings.DINGTALK_APP_SECRET,
                    "code": auth_code,
                    "grantType": "authorization_code",
                },
            )
            token_data = token_resp.json()

        access_token = token_data.get("accessToken")
        if not access_token:
            logger.error(f"钉钉token获取失败: {token_data}")
            return None

        # 2. 获取用户信息
        async with httpx.AsyncClient(timeout=10) as client:
            user_resp = await client.get(
                "https://api.dingtalk.com/v1.0/contact/users/me",
                headers={"x-acs-dingtalk-access-token": access_token},
            )
            user_data = user_resp.json()

        oauth_user_id = str(user_data.get("userId") or "").strip()
        open_id = str(user_data.get("openId") or "").strip()
        union_id = user_data.get("unionId", "")
        name = user_data.get("nick", "钉钉用户")
        avatar = user_data.get("avatarUrl", "")

        # OAuth contact/users/me 在新式应用里经常只给 openId，或 userId 也是
        # openId 风格；工作通知和部门查询必须使用企业通讯录 userid。
        try:
            userinfo_result = await dingtalk_client.get_user_id_by_auth_code(auth_code)
        except Exception as exc:  # noqa: BLE001
            userinfo_result = {"ok": False, "error": str(exc)}
        userinfo = (userinfo_result.get("data") or {}) if userinfo_result.get("ok") else {}
        if userinfo_result.get("ok"):
            union_id = union_id or userinfo.get("union_id", "")
            name = userinfo.get("name") or name
        auth_code_user_id = str(userinfo.get("user_id") or "").strip()
        verified_user_id = ""
        verified_detail: dict | None = None
        identity_source = "auth_code_unverified" if auth_code_user_id else "oauth_fallback"

        # Do not trust the field name alone: some DingTalk application modes
        # return an OAuth openId in a userId-shaped field.  Confirm it against
        # the enterprise address book before preferring it over other signals.
        if auth_code_user_id:
            try:
                candidate_detail = await dingtalk_client.get_user_detail(auth_code_user_id)
            except Exception as exc:  # noqa: BLE001
                candidate_detail = {"ok": False, "error": str(exc)}
            candidate_data = candidate_detail.get("data") or {}
            candidate_depts = candidate_data.get("dept_id_list") or []
            if candidate_detail.get("ok") and candidate_data.get("user_id") and candidate_depts:
                verified_user_id = str(candidate_data.get("user_id") or auth_code_user_id).strip()
                verified_detail = candidate_detail
                identity_source = "auth_code_verified"

        # OAuth unionId is stable across the application's OAuth and address-
        # book namespaces.  It is the authoritative fallback when authCode
        # lookup yields an openId or an unverified identifier.
        if not verified_user_id and union_id:
            try:
                union_result = await dingtalk_client.get_user_id_by_union_id(union_id)
            except Exception as exc:  # noqa: BLE001
                union_result = {"ok": False, "error": str(exc)}
            union_data = (union_result.get("data") or {}) if union_result.get("ok") else {}
            union_user_id = str(union_data.get("user_id") or "").strip()
            if union_user_id:
                try:
                    union_detail = await dingtalk_client.get_user_detail(union_user_id)
                except Exception as exc:  # noqa: BLE001
                    union_detail = {"ok": False, "error": str(exc)}
                union_detail_data = union_detail.get("data") or {}
                union_depts = union_detail_data.get("dept_id_list") or []
                if union_detail.get("ok") and union_detail_data.get("user_id") and union_depts:
                    verified_user_id = str(
                        union_detail_data.get("user_id") or union_user_id
                    ).strip()
                    verified_detail = union_detail
                    identity_source = "union_id_verified"

        # Preserve login availability when the application lacks address-book
        # permission, but clearly treat this as an unverified OAuth fallback.
        # Such an account receives no department permissions until a verified
        # directory identity or an audited admin assignment exists.
        dingtalk_user_id = verified_user_id or auth_code_user_id or oauth_user_id or open_id
        if not userinfo_result.get("ok"):
            logger.info(
                "钉钉免登码补偿不可用（新式应用正常现象）open_id={} oauth_user_id={} error={}",
                open_id,
                oauth_user_id,
                userinfo_result.get("error") or userinfo_result.get("data"),
            )

        # 新式应用直接用 OAuth 返回的 openId 登录，但必须确认非外部访客
        if dingtalk_user_id and user_data.get("visitor") is True:
            logger.warning(f"钉钉外部访客拒绝登录 open_id={open_id or dingtalk_user_id}")
            return None

        if not dingtalk_user_id:
            logger.error(f"钉钉用户信息缺少企业userid: {user_data}")
            return None

        # 3. 查找或创建用户
        lookup_ids = [item for item in (dingtalk_user_id, oauth_user_id, open_id) if item]
        user = None
        for lookup_id in dict.fromkeys(lookup_ids):
            result = await db.execute(
                select(User).where(User.dingtalk_user_id == lookup_id)
            )
            user = result.scalar_one_or_none()
            if user:
                break

        if not user:
            # 按unionId查找
            if union_id:
                result = await db.execute(
                    select(User).where(User.dingtalk_union_id == union_id)
                )
                user = result.scalar_one_or_none()

        # Old OAuth applications exposed an openId-shaped userId, while the
        # enterprise directory later created a second user with the canonical
        # numeric userid.  When both identities are presented by the *same*
        # verified callback, carry the old scan entitlement to the canonical
        # account and retire the duplicate unionId binding.  Do not promote an
        # arbitrary observer: the legacy account must be active and already
        # have the AIBP entitlement, and the enterprise identity must have
        # passed address-book verification above.
        legacy_shadow: User | None = None
        if user is not None and verified_user_id:
            legacy_ids = [
                item
                for item in dict.fromkeys((oauth_user_id, open_id, auth_code_user_id))
                if item and item != verified_user_id
            ]
            for legacy_id in legacy_ids:
                result = await db.execute(
                    select(User).where(
                        User.dingtalk_user_id == legacy_id,
                        User.id != user.id,
                    )
                )
                legacy_shadow = result.scalar_one_or_none()
                if legacy_shadow:
                    break

        reconciled_shadow_user_id: str | None = None
        if legacy_shadow is not None:
            shadow_state = getattr(legacy_shadow, "state", None) or (
                "active" if legacy_shadow.is_active else "disabled"
            )
            user_state = getattr(user, "state", None) or (
                "active" if user.is_active else "disabled"
            )
            if (
                shadow_state == "active"
                and legacy_shadow.role in {"aibp", "ai_engineer"}
                and user_state != "disabled"
                and user.role in {"observer", "operator"}
            ):
                user.role = "aibp"
                user.state = "active"
                user.is_active = True
                user.permissions_rev = int(getattr(user, "permissions_rev", 0) or 0) + 1
                reconciled_shadow_user_id = legacy_shadow.id
            if union_id and legacy_shadow.dingtalk_union_id == union_id:
                legacy_shadow.dingtalk_union_id = None
                legacy_shadow.permissions_rev = (
                    int(getattr(legacy_shadow, "permissions_rev", 0) or 0) + 1
                )

        if not user:
            # 钉钉扫码用户直接进入平台使用：默认 AIBP + active。
            user = User(
                id=f"dt_{dingtalk_user_id}",
                username=f"dingtalk_{dingtalk_user_id}",
                name=name,
                role="aibp",
                state="active",
                can_view_all=False,
                dingtalk_user_id=dingtalk_user_id,
                dingtalk_union_id=union_id,
                avatar_url=avatar,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            logger.info(f"钉钉自动创建用户: {name} ({dingtalk_user_id}) → aibp/active")
        else:
            # 更新头像等信息
            user.avatar_url = avatar
            user.dingtalk_user_id = dingtalk_user_id
            user.dingtalk_union_id = union_id
            # 兼容历史数据：旧扫码/组织同步曾创建 ai_engineer、operator 或 pending 用户。
            # 新业务要求钉钉扫码后可直接使用能力大厅，因此登录时收敛为 active AIBP。
            state = getattr(user, "state", None) or ("active" if user.is_active else "disabled")
            if state != "disabled" and (state == "pending" or user.role in {"ai_engineer", "operator"}):
                user.role = "aibp"
                user.state = "active"
                user.is_active = True
                user.permissions_rev = int(getattr(user, "permissions_rev", 0) or 0) + 1

        user.last_login_at = now_bjt()
        await db.flush()
        synced_org_unit_id = await _sync_login_user_org_membership(
            db,
            user,
            dingtalk_user_id,
            detail=verified_detail,
        )
        await db.flush()

        await audit.log(user.id, "user.login",
                        detail={
                            "method": "dingtalk_scan",
                            "identity_source": identity_source,
                            "enterprise_identity_verified": bool(verified_user_id),
                            "reconciled_shadow_user_id": reconciled_shadow_user_id,
                            "org_unit_id": synced_org_unit_id,
                        },
                        ip_address=ip_address)

        return user

    except Exception as e:
        logger.error(f"钉钉OAuth处理失败: {e}")
        return None


async def _sync_login_user_org_membership(
    db: AsyncSession,
    user: User,
    dingtalk_user_id: str,
    *,
    detail: dict | None = None,
) -> str | None:
    """Best-effort department backfill for DingTalk login users."""
    if detail is None:
        try:
            detail = await dingtalk_client.get_user_detail(dingtalk_user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("钉钉登录部门回填失败 user={} err={}", user.id, exc)
            return None
    if not detail.get("ok"):
        logger.warning(
            "钉钉登录身份未绑定企业通讯录 user={} source_id={} error={}",
            user.id,
            dingtalk_user_id,
            detail.get("error") or "directory_lookup_failed",
        )
        return None
    data = detail.get("data") or {}
    dept_ids = data.get("dept_id_list") or []
    if isinstance(dept_ids, (str, int)):
        dept_ids = [dept_ids]
    clean_ids = [str(item).strip() for item in dept_ids if str(item).strip()]
    if not clean_ids:
        return None
    resolved_orgs: list[OrgUnit] = []
    for dept_id in clean_ids:
        org = await db.get(OrgUnit, dept_id)
        if org is None:
            org = (
                await db.execute(
                    select(OrgUnit).where(OrgUnit.dingtalk_dept_id == dept_id)
                )
            ).scalar_one_or_none()
        if org is None:
            logger.warning("钉钉登录部门回填找不到组织单元 user={} dept_id={}", user.id, dept_id)
            continue
        resolved_orgs.append(org)
    if not resolved_orgs:
        return None

    from app.org.service import add_membership, remove_membership

    for index, org in enumerate(resolved_orgs):
        await add_membership(
            db,
            user_id=user.id,
            org_unit_id=org.id,
            membership_type="primary" if index == 0 else "secondary",
            is_manager=False,
            actor_id=user.id,
        )

    # The address book response is authoritative for DingTalk departments.
    # Remove obsolete directory memberships so staff moves do not accumulate
    # access to their former departments.  Manual project/team memberships are
    # preserved because they have no DingTalk department id.
    current_dept_ids = set(clean_ids)
    existing_rows = (
        await db.execute(
            select(UserOrgMembership, OrgUnit)
            .join(OrgUnit, UserOrgMembership.org_unit_id == OrgUnit.id)
            .where(UserOrgMembership.user_id == user.id)
        )
    ).all()
    for membership, membership_org in existing_rows:
        directory_dept_id = str(membership_org.dingtalk_dept_id or "").strip()
        if (
            membership_org.type == "department"
            and directory_dept_id
            and directory_dept_id not in current_dept_ids
        ):
            await remove_membership(
                db,
                user_id=user.id,
                org_unit_id=membership.org_unit_id,
                actor_id=user.id,
            )
    return resolved_orgs[0].id
