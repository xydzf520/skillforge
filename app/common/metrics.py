"""
Prometheus 指标收集。
在 main.py 中调用 setup_metrics(app) 启用。
/metrics 端点仅允许 admin 角色或本地 IP 访问。

依赖严格 — 不再有"未安装就跳过"的降级路径。
prometheus_fastapi_instrumentator / prometheus_client 缺失时, 这个模块 import
就会失败 → main.py import metrics 失败 → server 拒绝启动。
启动自检在 main.py:_verify_required_deps() 会再确认一遍。
"""

from fastapi import Request
from fastapi.responses import PlainTextResponse
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Gauge, Histogram

# ── Task Tree 双写补偿相关全局指标 ─────────────────────────────
# Group A：tasktree writer 解耦 + 补偿机制埋点。
# 命名保持 tasktree_ 前缀，便于 Prometheus 规则/Grafana 面板复用。
TASKTREE_WRITER_FAILURE_TOTAL = Counter(
    "tasktree_writer_failure_total",
    "task_nodes_light 写入失败总数（成功入 repair_queue 才计数）",
    ["source"],
)

TASKTREE_REPAIR_QUEUE_SIZE = Gauge(
    "tasktree_repair_queue_size",
    "当前 tasktree_repair_queue 中 pending + retrying 的记录数",
)

TASKTREE_REPAIR_LAG_SECONDS = Gauge(
    "tasktree_repair_lag_seconds",
    "tasktree_repair_queue 中最老 pending 条目的等待时长（秒）",
)

TASKTREE_DRIFT_COUNT = Gauge(
    "tasktree_drift_count",
    "最近一次 drift_scan 发现的 execution_runs 与 task_nodes_light 的缺口数",
)

# [codex-2026-04-14] dispatcher 死信：writer 失败 + enqueue_repair 也失败 →
# 数据可能永久丢失，只能靠告警/运维介入。此计数器不应该 > 0；长期增长要触发 P0 告警
TASKTREE_DISPATCHER_DEAD_LETTER_TOTAL = Counter(
    "tasktree_dispatcher_dead_letter_total",
    "dispatcher 双写失败（writer + enqueue_repair 都抛错）总数。>0 需 P0 告警",
    ["source"],
)


# ── Task Tree 读路径 / 价值 / 诊断 指标（Group B）─────────────
# get_task_tree 端到端耗时（秒）：用于 p99 SLO 与慢查询告警
tasktree_get_tree_p99_seconds = Histogram(
    "tasktree_get_tree_p99_seconds",
    "task tree get_tree 端到端耗时（秒）",
    buckets=[0.01, 0.025, 0.05, 0.08, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# 慢查询计数（按 operation 标签区分 get_tree / value_estimate / ai_diagnose 等）
tasktree_slow_query_total = Counter(
    "tasktree_slow_query_total",
    "task tree 慢查询累计次数",
    ["operation"],
)

# AI 诊断失败率（0~1，近似滑动窗口）
ai_diagnose_failure_rate = Gauge(
    "ai_diagnose_failure_rate",
    "task tree AI 诊断失败率（0~1，最近一次成功/失败滑窗）",
)

# 活跃根任务数（按部门标签汇总）
tasktree_active_roots = Gauge(
    "tasktree_active_roots",
    "task tree 当前活跃 root 数（按部门）",
    ["department"],
)


def setup_metrics(app):
    """挂载 Prometheus /metrics 端点（需认证或本地访问）。"""
    Instrumentator().instrument(app)

    # 不自动暴露 — 改为手动挂载带认证的端点
    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        # 允许本地 Prometheus 抓取
        client_ip = request.client.host if request.client else ""
        is_local = client_ip in ("127.0.0.1", "::1", "localhost")
        if not is_local:
            # 非本地访问需要 admin session
            from app.auth.dependencies import verify_session_token, SESSION_COOKIE
            token = request.cookies.get(SESSION_COOKIE)
            if not token:
                return PlainTextResponse("Unauthorized", status_code=401)
            # v2: verify_session_token 返回 (uid, rev) 元组
            verify_result = verify_session_token(token)
            if not verify_result:
                return PlainTextResponse("Unauthorized", status_code=401)
            user_id, token_rev = verify_result
            from app.database import async_session_factory
            from app.auth.models import User
            from sqlalchemy import select
            async with async_session_factory() as session:
                result = await session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
            if not user:
                return PlainTextResponse("Unauthorized", status_code=401)
            if getattr(user, "state", None) == "disabled" or not user.is_active:
                return PlainTextResponse("Forbidden", status_code=403)
            if token_rev != int(getattr(user, "permissions_rev", 0) or 0):
                return PlainTextResponse("Unauthorized", status_code=401)
            # v2: metrics 远程访问要求 system_admin（旧 admin 角色通过别名兼容）
            if user.role not in ("system_admin", "admin"):
                return PlainTextResponse("Forbidden", status_code=403)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
