"""ForkContext 单元测试（F5）。

覆盖：
- CacheSafeParams 构造
- ForkContext 构造 + system_prompt_hash
- fork() 返回结构（system 在前、base 居中、user 在后）
- system 消息的 cache_control 标记
- extra_system 拼接到 system_prompt 之后
- base_messages 注入
- to_messages_for_call() 多消息场景
- immutability：返回值修改不污染内部状态
- 边界：空 system_prompt / 空 base_messages
"""

from __future__ import annotations

import hashlib

import pytest

from app.common.fork_context import CacheSafeParams, ForkContext


# ========== CacheSafeParams ==========

class TestCacheSafeParams:
    def test_minimal_construction(self):
        p = CacheSafeParams(system_prompt="你是助手")
        assert p.system_prompt == "你是助手"
        assert p.base_messages == []
        assert p.tool_schemas is None

    def test_with_base_messages(self):
        base = [{"role": "user", "content": "上下文"}]
        p = CacheSafeParams(system_prompt="sys", base_messages=base)
        assert p.base_messages == base

    def test_frozen_is_immutable(self):
        """dataclass(frozen=True) 不允许字段赋值"""
        p = CacheSafeParams(system_prompt="sys")
        with pytest.raises((AttributeError, TypeError)):
            p.system_prompt = "改了"  # type: ignore[misc]


# ========== ForkContext 构造 + hash ==========

class TestForkContextInit:
    def test_basic_properties(self):
        fc = ForkContext(system_prompt="你是 SkillForge Workbench 助手")
        assert fc.system_prompt == "你是 SkillForge Workbench 助手"
        assert fc.base_messages == []
        assert fc.tool_schemas is None

    def test_hash_is_12_hex_chars(self):
        fc = ForkContext(system_prompt="hello")
        assert len(fc.system_prompt_hash) == 12
        assert all(c in "0123456789abcdef" for c in fc.system_prompt_hash)

    def test_hash_matches_sha256_prefix(self):
        """hash 必须是 sha256 的前 12 位十六进制"""
        text = "你是测试助手"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        fc = ForkContext(system_prompt=text)
        assert fc.system_prompt_hash == expected

    def test_hash_is_stable(self):
        """同样的 system_prompt 两次构造产生同一个 hash"""
        a = ForkContext(system_prompt="same")
        b = ForkContext(system_prompt="same")
        assert a.system_prompt_hash == b.system_prompt_hash

    def test_hash_changes_with_content(self):
        a = ForkContext(system_prompt="alpha")
        b = ForkContext(system_prompt="beta")
        assert a.system_prompt_hash != b.system_prompt_hash

    def test_empty_system_prompt_is_allowed(self):
        fc = ForkContext(system_prompt="")
        assert fc.system_prompt == ""
        assert fc.system_prompt_hash == hashlib.sha256(b"").hexdigest()[:12]

    def test_none_system_prompt_normalized_to_empty(self):
        """容忍 None（虽然类型提示是 str）— 避免调用方疏漏炸掉"""
        fc = ForkContext(system_prompt=None)  # type: ignore[arg-type]
        assert fc.system_prompt == ""

    def test_base_messages_deep_copied(self):
        """构造时深拷贝 base_messages，外部修改不应污染 ForkContext"""
        base = [{"role": "user", "content": "原始"}]
        fc = ForkContext(system_prompt="sys", base_messages=base)
        base[0]["content"] = "被改掉了"
        # ForkContext 内部仍然是原始值
        assert fc.base_messages[0]["content"] == "原始"


# ========== fork() 基本结构 ==========

