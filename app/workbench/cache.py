"""工作台 Redis 缓存层 — session/draft/patch/intent/command 5 种 key。"""

from app.common.cache import cache_delete_pattern
from app.common.cache_facade import NamespaceCache


# ── Key 模式 ──
# sf:wb:session:{session_id}:ctx     → Session 上下文快照     TTL 30min
# sf:wb:session:{session_id}:draft   → 前端 draft 快照        TTL 10min
# sf:wb:patch:{patch_id}:diff        → Patch diff 计算结果     TTL 30min
# sf:wb:skill:{skill_id}:intent_hint → 最近意图识别结果        TTL 5min
# sf:wb:cmd:{skill_id}:{command}     → Slash command 结果缓存  TTL 10min

TTL_SESSION_CTX = 1800    # 30 分钟
TTL_DRAFT = 600           # 10 分钟
TTL_PATCH_DIFF = 1800     # 30 分钟
TTL_INTENT_HINT = 300     # 5 分钟
TTL_COMMAND = 600         # 10 分钟

SESSION_CTX_CACHE = NamespaceCache("wb:session:ctx", ttl=TTL_SESSION_CTX)
DRAFT_CACHE = NamespaceCache("wb:session:draft", ttl=TTL_DRAFT)
PATCH_DIFF_CACHE = NamespaceCache("wb:patch:diff", ttl=TTL_PATCH_DIFF)
INTENT_HINT_CACHE = NamespaceCache("wb:skill:intent_hint", ttl=TTL_INTENT_HINT)
COMMAND_CACHE = NamespaceCache("wb:cmd", ttl=TTL_COMMAND)


async def get_session_context(session_id: str) -> dict | None:
    return await SESSION_CTX_CACHE.get(session_id)


async def set_session_context(session_id: str, context: dict):
    await SESSION_CTX_CACHE.set(session_id, value=context)


async def get_draft(session_id: str) -> dict | None:
    return await DRAFT_CACHE.get(session_id)


async def set_draft(session_id: str, draft: dict):
    await DRAFT_CACHE.set(session_id, value=draft)


async def get_patch_diff(patch_id: str) -> dict | None:
    return await PATCH_DIFF_CACHE.get(patch_id)


async def set_patch_diff(patch_id: str, diff: dict):
    await PATCH_DIFF_CACHE.set(patch_id, value=diff)


async def get_intent_hint(skill_id: str) -> dict | None:
    return await INTENT_HINT_CACHE.get(skill_id)


async def set_intent_hint(skill_id: str, hint: dict):
    await INTENT_HINT_CACHE.set(skill_id, value=hint)


async def get_command_result(skill_id: str, command: str) -> dict | None:
    return await COMMAND_CACHE.get(skill_id, command)


async def set_command_result(skill_id: str, command: str, result: dict):
    await COMMAND_CACHE.set(skill_id, command, value=result)


async def invalidate_workbench_cache(skill_id: str, scope: str = "all"):
    """
    工作台缓存失效。

    scope:
    - "all": Skill 保存/发布时，清除所有相关缓存
    - "draft": 前端发送新 draft 时，只清除 draft 缓存
    - "patch": Patch apply/reject 时，清除 patch 缓存
    """
    if scope in ("all", "draft"):
        await DRAFT_CACHE.invalidate()
        await cache_delete_pattern(f"sf:skill:{skill_id}:structure")

    if scope in ("all", "patch"):
        await PATCH_DIFF_CACHE.invalidate()

    if scope == "all":
        await COMMAND_CACHE.invalidate_prefix(skill_id)
        await INTENT_HINT_CACHE.delete(skill_id)
