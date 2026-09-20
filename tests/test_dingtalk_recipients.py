import pytest

from app.auth.models import User
from app.dingtalk.recipients import resolve_work_notice_user


@pytest.mark.asyncio
async def test_resolve_work_notice_user_prefers_org_synced_duplicate(client):
    from app.database import async_session_factory

    async with async_session_factory() as db:
        canonical = User(
            id="test_corp_zhangsan",
            username="test_corp_zhangsan",
            name="张三",
            role="admin",
            department="信息技术部",
            dingtalk_user_id="corp-zhangsan",
            state="active",
            is_active=True,
        )
        shadow = User(
            id="test_open_zhangsan",
            username="test_open_zhangsan",
            name="花名-张三",
            role="ai_engineer",
            department=None,
            dingtalk_user_id="openid-zhangsan",
            dingtalk_union_id="union-zhangsan",
            state="active",
            is_active=True,
        )
        db.add_all([canonical, shadow])
        await db.commit()

        resolved = await resolve_work_notice_user(db, shadow)

        assert resolved is not None
        assert resolved.id == "test_corp_zhangsan"
        assert resolved.dingtalk_user_id == "corp-zhangsan"


@pytest.mark.asyncio
async def test_resolve_work_notice_user_uses_name_head_and_avatar_fallback(client):
    from app.database import async_session_factory

    async with async_session_factory() as db:
        zhu_canonical = User(
            id="test_corp_zhudan",
            username="test_corp_zhudan",
            name="朱丹",
            role="operator",
            department="即时零售业务部",
            dingtalk_user_id="corp-zhudan",
            state="active",
            is_active=True,
        )
        zhu_shadow = User(
            id="test_open_zhudan",
            username="test_open_zhudan",
            name="朱丹-小荷包",
            role="aibp",
            department=None,
            dingtalk_user_id="openid-zhudan-userid-abcdef",
            state="active",
            is_active=True,
        )
        xing_canonical = User(
            id="test_corp_examplemember",
            username="test_corp_examplemember",
            name="示例成员甲",
            role="director",
            department="阿里店群组",
            dingtalk_user_id="corp-examplemember",
            avatar_url="https://example.com/avatar/examplemember.png",
            state="active",
            is_active=True,
        )
        xing_shadow = User(
            id="test_open_examplemember",
            username="test_open_examplemember",
            name="示例成员",
            role="ai_engineer",
            department=None,
            dingtalk_user_id="openid-examplemember-userid-abcdef",
            avatar_url="https://example.com/avatar/examplemember.png",
            state="active",
            is_active=True,
        )
        yang_canonical = User(
            id="test_corp_yangchangliang",
            username="test_corp_yangchangliang",
            name="杨昌亮",
            role="operator",
            department="办公室",
            dingtalk_user_id="0213644726269578",
            avatar_url="https://example.com/avatar/yang.jpg",
            state="active",
            is_active=True,
        )
        yang_shadow = User(
            id="test_open_yangchangliang",
            username="test_open_yangchangliang",
            name="老杨-杨昌亮",
            role="aibp",
            department="办公室",
            dingtalk_user_id="iPk5JHfadiiToiE",
            avatar_url="https://example.com/avatar/yang.jpg",
            state="active",
            is_active=True,
        )
        db.add_all([zhu_canonical, zhu_shadow, xing_canonical, xing_shadow, yang_canonical, yang_shadow])
        await db.commit()

        resolved_zhu = await resolve_work_notice_user(db, zhu_shadow)
        resolved_xing = await resolve_work_notice_user(db, xing_shadow)
        resolved_yang = await resolve_work_notice_user(db, yang_shadow)

        assert resolved_zhu is not None
        assert resolved_zhu.id == "test_corp_zhudan"
        assert resolved_xing is not None
        assert resolved_xing.id == "test_corp_examplemember"
        assert resolved_yang is not None
        assert resolved_yang.id == "test_corp_yangchangliang"
