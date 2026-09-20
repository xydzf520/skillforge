"""
上下文预算与分层压缩。

参考 aiclawcode/src/services/compact/autoCompact.ts 的设计思路，针对 SkillForge 的场景做了简化：
- 分层压缩：
  - F1 compress()：重压缩，调摘要 LLM 把最早历史压成 boundary 消息（成本高）
  - F6 micro_compact()：轻量微压缩，基于时间戳删除过期的工具结果消息（零成本）
- 摘要 LLM 走独立调用，与主对话调用解耦
- 提供 ok/warn/compact/error 四档水位用于前端提示

实现位置参考：docs/plans/aiclawcode-migration-plan.md §3.1 / §3.6
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from loguru import logger

from app.common.time_utils import BJT, parse_bjt_datetime


# ===== 配置 =====
@dataclass
class BudgetConfig:
    """上下文预算配置。

    所有字段单位为 token。默认按 Opus 1M context 取安全线（150k）。
    """

    effective_window: int = 150_000
    """有效上下文窗口（不是后端模型的硬上限，而是我们要主动管理的安全线）"""

    output_reserve: int = 4_000
    """为 assistant 响应预留的 token 数"""

    autocompact_buffer: int = 12_000
    """触发自动压缩的缓冲区。当 input + reserve > effective_window - buffer 时触发压缩"""

    warning_ratio: float = 0.80
    """达到这个比例时返回 warn 状态（前端可显示黄色进度条）"""

    error_ratio: float = 0.95
    """达到这个比例时返回 error 状态（拒绝处理新消息）"""

    keep_recent_turns: int = 6
    """压缩时保留最近 N 轮原文（user+assistant 算一轮）"""

    max_consecutive_failures: int = 3
    """连续压缩失败次数达到此值触发熔断"""

    summarizer_max_tokens: int = 600
    """摘要 LLM 调用的最大输出 token"""

    summarizer_temperature: float = 0.2
    """摘要 LLM 调用的温度（求稳）"""

    # ===== F6 · 缓存编辑微压缩 =====
    micro_compact_threshold_minutes: int = 10
    """微压缩时间阈值（分钟）。超过此时长的旧工具结果消息将被删除"""

    micro_compact_enabled: bool = True
    """是否启用微压缩。关闭后 micro_compact() 直接返回原列表"""


# ===== 数据结构 =====
@dataclass
class TokenEstimate:
    """Token 估算结果"""

    input_tokens: int
    reserved_output: int
    total: int  # = input_tokens + reserved_output


BudgetStatus = Literal["ok", "warn", "compact", "error"]


# ===== 核心类 =====
class ContextBudget:
    """会话上下文预算管理器。

    使用方式：
        budget = ContextBudget()
        status = budget.status(messages)
        if status == "compact":
            messages, boundary_count = await budget.compress(messages)
        elif status == "error":
            raise AppError("CONTEXT_OVERFLOW", 413)
    """

    # F6: 可被微压缩的消息类型（role 或 _kind 字段）
    # 语义：这类消息通常是"一次性快照"（执行结果、工具调用返回），
    # 过一段时间后信息已过时，可以零成本安全删除。
    MICRO_COMPACTABLE_ROLES: set[str] = {"tool_result", "execution_result"}

    def __init__(self, config: BudgetConfig | None = None):
        self.config = config or BudgetConfig()

    # ----- 估算 -----
    def estimate(self, messages: list[dict]) -> TokenEstimate:
        """粗估法估算消息列表的 token 数。

        公式：每消息 content 长度 / 4 * 4/3（4 chars per token + 4/3 安全边际）
        """
        total_chars = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 兼容 content 为 list 的情况（如 multimodal）
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
                    else:
                        total_chars += len(str(block))

        input_tokens = int(total_chars / 4 * (4 / 3))
        return TokenEstimate(
            input_tokens=input_tokens,
            reserved_output=self.config.output_reserve,
            total=input_tokens + self.config.output_reserve,
        )

    # ----- 水位判断 -----
    def status(self, messages: list[dict]) -> BudgetStatus:
        """返回当前上下文水位档位。

        阈值优先级（从高到低）：
        - error  >= effective_window * error_ratio
        - compact >= effective_window - autocompact_buffer
        - warn    >= effective_window * warning_ratio
        - ok      其他
        """
        est = self.estimate(messages)
        threshold_error = int(self.config.effective_window * self.config.error_ratio)
        threshold_compact = self.config.effective_window - self.config.autocompact_buffer
        threshold_warn = int(self.config.effective_window * self.config.warning_ratio)

        if est.total >= threshold_error:
            return "error"
        if est.total >= threshold_compact:
            return "compact"
        if est.total >= threshold_warn:
            return "warn"
        return "ok"

    def progress(self, messages: list[dict]) -> dict:
        """返回前端进度条所需信息。"""
        est = self.estimate(messages)
        return {
            "used": est.total,
            "limit": self.config.effective_window,
            "ratio": round(est.total / self.config.effective_window, 4),
            "status": self.status(messages),
        }

    # ----- 压缩 -----
    async def compress(
        self,
        messages: list[dict],
        summarizer=None,
    ) -> tuple[list[dict], int]:
        """压缩历史消息。

        策略：
        - 保留 system prompt（messages[0] if role == 'system'）
        - 保留最新 keep_recent_turns 轮（约 keep_recent_turns * 2 条消息）
        - 中间部分调摘要 LLM 生成 Boundary Message

        Args:
            messages: 完整消息列表
            summarizer: 可注入的摘要函数（用于测试 mock），签名 async (system, user, **kw) -> str | None
                        默认走 app.common.ai.call_llm

        Returns:
            (新消息列表, 被压缩的原始消息数)
            如果无需压缩或压缩失败兜底，第二项为 0。
        """
        if not messages:
            return messages, 0

        # 拆出 system prompt
        system_msg = None
        history = messages
        if messages[0].get("role") == "system":
            system_msg = messages[0]
            history = messages[1:]

        # 计算需要压缩的部分
        keep_count = self.config.keep_recent_turns * 2  # user + assistant
        if len(history) <= keep_count:
            return messages, 0

        to_compress = history[:-keep_count]
        to_keep = history[-keep_count:]

        if not to_compress:
            return messages, 0

        # 调摘要 LLM
        compact_user_input = self._build_compact_prompt(to_compress)
        summary = await self._call_summarizer(compact_user_input, summarizer)

        if summary is None:
            logger.warning("压缩摘要 LLM 调用失败，降级为硬截断")
            # 降级：丢掉最早部分，只保留 system + 最近若干条
            fallback = ([system_msg] if system_msg else []) + to_keep
            return fallback, 0

        # 构造 Boundary Message
        boundary_msg = {
            "role": "system",
            "content": f"[历史摘要] 前 {len(to_compress)} 条消息已由 AI 自动压缩：\n\n{summary}",
            "_boundary": True,
            "_original_count": len(to_compress),
        }

        new_messages: list[dict] = []
        if system_msg is not None:
            new_messages.append(system_msg)
        new_messages.append(boundary_msg)
        new_messages.extend(to_keep)

        return new_messages, len(to_compress)

    # ----- F6 · 微压缩 -----
    async def micro_compact(
        self,
        messages: list[dict],
        threshold_minutes: int | None = None,
    ) -> tuple[list[dict], int]:
        """微压缩：删除超过 threshold_minutes 的旧工具结果消息。

        与 compress() 不同：
        - 不调 LLM，零成本
        - 只删除带 timestamp 字段、且被判定为"可微压缩"的消息
        - 不动 system / boundary / 最近 keep_recent_turns*2 条 user/assistant 消息

        判定一条消息"可微压缩"的规则（任一满足即可）：
        1. 消息的 `role` 字段在 MICRO_COMPACTABLE_ROLES 中（future-proof：如未来引入 tool_result 角色）
        2. 消息的 `_kind` 字段在 MICRO_COMPACTABLE_ROLES 中（aiclawcode 约定的标记字段）
        3. 消息的 content 以 "[执行结果]" / "[工具结果]" / "[数据快照]" 等前缀开头（SkillForge 兼容）

        注意：
        - 不带 timestamp 的消息永远不会被删（容错：时间戳缺失无法判断新旧）
        - boundary 消息（`_boundary=True`）永远保留，即使它也是 system role
        - 最近 keep_recent_turns*2 条非 system 消息永远保留（即使它们带 timestamp 且可压缩）

        Args:
            messages: 完整消息列表（通常是 conversation.messages）
            threshold_minutes: 时间阈值，缺省用 config.micro_compact_threshold_minutes

        Returns:
            (新消息列表, 被删除的消息数)
            如果禁用 / 空列表 / 无可删消息，第二项为 0 并原样返回。
        """
        if not messages:
            return messages, 0

        if not self.config.micro_compact_enabled:
            return messages, 0

        threshold_min = (
            threshold_minutes
            if threshold_minutes is not None
            else self.config.micro_compact_threshold_minutes
        )
        # 允许 threshold_minutes=0 全部清理（测试/边界场景），但仍需 > 0 的 timedelta 意义
        # 约定：<0 视为非法，直接返回原列表
        if threshold_min < 0:
            return messages, 0

        # 内部压缩只比较时间间隔，统一转成 aware UTC 避免 naive / aware 混用。
        now = datetime.now(timezone.utc)
        threshold = timedelta(minutes=threshold_min)

        # 计算"最近 keep_recent_turns*2 条非 system/non-boundary 消息"的受保护区间
        # 从尾部往前数，直到凑够 keep_count 条对话消息
        keep_count = self.config.keep_recent_turns * 2
        protected_indices: set[int] = set()
        collected = 0
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            role = m.get("role", "")
            # system / boundary 不计入"对话消息"配额
            if role == "system" or m.get("_boundary"):
                continue
            protected_indices.add(i)
            collected += 1
            if collected >= keep_count:
                break

        removed = 0
        result: list[dict] = []
        for i, m in enumerate(messages):
            # 受保护的位置直接保留
            if i in protected_indices:
                result.append(m)
                continue

            # system / boundary 直接保留
            role = m.get("role", "")
            if role == "system" or m.get("_boundary"):
                result.append(m)
                continue

            # 只尝试删除"可微压缩"的消息
            if not self._is_micro_compactable(m):
                result.append(m)
                continue

            # 必须带 timestamp 才能判断新旧
            ts = m.get("timestamp")
            if not ts:
                result.append(m)
                continue

            # 解析时间戳；解析失败则保留（容错）。naive 字符串按北京时间理解，
            # 再统一转成 aware UTC 比较，避免部署机本地时区影响清理判断。
            try:
                if isinstance(ts, datetime):
                    parsed = ts
                else:
                    parsed = parse_bjt_datetime(str(ts))
                if parsed.tzinfo is None:
                    msg_time = parsed.replace(tzinfo=BJT).astimezone(timezone.utc)
                else:
                    msg_time = parsed.astimezone(timezone.utc)
            except (ValueError, TypeError):
                result.append(m)
                continue

            # 时间差 > 阈值 → 删除
            if (now - msg_time) > threshold:
                removed += 1
                continue

            result.append(m)

        if removed > 0:
            logger.info(
                f"micro_compact 已删除 {removed} 条过期消息"
                f"（threshold={threshold_min}min，剩余 {len(result)} 条）"
            )

        return result, removed

    def _is_micro_compactable(self, message: dict) -> bool:
        """判断一条消息是否属于"可微压缩"范畴。

        满足任一条件：
        - role ∈ MICRO_COMPACTABLE_ROLES
        - _kind ∈ MICRO_COMPACTABLE_ROLES
        - content 以已知的"工具结果"前缀开头
        """
        role = message.get("role")
        if role in self.MICRO_COMPACTABLE_ROLES:
            return True

        kind = message.get("_kind")
        if kind in self.MICRO_COMPACTABLE_ROLES:
            return True

        content = message.get("content", "")
        if isinstance(content, str):
            stripped = content.lstrip()
            # SkillForge 兼容前缀：执行结果 / 工具结果 / 数据快照 等一次性注入
            for prefix in ("[执行结果]", "[工具结果]", "[数据快照]", "[tool_result]", "[execution_result]"):
                if stripped.startswith(prefix):
                    return True

        return False

    # ----- 内部辅助 -----
    def _build_compact_prompt(self, messages: list[dict]) -> str:
        """把待压缩的历史消息拼成摘要输入文本。"""
        lines: list[str] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            # 单条消息内部超长时截断（避免摘要输入本身爆炸）
            if len(content) > 2000:
                content = content[:2000] + "...(单条已截断)"
            lines.append(f"[{role}]\n{content}")
        return "\n\n".join(lines)

    async def _call_summarizer(
        self,
        user_input: str,
        summarizer=None,
    ) -> str | None:
        """调用摘要 LLM。允许注入函数用于测试。"""
        # F3: 从 PromptRegistry 加载，registry 未注册时 fallback 到内联文本
        try:
            from app.common.prompt_registry import prompt_registry
            system_prompt, _ = prompt_registry.build("compact_summarizer")
        except KeyError:
            system_prompt = (
                "你是对话摘要专家。请用 300 字以内概括下面的对话，"
                "保留所有关键事实、数字、用户意图和已达成的结论。"
                "用第三人称客观叙述，不要省略任何参数或决策结果。"
            )

        if summarizer is not None:
            try:
                return await summarizer(
                    system=system_prompt,
                    user=user_input,
                    max_tokens=self.config.summarizer_max_tokens,
                    temperature=self.config.summarizer_temperature,
                )
            except Exception as e:
                logger.warning(f"注入的 summarizer 调用失败: {e}")
                return None

        # 默认走 app.common.ai.call_llm（json_mode=False，纯文本）
        try:
            from app.common.ai import call_llm

            result = await call_llm(
                system=system_prompt,
                user=user_input,
                max_tokens=self.config.summarizer_max_tokens,
                temperature=self.config.summarizer_temperature,
                json_mode=False,
            )
            if isinstance(result, str):
                return result.strip() or None
            return None
        except Exception as e:
            logger.warning(f"默认 summarizer 调用失败: {e}")
            return None


# 全局单例（默认配置，可在启动时替换）
context_budget = ContextBudget()
