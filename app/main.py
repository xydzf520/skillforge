"""
SkillForge 应用入口：FastAPI + Vue前端。
- /api/* → FastAPI后端API
- /*     → Vue 3 + Arco Design Vue 前端（SPA）
"""

import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from fastapi.exceptions import RequestValidationError

from app.common.exceptions import (
    AppError, app_error_handler, generic_exception_handler, validation_exception_handler,
)
from app.config import settings
from app.bootstrap.runtime_init import shutdown_runtime, startup_runtime

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_vue_dist_path(configured_path: str, project_root: Path) -> tuple[Path, str]:
    """Resolve the SPA build without allowing a release to serve another checkout.

    A release directory is immutable and must serve the ``web/dist`` built from
    that same release.  A stale absolute ``VUE_DIST_PATH`` previously made a new
    backend silently serve an older shared frontend for every user.
    """
    local_dist = project_root / "web" / "dist"
    if project_root.parent.name == "skillforge-releases":
        return local_dist, "release_local"
    configured = str(configured_path or "").strip()
    if configured:
        return Path(configured).expanduser(), "configured"
    return local_dist, "project_local"


def frontend_entry_asset(dist_path: Path) -> str | None:
    index_path = dist_path / "index.html"
    try:
        content = index_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?:src|href)=["\']/assets/([^"\']+)', content)
    return match.group(1) if match else None


VUE_DIST, VUE_DIST_SOURCE = resolve_vue_dist_path(settings.VUE_DIST_PATH, _PROJECT_ROOT)
ACTIVE_RELEASE_ID = (
    _PROJECT_ROOT.name
    if _PROJECT_ROOT.parent.name == "skillforge-releases"
    else str(os.environ.get("SKILLFORGE_RELEASE_SHA") or "").strip() or None
)
FRONTEND_ENTRY_ASSET = frontend_entry_asset(VUE_DIST)
if VUE_DIST_SOURCE == "release_local" and settings.VUE_DIST_PATH.strip():
    configured_dist = Path(settings.VUE_DIST_PATH.strip()).expanduser()
    if configured_dist != VUE_DIST:
        logger.warning(
            "忽略跨版本 VUE_DIST_PATH={}，当前 release 固定使用 {}",
            configured_dist,
            VUE_DIST,
        )


class SPAStaticFiles(StaticFiles):
    """SPA静态文件服务：未匹配的路径返回index.html，由Vue Router接管。

    缓存策略（修复浏览器缓存导致的"前端代码不更新"问题）：
    - index.html: no-cache + must-revalidate（每次请求都拉最新）
    - hash 化的 assets/*.js / *.css: 长缓存（Vite 已通过 hash 实现 cache busting）
    """

    async def get_response(self, path, scope):
        normalized_path = self._normalize_cache_path(path)
        # Unmatched API requests must never serve the SPA or stale static files.
        if normalized_path == "api" or normalized_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                if not self._should_serve_spa_fallback(normalized_path):
                    raise
                response = await super().get_response("index.html", scope)
                self._apply_no_cache(response)
                return response
            raise

        self._apply_cache_headers(response, normalized_path)
        return response

    @staticmethod
    def _normalize_cache_path(path: str | None) -> str:
        clean = str(path or "").split("?", 1)[0].lstrip("/")
        if clean in {"", ".", "/"}:
            return ""
        return clean

    @classmethod
    def _should_serve_spa_fallback(cls, normalized_path: str) -> bool:
        filename = normalized_path.rsplit("/", 1)[-1]
        return not (normalized_path.startswith("assets/") or "." in filename)

    @classmethod
    def _apply_cache_headers(cls, response, normalized_path: str) -> None:
        if cls._is_index_path(normalized_path):
            cls._apply_no_cache(response)
        elif cls._is_immutable_asset_path(normalized_path):
            cls._apply_immutable_cache(response)

    @staticmethod
    def _is_index_path(normalized_path: str) -> bool:
        return normalized_path in {"", "index.html"}

    @staticmethod
    def _is_immutable_asset_path(normalized_path: str) -> bool:
        filename = normalized_path.rsplit("/", 1)[-1]
        return normalized_path.startswith("assets/") and "." in filename

    @staticmethod
    def _apply_no_cache(response):
        """给响应加 no-cache 头，避免浏览器缓存旧版 index.html"""
        try:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        except Exception as e:
            logger.debug("设置 no-cache 响应头失败: {}", e)

    @staticmethod
    def _apply_immutable_cache(response):
        """给 Vite hash 产物加长缓存；文件名变更负责 cache busting。"""
        try:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        except Exception as e:
            logger.debug("设置 immutable 响应头失败: {}", e)


