"""
pydantic-settings 配置管理，从 .env 文件读取所有配置项。
"""

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_nonlocal_private_url(value: str) -> bool:
    host = urlparse(value).hostname
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback


class Settings(BaseSettings):
    """应用配置，全部从环境变量 / .env 文件读取"""

    # env_file 用项目根绝对路径, 不用 ".env" 相对路径 —— 否则任何以非项目根为 cwd
    # 启动的子进程 (如 aiclawcode 把 MCP subprocess 以 skills-repo/<skill>/ 为 cwd)
    # 读不到 .env, 配置全部 fallback 到类默认值 (例如 SKILL_REPO_PATH=/opt/...),
    # 子进程里业务查不到任何东西。2026-04-20 从 MCP 子进程 list_scripts 返回 [] 定位到。
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 基础 ──
    APP_NAME: str = "SkillForge"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    DEBUG: bool = False
    SQL_ECHO: bool = False
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    TRAINING_AUTOMATION_ENABLED: bool = False
    TRAINING_MODEL_TRANSFER_HOSTS: dict[str, str] = {}
    MEDIA_RECIPE_FILE: str = ""  # Optional local recipe pack; unset disables continuous creation.
    ALLOW_MOCK_EXECUTION: bool = False  # 仅开发环境开启mock执行
    VUE_DIST_PATH: str = ""  # 空=自动检测项目根/web/dist；非空则使用指定路径
    LOGIN_MAX_ATTEMPTS: int = 5  # 15分钟内最大登录失败次数
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 文件上传大小限制（默认50MB）
    PROJECT_TARGET_CONCURRENT_RUNS: int = 100  # 项目宿主并发运行目标容量
    PROJECT_TARGET_VISIBLE_PROJECTS: int = 1000  # 项目宿主企业级项目容量目标
    PROJECT_RUNTIME_RECENT_HOURS: int = 24  # 项目运行状态统计窗口
    PROJECT_INGEST_MAX_BYTES: int = 512 * 1024  # 项目输出回传单次载荷上限
    PROJECT_CAPABILITY_MAX_INPUT_BYTES: int = 256 * 1024  # 项目能力调用单次输入上限
    PROJECT_236_CAPABILITY_MAX_INPUT_BYTES: int = 2 * 1024 * 1024  # 236 常驻模型长上下文能力调用单次输入上限
    PROJECT_CAPABILITY_MAX_CALLS_PER_RUN: int = 100  # 单个项目运行最多能力调用次数
    PROJECT_SDK_QPS_PER_TOKEN: int = 5  # 外网 Project SDK token 每秒请求上限
    PROJECT_SDK_DAILY_LIMIT_PER_TOKEN: int = 10_000  # 外网 Project SDK token 每日请求上限
    PROJECT_SDK_CONCURRENT_236_CALLS_PER_TOKEN_MODEL: int = 4  # 同一 token+236 model 最大并发
    PROJECT_MAX_REPORTS_PER_INGEST: int = 50  # 单次输出最多报告数
    PROJECT_MAX_TODOS_PER_INGEST: int = 100  # 单次输出最多待办数
    PROJECT_MAX_PROOFS_PER_INGEST: int = 50  # 单次输出最多证据数
    EXECUTION_ARTIFACT_ROOT: str = "data/execution-artifacts"  # 执行原始数据归档目录
    CLOUD_VIDEO_ENABLED: bool = False  # 云视频素材库 MCP 是否启用
    CLOUD_VIDEO_API_BASE_URL: str = "https://sucaiwang-api-elb.zhishangsoft.com"
    CLOUD_VIDEO_LOGIN_ACCOUNT: str = ""  # 云视频登录账号；只由服务端环境变量或配置注入
    CLOUD_VIDEO_PASSWORD: str = ""  # 云视频登录密码；不得返回给前端/Codex
    CLOUD_VIDEO_REQUEST_TIMEOUT_SECONDS: int = 30
    CLOUD_VIDEO_VISUAL_CACHE_ROOT: str = "data/cloud-video-visual-cache"
    CLOUD_VIDEO_VISUAL_CACHE_TTL_SECONDS: int = 14 * 24 * 60 * 60
    CLOUD_VIDEO_VISUAL_MAX_MEDIA_BYTES: int = 30 * 1024 * 1024
    DINGTALK_REDIRECT_URI: str = ""  # 钉钉OAuth回调URI
    COOKIE_ENCRYPT_KEY: str = ""  # Cookie 加密密钥（Fernet）

    # ── 浏览器自动化 ──
    BROWSER_COMPOSE_FILE: str = "deploy/browser/docker-compose.yml"
    BROWSER_CDP_HOST: str = "127.0.0.1"
    BROWSER_CDP_PORT: int = 9222
    BROWSER_VERIFY_CDP_HOST: str = "127.0.0.1"
    BROWSER_VERIFY_CDP_PORT: int = 9224
    BROWSER_NOVNC_HOST: str = "127.0.0.1"
    BROWSER_NOVNC_PORT: int = 6080
    BROWSER_HEALTH_TIMEOUT: int = 30  # 容器启动健康检查超时秒数
    BROWSER_COLLECT_CONCURRENCY: int = 1  # 浏览器采集/discover 并发上限。page pool 就位前保持 1 串行。
    # cookie 心跳：定时调 verify_login 主动验证各平台登录态
    BROWSER_HEARTBEAT_ENABLED: bool = True
    BROWSER_HEARTBEAT_INTERVAL: int = 300   # 5 分钟
    BROWSER_HEARTBEAT_FIRST_DELAY: int = 90  # 启动后等 90s 再首次跑（避开启动负载）

    # ── 数据库 ──
    DATABASE_URL: str = "postgresql+asyncpg://skillforge:password@localhost:5432/skillforge"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://skillforge:password@localhost:5432/skillforge"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 15

    # ── Git仓库 ──
    SKILL_REPO_PATH: str = "/opt/skillforge/skills-repo"
    SKILL_REPO_REMOTE: str = ""
    SKILL_REPO_BRANCH: str = "main"
    SKILL_REPO_CANONICAL_REMOTE: str = ""
    SKILL_REPO_DOCKER_CONTAINER: str = ""
    SKILL_REPO_DOCKER_BARE_REPO: str = ""
    GIT_USER_NAME: str = "SkillForge"
    GIT_USER_EMAIL: str = "skillforge@example.com"

    # ── 钉钉 ──
    DINGTALK_APP_KEY: str = ""
    DINGTALK_APP_SECRET: str = ""
    DINGTALK_AGENT_ID: str = ""
    DINGTALK_CORP_ID: str = ""
    # 通讯录同步 root 部门 id。默认 1（全公司根）；若应用通讯录可见范围不是"全部员工"，
    # 钉钉会对 dept_id=1 返回 50004"不在授权范围内"，此时改成应用授权的实际部门 id。
    DINGTALK_ROOT_DEPT_ID: str = "1"
    DINGTALK_CALLBACK_URL: str = ""
    DINGTALK_LOGIN_REDIRECT: str = ""
    LINK_DECLINE_OPERATOR_RECIPIENT_QUERY: str = ""
    LINK_DECLINE_OPERATOR_DINGTALK_USER_ID: str = ""
    DINGTALK_CALLBACK_TOKEN: str = ""
    DINGTALK_CALLBACK_AES_KEY: str = ""
    DINGTALK_CARD_CALLBACK_URL: str = ""

    # ── 钉钉审批模板 ──
    DINGTALK_APPROVAL_PROCESS_CODE_L2: str = ""
    DINGTALK_APPROVAL_PROCESS_CODE_L3: str = ""

    # ── 模型网关（OpenAI 兼容接口，直连 DeepSeek/GLM 等，无需 LiteLLM 代理）──
    AI_API_BASE: str = "http://localhost:4000"
    AI_DEFAULT_MODEL: str = "deepseek-chat"
    AI_CHEAP_MODEL: str = "deepseek-chat"
    AI_API_KEY: str = ""
    # 跨模型一致性 check 用的副模型（v7 D4）。空则跑同一个模型对比
    AI_SECONDARY_MODEL: str = ""

    # ── 可选编程 Harness：旧运行时已退役，替代适配器尚未验收 ──
    # 保留开关以读取旧部署配置；即便设为 true 也不能恢复旧 CLI。
    CODING_AGENT_ENABLED: bool = False
    CODING_AGENT_IDLE_TIMEOUT_SECONDS: int = 600    # 会话空闲回收
    CODING_AGENT_MAX_SESSIONS: int = 16             # 最大并发 Node 进程
    CODING_AGENT_STARTUP_TIMEOUT_SECONDS: int = 15  # 等 system/init 事件超时
    CODING_AGENT_CONFIG_BASE_DIR: str = "/tmp/skillforge-coding-agent"

    # ── OpenClaw ──
    OPENCLAW_DEFAULT_URL: str = "ws://localhost:18789"
    OPENCLAW_DEFAULT_AUTH: str = ""
    OPENCLAW_RELOAD_HOOK_URL: str = "localhost:9000"
    OPENCLAW_RELOAD_TOKEN: str = ""
    AICLAW_SKILLS_DIR: str = ""  # AIClaw 用户 skills 目录，设置后创建 Skill 自动推送
    AICLAW_LOCAL_DEFAULT: str = "ws://127.0.0.1:18789"
    HERMES_DEFAULT_URL: str = "http://127.0.0.1:3200"  # Hermes 新实例默认网关（避开 Vite :3000）
    # 允许用 cookie 调 API 的 chrome-extension ID（公钥哈希 32 字符 a-p）。空 → 禁止扩展 origin。
    ALLOWED_EXTENSION_IDS: str = ""  # 逗号分隔，例："abcdefghijklmnopabcdefghijklmnop,..."
    # CORS 额外允许的公网 / 内网 host（逗号分隔，不含 schema/端口）。
    # 默认为空 — 部署到公网时必须在 .env 中显式配置 CORS_ALLOWED_HOSTS，否则跨域请求会被拒。
    # 历史公网默认值已从代码硬编码移除，改为配置驱动。
    CORS_ALLOWED_HOSTS: str = ""
    BRIDGE_REQUEST_TIMEOUT_SECONDS: int = 30
    AICLAW_CHAT_STREAM_TIMEOUT_SECONDS: int = 120
    BRIDGE_PENDING_MAX_PER_CONN: int = 50
    BRIDGE_ACTIVE_RUNS_MAX_PER_INSTANCE: int = 20
    BRIDGE_CHAT_QUEUE_MAX: int = 200
    BRIDGE_REGISTRY_MAX_CONNECTIONS: int = 100
    BRIDGE_MAX_FRAME_BYTES: int = 256 * 1024
    BRIDGE_MAX_ATTACHMENT_BYTES: int = 5 * 1024 * 1024
    BRIDGE_MAX_ATTACHMENTS_TOTAL_BYTES: int = 10 * 1024 * 1024
    BRIDGE_AUTH_NONCE_TTL_SECONDS: int = 30
    BRIDGE_PENDING_TTL_SECONDS: int = 60
    ENROLLMENT_TOKEN_TTL_MINUTES: int = 30
    ENROLLMENT_PEPPER: str = "dev-enrollment-pepper-change-in-production"
    CHAT_SEND_QPS_PER_USER: int = 5
    TODO_SLA_HOURS: int = 24
    TODO_EXPIRE_LOOP_INTERVAL: int = 300
    TRAINING_COLLECT_DUE_ENABLED: bool = True
    TRAINING_COLLECT_DUE_INTERVAL: int = 300
    TRAINING_COLLECT_DUE_STALE_SECONDS: int = 300
    TRAINING_COLLECT_DUE_MAX_JOBS: int = 20

    # ── 智能闭环自动流动 ──
    LEARNING_AUTO_FLOW_ENABLED: bool = True
    LEARNING_AUTO_FLOW_INTERVAL: int = 300
    LEARNING_AUTO_FLOW_FIRST_DELAY: int = 120
    LEARNING_AUTO_FLOW_DAYS: int = 7
    LEARNING_AUTO_FLOW_LIMIT: int = 10
    LEARNING_AUTO_FLOW_IN_WEB_WORKER_ENABLED: bool = True
    LEARNING_AUTO_MATERIALIZE_ENABLED: bool = False
    LEARNING_AUTO_MATERIALIZE_MAX_ARTIFACTS: int = 20
    LEARNING_AUTO_RUN_STALE_MINUTES: int = 20
    LEARNING_AUTO_TRAINING_ENABLED: bool = False
    LEARNING_AUTO_TRAINING_INTERVAL_SECONDS: int = 24 * 60 * 60
    LEARNING_AUTO_TRAINING_DAYS: int = 1
    LEARNING_AUTO_TRAINING_MIN_SAMPLES: int = 4
    LEARNING_AUTO_TRAINING_MAX_JOBS: int = 10
    LEARNING_AUTO_TRAINING_DISPATCH_ENABLED: bool = False
    LEARNING_AUTO_TRAINING_COLLECT_STALE_SECONDS: int = 0
    LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED: bool = False
    LEARNING_AUTO_AGENTIZATION_AI_ENABLED: bool = False
    LEARNING_AUTO_SELF_AUDIT_AI_ENABLED: bool = False
    LEARNING_AUTO_SELF_AUDIT_REMEDIATE_ENABLED: bool = False

    # ── 浏览器远程调用 ──
    BROWSER_REMOTE_TOKEN: str = ""  # 非空时启用 /api/browser/collect-remote 端点

    # ── 语忆开放平台 ──
    YUYIDATA_BASE_URL: str = "https://openapi.yuyidata.com"
    YUYIDATA_APP_KEY: str = ""
    YUYIDATA_APP_SECRET: str = ""

    # ── 调度 ──
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"

    # ── Redis缓存 ──
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_DEFAULT: int = 60        # 默认缓存秒数
    CACHE_TTL_DASHBOARD: int = 120     # 看板数据缓存2分钟
    CACHE_TTL_SKILL_LIST: int = 30     # Skill列表缓存30秒
    CACHE_TTL_COMPLIANCE: int = 300    # 合规规则缓存5分钟
    CACHE_TTL_EXECUTION: int = 30      # 执行记录缓存30秒
    CACHE_TTL_PLAYBOOK: int = 120      # Playbook缓存2分钟
    CACHE_TTL_DATASOURCE: int = 120    # 数据源缓存2分钟
    CACHE_TTL_AUDIT: int = 300         # 审计统计缓存5分钟
    CACHE_TTL_REVIEW: int = 30         # 审核列表缓存30秒
    CACHE_TTL_GENERATED_DATA: int = 6 * 60 * 60  # Skill 生成数据（报告/待办等）缓存6小时
    CACHE_TTL_KNOWLEDGE: int = 300     # 知识库列表/索引健康缓存5分钟
    CACHE_TTL_TRAINING: int = 60       # 训练控制面动态数据缓存1分钟
    CACHE_TTL_AGENT: int = 30          # Agent/Bridge 动态状态缓存30秒
    CACHE_TTL_ADMIN: int = 300         # 管理后台配置/用户/组织类缓存5分钟
    CACHE_TTL_SF_CATALOG: int = 300    # SF 命令与 MCP 目录缓存5分钟
    CACHE_TTL_TRACE: int = 60          # Trace/链路类详情缓存1分钟
    CACHE_ADAPTIVE_ENABLED: bool = True
    CACHE_ADAPTIVE_HOT_THRESHOLD: int = 3      # 同一 key 在窗口内访问 >=3 次视为热点
    CACHE_ADAPTIVE_HOT_MULTIPLIER: int = 3     # 热点 key TTL 自动放大倍数
    CACHE_ADAPTIVE_HOT_WINDOW: int = 10 * 60   # 热点统计窗口：10分钟
    CACHE_ADAPTIVE_MAX_TTL: int = 6 * 60 * 60  # 热点 TTL 上限：6小时
    CACHE_MAX_VALUE_BYTES: int = 2 * 1024 * 1024  # 单个缓存值上限，避免几十 MB JSON 反向拖慢页面
    # Task Tree 顶层响应缓存（秒）：兼顾新鲜度与性能，避免高频轮询打穿数据库
    CACHE_TTL_TASKTREE: int = 120
    # Task Tree 节点心跳判定阈值（秒）：超过则视为 maybe_offline
    TASKTREE_HEARTBEAT_ONLINE: int = 120
    # Task Tree 节点心跳判定阈值（秒）：超过此值认定节点彻底 offline
    TASKTREE_HEARTBEAT_MAYBE: int = 300
    # Task Tree 单节点展开的最大近期运行条数（条）：限制单次渲染数据量
    TASKTREE_MAX_RECENT_RUNS: int = 20
    # Task Tree 前端轮询间隔（秒）：基准值，前端带指数退避
    TASKTREE_POLL_INTERVAL: int = 10
    # Task Tree Lv1 归并根节点 id（默认指向“总经办”）
    TASKTREE_TOP_LEVEL_PARENT_ID: str = ""
    # Task Tree 虚拟组显隐开关（预留开关位）
    TASKTREE_SHOW_VIRTUAL_GROUPS: bool = True
    # Task Tree Skill 缺少 baseline 时的兜底人工耗时（分钟）：用于价值换算
    TASKTREE_DEFAULT_BASELINE: float = 15.0
    # Task Tree 主查询路径开关：True 走 task_nodes_light 单表，False 回退到 ExecutionRun 3 层 JOIN（回滚用）
    USE_TNL_READ: bool = True
    # Task Tree drift_scan 后台调度间隔（秒）：0 表示禁用；默认 1 小时
    TASKTREE_DRIFT_SCAN_INTERVAL: int = 3600
    # Task Tree drift_scan 单次扫描回溯窗口（小时）：默认 24，持久化游标允许跨窗口追赶更老缺口
    TASKTREE_DRIFT_SCAN_WINDOW_HOURS: int = 24

    # ── Session ──
    SESSION_MAX_AGE: int = 7 * 24 * 3600  # 7天
    SESSION_REFRESH_THRESHOLD: int = 3600  # 剩余1小时时续期
    COOKIE_SECURE: bool = True  # HTTPS部署设为True，开发环境可设为False
    WS_HEARTBEAT_INTERVAL_SECONDS: int = 25
    WS_MAX_CONNECTIONS_PER_USER: int = 2

    @property
    def skill_repo_dir(self) -> Path:
        return Path(self.SKILL_REPO_PATH)


    def validate_production(self) -> list[str]:
        """返回真正应该拒绝运行的配置错误。

        交给 main.py:_verify_required_dependencies() 在 lifespan startup 阶段
        统一处理 — raise 让 server 起不来, 不再用 warnings.warn 静默通过。

        哪些配置算"真正应该拒绝":
        - SECRET_KEY 使用默认值: 任何人都能伪造 session cookie, 必须 raise
        - ENROLLMENT_PEPPER 使用默认值: enrollment token 可被伪造
        - 其他配置 (COOKIE_SECURE 等) 是部署 / 网络架构层面的事, 由运维在
          .env 里显式设定, SkillForge 不强制 (本地 http 开发就需要 false)
        """
        issues: list[str] = []
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32 or self.SECRET_KEY == "dev-secret-key-change-in-production":
            issues.append("SECRET_KEY 使用了默认值, 任何人都能伪造 session cookie. 请在 .env 中设置一个长随机串.")
        if not self.ENROLLMENT_PEPPER or len(self.ENROLLMENT_PEPPER) < 32 or self.ENROLLMENT_PEPPER == "dev-enrollment-pepper-change-in-production":
            issues.append("ENROLLMENT_PEPPER 使用了默认值, enrollment token 可被伪造. 请在 .env 中设置一个长随机串.")
        if not self.COOKIE_ENCRYPT_KEY:
            issues.append("COOKIE_ENCRYPT_KEY 未设置, Chrome 扩展推送 cookies 会 RuntimeError. 生成命令: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
        return issues

    def validate_operational_warnings(self) -> list[str]:
        """返回不阻止启动但应该告警的配置问题（运行时功能会受影响）。"""
        warnings: list[str] = []
        db_urls = (str(self.DATABASE_URL), str(self.DATABASE_URL_SYNC))
        if any(token in url for url in db_urls for token in (":password@", ":CHANGE_ME_STRONG_PASSWORD@")):
            warnings.append("DATABASE_URL / DATABASE_URL_SYNC 仍在使用默认或占位密码 — 请在 .env 中替换")
        if not self.AI_API_KEY:
            warnings.append("AI_API_KEY 未设置 — .env 仅作为本地兜底；正式 AI 密钥应在后台 system_config 的 ai.api_key 配置")
        has_canonical_skill_git = bool(self.SKILL_REPO_CANONICAL_REMOTE) or bool(
            self.SKILL_REPO_DOCKER_CONTAINER and self.SKILL_REPO_DOCKER_BARE_REPO
        )
        if not has_canonical_skill_git:
            warnings.append("SKILL_REPO_CANONICAL_REMOTE 未设置 — 手动同步无法先写入本地 Docker/Gitea Skill Git 主线")
        if not self.DINGTALK_APP_KEY or not self.DINGTALK_APP_SECRET:
            warnings.append("DINGTALK_APP_KEY/SECRET 未设置 — 钉钉推送/审批/OAuth 不可用")
        if not self.COOKIE_ENCRYPT_KEY:
            warnings.append("COOKIE_ENCRYPT_KEY 未设置 — Chrome 扩展推送 cookies 会 500（生成命令: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')")  # noqa: E501
        if not self.DINGTALK_LOGIN_REDIRECT:
            warnings.append("DINGTALK_LOGIN_REDIRECT 未设置 — 钉钉扫码登录不可用")
        if not self.DINGTALK_CALLBACK_TOKEN:
            warnings.append("DINGTALK_CALLBACK_TOKEN 未设置 — 钉钉回调端点会 500")
        if not self.CORS_ALLOWED_HOSTS and not _is_nonlocal_private_url(self.PUBLIC_BASE_URL):
            warnings.append("CORS_ALLOWED_HOSTS 未设置 — 公网部署时跨域请求会被拒，请配置公网域名")
        if _is_nonlocal_private_url(self.OPENCLAW_DEFAULT_URL):
            warnings.append("OPENCLAW_DEFAULT_URL 使用内网非本机地址 — 非内网环境请在 .env 中配置")
        if self.ALLOW_MOCK_EXECUTION:
            warnings.append("[WARN] Mock execution enabled — do NOT use in production")
        return warnings


# 全局单例
settings = Settings()
