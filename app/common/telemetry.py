"""SkillForge KPI 埋点 — master plan §3.7 / §11.1 成功指标。

依赖严格 — prometheus_client 是必装的, 不再有"未安装降级到内存字典"路径。
内存字典只用于测试隔离 (reset_memory_metrics) 而不是生产降级。

测量的 KPI:
  - ttfr_seconds              §3.7 从描述到第一版可运行 Skill 的时长
  - first_pass_quality_total  §3.7 首版通过率（通过 / 总数）
  - draft_adoption_total      §3.7 Draft 采纳率（无大改 / 总数）
  - coach_suggestion_accepted §11 建议采纳率
  - guardian_mttd_seconds     §11 异常发现 → 访问的时差
  - publish_blocked_total     §11 发布被门禁阻断次数
"""

from __future__ import annotations
import time
import threading
from contextlib import contextmanager
from loguru import logger

# Prometheus 对象 — 必装依赖, 缺失时直接 ImportError 让 server 起不来
from prometheus_client import Counter, Histogram


# ═══════════════════════════════════════════════════════
# 内存后备存储（测试场景 + 降级）
# ═══════════════════════════════════════════════════════

_mem_lock = threading.Lock()
_mem_counters: dict[str, float] = {}
_mem_histograms: dict[str, list[float]] = {}


def _mem_inc(key: str, value: float = 1.0) -> None:
    with _mem_lock:
        _mem_counters[key] = _mem_counters.get(key, 0.0) + value


def _mem_observe(key: str, value: float) -> None:
    with _mem_lock:
        _mem_histograms.setdefault(key, []).append(value)


def get_memory_snapshot() -> dict:
    """返回内存 metrics 快照（测试/调试用）。"""
    with _mem_lock:
        return {
            "counters": dict(_mem_counters),
            "histograms": {k: list(v) for k, v in _mem_histograms.items()},
        }


def reset_memory_metrics() -> None:
    """清空内存 metrics（测试隔离用）。"""
    with _mem_lock:
        _mem_counters.clear()
        _mem_histograms.clear()


# ═══════════════════════════════════════════════════════
# Prometheus 指标定义（懒初始化）
# ═══════════════════════════════════════════════════════

_prom_metrics: dict = {}


def _get_counter(name: str, desc: str, labelnames: tuple = ()) -> object:
    if name not in _prom_metrics:
        try:
            _prom_metrics[name] = Counter(name, desc, labelnames=labelnames)
        except ValueError:
            # 重复注册（测试重入）— 从默认 registry 找回
            from prometheus_client import REGISTRY
            for coll in list(REGISTRY._collector_to_names.keys()):
                if getattr(coll, "_name", None) == name:
                    _prom_metrics[name] = coll
                    break
    return _prom_metrics[name]


def _get_histogram(name: str, desc: str, buckets: tuple = ()) -> object:
    if name not in _prom_metrics:
        try:
            kwargs = {"buckets": buckets} if buckets else {}
            _prom_metrics[name] = Histogram(name, desc, **kwargs)
        except ValueError:
            from prometheus_client import REGISTRY
            for coll in list(REGISTRY._collector_to_names.keys()):
                if getattr(coll, "_name", None) == name:
                    _prom_metrics[name] = coll
                    break
    return _prom_metrics[name]


# ═══════════════════════════════════════════════════════
# 公开 API — 业务代码调用这些
# ═══════════════════════════════════════════════════════


def record_ttfr(seconds: float, source: str = "unknown") -> None:
    """§3.7 TTFR：从用户输入业务描述 → 第一版 Skill 可运行的耗时。

    Args:
        seconds: 秒数
        source: 生成路径 (architect / swarm / direct / manual)
    """
    h = _get_histogram(
        "skillforge_ttfr_seconds",
        "Time from user description to first runnable Skill",
        buckets=(10, 30, 60, 120, 300, 600, 1800, 3600),
    )
    if h is not None:
        try:
            h.observe(seconds)
        except Exception as e:
            logger.debug(f"ttfr prom observe 失败: {e}")
    _mem_observe(f"ttfr_seconds:{source}", seconds)


def record_first_pass_quality(passed: bool, source: str = "unknown") -> None:
    """§3.7 首版通过率：创建的第一版 Skill 是否通过质检门禁。"""
    c = _get_counter(
        "skillforge_first_pass_quality_total",
        "First-pass quality gate result counter",
        labelnames=("result", "source"),
    )
    if c is not None:
        try:
            c.labels(result="pass" if passed else "fail", source=source).inc()
        except Exception as e:
            logger.debug(f"first_pass_quality prom inc 失败: {e}")
    _mem_inc(f"first_pass_quality:{'pass' if passed else 'fail'}:{source}")


def record_creation_step(step: str, source: str = "unknown") -> None:
    """v2.8.2 D4：Skill 创建漏斗的某一步发生了。

    step: describe / interview / synthesize / preview / commit / abort
    source: architect / swarm / fork / import / manual / report

    用 `/api/dashboard/skill-creation-funnel` 可以拿到各 step 的累计计数，
    算流失率（describe → commit 的转化率）。
    """
    c = _get_counter(
        "skillforge_creation_step_total",
        "Skill creation funnel step counter",
        labelnames=("step", "source"),
    )
    if c is not None:
        try:
            c.labels(step=step, source=source).inc()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"creation_step prom inc 失败: {e}")
    _mem_inc(f"creation_step:{step}:{source}")


