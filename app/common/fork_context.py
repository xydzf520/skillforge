"""
Forked Context：多个 LLM 调用共享同一份前缀，利用 prompt cache。

设计参考：aiclawcode/src/utils/forkedAgent.ts 的 CacheSafeParams 思路。

模块使命
--------
在 Workbench 同一 Skill 的多个 LLM 调用中（例如 patch 生成 + question 回答，或
未来的 coach / verifier / reviewer 并行评审），三者往往共享 80% 以上相同的
system prompt 和 Skill 上下文。如果每次都把这段前缀完整发送给模型，就要按
full price 支付 input_tokens 的成本。

ForkContext 的做法：
1. 把缓存安全的前缀（system_prompt + base_messages）集中维护一份
2. fork(user_message) 时在 system 消息上挂 ``cache_control: {type: "ephemeral"}``
3. 支持 prompt cache 的后端（Anthropic / 部分 OpenAI 兼容网关透传）
   在首次调用后会把前缀缓存起来，后续命中 cache_read，价格约为 full price 的 10%
4. 不支持 cache_control 的后端会忽略这个字段（无副作用），fork 出的消息结构仍
   是合法的 OpenAI chat.completions messages

设计边界
--------
- **不触碰 LLM 调用层**：ForkContext 只构造 messages 列表，交给 call_llm /
  call_llm_stream（两者都已支持 messages 参数）。是否透传 cache_control 字段
  由 LLM 后端决定，本模块不做配置管理。
- **不做并发编排**：asyncio.gather、信号量限流这些职责留给调用方（workbench
  service）。本模块只负责"构造可共享前缀的 messages"。
- **无副作用**：ForkContext 是 immutable 的数据容器，fork() 每次返回全新列表，
  调用方可以放心并发复用同一个 ForkContext 实例。

典型用法（伪代码）
----------------
    from app.common.fork_context import ForkContext
    from app.common.ai import call_llm_stream

    # 1. 一次性构建 ForkContext（系统前缀 + Skill 上下文）
    fork = ForkContext(
        system_prompt="你是 SkillForge Workbench 助手...",
        base_messages=[
            {"role": "user", "content": "【Skill 上下文】\n..."},
            {"role": "assistant", "content": "我已经理解这个 Skill。"},
        ],
    )

    # 2. 多次 fork，每次只拼一条新的 user_message
    async for ev in call_llm_stream(
        messages=fork.fork("请解释一下决策步骤 step_2"),
        call_source="workbench.question",
    ):
        ...

    async for ev in call_llm_stream(
        messages=fork.fork("请列出所有阈值参数"),
        call_source="workbench.question",
    ):
        ...
    # 两次调用共享同一个 system + Skill 上下文前缀，第二次走 cache_read。
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CacheSafeParams:
    """缓存安全的前缀参数容器。

    字段
    ----
    system_prompt : str
        系统提示词，所有 fork 共享。
    base_messages : list[dict]
        系统提示之后、user 问题之前插入的消息。常见用途：把 Skill 的 SKILL.md
        片段作为一条 ``user`` 消息 + 一条 ``assistant`` 确认消息，模拟"已加载
        背景"。必须是缓存安全的（内容在多次 fork 间保持不变）。
    tool_schemas : list[dict] | None
        可选的 tool 定义（若后端支持 function calling）。本字段当前未被 fork()
        使用，保留给后续 coach/verifier/reviewer 多 tool 场景扩展。
    """

    system_prompt: str
    base_messages: list[dict] = field(default_factory=list)
    tool_schemas: list[dict] | None = None


class ForkContext:
    """Forked Context：共享缓存前缀的 messages 构造器。

    构造
    ----
    system_prompt : str
        系统提示词（必填，可为空字符串）。
    base_messages : list[dict] | None
        缓存安全的前缀消息序列。构造时会做深拷贝防止外部修改污染。
    tool_schemas : list[dict] | None
        预留字段，当前仅保存不使用。

    属性
    ----
    system_prompt_hash : str
        基于 ``system_prompt`` 内容计算的 sha256 前 12 字符十六进制，用于成本
        追踪 / 审计日志中标识"这批调用共享的是哪份前缀"。

    方法
    ----
    fork(user_message, extra_system="") -> list[dict]
        构造一次调用的完整 messages：
        ``[system+extra_system(cache_control), *base_messages, {role:user,...}]``。
    to_messages_for_call(user_messages) -> list[dict]
        为包含多条 user/assistant 历史的调用构造 messages（例如多轮对话）。
    """

    def __init__(
        self,
        system_prompt: str,
        base_messages: list[dict] | None = None,
        tool_schemas: list[dict] | None = None,
    ) -> None:
        self._params = CacheSafeParams(
            system_prompt=system_prompt or "",
            # 深拷贝防止调用方后续修改 list 影响已构造的 ForkContext
            base_messages=copy.deepcopy(base_messages) if base_messages else [],
            tool_schemas=copy.deepcopy(tool_schemas) if tool_schemas else None,
        )
        self._system_prompt_hash = self._compute_hash(self._params.system_prompt)

    # ---------- 属性 ----------

    @property
    def system_prompt(self) -> str:
        """系统提示词原文。"""
        return self._params.system_prompt

    @property
    def base_messages(self) -> list[dict]:
        """缓存前缀消息的只读拷贝。调用方修改返回值不会影响 ForkContext。"""
        return copy.deepcopy(self._params.base_messages)

    @property
    def tool_schemas(self) -> list[dict] | None:
        """tool schemas 的只读拷贝。"""
        return copy.deepcopy(self._params.tool_schemas) if self._params.tool_schemas else None

    @property
    def system_prompt_hash(self) -> str:
        """system_prompt 的 sha256[:12]，用于审计 / 成本归因。"""
        return self._system_prompt_hash

    # ---------- 工具方法 ----------

    @staticmethod
    def _compute_hash(text: str) -> str:
        """计算文本的 sha256 前 12 字符十六进制。"""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]

    def _build_system_message(self, extra_system: str = "") -> dict | None:
        """构造带 cache_control 的 system 消息。若 system_prompt 和 extra_system
        都为空，返回 None（此时 fork 出的 messages 将没有 system 条目）。
        """
        base = self._params.system_prompt
        extra = (extra_system or "").strip()
        if not base and not extra:
            return None
        if base and extra:
            combined = f"{base}\n\n{extra}"
        else:
            combined = base or extra
        return {
            "role": "system",
            "content": combined,
            # 关键标记：支持 prompt cache 的后端会把这段 system 缓存为 ephemeral
            # block；不支持的后端会直接忽略这个字段。
            "cache_control": {"type": "ephemeral"},
        }

    # ---------- 主要 API ----------

    def fork(self, user_message: str, extra_system: str = "") -> list[dict]:
        """生成一次 LLM 调用的完整 messages 列表。

        参数
        ----
        user_message : str
            本次 fork 独有的用户提问 / 指令。
        extra_system : str
            追加到共享 system_prompt 之后的补充指令（例如"请用 JSON 格式回答"）。
            会和 system_prompt 拼成同一条 system 消息，仍然走 cache_control
            标记（只要前缀部分相同，大部分后端仍能命中 cache）。

        返回
        ----
        messages : list[dict]
            结构：``[system(cache_control), *base_messages, {role: user, ...}]``。
            每次调用都返回全新的列表，调用方可以放心修改。
        """
        messages: list[dict] = []
        system_msg = self._build_system_message(extra_system)
        if system_msg is not None:
            messages.append(system_msg)
        # base_messages 用深拷贝，避免上层修改返回值回流污染 ForkContext
        messages.extend(copy.deepcopy(self._params.base_messages))
        messages.append({"role": "user", "content": user_message})
        return messages

    def to_messages_for_call(
        self,
        user_messages: list[dict],
        extra_system: str = "",
    ) -> list[dict]:
        """为多轮对话构造 messages（append 一串 user/assistant 消息而非单条）。

        适合"在共享前缀上继续对话"的场景：例如第二次调用带上前一次的
        ``assistant`` 回答以及新的 ``user`` 追问。

        参数
        ----
        user_messages : list[dict]
            追加在 base_messages 之后的消息序列，通常是 user/assistant 交替。
            允许为空列表，此时等价于 ``fork("")`` 去掉最后的 user 条目。
        extra_system : str
            同 fork()。

        返回
        ----
        messages : list[dict]
            结构：``[system(cache_control), *base_messages, *user_messages]``。
        """
        messages: list[dict] = []
        system_msg = self._build_system_message(extra_system)
        if system_msg is not None:
            messages.append(system_msg)
        messages.extend(copy.deepcopy(self._params.base_messages))
        messages.extend(copy.deepcopy(user_messages or []))
        return messages