class TestForkBasicStructure:
    def test_fork_returns_list(self):
        fc = ForkContext(system_prompt="sys")
        msgs = fc.fork("你好")
        assert isinstance(msgs, list)
        assert len(msgs) == 2  # system + user

    def test_system_first_user_last(self):
        fc = ForkContext(system_prompt="sys")
        msgs = fc.fork("请回答")
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "请回答"

    def test_system_content_matches(self):
        fc = ForkContext(system_prompt="你是助手 X")
        msgs = fc.fork("hi")
        assert msgs[0]["content"] == "你是助手 X"

    def test_system_carries_cache_control(self):
        """关键：system 消息必须带 cache_control: ephemeral"""
        fc = ForkContext(system_prompt="sys")
        msgs = fc.fork("hi")
        assert "cache_control" in msgs[0]
        assert msgs[0]["cache_control"] == {"type": "ephemeral"}

    def test_user_message_has_no_cache_control(self):
        """user 消息不应被加上 cache_control"""
        fc = ForkContext(system_prompt="sys")
        msgs = fc.fork("hi")
        assert "cache_control" not in msgs[-1]

    def test_fork_each_call_returns_fresh_list(self):
        """每次 fork() 返回新的 list，修改返回值不影响下次调用"""
        fc = ForkContext(system_prompt="sys")
        a = fc.fork("q1")
        b = fc.fork("q2")
        assert a is not b
        a.append({"role": "assistant", "content": "pollute"})
        # 第二次调用得到的长度仍然是 system + user = 2
        c = fc.fork("q3")
        assert len(c) == 2


# ========== extra_system 拼接 ==========

class TestExtraSystem:
    def test_extra_system_appended_after_base(self):
        fc = ForkContext(system_prompt="你是助手")
        msgs = fc.fork("hi", extra_system="请用 JSON 回答")
        sys_content = msgs[0]["content"]
        assert "你是助手" in sys_content
        assert "请用 JSON 回答" in sys_content
        # 顺序：base prompt 在前，extra 在后
        assert sys_content.index("你是助手") < sys_content.index("请用 JSON 回答")

    def test_extra_system_separator(self):
        """base 和 extra 之间应该有空行分隔"""
        fc = ForkContext(system_prompt="base")
        msgs = fc.fork("hi", extra_system="extra")
        assert msgs[0]["content"] == "base\n\nextra"

    def test_extra_system_only(self):
        """base 为空但 extra 有内容，也应该产生 system 消息"""
        fc = ForkContext(system_prompt="")
        msgs = fc.fork("hi", extra_system="仅 extra")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "仅 extra"
        assert msgs[0]["cache_control"] == {"type": "ephemeral"}

    def test_no_system_when_both_empty(self):
        """base 和 extra 都为空，fork 应跳过 system 消息"""
        fc = ForkContext(system_prompt="")
        msgs = fc.fork("hi", extra_system="")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_extra_system_whitespace_treated_as_empty(self):
        """纯空白 extra_system 不应重复加分隔符"""
        fc = ForkContext(system_prompt="base")
        msgs = fc.fork("hi", extra_system="   ")
        assert msgs[0]["content"] == "base"


# ========== base_messages 注入 ==========

class TestBaseMessages:
    def test_base_messages_between_system_and_user(self):
        base = [
            {"role": "user", "content": "【Skill 上下文】..."},
            {"role": "assistant", "content": "已理解。"},
        ]
        fc = ForkContext(system_prompt="sys", base_messages=base)
        msgs = fc.fork("问题")
        assert len(msgs) == 4
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "【Skill 上下文】..."
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "已理解。"
        assert msgs[3]["role"] == "user"
        assert msgs[3]["content"] == "问题"

    def test_base_messages_deep_copied_in_fork(self):
        """fork 返回的 base 消息副本被修改，不应影响下一次 fork"""
        base = [{"role": "user", "content": "ctx"}]
        fc = ForkContext(system_prompt="sys", base_messages=base)
        msgs1 = fc.fork("q1")
        msgs1[1]["content"] = "被改了"
        msgs2 = fc.fork("q2")
        assert msgs2[1]["content"] == "ctx"

    def test_empty_base_messages_default(self):
        fc = ForkContext(system_prompt="sys")
        msgs = fc.fork("q")
        assert len(msgs) == 2

    def test_base_messages_property_returns_copy(self):
        """base_messages 属性返回的是拷贝，调用方修改不能污染内部"""
        base = [{"role": "user", "content": "ctx"}]
        fc = ForkContext(system_prompt="sys", base_messages=base)
        got = fc.base_messages
        got.append({"role": "user", "content": "注入"})
        # 再次取出仍然只有一条
        assert len(fc.base_messages) == 1