def record_draft_adoption(adopted: bool, source: str = "unknown") -> None:
    """§3.7 Draft 采纳率：AI 生成的 draft 是否被直接采纳（无大改）。"""
    c = _get_counter(
        "skillforge_draft_adoption_total",
        "Draft adoption counter",
        labelnames=("adopted", "source"),
    )
    if c is not None:
        try:
            c.labels(adopted="yes" if adopted else "no", source=source).inc()
        except Exception as e:
            logger.debug(f"draft_adoption prom inc 失败: {e}")
    _mem_inc(f"draft_adoption:{'yes' if adopted else 'no'}:{source}")


def record_coach_suggestion(accepted: bool, action: str = "unknown") -> None:
    """§11 建议采纳率：Coach 主动建议是否被用户接受。"""
    c = _get_counter(
        "skillforge_coach_suggestion_total",
        "Coach suggestion outcome",
        labelnames=("accepted", "action"),
    )
    if c is not None:
        try:
            c.labels(accepted="yes" if accepted else "no", action=action).inc()
        except Exception as e:
            logger.debug(f"coach_suggestion prom inc 失败: {e}")
    _mem_inc(f"coach_suggestion:{'yes' if accepted else 'no'}:{action}")


def record_guardian_mttd(seconds: float) -> None:
    """§11 MTTD：Guardian 检测到异常 → 用户首次查看的时差。"""
    h = _get_histogram(
        "skillforge_guardian_mttd_seconds",
        "Time from anomaly detection to user acknowledgment",
        buckets=(60, 300, 600, 1800, 3600, 7200, 14400, 86400),
    )
    if h is not None:
        try:
            h.observe(seconds)
        except Exception as e:
            logger.debug(f"guardian_mttd prom observe 失败: {e}")
    _mem_observe("guardian_mttd_seconds", seconds)


def record_publish_blocked(reason: str = "quality_gate") -> None:
    """§11 发布被质检门禁阻断次数。"""
    c = _get_counter(
        "skillforge_publish_blocked_total",
        "Publish blocked by quality gate",
        labelnames=("reason",),
    )
    if c is not None:
        try:
            c.labels(reason=reason).inc()
        except Exception as e:
            logger.debug(f"publish_blocked prom inc 失败: {e}")
    _mem_inc(f"publish_blocked:{reason}")


def record_rollback(skill_id: str) -> None:
    """§11 线上回滚率：已发布 Skill 被回滚的次数。"""
    c = _get_counter(
        "skillforge_rollback_total",
        "Published skill rollbacks",
    )
    if c is not None:
        try:
            c.inc()
        except Exception as e:
            logger.debug(f"rollback prom inc 失败: {e}")
    _mem_inc(f"rollback:{skill_id}")


def record_edit_duration(seconds: float, skill_id: str = "unknown") -> None:
    """§4.8 编辑完成时长：从打开 Skill 到保存的耗时。"""
    h = _get_histogram(
        "skillforge_edit_duration_seconds",
        "Time from Skill opened to save",
        buckets=(30, 60, 180, 300, 600, 1800, 3600, 7200),
    )
    if h is not None:
        try:
            h.observe(seconds)
        except Exception as e:
            logger.debug(f"edit_duration prom observe 失败: {e}")
    _mem_observe(f"edit_duration:{skill_id}", seconds)


def record_coverage_delta(old_coverage: float, new_coverage: float, skill_id: str = "unknown") -> None:
    """§4.8 覆盖率提升：编辑前后分支覆盖率变化。"""
    delta = new_coverage - old_coverage
    h = _get_histogram(
        "skillforge_coverage_delta",
        "Branch coverage improvement from edit session",
        buckets=(-0.5, -0.1, 0, 0.05, 0.1, 0.2, 0.3, 0.5),
    )
    if h is not None:
        try:
            h.observe(delta)
        except Exception as e:
            logger.debug(f"coverage_delta prom observe 失败: {e}")
    _mem_observe(f"coverage_delta:{skill_id}", delta)


def record_repeated_edit(location: str, revert_count: int) -> None:
    """§4.8 同一问题反复修改次数。"""
    c = _get_counter(
        "skillforge_repeated_edit_total",
        "Repeated edits at same location",
        labelnames=("location_type",),
    )
    if c is not None:
        loc_type = location.split(":")[0] if ":" in location else "unknown"
        try:
            c.labels(location_type=loc_type).inc(revert_count)
        except Exception as e:
            logger.debug(f"repeated_edit prom inc 失败: {e}")
    _mem_inc(f"repeated_edit:{location}", revert_count)


def record_review_decision_time(seconds: float, decision: str = "unknown") -> None:
    """§4.8/§11 审批决策时间：提交 → 批准/驳回的耗时。"""
    h = _get_histogram(
        "skillforge_review_decision_seconds",
        "Time from review submission to approve/reject",
        buckets=(300, 1800, 3600, 7200, 21600, 86400, 259200),
    )
    if h is not None:
        try:
            h.observe(seconds)
        except Exception as e:
            logger.debug(f"review_decision prom observe 失败: {e}")
    _mem_observe(f"review_decision:{decision}", seconds)


@contextmanager
def time_span(metric_fn, *args, **kwargs):
    """上下文管理器：测量一段代码的执行时间并上报。

    用法:
        with time_span(record_ttfr, source="swarm"):
            await run_swarm(...)
    """
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        try:
            metric_fn(elapsed, *args, **kwargs)
        except Exception as e:
            logger.debug(f"time_span 上报失败: {e}")
