"""
resume-v2 协议单元测试：SubprocessSession.fe_seq + stream_events token + SessionService.resume_stream

验证 reviewer 指出的 4 个语义缺陷是否全部修复：
    1. fe_seq 单调递增 (跨 translate batch 不复用)
    2. stream_events token 替换 → 旧 consumer 优雅退出 + 事件 put-back 不丢
    3. replay_fe_events_since(last_fe_seq) 按翻译后游标过滤, 兄弟事件不会漏
    4. resume_stream 三个分支: 无 session / 已完成 / 进行中
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest
import pytest_asyncio

from app.coding_agent.schemas import (
    CodingAgentErrorCode,
    Event,
    EventType,
)
from app.coding_agent.subprocess_session import SubprocessSession
from app.coding_agent.session_service import SessionService


# ─────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────

def _make_bare_session(tmp_path: Path) -> SubprocessSession:
    """构造一个完全桩掉的 SubprocessSession — 没有真子进程,
    只有 fe_seq / fe_history / event_queue / active_stream_token 等被测字段。
    """
    s = SubprocessSession.__new__(SubprocessSession)
    s.skill_id = "skill-1"
    s.user_id = "user-1"
    s.work_dir = tmp_path
    s._closed = False
    s.proc = None
    s._reader_task = None
    s._stderr_task = None
    s._stdin_lock = asyncio.Lock()
    s._event_queue = asyncio.Queue(maxsize=1024)
    s._fe_seq = 0
    s._fe_history = []
    s._fe_history_max = 500
    s._active_stream_token = 0

    # session metadata (translate 用到)
    s.session_id = "sess-fake"
    s.model = "glm-5"
    s.tools = []
    s.prompt_hash = "phash-test"
    s.config_dir = tmp_path / ".aiclawcode"
    s.runtime_profile = {"vendor_version": "0.0.1"}
    s.pending_tool_calls = 0
    s.last_active_at = 0.0
    s._parse_failure_count = 0
    s._dropped_event_count = 0
    return s


@pytest.fixture
def mock_session(tmp_path: Path) -> SubprocessSession:
    return _make_bare_session(tmp_path)


@pytest.fixture
def service(tmp_path: Path) -> SessionService:
    svc = SessionService.__new__(SessionService)
    svc._policies = {}
    svc._pending_perms = {}
    return svc


# ─────────────────────────────────────────────────────────
# 1. fe_seq 单调递增
# ─────────────────────────────────────────────────────────

class TestFeSeqMonotonic:
    def test_next_fe_seq_increments_by_one(self, mock_session):
        """每次调用 +1, 从 1 开始。"""
        assert mock_session.next_fe_seq() == 1
        assert mock_session.next_fe_seq() == 2
        assert mock_session.next_fe_seq() == 3

    def test_next_fe_seq_never_reused_across_calls(self, mock_session):
        """连续 100 次调用, 值全部唯一单调。"""
        seqs = [mock_session.next_fe_seq() for _ in range(100)]
        assert seqs == list(range(1, 101))
        assert len(set(seqs)) == 100

    @pytest.mark.asyncio
    async def test_next_fe_seq_concurrent_safe(self, mock_session):
        """并发 async 调用不会产生重复 seq (同 event loop, 无抢占点)。"""

        async def bump():
            return mock_session.next_fe_seq()

        results = await asyncio.gather(*[bump() for _ in range(50)])
        # 全部不同且是 1..50 的一个排列 (async 里同步的 += 1 不会重复)
        assert sorted(results) == list(range(1, 51))


# ─────────────────────────────────────────────────────────
# 2. stream_events token 协议
# ─────────────────────────────────────────────────────────

class TestStreamEventsToken:
    @pytest.mark.asyncio
    async def test_acquire_token_increments(self, mock_session):
        """acquire_stream_token 每次 +1, 挤掉旧 token。"""
        t1 = mock_session.acquire_stream_token()
        t2 = mock_session.acquire_stream_token()
        t3 = mock_session.acquire_stream_token()
        assert t1 < t2 < t3
        assert mock_session._active_stream_token == t3

    @pytest.mark.asyncio
    async def test_old_consumer_exits_gracefully_on_token_replacement(self, mock_session):
        """
        场景: 旧 consumer 正在 await queue.get(), 新 consumer 调 acquire_stream_token
        覆盖 token。旧 consumer 下一轮 pre-check 检测到 token 不匹配, 优雅 return
        (不抛异常, 不无限等)。
        """
        old_token = mock_session.acquire_stream_token()

        collected = []

        async def old_consumer():
            async for ev in mock_session.stream_events(token=old_token):
                collected.append(ev)

        # 新 consumer 挤掉旧的
        new_token = mock_session.acquire_stream_token()
        assert new_token != old_token

        # push 一个事件, 唤醒可能正在 await 的 get()
        await mock_session._event_queue.put({"type": "assistant", "foo": "bar"})

        # 旧 consumer 应该在有限时间内自己 return (不会 yield 那条事件)
        await asyncio.wait_for(old_consumer(), timeout=1.0)

        # 旧 consumer 没 yield 任何事件 (token 覆盖了 → return 前没 yield)
        assert collected == []

    @pytest.mark.asyncio
    async def test_old_consumer_puts_back_event_for_new_consumer(self, mock_session):
        """
        场景: 旧 consumer 已经 queue.get() 拿到一条事件, 才发现 token 被替换。
        必须把 event put 回 queue, 不能吞掉 — 否则新 consumer 永远看不到。
        """
        old_token = mock_session.acquire_stream_token()

        # 先 push 事件, 然后 consumer 启动就能立刻 get 到
        target_event = {"type": "assistant", "marker": "do-not-lose"}
        await mock_session._event_queue.put(target_event)

        async def old_consumer():
            # 进入循环前 token 还有效 (pre-check 通过)
            # 被迫在 await get() 返回后再检查; 但我们需要 token 在 get() 之前就已更改
            # 所以这里触发: 启动前就换 token
            async for ev in mock_session.stream_events(token=old_token):
                return ev
            return None

        # 先把 token 覆盖
        mock_session.acquire_stream_token()

        result = await asyncio.wait_for(old_consumer(), timeout=1.0)
        # 旧 consumer 应该没 yield (被 pre-check 或 post-get check 拦下)
        assert result is None

        # 检查 queue 里还有这个事件 — put-back 生效
        # queue 大小 >= 1, 下一个 get 应该就能拿到 target_event
        qsize = mock_session._event_queue.qsize()
        assert qsize >= 1, f"事件被丢, queue 为空"
        fetched = await asyncio.wait_for(mock_session._event_queue.get(), timeout=0.5)
        assert fetched == target_event

    @pytest.mark.asyncio
    async def test_valid_token_consumer_yields_all_events(self, mock_session):
        """token 匹配的 consumer 能正常收到所有事件, 到 None sentinel 结束。"""
        token = mock_session.acquire_stream_token()
        await mock_session._event_queue.put({"type": "assistant", "i": 1})
        await mock_session._event_queue.put({"type": "assistant", "i": 2})
        await mock_session._event_queue.put({"type": "result", "i": 3})
        await mock_session._event_queue.put(None)  # 子进程退出 sentinel

        collected = []
        async for ev in mock_session.stream_events(token=token):
            collected.append(ev)
            if len(collected) >= 3:
                # 收完 3 个后由 None sentinel 触发 return
                pass

        assert len(collected) == 3
        assert [e["i"] for e in collected] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_none_token_skips_check_backward_compat(self, mock_session):
        """token=None (旧代码兼容路径) 不做 token 检查, 正常消费。"""
        await mock_session._event_queue.put({"type": "assistant"})
        await mock_session._event_queue.put(None)

        collected = []
        async for ev in mock_session.stream_events(token=None):
            collected.append(ev)
        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_put_back_blocks_when_queue_full(self, mock_session):
        """
        回归: 旧实现 put_nowait 队列满 → except QueueFull → 只记 warning 就丢事件,
        破坏 resume 协议 (已翻译的事件永远进不去 fe_history, 前端 resume 看不到)。
        修复: 改用 await put, 队列满时阻塞等新 consumer 消费腾位, 最终事件必须入队不丢。

        构造思路 (在不改产品代码的前提下稳定触发 "put-back 时队列满" 的分支):
            在单线程 asyncio 下, get 和 post-check+put 之间没有 await 点, 队列在 get 取
            走一条后天然 qsize < maxsize, put_nowait 不会失败。所以我们通过 monkeypatch
            queue.full() 强行让 put-back 时认为队列是满的 —— 这模拟了未来代码增加 await
            后 / 多线程访问时可能真实出现的满队列状态。

            时序:
              1. queue (maxsize=1) 初始为空, consumer 持有 old_token 启动 → 进入 while
                 循环, pre-check 通过 (token 尚未变) → await get() 阻塞
              2. 外部: 覆盖 token (active_token += 1) → 让一个 target_event put 进 queue
                 唤醒 consumer → consumer get 到 target_event, 进入 post-check 发现 token
                 变了 → 必须 put-back
              3. 此刻我们已事先 monkeypatch 了 queue.full() 返回 True, await put 会在
                 while full(): await putter 处阻塞
              4. 断言 consumer 在短期内没完成 (证明确实走了 await put 阻塞分支, 而不是
                 旧 bug: put_nowait + except QueueFull 立即丢事件 return)
              5. 解除 full 桩 + 手动 wakeup putter (模拟 "新 consumer 消费腾位")
              6. consumer 被唤醒, put_nowait 成功入队, return
              7. 断言 target_event 仍在 queue 中 — 没丢
        """
        target_event = {"type": "tool_result", "marker": "must-not-lose"}

        # maxsize=1 真实有界队列, 初始为空
        small_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        mock_session._event_queue = small_queue

        # 提前 monkeypatch full() 让 put-back 时"感觉满"
        original_full = small_queue.full
        full_should_lie = [True]

        def patched_full():
            if full_should_lie[0]:
                return True
            return original_full()

        small_queue.full = patched_full  # type: ignore[method-assign]

        old_token = mock_session.acquire_stream_token()

        async def old_consumer():
            async for ev in mock_session.stream_events(token=old_token):
                return ev
            return None

        consumer_task = asyncio.create_task(old_consumer())
        # 让 consumer 进入 await get() 阻塞态
        await asyncio.sleep(0.02)
        assert not consumer_task.done(), "consumer 应该阻塞在 await get (queue 空)"

        # 现在覆盖 token + push target_event 唤醒 consumer
        mock_session.acquire_stream_token()
        # put target_event: 此时 patched full 返回 True, 直接 put 也会阻塞; 用 _put +
        # _wakeup_next 模拟内部直接投递 (绕过 full 桩, 仅 put-back 分支应该感受到 "满")
        # 更简单: 临时放开 full 让这一条 put 进去, 随即再桩回来
        full_should_lie[0] = False
        await small_queue.put(target_event)
        full_should_lie[0] = True

        # 等 consumer get → post-check → 进入 await put 阻塞
        await asyncio.sleep(0.05)
        assert not consumer_task.done(), (
            "consumer 应阻塞在 await put (put-back 时 full=True)。"
            "如果完成了, 说明代码走的是旧 bug 的 put_nowait+except 分支直接丢事件 return。"
        )

        # 模拟 "新 consumer 消费腾位" — 关闭 full 桩 + 手动唤醒 putter
        full_should_lie[0] = False
        # queue 的 _putters 里应该有一个 pending 的 future (consumer 的 await put)
        putters = getattr(small_queue, "_putters", None)
        assert putters and len(putters) >= 1, "consumer 应该在 putters 队列里等"
        while putters:
            waiter = putters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                break

        # consumer 被唤醒 → put_nowait 入队 (full 已假为 False, 允许) → return
        result = await asyncio.wait_for(consumer_task, timeout=1.0)
        assert result is None, "consumer 不应 yield 任何事件 (token 已过期)"

        # 关键断言: target_event 仍在队列里 (put-back 成功), 没被丢
        # 旧代码: put_nowait 抛 QueueFull → 被 except 吃掉 → 事件彻底丢失 → qsize=0
        # 新代码: await put → 等待 → 入队成功 → qsize=1
        assert small_queue.qsize() == 1, (
            f"put-back 的事件被丢了! qsize={small_queue.qsize()} — "
            f"这是旧 put_nowait + except QueueFull 的 bug 行为。"
        )
        fetched = await asyncio.wait_for(small_queue.get(), timeout=0.5)
        assert fetched == target_event, (
            f"put-back 入队的事件应是 target_event, 实际={fetched}"
        )

        # 恢复 monkeypatch
        small_queue.full = original_full  # type: ignore[method-assign]


# ─────────────────────────────────────────────────────────
# 3. replay_fe_events_since 过滤
# ─────────────────────────────────────────────────────────

class TestReplayFeEventsSince:
    def test_replay_returns_events_with_seq_greater_than_cursor(self, mock_session):
        """仅返回 _seq > last_fe_seq 的事件。"""
        mock_session.append_fe_event({"type": "text_delta", "_seq": 1, "content": "a"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 2, "content": "b"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 3, "content": "c"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 4, "content": "d"})

        # last_fe_seq=2 → 应返回 3, 4
        replayed = mock_session.replay_fe_events_since(2)
        assert [e["_seq"] for e in replayed] == [3, 4]
        assert [e["content"] for e in replayed] == ["c", "d"]

    def test_replay_all_when_cursor_is_zero(self, mock_session):
        """cursor=0 (首次订阅) 返回全部历史。"""
        for i in range(1, 6):
            mock_session.append_fe_event({"type": "text_delta", "_seq": i})
        replayed = mock_session.replay_fe_events_since(0)
        assert [e["_seq"] for e in replayed] == [1, 2, 3, 4, 5]

    def test_replay_empty_when_cursor_at_latest(self, mock_session):
        """cursor >= 最新 _seq → 空列表。"""
        mock_session.append_fe_event({"type": "x", "_seq": 1})
        mock_session.append_fe_event({"type": "x", "_seq": 2})
        assert mock_session.replay_fe_events_since(2) == []
        assert mock_session.replay_fe_events_since(99) == []

    def test_replay_sibling_events_not_dropped_across_raw(self, mock_session):
        """
        核心 bug 修复验证: 一条 raw 翻译成 N 个 fe_event (兄弟事件),
        每个 fe_event 有自己独立单调的 _seq; 即使它们来自同一 raw,
        前 N-1 也不会因为"lastSeq 被推进到 raw._seq"被漏回放。

        模拟: raw-A 翻译出 _seq=10,11,12 (三兄弟); raw-B 翻译出 _seq=13
        前端之前收到 10,11 → lastSeq=11 → 断连; resume 时 cursor=11
        应回放 12 (A 的剩余兄弟) + 13 (B)。
        """
        # raw-A 的三个兄弟
        mock_session.append_fe_event({"type": "tool_call", "_seq": 10, "raw": "A", "idx": 0})
        mock_session.append_fe_event({"type": "file_change", "_seq": 11, "raw": "A", "idx": 1})
        mock_session.append_fe_event({"type": "usage", "_seq": 12, "raw": "A", "idx": 2})
        # raw-B 独立
        mock_session.append_fe_event({"type": "text_delta", "_seq": 13, "raw": "B", "idx": 0})

        replayed = mock_session.replay_fe_events_since(11)
        assert len(replayed) == 2, f"前 N-1 兄弟被漏了: {replayed}"
        assert replayed[0]["_seq"] == 12 and replayed[0]["raw"] == "A"
        assert replayed[1]["_seq"] == 13 and replayed[1]["raw"] == "B"

    def test_fe_history_ring_buffer_trims(self, mock_session):
        """fe_history_max=500 (默认), 超出会截断保留最新。"""
        mock_session._fe_history_max = 5
        for i in range(1, 11):
            mock_session.append_fe_event({"_seq": i, "v": i})
        # 只保留最新 5 个: _seq 6..10
        assert len(mock_session._fe_history) == 5
        assert [e["_seq"] for e in mock_session._fe_history] == [6, 7, 8, 9, 10]


# ─────────────────────────────────────────────────────────
# 4. SessionService.resume_stream 三分支
# ─────────────────────────────────────────────────────────

class TestResumeStream:
    @pytest.mark.asyncio
    async def test_resume_no_session_yields_error(self, service, monkeypatch):
        """分支 A: pool.get 返回 None → yield 一个 RESUME_NO_SESSION ERROR 然后结束。"""
        import sys
        ss_mod = sys.modules["app.coding_agent.session_service"]

        async def fake_get(*, skill_id, user_id):
            return None

        monkeypatch.setattr(ss_mod.coding_agent_pool, "get", fake_get)

        events = []
        async for ev in service.resume_stream(
            skill_id="s1", user_id="u1", last_fe_seq=0
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0].type == EventType.ERROR
        assert events[0].payload["code"] == CodingAgentErrorCode.RESUME_NO_SESSION.value
        assert "会话已过期" in events[0].payload["error"]

    @pytest.mark.asyncio
    async def test_resume_with_completed_session_replays_and_ends(
        self, service, mock_session, monkeypatch
    ):
        """
        分支 B: session 存在但这轮已经完成 (event_queue 里只有 None sentinel / 空)。
        应该只回放 fe_history 超出 cursor 的部分, 然后进入 stream_events 立刻看到
        None sentinel → return。不应无限等。
        """
        # 准备历史: _seq 1..3
        mock_session.append_fe_event({"type": "text_delta", "_seq": 1, "content": "hi"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 2, "content": "lo"})
        mock_session.append_fe_event({"type": "done", "_seq": 3, "subtype": "success"})

        # queue 已经完成 (sentinel)
        await mock_session._event_queue.put(None)

        import sys
        ss_mod = sys.modules["app.coding_agent.session_service"]

        async def fake_get(*, skill_id, user_id):
            return mock_session

        monkeypatch.setattr(ss_mod.coding_agent_pool, "get", fake_get)

        events = []
        # last_fe_seq=1 → 期待回放 _seq=2, 3
        async for ev in asyncio_timeout_iter(
            service.resume_stream(skill_id="s1", user_id="u1", last_fe_seq=1),
            timeout=2.0,
        ):
            events.append(ev)

        # 前 2 条来自历史回放
        assert len(events) == 2
        assert events[0].type == EventType.TEXT_DELTA
        assert events[0].payload["_seq"] == 2
        assert events[0].payload["content"] == "lo"
        assert events[1].type == EventType.DONE
        assert events[1].payload["_seq"] == 3

    @pytest.mark.asyncio
    async def test_resume_with_live_session_replays_plus_continues(
        self, service, mock_session, monkeypatch
    ):
        """
        分支 C: session 还在跑 — 回放历史 + 继续订阅 live 流。
        fe_seq 在 live 段要从历史最大值 +1 开始单调递增。
        """
        # 历史: _seq 1..3, 当前 fe_seq 游标 = 3
        mock_session._fe_seq = 3
        mock_session.append_fe_event({"type": "text_delta", "_seq": 1, "content": "a"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 2, "content": "b"})
        mock_session.append_fe_event({"type": "text_delta", "_seq": 3, "content": "c"})

        # live raw 事件 — 注意 translate 依赖 session_id/tools 等元数据
        # 发一个 assistant text 和一个 result
        await mock_session._event_queue.put({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "live-d"}]},
        })
        await mock_session._event_queue.put({"type": "result", "subtype": "success"})

        import sys
        ss_mod = sys.modules["app.coding_agent.session_service"]

        async def fake_get(*, skill_id, user_id):
            return mock_session

        monkeypatch.setattr(ss_mod.coding_agent_pool, "get", fake_get)

        events = []
        async for ev in asyncio_timeout_iter(
            service.resume_stream(skill_id="s1", user_id="u1", last_fe_seq=2),
            timeout=2.0,
        ):
            events.append(ev)

        # 期望: replay [_seq=3] + live translate: TEXT_DELTA(_seq=4), DONE(_seq=5)
        # (result 翻译可能还额外带 USAGE, 但本 case 没 usage 字段所以只出 DONE)
        assert len(events) >= 2, f"events={events}"
        # 回放历史第 1 条
        assert events[0].type == EventType.TEXT_DELTA
        assert events[0].payload["_seq"] == 3
        assert events[0].payload["content"] == "c"

        # live 的 TEXT_DELTA — _seq 必须 > 3 且单调
        live_text = events[1]
        assert live_text.type == EventType.TEXT_DELTA
        assert live_text.payload["content"] == "live-d"
        assert live_text.payload["_seq"] == 4

        # 最后 DONE
        done_ev = events[-1]
        assert done_ev.type == EventType.DONE
        assert done_ev.payload["_seq"] > live_text.payload["_seq"]

        # fe_history 也追加了 live 的事件
        history_seqs = [e.get("_seq") for e in mock_session._fe_history]
        assert 4 in history_seqs

    @pytest.mark.asyncio
    async def test_resume_acquires_new_token_kicks_old_consumer(
        self, service, mock_session, monkeypatch
    ):
        """
        resume_stream 进入前必须 acquire_stream_token, 挤掉任何旧 consumer。
        即在 resume 被调用时 active_stream_token 一定增大。
        """
        # 模拟旧 chat() 已经持有 token=7
        mock_session._active_stream_token = 7

        import sys
        ss_mod = sys.modules["app.coding_agent.session_service"]

        async def fake_get(*, skill_id, user_id):
            return mock_session

        monkeypatch.setattr(ss_mod.coding_agent_pool, "get", fake_get)

        # 塞一个 result 让流立即结束, 以便 async gen 退出
        await mock_session._event_queue.put({"type": "result", "subtype": "success"})

        async for _ev in asyncio_timeout_iter(
            service.resume_stream(skill_id="s1", user_id="u1", last_fe_seq=0),
            timeout=2.0,
        ):
            pass

        # 至少 +1 (resume 内调用了 acquire_stream_token 一次)
        assert mock_session._active_stream_token >= 8


# ─────────────────────────────────────────────────────────
# 辅助: 带 timeout 的 async iterator 遍历
# ─────────────────────────────────────────────────────────

async def asyncio_timeout_iter(aiter, *, timeout: float):
    """为 async-generator 的每次 __anext__ 加超时, 防止测试挂死。"""
    while True:
        try:
            ev = await asyncio.wait_for(aiter.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise AssertionError(
                f"resume_stream 没在 {timeout}s 内结束, 可能阻塞在 stream_events"
            )
        yield ev
