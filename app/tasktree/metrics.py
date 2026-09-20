"""任务树监控指标。"""

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


TREE_DURATION = Histogram(
    "tasktree_request_duration_seconds",
    "Task tree API response time",
    ["endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.08, 0.1, 0.25, 0.5, 1.0],
)

CACHE_HIT = Counter("tasktree_cache_hits_total", "Cache hits", ["cache_key"])
CACHE_MISS = Counter("tasktree_cache_misses_total", "Cache misses", ["cache_key"])
ETAG_HIT = Counter("tasktree_etag_hits_total", "ETag 304 responses")
# 活跃 root 数按部门拆分，见 app/common/metrics.py:tasktree_active_roots
NODE_STATE_ERRORS = Counter("tasktree_node_state_errors_total", "Node state mismatch events")
TASKTREE_VIRTUAL_GROUP_HITS = Counter(
    "tasktree_virtual_group_hits_total",
    "Instances routed into tasktree virtual groups",
    ["group"],
)
