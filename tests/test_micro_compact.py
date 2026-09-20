"""F6 缓存编辑微压缩（micro_compact）单元测试。

覆盖：
- 删除超过 threshold 的旧可压缩消息
- 保留最近 keep_recent_turns * 2 条对话消息（即使它们可压缩且过期）
- 保留 system / boundary 消息
- 不带 timestamp 的可压缩消息不被删除（容错）
- threshold=0 时全部过期消息被清理
- 空消息列表不崩溃
- 禁用配置时不动任何消息
- 时间戳解析失败时不崩溃
- 纯普通 user/assistant 消息（非可压缩）不被删除
- 基于 content 前缀识别可压缩消息（SkillForge 兼容路径）

不依赖数据库，不依赖 LLM。
"""

from datetime import datetime, timedelta

import pytest

from app.common.time_utils import now_bjt
from app.common.context_budget import BudgetConfig, ContextBudget


def _ts(minutes_ago: int) -> str:
    """返回 N 分钟前的 ISO 时间戳字符串"""
    return (now_bjt() - timedelta(minutes=minutes_ago)).isoformat()


def _make_budget(threshold: int = 10, keep_turns: int = 3, enabled: bool = True) -> ContextBudget:
    """造一个可配置阈值的 ContextBudget 实例"""
    return ContextBudget(
        BudgetConfig(
            micro_compact_threshold_minutes=threshold,
            micro_compact_enabled=enabled,
            keep_recent_turns=keep_turns,
        )
    )


