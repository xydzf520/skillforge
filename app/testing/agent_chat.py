"""
Agent对话服务：加载SKILL.md + policy_pack + 历史执行结果为System Prompt，
通过 httpx 调用 OpenAI 兼容接口（直连 DeepSeek/GLM 等）与用户对话，验证Skill逻辑。
会话持久化到conversations表，支持多轮对话和历史管理。

F1 集成：
- 调 LLM 前先调 ContextBudget 检查水位
- 超过 compact 阈值时调 compress 生成 Boundary Message
- 连续 3 次压缩失败触发 503 熔断
"""

import uuid
from datetime import datetime, timedelta

import httpx
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context_budget import context_budget
from app.common.exceptions import AppError
from app.common.prompt_registry import prompt_registry
from app.config import settings
from app import database as _db_mod
from app.datasources.models import DataSource
from app.execution.models import DecisionLog
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser
from app.testing.models import Conversation
from app.common.time_utils import isoformat_bjt, now_bjt


# F3: SYSTEM_PROMPT_TEMPLATE 已迁移到 app/common/prompts/agent_chat@v1.md
# 通过 prompt_registry.build("agent_chat", context) 获取


class _StreamError(Exception):
    """内部异常：在流式准备阶段需要 yield 一个 error 事件并停止。

    携带可直接 yield 的事件 dict，避免在生成器内部用普通异常导致清理路径混乱。
    """

    def __init__(self, event: dict):
        super().__init__(event.get("error", "stream error"))
        self.event = event