# ========== to_messages_for_call ==========

class TestToMessagesForCall:
    def test_multi_messages_appended(self):
        fc = ForkContext(system_prompt="sys")
        extras = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        msgs = fc.to_messages_for_call(extras)
        assert len(msgs) == 4  # system + 3
        assert msgs[0]["role"] == "system"
        assert msgs[0]["cache_control"] == {"type": "ephemeral"}
        assert msgs[1:] == extras

    def test_with_base_messages(self):
        base = [{"role": "user", "content": "ctx"}]
        fc = ForkContext(system_prompt="sys", base_messages=base)
        extras = [{"role": "user", "content": "问题"}]
        msgs = fc.to_messages_for_call(extras)
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1] == {"role": "user", "content": "ctx"}
        assert msgs[2] == {"role": "user", "content": "问题"}

    def test_empty_user_messages_allowed(self):
        fc = ForkContext(system_prompt="sys")
        msgs = fc.to_messages_for_call([])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_none_user_messages_treated_as_empty(self):
        fc = ForkContext(system_prompt="sys")
        msgs = fc.to_messages_for_call(None)  # type: ignore[arg-type]
        assert len(msgs) == 1

    def test_user_messages_deep_copied(self):
        """调用方修改传入的 list 不会污染 ForkContext 返回的消息"""
        fc = ForkContext(system_prompt="sys")
        extras = [{"role": "user", "content": "q"}]
        msgs = fc.to_messages_for_call(extras)
        extras[0]["content"] = "被改了"
        assert msgs[1]["content"] == "q"

    def test_to_messages_with_extra_system(self):
        fc = ForkContext(system_prompt="base")
        msgs = fc.to_messages_for_call(
            [{"role": "user", "content": "q"}],
            extra_system="请简洁",
        )
        assert msgs[0]["content"] == "base\n\n请简洁"
        assert msgs[0]["cache_control"] == {"type": "ephemeral"}


# ========== tool_schemas 预留字段 ==========

class TestToolSchemas:
    def test_tool_schemas_stored(self):
        schemas = [{"name": "lookup", "description": "查询 Skill"}]
        fc = ForkContext(system_prompt="sys", tool_schemas=schemas)
        assert fc.tool_schemas == schemas

    def test_tool_schemas_deep_copied(self):
        schemas = [{"name": "lookup"}]
        fc = ForkContext(system_prompt="sys", tool_schemas=schemas)
        schemas[0]["name"] = "mutated"
        assert fc.tool_schemas[0]["name"] == "lookup"

    def test_tool_schemas_default_none(self):
        fc = ForkContext(system_prompt="sys")
        assert fc.tool_schemas is None


# ========== 与 call_llm_stream 集成（mock） ==========

class TestForkIntegration:
    """验证 ForkContext 产出的 messages 结构可以被 call_llm_stream 直接吃。"""

    def test_fork_messages_are_valid_openai_format(self):
        """每条消息必须有 role + content"""
        fc = ForkContext(
            system_prompt="sys",
            base_messages=[{"role": "user", "content": "ctx"}],
        )
        msgs = fc.fork("hi")
        for m in msgs:
            assert "role" in m
            assert "content" in m
            assert m["role"] in ("system", "user", "assistant")

    def test_multiple_forks_share_same_hash(self):
        """同一个 ForkContext 的多次 fork 共享同一个 system_prompt_hash"""
        fc = ForkContext(system_prompt="共享前缀")
        h1 = fc.system_prompt_hash
        _ = fc.fork("q1")
        _ = fc.fork("q2", extra_system="额外指令")
        h2 = fc.system_prompt_hash
        assert h1 == h2
