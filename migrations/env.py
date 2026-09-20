"""Alembic迁移环境配置"""

import sys
from pathlib import Path

# 确保项目根目录在Python路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base

# 导入所有模型，确保Base.metadata包含所有表
from app.auth.models import User, LoginLog  # noqa: F401
from app.skills.core.models import Skill, SkillLock  # noqa: F401
from app.reviews.models import Review, ReviewComment  # noqa: F401
from app.execution.models import ExecutionRun, ExecutionStep, DecisionLog, OpenClawInstance, ExecutionArtifact, ExecutionRunLog  # noqa: F401
from app.agents.models import DepartmentAnalysisAgent  # noqa: F401
from app.datasources.models import DataSource, DataIngestionLog, DataAccessGrant  # noqa: F401
from app.dingtalk.models import DingTalkOutbox  # noqa: F401
from app.testing.models import Conversation  # noqa: F401
from app.common.audit import AuditLog  # noqa: F401
from app.common.models import ComplianceRule  # noqa: F401
from app.optimizer.models import (  # noqa: F401
    OptimizerSession, OptimizerCandidate, BenchmarkPack, BenchmarkCase, BenchmarkRun,
)
from app.hall.models import (  # noqa: F401
    AIChatAttachment,
    AIChatMessage,
    AIChatThread,
    AIChatUserPreference,
    DirectCapabilityImageHistory,
    DirectCapabilityTask,
)
from app.inbox.models import InboxReportCard, UserInboxPreference  # noqa: F401
from app.codex.models import (  # noqa: F401
    CodexCliSession,
    CodexDebugRun,
    CodexLoginIntent,
    CodexMcpCallAudit,
    CodexRunToken,
    CodexSkillSubmission,
    CodexOutputPreview,
    SfDataRecord,
)
from app.portal.models import SkillSubmission, UserSkillUIPreference  # noqa: F401
from app.portal.market_models import MarketCertification, MarketRating  # noqa: F401
from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment  # noqa: F401
from app.knowledge.models import (  # noqa: F401
    DepartmentKnowledgeBase, KnowledgeDocument, KnowledgeChunk, KnowledgeQueryLog, KnowledgeIndexJob,
)
from app.learning.models import (  # noqa: F401
    LearningEvent, LearningArtifact, LearningFlowEdge, LearningIngestionJob, ImprovementCandidate,
)

config = context.config

# 从settings覆盖数据库URL（使用同步驱动）
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成SQL脚本"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接连接数据库执行迁移"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