class AgentChatService:
    """Agent对话测试服务，管理会话生命周期和LLM调用"""

    async def start_conversation(self, skill_id: str, user_id: str) -> dict:
        """
        创建新的对话会话。
        验证Skill存在后，在conversations表中插入一条记录。
        返回 {conversation_id, skill_id, created_at}。
        """
        # 检查Skill文件是否存在
        skill_md = git_service.read_file(skill_id, "SKILL.md")
        if not skill_md:
            raise AppError("SKILL_NOT_FOUND", 404)

        conversation_id = f"chat-{uuid.uuid4().hex[:12]}"
        now = now_bjt()

        # 初始消息：系统欢迎语
        initial_messages = [
            {
                "role": "assistant",
                "content": f"我是Skill「{skill_id}」的测试助手。已加载判断逻辑和参数配置，请提问。",
                "timestamp": isoformat_bjt(now),
            }
        ]

        conversation = Conversation(
            id=conversation_id,
            skill_id=skill_id,
            user_id=user_id,
            messages=initial_messages,
            model_id=settings.AI_DEFAULT_MODEL,
            total_tokens=0,
            created_at=now,
            updated_at=now,
        )

        async with _db_mod.async_session_factory() as session:
            session.add(conversation)
            await session.commit()

        logger.info(f"创建对话会话: {conversation_id}, skill={skill_id}, user={user_id}")

        return {
            "conversation_id": conversation_id,
            "skill_id": skill_id,
            "created_at": isoformat_bjt(now),
            "messages": initial_messages,
        }

    async def send_message(self, conversation_id: str, user_message: str) -> dict:
        """
        发送用户消息并获取AI回复。
        1. 从DB加载会话和消息历史
        2. 构建system prompt（加载SKILL.md、policy_pack、最新执行结果）
        3. F1: 检查上下文水位，必要时压缩
        4. 调用 LLM 获取回复
        5. 将用户消息和AI回复保存到会话
        6. 返回AI回复
        """
        async with _db_mod.async_session_factory() as session:
            # 加载会话
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if not conversation:
                raise AppError("SKILL_NOT_FOUND", 404, {"detail": "对话会话不存在"})

            skill_id = conversation.skill_id
            now = now_bjt()

            # 构建系统提示词
            system_prompt = await self._build_system_prompt(skill_id, session)

            # 构建消息列表（system + 历史 + 当前用户消息）
            # F1 修复：boundary 消息（_boundary=True）必须作为 system 注入给 LLM，
            # 否则历史摘要不会回到模型，F1 等于失效
            messages = [{"role": "system", "content": system_prompt}]
            existing_messages = conversation.messages or []
            for msg in existing_messages:
                if msg.get("_boundary"):
                    # 带过 boundary 标记，F6 micro_compact 会跳过它
                    messages.append({
                        "role": "system",
                        "content": msg.get("content", ""),
                        "_boundary": True,
                    })
                elif msg["role"] in ("user", "assistant"):
                    # F6: 带上 timestamp 和 _kind 字段（如果有），否则微压缩无法识别旧工具消息
                    built = {"role": msg["role"], "content": msg["content"]}
                    if "timestamp" in msg:
                        built["timestamp"] = msg["timestamp"]
                    if "_kind" in msg:
                        built["_kind"] = msg["_kind"]
                    messages.append(built)
            messages.append({"role": "user", "content": user_message})

            # === F1 + F6: 上下文预算与压缩 ===
            failure_count = conversation.compact_failure_count or 0
            new_boundary_idx = conversation.compact_boundary_idx or 0
            compact_event: dict | None = None
            micro_compact_event: dict | None = None

            status = context_budget.status(messages)

            # F6: 在 warn 或 compact 档位时先尝试微压缩（零成本，不调 LLM）
            # 若微压缩删除了消息，重新计算 status。若回到 ok，直接跳过 F1 压缩。
            if status in ("warn", "compact") and context_budget.config.micro_compact_enabled:
                try:
                    micro_msgs, removed = await context_budget.micro_compact(messages)
                    if removed > 0:
                        messages = micro_msgs
                        micro_compact_event = {
                            "removed_count": removed,
                            "at": isoformat_bjt(now_bjt()),
                        }
                        status = context_budget.status(messages)
                        logger.info(
                            f"会话 {conversation_id} 微压缩删除 {removed} 条旧消息，"
                            f"status={status}"
                        )
                except Exception as e:
                    # 微压缩失败不影响主流程，降级到 F1
                    logger.warning(f"会话 {conversation_id} 微压缩异常: {e}")

            if status == "error":
                raise AppError(
                    "CONTEXT_OVERFLOW",
                    413,
                    {
                        "detail": "对话上下文已超过安全上限，请开启新会话",
                        "budget": context_budget.progress(messages),
                    },
                )

            if status == "compact":
                if failure_count >= context_budget.config.max_consecutive_failures:
                    raise AppError(
                        "COMPACT_CIRCUIT_BREAKER",
                        503,
                        {
                            "detail": "连续压缩失败次数过多，请稍后重试",
                            "failure_count": failure_count,
                        },
                    )

                try:
                    compressed_messages, original_count = await context_budget.compress(messages)
                    if original_count > 0:
                        # 压缩成功
                        messages = compressed_messages
                        failure_count = 0
                        compact_event = {
                            "original_count": original_count,
                            "boundary_inserted_at": isoformat_bjt(now_bjt()),
                        }
                        # boundary 在持久化 messages（不含 system）中的位置
                        # 由于压缩后 boundary 紧跟 system，去掉 system 后 boundary 在第 0 位
                        new_boundary_idx = 0
                        logger.info(
                            f"会话 {conversation_id} 已压缩 {original_count} 条历史消息"
                        )
                    else:
                        # compress 内部已降级（hard truncate），但保留 messages 用于继续调用
                        messages = compressed_messages
                        failure_count += 1
                        logger.warning(
                            f"会话 {conversation_id} 压缩失败，降级为硬截断（失败次数 {failure_count}）"
                        )
                except Exception as e:
                    failure_count += 1
                    logger.error(f"会话 {conversation_id} 压缩异常: {e}（失败次数 {failure_count}）")
                    if failure_count >= context_budget.config.max_consecutive_failures:
                        # 写回失败计数后再抛
                        await session.execute(
                            update(Conversation)
                            .where(Conversation.id == conversation_id)
                            .values(compact_failure_count=failure_count)
                        )
                        await session.commit()
                        raise AppError(
                            "COMPACT_CIRCUIT_BREAKER",
                            503,
                            {"detail": "压缩调用异常已达熔断阈值", "error": str(e)[:200]},
                        )

            # === 调用 LLM ===
            # 移除内部标记字段（_boundary/_original_count 不应发给 LLM）
            llm_messages = [
                {k: v for k, v in m.items() if not k.startswith("_")}
                for m in messages
            ]
            # F4 成本追踪上下文：user/skill/conversation + 最近一次 prompt_hash
            cost_context = {
                "user_id": conversation.user_id,
                "skill_id": conversation.skill_id,
                "conversation_id": conversation_id,
                "prompt_hash": getattr(self, "_last_prompt_hash", None),
            }
            assistant_reply, usage_tokens = await self._call_llm(
                llm_messages,
                cost_context=cost_context,
            )

            # 更新会话消息记录
            user_msg_record = {
                "role": "user",
                "content": user_message,
                "timestamp": isoformat_bjt(now),
            }
            assistant_msg_record = {
                "role": "assistant",
                "content": assistant_reply,
                "timestamp": isoformat_bjt(now_bjt()),
            }

            # 持久化的消息列表：如果发生压缩（F1 或 F6），要基于 messages 重建
            if compact_event or micro_compact_event:
                # 重建 existing_messages：保留压缩后的历史结构 + 这一轮新消息
                # 注意：messages 是 [system, (boundary?), ...kept_history, current_user]
                # 持久化时跳过真正的 system prompt，但保留 boundary（它的 _boundary=True）
                persisted_history = [
                    m for m in messages
                    if m.get("role") != "system" or m.get("_boundary")
                ]
                # 去掉刚才追加的当前 user message（要按"user 消息记录"格式追加，含时间戳）
                if persisted_history and persisted_history[-1].get("role") == "user":
                    persisted_history = persisted_history[:-1]
                updated_messages = persisted_history + [user_msg_record, assistant_msg_record]
            else:
                updated_messages = list(existing_messages) + [user_msg_record, assistant_msg_record]

            new_total_tokens = (conversation.total_tokens or 0) + usage_tokens

            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    messages=updated_messages,
                    total_tokens=new_total_tokens,
                    compact_boundary_idx=new_boundary_idx,
                    compact_failure_count=failure_count,
                    usage_snapshot={
                        "last_total_tokens": usage_tokens,
                        "ts": isoformat_bjt(now_bjt()),
                    },
                    updated_at=now_bjt(),
                )
            )
            await session.commit()

        response: dict = {
            "conversation_id": conversation_id,
            "user_message": user_msg_record,
            "assistant_message": assistant_msg_record,
            "total_tokens": new_total_tokens,
        }
        if compact_event:
            response["compact_event"] = compact_event
        if micro_compact_event:
            response["micro_compact_event"] = micro_compact_event
        return response

    async def send_message_stream(
        self,
        conversation_id: str,
        user_message: str,
    ):
        """
        send_message 的流式版本。返回异步生成器，按顺序 yield 事件 dict。

        修复 Codex 审计：DB session 不再跨 LLM 流持有，分三阶段：
        - Phase 1（短 session）：加载 + 构建 prompt + 压缩
        - Phase 2（无 session）：流式 LLM 调用
        - Phase 3（短 session）：持久化最终消息

        事件序列：
            {"type": "compact", "original_count": N}    - 触发了压缩
            {"type": "delta",   "content": "片段"}      - 增量回复
            {"type": "done",    "usage": {...}, "total_tokens": X}
            {"type": "error",   "code": "...", "error": "..."}
        """
        from app.common.ai import call_llm_stream

        # ─── Phase 1: 加载 + 构建 + 压缩（短 session）───
        try:
            phase1 = await self._stream_phase1_prepare(conversation_id, user_message)
        except _StreamError as e:
            yield e.event
            return

        if phase1.get("micro_compact_event"):
            yield {
                "type": "micro_compact",
                "removed_count": phase1["micro_compact_event"]["removed_count"],
            }

        if phase1["compact_event"]:
            yield {
                "type": "compact",
                "original_count": phase1["compact_event"]["original_count"],
            }

        # ─── Phase 2: 流式调用 LLM（无 session）───
        llm_messages = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in phase1["messages"]
        ]

        # F4 成本追踪上下文：phase1 已拿到 conversation 基本信息；
        # prompt_hash 由 _build_system_prompt（F3 已集成 prompt_registry）设置
        stream_cost_context = {
            "user_id": phase1.get("user_id"),
            "skill_id": phase1.get("skill_id"),
            "conversation_id": conversation_id,
            "prompt_hash": getattr(self, "_last_prompt_hash", None),
        }

        assistant_content = ""
        final_usage: dict | None = None
        finish_reason = None
        llm_error: dict | None = None

        async for ev in call_llm_stream(
            messages=llm_messages,
            call_source="agent_chat",
            cost_context=stream_cost_context,
        ):
            ev_type = ev.get("type")
            if ev_type == "delta":
                assistant_content += ev.get("content", "")
                yield ev
            elif ev_type == "usage":
                final_usage = ev
            elif ev_type == "done":
                finish_reason = ev.get("finish_reason")
            elif ev_type == "error":
                llm_error = ev
                break

        # ─── Phase 3: 持久化（短 session）───
        try:
            done_event = await self._stream_phase3_persist(
                conversation_id=conversation_id,
                user_message=user_message,
                assistant_content=assistant_content or "（模型未返回内容）",
                phase1=phase1,
                final_usage=final_usage,
                finish_reason=finish_reason,
            )
        except Exception as e:
            logger.exception(f"流式持久化失败 conv={conversation_id}: {e}")
            yield {
                "type": "error",
                "code": "PERSIST_FAILED",
                "error": str(e)[:300],
            }
            return

        if llm_error:
            yield llm_error
            return

        yield done_event

    async def _stream_phase1_prepare(
        self,
        conversation_id: str,
        user_message: str,
    ) -> dict:
        """Phase 1: 加载会话 + 构建 prompt + 压缩判断。短 session。"""
        async with _db_mod.async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if not conversation:
                raise _StreamError({
                    "type": "error",
                    "code": "SKILL_NOT_FOUND",
                    "error": "对话会话不存在",
                })

            skill_id = conversation.skill_id
            now = now_bjt()

            try:
                system_prompt = await self._build_system_prompt(skill_id, session)
            except Exception as e:
                raise _StreamError({
                    "type": "error",
                    "code": "BUILD_PROMPT_FAILED",
                    "error": str(e)[:300],
                })

            # 拷贝 ORM 字段为普通值，session 关闭后仍可用
            existing_messages = list(conversation.messages or [])
            failure_count = conversation.compact_failure_count or 0
            new_boundary_idx = conversation.compact_boundary_idx or 0
            current_total_tokens = conversation.total_tokens or 0
            # F4 成本追踪需要：user_id / skill_id 提前在 session 内拷出
            conversation_user_id = conversation.user_id
            conversation_skill_id_for_cost = conversation.skill_id

        # session 已关闭，下面是纯计算 + 摘要 LLM 调用

        messages = [{"role": "system", "content": system_prompt}]
        for msg in existing_messages:
            if msg.get("_boundary"):
                messages.append({
                    "role": "system",
                    "content": msg.get("content", ""),
                    "_boundary": True,
                })
            elif msg["role"] in ("user", "assistant"):
                # F6: 带上 timestamp 和 _kind 字段，否则微压缩无法识别旧工具消息
                built = {"role": msg["role"], "content": msg["content"]}
                if "timestamp" in msg:
                    built["timestamp"] = msg["timestamp"]
                if "_kind" in msg:
                    built["_kind"] = msg["_kind"]
                messages.append(built)
        messages.append({"role": "user", "content": user_message})

        compact_event: dict | None = None
        micro_compact_event: dict | None = None
        status = context_budget.status(messages)

        # F6: 在 warn / compact 档位先尝试零成本微压缩
        if status in ("warn", "compact") and context_budget.config.micro_compact_enabled:
            try:
                micro_msgs, removed = await context_budget.micro_compact(messages)
                if removed > 0:
                    messages = micro_msgs
                    micro_compact_event = {
                        "removed_count": removed,
                        "at": isoformat_bjt(now_bjt()),
                    }
                    status = context_budget.status(messages)
                    logger.info(
                        f"流式: 会话 {conversation_id} 微压缩删除 {removed} 条旧消息，status={status}"
                    )
            except Exception as e:
                logger.warning(f"流式: 会话 {conversation_id} 微压缩异常: {e}")

        if status == "error":
            raise _StreamError({
                "type": "error",
                "code": "CONTEXT_OVERFLOW",
                "error": "对话上下文已超过安全上限，请开启新会话",
            })

        if status == "compact":
            if failure_count >= context_budget.config.max_consecutive_failures:
                raise _StreamError({
                    "type": "error",
                    "code": "COMPACT_CIRCUIT_BREAKER",
                    "error": "连续压缩失败次数过多，请稍后重试",
                })

            try:
                compressed_messages, original_count = await context_budget.compress(messages)
                if original_count > 0:
                    messages = compressed_messages
                    failure_count = 0
                    compact_event = {
                        "original_count": original_count,
                        "boundary_inserted_at": isoformat_bjt(now_bjt()),
                    }
                    new_boundary_idx = 0
                    logger.info(f"流式: 会话 {conversation_id} 已压缩 {original_count} 条历史")
                else:
                    messages = compressed_messages
                    failure_count += 1
                    logger.warning(
                        f"流式: 会话 {conversation_id} 压缩失败，硬截断（失败次数 {failure_count}）"
                    )
            except Exception as e:
                failure_count += 1
                logger.error(f"流式: 会话 {conversation_id} 压缩异常: {e}")
                # 写回 failure_count（短 session）
                async with _db_mod.async_session_factory() as session2:
                    await session2.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation_id)
                        .values(compact_failure_count=failure_count)
                    )
                    await session2.commit()
                if failure_count >= context_budget.config.max_consecutive_failures:
                    raise _StreamError({
                        "type": "error",
                        "code": "COMPACT_CIRCUIT_BREAKER",
                        "error": str(e)[:200],
                    })

        return {
            "messages": messages,
            "existing_messages": existing_messages,
            "failure_count": failure_count,
            "new_boundary_idx": new_boundary_idx,
            "current_total_tokens": current_total_tokens,
            "compact_event": compact_event,
            "micro_compact_event": micro_compact_event,
            "user_msg_ts": now,
            # F4: 成本追踪用上下文
            "user_id": conversation_user_id,
            "skill_id": conversation_skill_id_for_cost,
        }

    async def _stream_phase3_persist(
        self,
        conversation_id: str,
        user_message: str,
        assistant_content: str,
        phase1: dict,
        final_usage: dict | None,
        finish_reason: str | None,
    ) -> dict:
        """Phase 3: 把这一轮对话持久化到 DB。短 session。"""
        user_msg_record = {
            "role": "user",
            "content": user_message,
            "timestamp": isoformat_bjt(phase1["user_msg_ts"]),
        }
        assistant_msg_record = {
            "role": "assistant",
            "content": assistant_content,
            "timestamp": isoformat_bjt(now_bjt()),
        }

        if phase1.get("compact_event") or phase1.get("micro_compact_event"):
            persisted_history = [
                m for m in phase1["messages"]
                if m.get("role") != "system" or m.get("_boundary")
            ]
            if persisted_history and persisted_history[-1].get("role") == "user":
                persisted_history = persisted_history[:-1]
            updated_messages = persisted_history + [user_msg_record, assistant_msg_record]
        else:
            updated_messages = list(phase1["existing_messages"]) + [user_msg_record, assistant_msg_record]

        usage_total = (final_usage or {}).get("total_tokens", 0)
        new_total_tokens = phase1["current_total_tokens"] + usage_total

        async with _db_mod.async_session_factory() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    messages=updated_messages,
                    total_tokens=new_total_tokens,
                    compact_boundary_idx=phase1["new_boundary_idx"],
                    compact_failure_count=phase1["failure_count"],
                    usage_snapshot={
                        "input_tokens": (final_usage or {}).get("input_tokens", 0),
                        "output_tokens": (final_usage or {}).get("output_tokens", 0),
                        "total_tokens": usage_total,
                        "ts": isoformat_bjt(now_bjt()),
                    } if final_usage else None,
                    updated_at=now_bjt(),
                )
            )
            await session.commit()

        return {
            "type": "done",
            "finish_reason": finish_reason or "stop",
            "usage": final_usage,
            "total_tokens": new_total_tokens,
        }

    async def get_conversation(self, conversation_id: str) -> dict:
        """获取完整的对话历史，包含 F1 预算/压缩状态"""
        async with _db_mod.async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if not conversation:
                raise AppError("SKILL_NOT_FOUND", 404, {"detail": "对话会话不存在"})

            messages = conversation.messages or []

            # 计算当前预算状态（基于持久化的 messages，不含 system prompt）
            # 加一个临时 system 占位来更接近真实 LLM 调用时的 token 量
            estimate_msgs = [{"role": "system", "content": "(系统提示词占位)"}] + messages
            progress = context_budget.progress(estimate_msgs)

            return {
                "conversation_id": conversation.id,
                "skill_id": conversation.skill_id,
                "user_id": conversation.user_id,
                "messages": messages,
                "model_id": conversation.model_id,
                "total_tokens": conversation.total_tokens or 0,
                # F1 字段
                "compact_boundary_idx": conversation.compact_boundary_idx or 0,
                "compact_failure_count": conversation.compact_failure_count or 0,
                "usage_snapshot": conversation.usage_snapshot,
                "budget_status": progress["status"],
                "budget_used": progress["used"],
                "budget_limit": progress["limit"],
                "budget_ratio": progress["ratio"],
                "created_at": isoformat_bjt(conversation.created_at),
                "updated_at": isoformat_bjt(conversation.updated_at),
            }

    async def list_conversations(self, skill_id: str, user_id: str | None = None) -> list:
        """
        列出某个Skill的所有对话会话。
        可选按user_id过滤，按更新时间倒序排列。
        """
        async with _db_mod.async_session_factory() as session:
            query = select(Conversation).where(
                Conversation.skill_id == skill_id
            )
            if user_id:
                query = query.where(Conversation.user_id == user_id)
            query = query.order_by(Conversation.updated_at.desc())

            result = await session.execute(query)
            conversations = result.scalars().all()

            return [
                {
                    "conversation_id": c.id,
                    "skill_id": c.skill_id,
                    "user_id": c.user_id,
                    "message_count": len(c.messages) if c.messages else 0,
                    "total_tokens": c.total_tokens or 0,
                    "created_at": isoformat_bjt(c.created_at),
                    "updated_at": isoformat_bjt(c.updated_at),
                    # 取最后一条用户消息作为摘要
                    "last_user_message": self._get_last_user_message(c.messages),
                }
                for c in conversations
            ]

    async def delete_conversation(self, conversation_id: str, user_id: str) -> dict:
        """删除对话（仅对话创建者可删除）"""
        async with _db_mod.async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                from app.common.exceptions import AppError
                raise AppError("CONVERSATION_NOT_FOUND", 404)
            if conv.user_id != user_id:
                from app.common.exceptions import AppError
                raise AppError("AUTH_PERMISSION_DENIED", 403)
            await session.delete(conv)
            await session.commit()
        return {"deleted": True, "conversation_id": conversation_id}

    async def _build_system_prompt(self, skill_id: str, session: AsyncSession) -> str:
        """
        构建完整的系统提示词。
        加载SKILL.md、policy_pack.yaml、反例、测试用例、最近3次执行结果和数据源状态。
        """
        # 加载SKILL.md
        skill_md = git_service.read_file(skill_id, "SKILL.md") or "（未找到SKILL.md文件）"

        # 加载policy_pack.yaml
        policy_pack = git_service.read_file(skill_id, "policy_pack.yaml") or "（未找到policy_pack.yaml文件）"

        # 从Skill元数据获取名称
        skill_name = skill_id
        skill_result = await session.execute(
            select(Skill.name).where(Skill.id == skill_id)
        )
        row = skill_result.scalar_one_or_none()
        if row:
            skill_name = row

        # 解析SKILL.md提取反例和测试用例
        antipatterns_text, test_cases_text = self._extract_parsed_sections(skill_md)

        # 获取最近3次非沙箱执行结果
        recent_executions = await self._get_recent_executions(skill_id, session)

        # 获取数据源状态
        datasource_status = await self._get_datasource_status(skill_id, session)

        # F3: 通过 PromptRegistry 渲染（hash 暂时仅日志，Task #18 接入审计）
        try:
            rendered, prompt_hash = prompt_registry.build(
                "agent_chat",
                context={
                    "skill_name": skill_name,
                    "skill_md_content": skill_md,
                    "policy_pack_yaml": policy_pack,
                    "antipatterns": antipatterns_text,
                    "test_cases": test_cases_text,
                    "recent_executions": recent_executions,
                    "datasource_status": datasource_status,
                },
            )
            self._last_prompt_hash = prompt_hash
            return rendered
        except KeyError:
            # registry 未加载（非常罕见，启动时会注册）。回退到最小提示，避免崩溃
            logger.warning("agent_chat prompt 未注册，使用 fallback")
            return f"你是 Skill「{skill_name}」的测试助手。\n\n{skill_md}"

    def _extract_parsed_sections(self, skill_md: str) -> tuple[str, str]:
        """解析SKILL.md，提取反例和测试用例章节的摘要文本"""
        try:
            parsed = skill_parser.parse(skill_md)
        except Exception as e:
            logger.warning(f"解析SKILL.md失败: {e}")
            return "（解析失败）", "（解析失败）"

        # 反例摘要
        if parsed.antipatterns:
            ap_lines = []
            for i, ap in enumerate(parsed.antipatterns, 1):
                ap_lines.append(f"{i}. 误判场景: {ap.scenario}")
                if ap.correct_action:
                    ap_lines.append(f"   正确做法: {ap.correct_action}")
            antipatterns_text = "\n".join(ap_lines)
        else:
            antipatterns_text = "（无反例定义）"

        # 测试用例摘要
        if parsed.test_cases:
            tc_lines = []
            for tc in parsed.test_cases:
                tc_lines.append(f"- {tc.name}")
                if tc.expected_output:
                    # 只展示期望输出的key和值，不输出完整JSON
                    summary_parts = [f"{k}={v}" for k, v in tc.expected_output.items()]
                    tc_lines.append(f"  期望: {', '.join(summary_parts[:5])}")
            test_cases_text = "\n".join(tc_lines)
        else:
            test_cases_text = "（无测试用例）"

        return antipatterns_text, test_cases_text

    async def _get_recent_executions(self, skill_id: str, session: AsyncSession) -> str:
        """获取最近3次非沙箱执行结果的摘要文本"""
        try:
            result = await session.execute(
                select(DecisionLog)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .order_by(DecisionLog.created_at.desc())
                .limit(3)
            )
            logs = result.scalars().all()
            if not logs:
                return "（暂无执行记录）"

            sections = []
            for idx, log in enumerate(logs, 1):
                parts = [f"### 第{idx}次（最近）" if idx == 1 else f"### 第{idx}次"]
                if log.created_at:
                    parts.append(f"执行时间: {log.created_at.strftime('%Y-%m-%d %H:%M')}")
                if log.output_result:
                    parts.append(f"输出结果: {_format_dict(log.output_result)}")
                if log.suggested_action:
                    parts.append(f"建议动作: {_format_dict(log.suggested_action)}")
                if log.approval_status:
                    parts.append(f"审批状态: {log.approval_status}")
                if log.user_action:
                    parts.append(f"用户操作: {log.user_action}")
                sections.append("\n".join(parts))

            return "\n\n".join(sections)

        except Exception as e:
            logger.warning(f"获取最近执行结果失败: {e}")
            return "（获取执行记录失败）"

    async def _get_datasource_status(self, skill_id: str, session: AsyncSession) -> str:
        """检查关联数据源的新鲜度，返回状态摘要"""
        try:
            result = await session.execute(
                select(DataSource)
                .where(DataSource.related_skills.contains([skill_id]))
                .where(DataSource.is_active == True)  # noqa: E712
            )
            sources = result.scalars().all()
            if not sources:
                return "（无关联数据源）"

            now = now_bjt()
            lines = []
            has_stale = False

            for src in sources:
                threshold = timedelta(hours=src.stale_threshold_hours or 24)
                is_stale = (now - src.updated_at) > threshold if src.updated_at else True
                if is_stale:
                    has_stale = True
                    hours_ago = int((now - src.updated_at).total_seconds() / 3600) if src.updated_at else -1
                    if hours_ago >= 0:
                        lines.append(f"- ⚠ {src.name}（{src.source_type}）: 已过期，上次更新在{hours_ago}小时前，阈值{src.stale_threshold_hours}小时")
                    else:
                        lines.append(f"- ⚠ {src.name}（{src.source_type}）: 从未更新过数据")
                else:
                    hours_ago = int((now - src.updated_at).total_seconds() / 3600)
                    lines.append(f"- ✓ {src.name}（{src.source_type}）: 正常，{hours_ago}小时前更新")

            if has_stale:
                lines.insert(0, "⚠ 警告：部分数据源已过期，执行结果可能基于过时数据，请注意判断。")

            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"获取数据源状态失败: {e}")
            return "（获取数据源状态失败）"

    async def _call_llm(
        self,
        messages: list[dict],
        *,
        cost_context: dict | None = None,
    ) -> tuple[str, int]:
        """
        通过 httpx 调用 OpenAI 兼容接口。
        返回 (回复文本, token消耗数)。

        F4 成本追踪：
            cost_context 可选 dict，含 user_id / skill_id / conversation_id /
            prompt_hash 等，用于写入 usage_logs 做成本归因。
        """
        import time
        from app.common.ai import get_ai_config, _record_usage
        config = await get_ai_config()
        api_base = str(config["ai.api_base"]).rstrip("/")
        url = f"{api_base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        api_key = str(config.get("ai.api_key", ""))
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        model = str(config["ai.model"])
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 2000,
            "temperature": 0.3,
        }

        start_ts = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=120,
                )

            duration_ms = int((time.monotonic() - start_ts) * 1000)

            if resp.status_code != 200:
                error_detail = resp.text[:500]
                logger.error(f"LLM 返回错误 status={resp.status_code}: {error_detail}")
                raise AppError("LLM_API_ERROR", 502, {"detail": error_detail})

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise AppError("LLM_API_ERROR", 502, {"detail": "LLM返回空choices"})

            content = choices[0].get("message", {}).get("content", "")
            if not content:
                content = "（模型未返回内容）"

            # 提取token用量
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)

            # F4: 写入 cost_tracker（失败不影响主流程）
            await _record_usage(
                model, usage, "agent_chat", cost_context, duration_ms,
            )

            return content, total_tokens

        except httpx.TimeoutException:
            logger.error("LLM 请求超时")
            raise AppError("LLM_TIMEOUT", 504)

        except httpx.ConnectError as e:
            logger.error(f"无法连接模型网关: {e}")
            raise AppError("LLM_API_ERROR", 502, {"detail": f"无法连接模型网关: {settings.AI_API_BASE}"})

        except AppError:
            raise

        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            raise AppError("LLM_API_ERROR", 502, {"detail": str(e)})

    @staticmethod
    def _get_last_user_message(messages: list[dict] | None) -> str:
        """从消息列表中提取最后一条用户消息作为摘要"""
        if not messages:
            return ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # 截断过长的消息
                return content[:80] + "..." if len(content) > 80 else content
        return ""


def _format_dict(d: dict, max_len: int = 500) -> str:
    """将dict格式化为可读文本，超长时截断"""
    import json
    try:
        text = json.dumps(d, ensure_ascii=False, indent=2)
        if len(text) > max_len:
            text = text[:max_len] + "...(已截断)"
        return text
    except Exception:
        return str(d)[:max_len]


# 全局单例
agent_chat_service = AgentChatService()