# ===== 1. 基础删除逻辑 =====
class TestBasicRemoval:
    @pytest.mark.asyncio
    async def test_removes_old_tool_result_messages(self):
        """删除 role=tool_result 且 timestamp 超过阈值的消息"""
        budget = _make_budget(threshold=10)
        messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "提问1", "timestamp": _ts(30)},
            {"role": "tool_result", "content": "旧的执行结果", "timestamp": _ts(20)},
            {"role": "assistant", "content": "回答1", "timestamp": _ts(15)},
            # 保证 keep_turns*2=6 个对话位，下面凑够"最近"槽位
            {"role": "user", "content": "提问2", "timestamp": _ts(8)},
            {"role": "assistant", "content": "回答2", "timestamp": _ts(7)},
            {"role": "user", "content": "提问3", "timestamp": _ts(5)},
            {"role": "assistant", "content": "回答3", "timestamp": _ts(4)},
            {"role": "user", "content": "提问4", "timestamp": _ts(3)},
            {"role": "assistant", "content": "回答4", "timestamp": _ts(2)},
        ]
        # 此时 tool_result 不在最近 6 条对话槽位里，应被删除
        result, removed = await budget.micro_compact(messages)

        assert removed == 1
        assert len(result) == len(messages) - 1
        # 应该没有任何 tool_result 消息剩下
        assert not any(m.get("role") == "tool_result" for m in result)
        # system 保留
        assert result[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_removes_execution_result_by_kind(self):
        """通过 _kind 字段识别可压缩消息"""
        budget = _make_budget(threshold=10)
        messages = [
            {"role": "system", "content": "系统"},
            {
                "role": "user",
                "_kind": "execution_result",
                "content": "执行结果 A",
                "timestamp": _ts(25),
            },
            {"role": "user", "content": "U1", "timestamp": _ts(8)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(7)},
            {"role": "user", "content": "U2", "timestamp": _ts(6)},
            {"role": "assistant", "content": "A2", "timestamp": _ts(5)},
            {"role": "user", "content": "U3", "timestamp": _ts(4)},
            {"role": "assistant", "content": "A3", "timestamp": _ts(3)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 1
        assert not any(m.get("_kind") == "execution_result" for m in result)

    @pytest.mark.asyncio
    async def test_removes_by_content_prefix(self):
        """通过 content 前缀识别可压缩消息（SkillForge 兼容路径）"""
        budget = _make_budget(threshold=10)
        messages = [
            {"role": "system", "content": "系统"},
            {
                "role": "assistant",
                "content": "[执行结果] {\"roi\": 1.2}",
                "timestamp": _ts(30),
            },
            # 填满"最近 6 条"窗口
            {"role": "user", "content": "U1", "timestamp": _ts(8)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(7)},
            {"role": "user", "content": "U2", "timestamp": _ts(6)},
            {"role": "assistant", "content": "A2", "timestamp": _ts(5)},
            {"role": "user", "content": "U3", "timestamp": _ts(4)},
            {"role": "assistant", "content": "A3", "timestamp": _ts(3)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 1
        # 旧执行结果已删
        assert not any(
            isinstance(m.get("content"), str) and m["content"].startswith("[执行结果]")
            for m in result
        )


# ===== 2. 保留逻辑 =====
class TestPreservation:
    @pytest.mark.asyncio
    async def test_preserves_system_messages(self):
        """system 消息永远保留，即使带 timestamp 且看起来'过期'"""
        budget = _make_budget(threshold=5)
        messages = [
            {"role": "system", "content": "系统提示", "timestamp": _ts(60)},
            {"role": "user", "content": "U1", "timestamp": _ts(3)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(2)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 0
        assert result[0]["role"] == "system"
        assert result == messages

    @pytest.mark.asyncio
    async def test_preserves_boundary_messages(self):
        """带 _boundary=True 的消息永远保留"""
        budget = _make_budget(threshold=5)
        messages = [
            {
                "role": "system",
                "content": "[历史摘要] xxx",
                "_boundary": True,
                "timestamp": _ts(60),
            },
            {"role": "user", "content": "U1", "timestamp": _ts(3)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(2)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 0
        assert result[0].get("_boundary") is True

    @pytest.mark.asyncio
    async def test_preserves_recent_turns_even_if_old_tool_results(self):
        """最近 keep_recent_turns*2 条对话消息即使是过期的 tool_result 也保留"""
        budget = _make_budget(threshold=5, keep_turns=2)  # 保留最近 4 条
        messages = [
            {"role": "system", "content": "系统"},
            # 这一条"靠后"但也过期。是否保留取决于它是否落在最近 4 条窗口内
            {"role": "tool_result", "content": "结果1", "timestamp": _ts(30)},
            {"role": "tool_result", "content": "结果2", "timestamp": _ts(29)},
            {"role": "tool_result", "content": "结果3", "timestamp": _ts(28)},
            {"role": "tool_result", "content": "结果4", "timestamp": _ts(27)},
        ]
        # 4 条 tool_result，全部过期，但最近 4 条都在保护区内
        result, removed = await budget.micro_compact(messages)

        # 窗口刚好 4 条，所以没有删除
        assert removed == 0
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_preserves_non_compactable_messages(self):
        """普通 user/assistant 消息即使过期也不被删除（不属于可压缩集合）"""
        budget = _make_budget(threshold=5)
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "user", "content": "很早的问题", "timestamp": _ts(100)},
            {"role": "assistant", "content": "很早的回答", "timestamp": _ts(99)},
            {"role": "user", "content": "U2", "timestamp": _ts(3)},
            {"role": "assistant", "content": "A2", "timestamp": _ts(2)},
        ]
        result, removed = await budget.micro_compact(messages)

        # user/assistant 不在可压缩集合 → 不删除
        assert removed == 0
        assert len(result) == 5


# ===== 3. 边界与容错 =====
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """空消息列表不崩溃"""
        budget = _make_budget()
        result, removed = await budget.micro_compact([])
        assert result == []
        assert removed == 0

    @pytest.mark.asyncio
    async def test_threshold_zero_removes_all_expired(self):
        """阈值 0 时所有可压缩 + 带时间戳的过期消息都被清理"""
        budget = _make_budget(threshold=0, keep_turns=1)  # 最近 2 条对话保留
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "tool_result", "content": "结果1", "timestamp": _ts(5)},
            {"role": "tool_result", "content": "结果2", "timestamp": _ts(4)},
            {"role": "tool_result", "content": "结果3", "timestamp": _ts(3)},
            {"role": "user", "content": "U1", "timestamp": _ts(2)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(1)},
        ]
        # threshold=0 → 所有超过 0min 的过期
        # 受保护：system + 最近 2 条对话（U1, A1）
        # tool_result 都过期且不在保护区 → 全删
        result, removed = await budget.micro_compact(messages)

        assert removed == 3
        # 剩 system + U1 + A1
        assert len(result) == 3
        assert not any(m.get("role") == "tool_result" for m in result)

    @pytest.mark.asyncio
    async def test_no_timestamp_preserved(self):
        """可压缩但没有 timestamp 字段的消息不被删除（容错）"""
        budget = _make_budget(threshold=5)
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "tool_result", "content": "无时间戳的旧结果"},  # 没有 timestamp
            {"role": "user", "content": "U1", "timestamp": _ts(3)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(2)},
        ]
        result, removed = await budget.micro_compact(messages)

        # 因为没有 timestamp，无法判断新旧，保留
        assert removed == 0
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_malformed_timestamp_preserved(self):
        """时间戳解析失败的消息保留（容错）"""
        budget = _make_budget(threshold=5)
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "tool_result", "content": "烂时间戳", "timestamp": "not-a-timestamp"},
            {"role": "user", "content": "U1", "timestamp": _ts(3)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(2)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 0
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_disabled_config_skips(self):
        """micro_compact_enabled=False 时完全跳过"""
        budget = _make_budget(threshold=1, enabled=False)
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "tool_result", "content": "旧结果", "timestamp": _ts(60)},
            {"role": "user", "content": "U1", "timestamp": _ts(2)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(1)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 0
        assert result == messages

    @pytest.mark.asyncio
    async def test_negative_threshold_returns_original(self):
        """负阈值非法，原样返回"""
        budget = _make_budget()
        messages = [
            {"role": "tool_result", "content": "结果", "timestamp": _ts(60)},
        ]
        result, removed = await budget.micro_compact(messages, threshold_minutes=-1)
        assert removed == 0
        assert result == messages

    @pytest.mark.asyncio
    async def test_fresh_tool_result_preserved(self):
        """时间戳新鲜（< threshold）的 tool_result 不被删除"""
        budget = _make_budget(threshold=10)
        messages = [
            {"role": "system", "content": "系统"},
            {"role": "tool_result", "content": "刚刚的结果", "timestamp": _ts(2)},
            {"role": "user", "content": "U1", "timestamp": _ts(1)},
        ]
        result, removed = await budget.micro_compact(messages)

        assert removed == 0
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_explicit_threshold_overrides_config(self):
        """方法参数 threshold_minutes 优先于 config"""
        # 用 keep_turns=1 让保留窗口只覆盖最近 2 条对话
        budget = _make_budget(threshold=60, keep_turns=1)  # config 说 60 分钟
        messages = [
            {"role": "system", "content": "系统"},
            # 这条 tool_result 在位置 1，后面跟着 2 条 user/assistant（满窗口）
            # 所以它不在最近 2 条对话保护区内
            {"role": "tool_result", "content": "10 分钟前的结果", "timestamp": _ts(10)},
            {"role": "user", "content": "U1", "timestamp": _ts(2)},
            {"role": "assistant", "content": "A1", "timestamp": _ts(1)},
        ]
        # 用参数覆盖为 5 分钟 → 10 分钟前的已过期
        result, removed = await budget.micro_compact(messages, threshold_minutes=5)

        assert removed == 1
        assert not any(m.get("role") == "tool_result" for m in result)

        # 反向验证：用 config 默认的 60 分钟，不应该删除
        result2, removed2 = await budget.micro_compact(messages)
        assert removed2 == 0


# ===== 4. 配置测试 =====
class TestConfig:
    def test_default_config_values(self):
        cfg = BudgetConfig()
        assert cfg.micro_compact_threshold_minutes == 10
        assert cfg.micro_compact_enabled is True

    def test_micro_compactable_roles_constant(self):
        """MICRO_COMPACTABLE_ROLES 类常量存在且包含预期角色"""
        assert "tool_result" in ContextBudget.MICRO_COMPACTABLE_ROLES
        assert "execution_result" in ContextBudget.MICRO_COMPACTABLE_ROLES

    def test_is_micro_compactable_helper(self):
        """_is_micro_compactable 辅助方法覆盖所有三种识别路径"""
        budget = _make_budget()
        assert budget._is_micro_compactable({"role": "tool_result", "content": "x"}) is True
        assert budget._is_micro_compactable({"role": "user", "_kind": "execution_result", "content": "x"}) is True
        assert budget._is_micro_compactable({"role": "assistant", "content": "[执行结果] foo"}) is True
        assert budget._is_micro_compactable({"role": "user", "content": "普通提问"}) is False
        assert budget._is_micro_compactable({"role": "system", "content": "系统提示"}) is False


# ===== 5. Codex 审计追加：时区安全 =====

def _build_msgs_with_old_tools(*tool_msgs: dict) -> list[dict]:
    """造一组：system + 若干 tool_result + 6 条新对话保护窗口。

    keep_turns=3 → keep_count=6，所以保护窗口需要 ≥ 6 条非 system 对话消息，
    否则被测的 tool_result 会落进保护区导致 removed==0。
    """
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    msgs.extend(tool_msgs)
    for i in range(6):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"近{i}"})
    return msgs


class TestTimezoneAware:
    """Codex 审计点：aware/naive 时间戳混用应不崩溃，且语义正确"""

    @pytest.mark.asyncio
    async def test_aware_utc_timestamp(self):
        """带 UTC 时区的时间戳应被正确比较"""
        from datetime import timezone
        budget = _make_budget(threshold=10, keep_turns=3)
        old_aware = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        messages = _build_msgs_with_old_tools(
            {"role": "tool_result", "content": "old", "timestamp": old_aware},
        )
        _, removed = await budget.micro_compact(messages, threshold_minutes=10)
        assert removed == 1, f"60 分钟前的 UTC aware 应被删除 (removed={removed})"

    @pytest.mark.asyncio
    async def test_aware_non_utc_timestamp(self):
        """带 +08:00 时区的时间戳应被正确转换为 UTC 后比较"""
        from datetime import timezone
        budget = _make_budget(threshold=10, keep_turns=3)
        cn = timezone(timedelta(hours=8))
        old_cn = (datetime.now(cn) - timedelta(minutes=60)).isoformat()
        messages = _build_msgs_with_old_tools(
            {"role": "tool_result", "content": "old", "timestamp": old_cn},
        )
        _, removed = await budget.micro_compact(messages, threshold_minutes=10)
        assert removed == 1, f"+08:00 时区的 60 分钟前应被删除 (removed={removed})"

    @pytest.mark.asyncio
    async def test_mixed_aware_naive_does_not_crash(self):
        """混用 aware 和 naive 时间戳不应抛 TypeError"""
        from datetime import timezone
        budget = _make_budget(threshold=10, keep_turns=3)
        old_aware = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        old_naive = (datetime.utcnow() - timedelta(minutes=60)).isoformat()
        messages = _build_msgs_with_old_tools(
            {"role": "tool_result", "content": "aware", "timestamp": old_aware},
            {"role": "tool_result", "content": "naive", "timestamp": old_naive},
        )
        # 关键断言：不抛 TypeError
        _, removed = await budget.micro_compact(messages, threshold_minutes=10)
        assert removed == 2, f"两条过期 tool_result 都应被删除 (removed={removed})"

    @pytest.mark.asyncio
    async def test_datetime_object_timestamp(self):
        """timestamp 是 datetime 对象（非字符串）也应正确处理"""
        from datetime import timezone
        budget = _make_budget(threshold=10, keep_turns=3)
        old_dt = datetime.now(timezone.utc) - timedelta(minutes=60)
        messages = _build_msgs_with_old_tools(
            {"role": "tool_result", "content": "old", "timestamp": old_dt},
        )
        _, removed = await budget.micro_compact(messages, threshold_minutes=10)
        assert removed == 1