async def _warmup_value_service_columns() -> None:
    """lifespan 启动期跑一次列存在性探测，避免请求 path 高频 inspect(pg_attribute)。"""
    try:
        from app.database import async_session_factory
        from app.tasktree.value_service import warmup_value_column_flags

        async with async_session_factory() as session:
            await warmup_value_column_flags(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("value_service 列预热异常（请求 path 会走动态探测降级）: {}", exc)


async def _warmup_metric_dept_whitelist() -> None:
    """[M3] 启动期加载部门白名单，限制 tasktree_active_roots label 基数。"""
    try:
        from app.tasktree.service import warmup_metric_dept_whitelist

        await warmup_metric_dept_whitelist()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tasktree metric dept 白名单预热异常（降级放行）: {}", exc)


async def _verify_role_matrix_schema() -> None:
    """role-matrix-v2 必需列（users.state / users.permissions_rev）启动时校验。

    缺失即 fail fast，避免请求路径隐式 ALTER TABLE 掩盖"忘跑迁移"的运维故障。
    """
    from app.database import async_session_factory
    from app.users.role_matrix import verify_role_matrix_schema

    async with async_session_factory() as session:
        await verify_role_matrix_schema(session)


async def _reconcile_stale_skill_drafts() -> None:
    """启动时把上次进程遗留的 running/pending draft 标成 failed。

    uvicorn --reload 重启后,内存 CreationTaskManager 是空的,但 DB 里
    generation_status='running'/'pending' 的 draft 没人重置 →
    前端 /my-active-draft 返回僵尸,用户一直看到"AI 合成中,请稍候..."。
    """
    from sqlalchemy import update

    from app.database import async_session_factory
    from app.workbench.models import SkillStudioDraft

    now_naive = now_bjt()  # 表 updated_at 是 naive TIMESTAMP
    try:
        async with async_session_factory() as session:
            stmt = (
                update(SkillStudioDraft)
                .where(SkillStudioDraft.generation_status.in_(["running", "pending"]))
                .values(
                    generation_status="failed",
                    error_detail={
                        "code": "SERVICE_RESTART",
                        "detail": "服务重启,前次生成中断",
                        "reconciled_at": isoformat_bjt(now_naive),
                    },
                    updated_at=now_naive,
                )
                .execution_options(synchronize_session=False)
            )
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount:
                logger.info(
                    "[reconcile-drafts] 回收 stale running/pending draft {} 条",
                    result.rowcount,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reconcile-drafts] 异常（启动不阻断）: {}", exc)


async def _data_access_request_expire_loop():
    """v2.7 大厅 v3：后台周期性扫过期申请（24h 一次）。"""
    import asyncio

    from loguru import logger

    from app.database import async_session_factory
    from app.datasources.service import expire_stale_access_requests

    # 首次延迟 60s，避免启动抖动
    await asyncio.sleep(60)
    while True:
        try:
            async with async_session_factory() as db:
                n = await expire_stale_access_requests(db)
                await db.commit()
                if n:
                    logger.info(f"[data-access-expire-cron] 回收 pending > 14d 申请 {n} 条")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[data-access-expire-cron] 扫描失败: {e}")
        # 24 小时一次
        await asyncio.sleep(24 * 60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理"""
    import asyncio

    runtime_state = await startup_runtime()
    await _verify_role_matrix_schema()
    await _warmup_value_service_columns()
    await _warmup_metric_dept_whitelist()
    await _reconcile_stale_skill_drafts()

    # v2.7 大厅 v3：挂 DataAccessRequest 24h 过期扫描
    expire_task = asyncio.create_task(_data_access_request_expire_loop())

    try:
        yield
    finally:
        expire_task.cancel()
        try:
            await expire_task
        except asyncio.CancelledError:
            pass
        await shutdown_runtime(runtime_state)


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)


# ── 全局 JSON 时间序列化：naive datetime → 自动附加北京时间 +08:00 ──
def _patch_fastapi_datetime_encoder() -> None:
    """让所有 API 响应中的 datetime 自动以北京时间输出。

    DB 列是 timestamp without time zone，SQLAlchemy 读回 naive datetime。
    手写 JSON 以外的 FastAPI datetime 响应在这里补上 Asia/Shanghai 时区，
    ISO 字符串带 +08:00，前端可按北京时间稳定显示。
    """
    import fastapi.encoders
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo

    _bjt = ZoneInfo("Asia/Shanghai")
    _orig_isoformat = fastapi.encoders.isoformat

    def _patched_isoformat(o):
        if isinstance(o, datetime) and o.tzinfo is None:
            o = o.replace(tzinfo=_bjt)
        return _orig_isoformat(o)

    fastapi.encoders.isoformat = _patched_isoformat
    fastapi.encoders.ENCODERS_BY_TYPE[datetime] = _patched_isoformat
    try:
        import datetime as _dt
        fastapi.encoders.ENCODERS_BY_TYPE[_dt.datetime] = _patched_isoformat
    except Exception:
        pass
    # 验证：用当前时间测试输出
    _test_dt = datetime(2026, 4, 26, 8, 0, 0)
    _test_result = fastapi.encoders.ENCODERS_BY_TYPE.get(datetime, lambda x: '?')(_test_dt)
    import sys as _sys
    print(f"[BJTPATCH] {_test_result}", file=_sys.stderr)

_patch_fastapi_datetime_encoder()
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# ===== CORS =====
# 允许：
#   - Vite dev server（开发时 npm run dev 的 http://localhost:3000）
#   - SkillForge 自身的公网/本机 origin（前端 SPA 自己的请求）
#   - Chrome 扩展（push cookie / 抓取 API 上报）
from fastapi.middleware.cors import CORSMiddleware

# 允许的 chrome-extension ID（settings.ALLOWED_EXTENSION_IDS 逗号分隔；空 → 不允许扩展 origin）。
# 安全考虑：`chrome-extension://*` 通配 + `allow_credentials=True` = 任何本地恶意扩展都能带 cookie
# 访问 API。改为 allow-list，未显式配置 ID 时扩展被 CORS 阻止。
_ext_ids = [x.strip() for x in (settings.ALLOWED_EXTENSION_IDS or "").split(",") if x.strip()]
# 本机 host 恒定；公网 host 从 CORS_ALLOWED_HOSTS 环境变量来（逗号分隔）
_base_hosts = ["localhost", "127.0.0.1"]
_extra_hosts = [h.strip() for h in (settings.CORS_ALLOWED_HOSTS or "").split(",") if h.strip()]
_all_hosts = _base_hosts + _extra_hosts
_host_regex = "|".join(re.escape(h) for h in _all_hosts)
if _ext_ids:
    # chrome-extension ID 规范为 32 位 a-p 小写字母；此处只允许白名单中的具体 ID
    _ext_regex = "|".join(f"chrome-extension://{re.escape(x)}" for x in _ext_ids)
    _origin_regex = rf"^({_ext_regex}|https?://({_host_regex})(:\d+)?)$"
else:
    _origin_regex = rf"^https?://({_host_regex})(:\d+)?$"

_cors_origins = [
    "http://localhost:3000",  # Vite dev server
    "http://localhost",        # nginx 本机
    "http://localhost:8000",   # uvicorn 直连
    "http://127.0.0.1",
    "http://127.0.0.1:8000",
]
for _h in _extra_hosts:
    _cors_origins.append(f"http://{_h}")
    _cors_origins.append(f"https://{_h}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 安全响应头 =====
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Playbook editor is embedded via same-origin iframes under /playbook-editor/.
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ===== Prometheus /metrics 端点 =====
from app.common.metrics import setup_metrics
setup_metrics(app)

# ===== 注册API路由 =====
from app.auth.router import router as auth_router  # noqa: E402
from app.skills.router import router as skills_router  # noqa: E402
from app.reviews.router import router as reviews_router  # noqa: E402
from app.dingtalk.router import router as dingtalk_router  # noqa: E402
from app.execution.router import router as execution_router  # noqa: E402
from app.execution.run_trace_router import router as run_trace_router  # noqa: E402
from app.datasources.router import router as datasources_router  # noqa: E402
from app.hall.router import router as hall_router  # noqa: E402
from app.browser.router import router as browser_router  # noqa: E402
from app.collection.router import router as collection_router  # noqa: E402
from app.org.router import router as org_router  # noqa: E402
from app.dashboard.router import router as dashboard_router  # noqa: E402
from app.testing.router import router as testing_router  # noqa: E402
from app.compliance.router import router as compliance_router  # noqa: E402
from app.playbooks.router import router as playbooks_router  # noqa: E402
from app.playbooks.live import router as playbooks_live_router  # noqa: E402
from app.projects.router import router as projects_router  # noqa: E402
from app.users.router import router as users_router  # noqa: E402
from app.audit.router import router as audit_router  # noqa: E402
from app.editor.router import router as editor_router  # noqa: E402
from app.execution.ws import ws_router as execution_ws_router  # noqa: E402
from app.notifications.router import router as notifications_router  # noqa: E402
from app.optimizer.router import router as optimizer_router  # noqa: E402
from app.workbench.router import router as workbench_router  # noqa: E402
from app.common.prompts_router import router as prompts_router  # noqa: E402
from app.todos.router import router as todos_router  # noqa: E402
from app.inbox.router import router as inbox_router  # noqa: E402
from app.aiclaw.router import router as aiclaw_router  # noqa: E402
from app.aiclaw.bridge_router import ws_router as aiclaw_bridge_router  # noqa: E402
from app.agent_core.router import router as agent_core_router  # noqa: E402
from app.coding_agent.router import router as coding_agent_router  # noqa: E402
from app.portal.router import router as portal_router  # noqa: E402
from app.training.router import router as training_router  # noqa: E402
from app.approval.router import router as approval_router  # noqa: E402
from app.tasktree.router import router as tasktree_router  # noqa: E402
from app.intelligence.router import router as intelligence_router  # noqa: E402
from app.codex.router import router as codex_router  # noqa: E402
from app.sf.router import router as sf_router  # noqa: E402
from app.knowledge.router import router as knowledge_router  # noqa: E402
from app.learning.router import router as learning_router  # noqa: E402
from app.agents.router import router as agents_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(skills_router, prefix="/api/skills", tags=["Skill管理"])
app.include_router(workbench_router, prefix="/api/skills", tags=["Skill工作台"])  # 与 skills_router 共用前缀，路由通过 /workbench/ 路径段区分
app.include_router(agent_core_router, prefix="/api/agent-core", tags=["AgentCore"])
app.include_router(reviews_router, prefix="/api/reviews", tags=["审核"])
app.include_router(dingtalk_router, prefix="/api/dingtalk", tags=["钉钉"])
app.include_router(execution_router, prefix="/api/executions", tags=["执行"])
app.include_router(run_trace_router, prefix="/api/executions", tags=["执行追溯"])
app.include_router(datasources_router, prefix="/api/data-sources", tags=["数据源"])
app.include_router(hall_router, prefix="/api/hall", tags=["大厅"])
app.include_router(browser_router, prefix="/api/browser", tags=["浏览器自动化"])
app.include_router(collection_router, prefix="/api/collection", tags=["采集服务"])
app.include_router(org_router, prefix="/api/org", tags=["组织架构"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["看板"])
app.include_router(testing_router, prefix="/api/skills", tags=["测试"])
app.include_router(compliance_router, prefix="/api/compliance", tags=["合规规则"])
app.include_router(playbooks_router, prefix="/api/playbooks", tags=["Playbook"])
app.include_router(playbooks_live_router, prefix="/api/playbooks", tags=["Playbook实时"])
app.include_router(projects_router, prefix="/api/projects", tags=["项目宿主"])
app.include_router(users_router, prefix="/api/users", tags=["用户管理"])
app.include_router(audit_router, prefix="/api/audit", tags=["审计日志"])
app.include_router(editor_router, prefix="/api/editor", tags=["编辑器"])
app.include_router(optimizer_router, prefix="/api", tags=["优化器"])
app.include_router(execution_ws_router, prefix="/api/executions", tags=["执行WS"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["通知"])
app.include_router(prompts_router, prefix="/api/admin", tags=["Prompt 管理"])
app.include_router(coding_agent_router, prefix="/api/admin", tags=["AI 编程助手"])
app.include_router(run_trace_router, prefix="/api/admin", tags=["执行追溯"])
app.include_router(todos_router, prefix="/api/todos", tags=["AI待办"])
app.include_router(inbox_router, prefix="/api/inbox", tags=["收件箱"])
app.include_router(aiclaw_router, prefix="/api/aiclaw", tags=["AIClaw"])
app.include_router(aiclaw_bridge_router, prefix="/api/aiclaw", tags=["AIClawBridge"])
app.include_router(portal_router, prefix="/api/portal", tags=["应用门户"])
app.include_router(training_router, prefix="/api/training", tags=["训练"])
app.include_router(approval_router, prefix="/api/approval", tags=["审批"])
app.include_router(tasktree_router, prefix="/api", tags=["任务树"])
app.include_router(intelligence_router, prefix="/api/intelligence", tags=["平台智能分析"])
app.include_router(codex_router, prefix="/api/codex", tags=["Codex SkillForge"])
app.include_router(sf_router, prefix="/api/sf", tags=["SF"])
app.include_router(knowledge_router, prefix="/api/knowledge", tags=["部门知识库"])
app.include_router(learning_router, prefix="/api/learning", tags=["智能闭环"])
app.include_router(agents_router, prefix="/api/agents", tags=["业务 Agent"])

# ===== P2+ 新增路由 =====
from app.datasources.governance_router import router as governance_router  # noqa: E402
from app.execution.queue_router import router as execution_queue_router  # noqa: E402
from app.auth.abac_router import router as abac_router  # noqa: E402

app.include_router(governance_router, prefix="/api/data-governance", tags=["数据治理"])
app.include_router(execution_queue_router, prefix="/api/executions", tags=["执行队列"])
app.include_router(abac_router, prefix="/api/abac", tags=["ABAC策略"])


# ===== 系统配置 API =====
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.models import SystemConfig
from datetime import datetime
from app.common.time_utils import isoformat_bjt, now_bjt


# 这些 key 前缀属于"运行时幂等锁/缓存"，不应在配置 UI 列出
# （Codex 1.6.0 二轮 MEDIUM-2 修复：避免 cost_alert_seen.* 污染配置表 UI）
_INTERNAL_CONFIG_KEY_PREFIXES = ("cost_alert_seen.",)


@app.get("/api/system-config/", tags=["系统配置"])
async def list_system_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    items = result.scalars().all()
    visible = [
        i for i in items
        if not any(i.key.startswith(p) for p in _INTERNAL_CONFIG_KEY_PREFIXES)
    ]
    return [
        {
            "key": i.key,
            "value": _public_system_config_value(i.key, i.value),
            "secret_configured": _is_secret_config_key(i.key) and i.value not in (None, ""),
            "updated_by": i.updated_by,
            "updated_at": isoformat_bjt(i.updated_at),
        }
        for i in visible
    ]


async def _propagate_system_config_change(key: str) -> list[str]:
    """系统配置变更后，刷新相关 in-process 缓存 + 强制重建子进程。

    没有这一步，60s 内的 spawn 仍会用旧 env，导致用户填了 API key 但子进程
    还报 "Provider 'tencent' requires credentials"。

    返回失败的传播步骤列表（空列表 = 全部成功），由调用方回传客户端
    以便用户知道是否需要手动 reload。
    """
    failures: list[str] = []
    if key.startswith("coding_agent."):
        try:
            from app.common.ai import invalidate_coding_agent_config_cache
            invalidate_coding_agent_config_cache()
        except Exception as e:
            logger.warning("coding_agent 配置缓存失效失败 key={}: {}", key, e)
            failures.append(f"coding_agent_config_cache: {e}")
        # 已存在的 coding_agent 子进程是按 (skill_id, user_id) 池化的，
        # 它们的 env 是 spawn 时定的，无法热更新 — 必须 close_all 让下次 spawn 用新 env
        try:
            from app.coding_agent.session_pool import coding_agent_pool
            await coding_agent_pool.close_all()
        except Exception as e:
            logger.warning("coding_agent 子进程池关闭失败 key={}: {}", key, e)
            failures.append(f"coding_agent_pool_close: {e}")
    elif key.startswith("ai.") or key.startswith("llm."):
        try:
            from app.common.ai import invalidate_ai_config_cache  # type: ignore
            invalidate_ai_config_cache()
        except Exception as e:
            logger.warning("AI 配置缓存失效失败 key={}: {}", key, e)
            failures.append(f"ai_config_cache: {e}")
    return failures


def _is_secret_config_key(key: str) -> bool:
    key_norm = key.lower()
    return any(token in key_norm for token in ("api_key", "app_key", "token", "secret", "password", "credential"))


def _public_system_config_value(key: str, value):
    if _is_secret_config_key(key):
        return ""
    return value


def _json_safe_config_value(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _audit_system_config_value(key: str, value):
    if not _is_secret_config_key(key):
        return _json_safe_config_value(value)
    configured = value is not None and value != ""
    payload = {
        "configured": configured,
        "value_type": type(value).__name__,
    }
    if configured:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        payload["hash_prefix"] = f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
    return payload


@app.put("/api/system-config/{key}", tags=["系统配置"])
async def upsert_system_config(
    key: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    # Old saved settings cannot resurrect the removed CLI. Explicit false remains
    # writable so an operator can retire a migrated configuration.
    if key == "coding_agent.enabled" and body.get("value") is not False:
        from app.coding_agent.runtime_availability import require_authoring_runtime
        require_authoring_runtime()
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    item = result.scalar_one_or_none()
    old_value = item.value if item else None
    new_value = body.get("value")
    existed = item is not None
    if item:
        item.value = new_value
        item.updated_by = current_user.id
        item.updated_at = now_bjt()
    else:
        db.add(SystemConfig(key=key, value=new_value, updated_by=current_user.id))
    await db.commit()
    propagation_failures = await _propagate_system_config_change(key)
    await audit.log(
        current_user.id,
        f"system_config.update.{key}",
        "system_config",
        key,
        {
            "key": key,
            "existed": existed,
            "old_value": _audit_system_config_value(key, old_value),
            "new_value": _audit_system_config_value(key, new_value),
            "propagation_failures": propagation_failures,
        },
    )
    resp = {"message": "保存成功"}
    if propagation_failures:
        # DB 已提交但运行时缓存/子进程刷新失败；让用户知道需要重启进程才能生效
        resp["warning"] = "配置已保存但运行时未完全应用，可能需要重启服务以生效"
        resp["propagation_failures"] = propagation_failures
    return resp


@app.delete("/api/system-config/{key}", tags=["系统配置"])
async def delete_system_config(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    item = result.scalar_one_or_none()
    old_value = item.value if item else None
    if item:
        await db.delete(item)
        await db.commit()
    propagation_failures = await _propagate_system_config_change(key)
    if item:
        await audit.log(
            current_user.id,
            f"system_config.delete.{key}",
            "system_config",
            key,
            {
                "key": key,
                "old_value": _audit_system_config_value(key, old_value),
                "propagation_failures": propagation_failures,
            },
        )
    resp = {"message": "已删除"}
    if propagation_failures:
        resp["warning"] = "配置已删除但运行时未完全应用，可能需要重启服务以生效"
        resp["propagation_failures"] = propagation_failures
    return resp


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "release": ACTIVE_RELEASE_ID,
        "frontend_asset": FRONTEND_ENTRY_ASSET,
        "frontend_source": VUE_DIST_SOURCE,
    }


@app.get("/api/system-info", tags=["系统"])
async def system_info(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """返回真实系统运行信息（含 DB/Redis/Gitea 版本、commit、uptime，仅管理员）"""
    import platform, subprocess, sys, os
    from datetime import datetime

    info = {
        "app_name": settings.APP_NAME,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "host": platform.node(),
    }

    # Git 版本（应用代码仓库）
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%h|%ai|%s"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split("|", 2)
            info["version"] = parts[0]
            info["last_deploy"] = parts[1][:19]
            info["last_commit"] = parts[2]
        # 提交总数作为构建号
        r2 = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if r2.returncode == 0:
            info["build"] = r2.stdout.strip()
    except Exception:
        info["version"] = "unknown"

    # 数据库状态
    try:
        from app.database import engine
        from sqlalchemy import text as sql_text
        async with engine.connect() as conn:
            row = await conn.execute(sql_text("SELECT version()"))
            info["db_version"] = row.scalar().split(",")[0]
            info["db_status"] = "ok"
    except Exception as e:
        info["db_status"] = f"error: {e}"

    # Redis 状态
    try:
        from app.common.cache import _pool as redis_pool
        if redis_pool:
            redis_info = await redis_pool.info("server")
            info["redis_version"] = redis_info.get("redis_version", "unknown")
            info["redis_status"] = "ok"
        else:
            info["redis_status"] = "未配置"
    except Exception as e:
        info["redis_status"] = f"error: {e}"

    # Gitea 状态（优先后台 Skill Git 配置，其次 SKILL_REPO_REMOTE / git remote）
    skill_git_cfg: dict[str, str] = {}
    try:
        cfg_rows = (
            await db.execute(
                select(SystemConfig).where(
                    SystemConfig.key.in_([
                        "skill_git.local_remote_url",
                        "skill_git.username",
                        "skill_git.password",
                    ])
                )
            )
        ).scalars().all()
        key_map = {
            "skill_git.local_remote_url": "local_remote_url",
            "skill_git.username": "username",
            "skill_git.password": "password",
        }
        for row in cfg_rows:
            if row.value is not None and row.key in key_map:
                skill_git_cfg[key_map[row.key]] = str(row.value)
    except Exception as e:
        logger.debug("读取 Skill Git 配置失败: {}", e)

    gitea_url = skill_git_cfg.get("local_remote_url") or settings.SKILL_REPO_REMOTE
    if not gitea_url:
        try:
            r2 = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
                cwd=str(settings.SKILL_REPO_PATH),
            )
            gitea_url = r2.stdout.strip() if r2.returncode == 0 else ""
        except Exception:
            pass
    if gitea_url:
        try:
            import httpx, re
            url = gitea_url
            base = re.sub(r'/[^/]+/[^/]+\.git$', '', url)
            m = re.search(r'//([^@]+)@', url)
            auth = None
            if m:
                from urllib.parse import unquote
                parts = m.group(1).split(':', 1)
                if len(parts) == 2:
                    auth = httpx.BasicAuth(unquote(parts[0]), unquote(parts[1]))
                base = re.sub(r'//[^@]+@', '//', base)
            if auth is None and skill_git_cfg.get("username"):
                auth = httpx.BasicAuth(
                    skill_git_cfg.get("username", ""),
                    skill_git_cfg.get("password", ""),
                )
            async with httpx.AsyncClient(timeout=5, auth=auth) as client:
                r = await client.get(f"{base}/api/v1/version")
                if r.status_code == 200:
                    info["gitea_version"] = r.json().get("version", "unknown")
                    info["gitea_status"] = "ok"
                else:
                    info["gitea_status"] = f"HTTP {r.status_code}"
        except Exception as e:
            info["gitea_status"] = f"error: {e}"
    else:
        info["gitea_status"] = "未配置"

    # Skills-repo 统计
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(settings.SKILL_REPO_PATH),
        )
        info["skill_repo_commits"] = r.stdout.strip() if r.returncode == 0 else "0"
        # Skill 目录数
        skill_dirs = [d for d in Path(settings.SKILL_REPO_PATH).iterdir()
                      if d.is_dir() and not d.name.startswith('.')]
        info["skill_repo_skills"] = len(skill_dirs)
    except Exception as e:
        logger.warning("读取 skills-repo 统计失败: {}", e)

    # 进程启动时间
    try:
        info["uptime_seconds"] = int((datetime.now() - datetime.fromtimestamp(
            os.path.getctime(f"/proc/{os.getpid()}")
        )).total_seconds())
    except Exception as e:
        logger.debug("读取进程 uptime 失败: {}", e)

    return info


@app.get("/api/changelog", tags=["系统"])
async def changelog(limit: int = 100):
    """从 CHANGELOG.md 解析更新日志，按版本分组展示"""
    from app.changelog import parse_changelog

    changelog_path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    if not changelog_path.exists():
        return {"versions": []}

    content = changelog_path.read_text(encoding="utf-8")
    return {"versions": parse_changelog(content, limit=limit)}


# ===== 执行简报静态页（公开访问，docs/overview/）=====
# 必须在 SPA catch-all 之前挂载,否则 /briefing/* 会被 SPA fallback 接管。
# - GET /briefing               → 308 到 /briefing/（带斜杠,保证 HTML 里相对路径 assets/*.png 正确解析）
# - GET /briefing/              → 直接返回 skillforge_exec_briefing.html 主页面,URL 保持 /briefing/
# - GET /briefing/assets/...    → 仅 serve assets/ 子目录下的静态资源（图片等）
#
# ⚠️ 安全（C1 收口）: 不再把整个 docs/overview/ 目录挂出来,否则会泄露
#    project-atlas.md / skillforge_intro.html / briefing_latest_*.png 等
#    内部架构与历史快照。只放行 HTML 真正引用的 assets/ 子目录。
_BRIEFING_DIR = Path(__file__).resolve().parent.parent / "docs" / "overview"
if _BRIEFING_DIR.is_dir() and (_BRIEFING_DIR / "skillforge_exec_briefing.html").is_file():
    from fastapi.responses import FileResponse, RedirectResponse

    @app.get("/briefing", include_in_schema=False)
    async def _briefing_redirect_no_slash():
        return RedirectResponse(url="/briefing/", status_code=308)

    @app.get("/briefing/", include_in_schema=False)
    async def _briefing_index():
        return FileResponse(
            _BRIEFING_DIR / "skillforge_exec_briefing.html",
            media_type="text/html",
        )

    # 只挂 assets/ 子目录,其它 /briefing/* 路径必须显式 404。
    # ⚠️ 没有这层显式 404 的话,后面 / 上的 SPA catch-all(SPAStaticFiles)
    #    会把所有未匹配路径回退到 Vue index.html(状态 200),意味着
    #    /briefing/project-atlas.md 不会泄露 markdown 原文,但用户会看到
    #    一个 SPA 首页(语义上仍属"未隔离");并且静态资源探测攻击也会
    #    错误地认为该路径"存在"。这里用一个显式 catch-all 路由强制 404,
    #    保证 /briefing/* 命名空间只暴露白名单内容。
    _BRIEFING_ASSETS_DIR = _BRIEFING_DIR / "assets"
    if _BRIEFING_ASSETS_DIR.is_dir():
        app.mount(
            "/briefing/assets",
            StaticFiles(directory=str(_BRIEFING_ASSETS_DIR)),
            name="briefing_assets",
        )

    @app.get("/briefing/{any_path:path}", include_in_schema=False)
    async def _briefing_catch_all(any_path: str):
        # 走到这里说明既不是 / 也不是 /assets/* — 一律 404,不要回退到 SPA。
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not Found")

else:
    @app.get("/briefing", include_in_schema=False)
    @app.get("/briefing/{any_path:path}", include_in_schema=False)
    async def _briefing_unavailable(any_path: str = ""):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Briefing is not distributed in this edition")


# ===== 项目宿主静态资源（历史兼容）=====
# 新上传项目的入口由 /api/projects/assets/... 鉴权路由提供。历史 /project-assets/...
# 也改为同一鉴权解析，避免部门/私有项目资产绕过平台权限。
@app.get("/project-assets/{project_id}/{version_token}/{asset_path:path}", include_in_schema=False)
async def _legacy_project_asset(
    project_id: str,
    version_token: str,
    asset_path: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    from app.projects import service as project_service

    path = await project_service.project_asset_path(db, current_user, project_id, version_token, asset_path)
    headers = {
        **project_service.project_asset_response_headers(path),
        "X-SkillForge-Project-Assets": "authenticated-legacy",
    }
    return FileResponse(
        path,
        headers=headers,
    )


# ===== Vue SPA前端（必须放在最后，作为catch-all）=====
if VUE_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(VUE_DIST), html=True), name="vue")
