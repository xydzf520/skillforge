"""
编辑器 AI 补全服务

通过 OpenAI 兼容接口为 SKILL.md 编辑提供上下文感知的 inline completion。
所有配置从 system_config 表读取，管理员在后台统一管理。
AI 请求结果缓存到 Redis，相同上下文命中缓存直接返回，节省 token。
"""

import hashlib
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.common.models import SystemConfig
from app.common.cache import cache_get, cache_set

# ── 默认值 ──

DEFAULTS = {
    "editor.ai_enabled": True,
    "editor.api_base": settings.AI_API_BASE,
    "editor.api_key": settings.AI_API_KEY,
    "editor.model": settings.AI_DEFAULT_MODEL,
    "editor.max_tokens": 150,
    "editor.temperature": 0.0,
    "editor.trigger": "onIdle",
    "editor.cache_enabled": True,
    "editor.cache_ttl": 3600,
    "editor.system_prompt": """你是 SKILL.md 续写引擎。

规则：
1. 只输出光标处应该续写的文本片段
2. 保持与上文相同的缩进和格式
3. 决策分支格式: "条件: 表达式 → 结论"，下一行 "- 动作: 具体操作"
4. 参数用 {变量名} 引用 policy_pack.yaml 中的值
5. 不要重复上文已有的内容
6. 不要输出解释、注释或代码围栏

SKILL.md 章节结构：
- ## 判断逻辑 → ### step_N: 步骤名 → 条件/动作/结论
- ## 反例 → 1. **误判场景**: xxx / **正确做法**: xxx
- ## 输出定义 → Markdown 表格
- ## 数据输入 → Markdown 表格
- ## 测试用例 → ### 用例名 → yaml 代码块""",
}


async def get_editor_config(db: AsyncSession) -> dict:
    """从 system_config 表读取 editor.* 配置，合并默认值"""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key.like("editor.%"))
    )
    rows = result.scalars().all()

    config = dict(DEFAULTS)
    for row in rows:
        config[row.key] = row.value
    return config


def _build_cache_key(filename: str, text_before: str, text_after: str) -> str:
    """
    用光标前最后5行 + 光标后前2行生成缓存 key。
    相同位置相同上下文 → 同一个 key → 命中缓存。
    """
    before_lines = text_before.strip().split("\n")
    after_lines = text_after.strip().split("\n")
    context = "\n".join(before_lines[-5:]) + "|||" + "\n".join(after_lines[:2])
    digest = hashlib.md5(f"{filename}:{context}".encode()).hexdigest()[:16]
    return f"ai_completion:{digest}"


async def generate_completion(
    db: AsyncSession,
    filename: str,
    language: str,
    text_before_cursor: str,
    text_after_cursor: str,
) -> str:
    """
    调用 LLM 生成补全建议，优先查 Redis 缓存
    """
    config = await get_editor_config(db)

    if not config.get("editor.ai_enabled", True):
        return ""

    api_base = config["editor.api_base"]
    api_key = config["editor.api_key"]
    model = config["editor.model"]
    max_tokens = int(config.get("editor.max_tokens", 200))
    temperature = float(config.get("editor.temperature", 0.3))
    system_prompt = config["editor.system_prompt"]
    cache_enabled = config.get("editor.cache_enabled", True)
    cache_ttl = int(config.get("editor.cache_ttl", 600))

    if not api_base or not model:
        logger.warning("AI补全未配置: 缺少 editor.api_base 或 editor.model")
        return ""

    # ── 查缓存 ──
    cache_key = _build_cache_key(filename, text_before_cursor, text_after_cursor)
    if cache_enabled:
        try:
            cached = await cache_get(cache_key)
            if cached is not None:
                logger.debug("AI补全命中缓存: {}", cache_key)
                return cached
        except Exception as e:
            logger.warning("Redis缓存不可用，跳过: {}", e)

    # ── 调 LLM ──
    user_prompt = f"文件: {filename}\n\n"
    user_prompt += f"--- 光标前内容 ---\n{text_before_cursor}\n"
    user_prompt += "█  ← 在此位置续写\n"
    if text_after_cursor.strip():
        user_prompt += f"--- 光标后内容 ---\n{text_after_cursor}\n"
    user_prompt += "\n请续写光标处的内容（1-3行），只返回续写部分："

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{api_base}/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stop": ["\n\n\n", "---"],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            completion = data["choices"][0]["message"]["content"].strip()

            # ── 写缓存 ──
            if cache_enabled and completion:
                try:
                    await cache_set(cache_key, completion, ttl=cache_ttl)
                    logger.debug("AI补全写入缓存: {} (ttl={}s)", cache_key, cache_ttl)
                except Exception as e:
                    logger.warning("AI补全缓存写入失败: {}", e)

            return completion
    except Exception as e:
        logger.warning("AI补全失败: {}", e)
        return ""
