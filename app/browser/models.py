"""浏览器自动化 ORM 模型"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base
from app.common.time_utils import now_bjt


class BrowserSession(Base):
    """浏览器实例会话（每个容器一条记录）"""
    __tablename__ = "browser_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, default="default")
    container_id = Column(String(100))
    status = Column(String(20), nullable=False, default="stopped")  # stopped/starting/running/error
    cdp_url = Column(String(255))
    novnc_url = Column(String(255))
    department = Column(String(50))
    created_by = Column(String(50))
    created_at = Column(DateTime, default=now_bjt)
    updated_at = Column(DateTime, default=now_bjt, onupdate=now_bjt)


class BrowserSlot(Base):
    """浏览器采集 slot（M2.5 预留，M2 仅建模和 proof 引用）。"""

    __tablename__ = "browser_slots"

    slot_id = Column(String(80), primary_key=True)
    browser_session_id = Column(Integer, ForeignKey("browser_sessions.id", ondelete="SET NULL"))
    cdp_url = Column(String(255), nullable=False)
    profile_dir = Column(String(500))
    node_id = Column(String(100))
    egress_group = Column(String(100))
    egress_ip_hash = Column(String(64))
    status = Column(String(30), nullable=False, default="idle")
    current_pool_id = Column(Integer)
    current_credential_alias = Column(String(80))
    last_used_at = Column(DateTime)
    created_at = Column(DateTime, default=now_bjt)
    updated_at = Column(DateTime, default=now_bjt, onupdate=now_bjt)


class PlatformApiCache(Base):
    """平台 API 注册表 — 全局共享，按域名+页面路径缓存 API 发现结果。

    ~30 个平台 × 每个平台几个页面 = 百来条记录。
    所有 Skill 创建时从这里读，不需要每次 capture_apis。
    """
    __tablename__ = "platform_api_cache"
    __table_args__ = (
        UniqueConstraint("domain", "page_path", name="uq_platform_api_domain_page"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(200), nullable=False, index=True)   # sycm.taobao.com
    page_path = Column(String(500), nullable=False)            # /cc/item_rank
    page_title = Column(String(200))                           # 商品排行
    apis_json = Column(JSONB, nullable=False)                  # 完整的 API 发现结果
    api_count = Column(Integer, default=0)                     # 发现的数据 API 数量
    discovery_method = Column(String(100))                     # capture_apis / manual
    updated_by = Column(String(100))                           # 谁更新的
    created_at = Column(DateTime, default=now_bjt)
    updated_at = Column(DateTime, default=now_bjt, onupdate=now_bjt)

    @property
    def registry_meta(self) -> dict:
        """JSON 兼容注册表元数据，避免为 proof 字段引入高风险迁移。"""
        if isinstance(self.apis_json, dict):
            return self.apis_json.get("_registry_meta") or {}
        return {}

    @property
    def proof(self) -> dict:
        """JSON 兼容抓包 proof。"""
        if isinstance(self.apis_json, dict):
            return self.apis_json.get("_proof") or {}
        return {}

    @property
    def page_url(self) -> str | None:
        return self.registry_meta.get("page_url") or self.proof.get("page_url")

    @property
    def capture_task_id(self) -> int | None:
        return self.registry_meta.get("capture_task_id") or self.proof.get("capture_task_id")

    @property
    def browser_session_id(self) -> int | None:
        return self.registry_meta.get("browser_session_id") or self.proof.get("browser_session_id")

    @property
    def login_source_id(self) -> str | None:
        return self.registry_meta.get("login_source_id") or self.proof.get("login_source_id")

    @property
    def response_hashes(self) -> list:
        return self.registry_meta.get("response_hashes") or self.proof.get("response_hashes") or []

    @property
    def last_verified_at(self) -> str | None:
        return self.registry_meta.get("last_verified_at")

    @property
    def verification_status(self) -> str | None:
        return self.registry_meta.get("verification_status")


class CollectionTask(Base):
    """数据采集任务记录"""
    __tablename__ = "collection_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    browser_session_id = Column(Integer, ForeignKey("browser_sessions.id"))
    platform = Column(String(50), nullable=False)     # sycm / douyin / xiaohongshu ...
    task_type = Column(String(50), nullable=False)     # dashboard / orders / hot ...
    status = Column(String(20), nullable=False, default="pending")  # pending/running/success/failed
    params = Column(JSONB)
    result = Column(JSONB)
    error_message = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, default=now_bjt)
    completed_at = Column(DateTime)
