"""AIClaw bridge Prometheus 指标。

测试或精简环境中如果 `prometheus_client` 不可用，则自动降级为 no-op。
"""

try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:  # pragma: no cover
    class _NoopMetric:
        def labels(self, **kwargs):
            return self

        def inc(self, amount=1):
            return None

        def set(self, value):
            return None

        def observe(self, value):
            return None

    def Counter(*args, **kwargs):  # type: ignore
        return _NoopMetric()

    def Gauge(*args, **kwargs):  # type: ignore
        return _NoopMetric()

    def Histogram(*args, **kwargs):  # type: ignore
        return _NoopMetric()


bridge_reject_total = Counter(
    "bridge_reject_total",
    "AIClaw bridge 请求被拒次数",
    labelnames=["instance_id", "reason"],
)

bridge_chat_queue_depth = Gauge(
    "bridge_chat_queue_depth",
    "AIClaw bridge chat queue 当前深度",
    labelnames=["instance_id", "run_id"],
)

bridge_active_runs = Gauge(
    "bridge_active_runs",
    "AIClaw bridge 实例当前 active chat runs",
    labelnames=["instance_id"],
)

dingtalk_callback_latency = Histogram(
    "dingtalk_callback_latency_seconds",
    "钉钉回调端到端延迟",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

bridge_online = Gauge(
    "bridge_online",
    "AIClaw bridge 在线状态",
    labelnames=["instance_id"],
)

bridge_epoch_swap_total = Counter(
    "bridge_epoch_swap_total",
    "AIClaw bridge 重连切换次数",
    labelnames=["instance_id"],
)

chat_queue_overflow_total = Counter(
    "chat_queue_overflow_total",
    "AIClaw chat queue 溢出次数",
    labelnames=["instance_id"],
)
