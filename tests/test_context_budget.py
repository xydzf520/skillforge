"""ContextBudget 单元测试。

覆盖：估算、水位判断、压缩、降级、参数边界。
不依赖数据库，所有 LLM 调用通过注入 summarizer mock。
"""

import pytest

from app.common.context_budget import (
    BudgetConfig,
    ContextBudget,
    TokenEstimate,
)


# ===== 1. estimate =====
class TestEstimate:
    def test_empty_messages(self):
        budget = ContextBudget()
        est = budget.estimate([])
        assert est.input_tokens == 0
        assert est.reserved_output == 4000
        assert est.total == 4000

    def test_simple_messages(self):
        """4 chars ≈ 1 token，乘以 4/3 安全边际"""
        budget = ContextBudget()
        msgs = [
            {"role": "user", "content": "a" * 400},  # 400 chars → 100 tokens → 133 with margin
        ]
        est = budget.estimate(msgs)
        # 400 / 4 * 4/3 ≈ 133
        assert est.input_tokens == 133
        assert est.total == 133 + 4000

    def test_multiple_messages_summed(self):
        budget = ContextBudget()
        msgs = [
            {"role": "system", "content": "x" * 100},
            {"role": "user", "content": "y" * 200},
            {"role": "assistant", "content": "z" * 300},
        ]
        est = budget.estimate(msgs)
        # 总字符 600 → 600/4*4/3 = 200
        assert est.input_tokens == 200

    def test_non_string_content_skipped_safely(self):
        """content 为 list 时也不应崩溃（兼容 multimodal）"""
        budget = ContextBudget()
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": "world"},
        ]
        est = budget.estimate(msgs)
        # "hello"=5 + "world"=5 = 10 chars
        assert est.input_tokens >= 1


# ===== 2. status =====
class TestStatus:
    def _make_msgs(self, total_chars: int) -> list[dict]:
        """造一条 user 消息，content 长度刚好对应预期 chars"""
        return [{"role": "user", "content": "a" * total_chars}]

    def test_ok_status(self):
        budget = ContextBudget()
        # 远小于 warning_ratio
        assert budget.status(self._make_msgs(1000)) == "ok"

    def test_warn_status(self):
        """达到 warning_ratio (80%) 但未到 compact 阈值"""
        cfg = BudgetConfig(
            effective_window=10_000,
            output_reserve=0,
            autocompact_buffer=1000,  # compact 阈值 = 9000
            warning_ratio=0.80,        # warn 阈值 = 8000
            error_ratio=0.95,
        )
        budget = ContextBudget(cfg)
        # 想让 total = 8500（8000 < 8500 < 9000）
        # input_tokens = chars/4*4/3，反推 chars = 8500*3 = 25500
        msgs = [{"role": "user", "content": "a" * 25500}]
        assert budget.status(msgs) == "warn"

    def test_compact_status(self):
        """达到 compact 阈值（effective - buffer）"""
        cfg = BudgetConfig(
            effective_window=10_000,
            output_reserve=0,
            autocompact_buffer=1000,  # compact 阈值 = 9000
            error_ratio=0.99,          # error 阈值 = 9900
        )
        budget = ContextBudget(cfg)
        # 想让 total = 9500（9000 ≤ 9500 < 9900）
        msgs = [{"role": "user", "content": "a" * (9500 * 3)}]
        assert budget.status(msgs) == "compact"

    def test_error_status(self):
        """达到 error_ratio (95%) 且 ≥ error 阈值"""
        cfg = BudgetConfig(
            effective_window=10_000,
            output_reserve=0,
            autocompact_buffer=500,
            error_ratio=0.90,  # error 阈值 = 9000，注意 compact 阈值 = 9500
        )
        # 当 error_ratio 设得比 compact 低时，error 优先于 compact
        budget = ContextBudget(cfg)
        msgs = [{"role": "user", "content": "a" * (9200 * 3)}]
        assert budget.status(msgs) == "error"

    def test_progress_returns_dict(self):
        budget = ContextBudget()
        info = budget.progress([{"role": "user", "content": "hello"}])
        assert "used" in info
        assert "limit" in info
        assert "ratio" in info
        assert "status" in info
        assert info["status"] in ("ok", "warn", "compact", "error")


# ===== 3. compress =====
class FakeSummarizer:
    """可配置返回值的 mock 摘要函数"""

    def __init__(self, return_value: str | None = "这是摘要", raise_exc: bool = False):
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.call_count = 0
        self.last_user_input = None

    async def __call__(self, system: str, user: str, **kwargs) -> str | None:
        self.call_count += 1
        self.last_user_input = user
        if self.raise_exc:
            raise RuntimeError("模拟摘要失败")
        return self.return_value


class TestCompress:
    def _build_messages(self, n_turns: int, with_system: bool = True) -> list[dict]:
        """造 n_turns 轮 user/assistant 消息（含 1 条 system）"""
        msgs = []
        if with_system:
            msgs.append({"role": "system", "content": "你是测试助手"})
        for i in range(n_turns):
            msgs.append({"role": "user", "content": f"第{i+1}轮提问内容内容内容内容内容"})
            msgs.append({"role": "assistant", "content": f"第{i+1}轮回答回答回答回答回答"})
        return msgs

    @pytest.mark.asyncio
    async def test_compress_skipped_when_few_messages(self):
        """消息太少时不压缩"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=6))
        msgs = self._build_messages(n_turns=3)  # 总共 7 条（system + 6）
        summarizer = FakeSummarizer()

        new_msgs, count = await budget.compress(msgs, summarizer=summarizer)

        assert count == 0
        assert new_msgs == msgs
        assert summarizer.call_count == 0

    @pytest.mark.asyncio
    async def test_compress_inserts_boundary(self):
        """正常压缩：保留 system + boundary + 最近 6 轮"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=6))
        msgs = self._build_messages(n_turns=10)  # system + 20 条 = 21 条
        summarizer = FakeSummarizer(return_value="前 4 轮的摘要内容")

        new_msgs, count = await budget.compress(msgs, summarizer=summarizer)

        # 应该压缩 4 轮 = 8 条
        assert count == 8
        assert summarizer.call_count == 1

        # 结构：system + boundary + 最近 12 条 = 14 条
        assert len(new_msgs) == 1 + 1 + 12
        assert new_msgs[0]["role"] == "system"
        assert new_msgs[0]["content"] == "你是测试助手"
        assert new_msgs[1].get("_boundary") is True
        assert new_msgs[1]["_original_count"] == 8
        assert "前 4 轮的摘要内容" in new_msgs[1]["content"]
        # 最后 12 条应该是原始的最近 6 轮
        assert new_msgs[-1]["content"] == "第10轮回答回答回答回答回答"
        assert new_msgs[-12]["content"] == "第5轮提问内容内容内容内容内容"

    @pytest.mark.asyncio
    async def test_compress_preserves_when_no_system(self):
        """无 system prompt 时也能正常压缩"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=6))
        msgs = self._build_messages(n_turns=10, with_system=False)
        summarizer = FakeSummarizer()

        new_msgs, count = await budget.compress(msgs, summarizer=summarizer)
        assert count == 8
        # 无 system 时：boundary + 最近 12 条
        assert len(new_msgs) == 13
        assert new_msgs[0].get("_boundary") is True

    @pytest.mark.asyncio
    async def test_compress_fallback_when_summarizer_returns_none(self):
        """摘要返回 None 时降级为硬截断（保留 system + 最近若干条）"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=6))
        msgs = self._build_messages(n_turns=10)
        summarizer = FakeSummarizer(return_value=None)

        new_msgs, count = await budget.compress(msgs, summarizer=summarizer)

        assert count == 0  # 标志：未真正压缩
        # 降级结构：system + 最近 12 条 = 13 条
        assert len(new_msgs) == 13
        assert new_msgs[0]["role"] == "system"
        # 没有 boundary 消息
        assert not any(m.get("_boundary") for m in new_msgs)

    @pytest.mark.asyncio
    async def test_compress_fallback_when_summarizer_raises(self):
        """摘要抛异常时也降级为硬截断"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=6))
        msgs = self._build_messages(n_turns=10)
        summarizer = FakeSummarizer(raise_exc=True)

        new_msgs, count = await budget.compress(msgs, summarizer=summarizer)
        assert count == 0
        assert len(new_msgs) == 13  # 同上

    @pytest.mark.asyncio
    async def test_compress_empty_messages(self):
        """空消息列表不应崩溃"""
        budget = ContextBudget()
        new_msgs, count = await budget.compress([], summarizer=FakeSummarizer())
        assert new_msgs == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_compress_summarizer_receives_history_text(self):
        """summarizer 应该收到拼接后的历史文本"""
        budget = ContextBudget(BudgetConfig(keep_recent_turns=2))
        msgs = self._build_messages(n_turns=5)
        summarizer = FakeSummarizer()

        await budget.compress(msgs, summarizer=summarizer)

        assert summarizer.last_user_input is not None
        # 摘要输入应该包含被压缩的消息内容
        assert "第1轮提问内容内容内容内容内容" in summarizer.last_user_input
        assert "第3轮回答回答回答回答回答" in summarizer.last_user_input
        # 不应该包含未被压缩的最近 2 轮
        assert "第5轮回答回答回答回答回答" not in summarizer.last_user_input


# ===== 4. 配置边界 =====
class TestConfig:
    def test_default_config(self):
        cfg = BudgetConfig()
        assert cfg.effective_window == 150_000
        assert cfg.output_reserve == 4_000
        assert cfg.warning_ratio == 0.80
        assert cfg.error_ratio == 0.95
        assert cfg.max_consecutive_failures == 3
        assert cfg.keep_recent_turns == 6

    def test_custom_config_overrides(self):
        cfg = BudgetConfig(effective_window=50_000, keep_recent_turns=3)
        budget = ContextBudget(cfg)
        assert budget.config.effective_window == 50_000
        assert budget.config.keep_recent_turns == 3

    def test_global_singleton_exists(self):
        from app.common.context_budget import context_budget
        assert context_budget is not None
        assert isinstance(context_budget, ContextBudget)
