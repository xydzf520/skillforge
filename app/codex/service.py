"""Service helpers for Codex local SkillForge gateway."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import yaml
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import get_accessible_departments, role_matches_any
from app.auth.models import User
from app.codex.models import (
    CodexCliSession,
    CodexDebugRun,
    CodexLoginIntent,
    CodexMcpCallAudit,
    CodexRunToken,
    CodexSkillSubmission,
)
from app.common.audit import audit
from app.common.ai import call_llm, get_ai_profile_config, redact_secret_text
from app.common.contract_schema import get_output_schema, lint_output_schema, parse_skill_md_output_fields
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.common.yuyidata_config import inject_yuyidata_arguments, yuyidata_mcp_env
from app.config import settings
from app.codex import cloud_video as codex_cloud_video
from app.collection.models import CollectionProof, PlatformApiSchemaSnapshot
from app.dingtalk.client import dingtalk_client
from app.dingtalk.models import DingTalkOutbox
from app.dingtalk.recipients import resolve_work_notice_user
from app.execution.artifact_service import artifact_abs_path
from app.execution.models import DecisionLog, ExecutionArtifact, ExecutionRun, ExecutionStep
from app.org.models import OrgUnit, UserOrgMembership
from app.qianchuan.video_content_analysis import (
    collect_qianchuan_video_content_analysis_async,
    qianchuan_video_content_analysis_input_schema,
)
from app.sf import data_service as sf_data_service
from app.skills.core.access import SkillMember, build_skill_access_filter, get_skill_permissions, require_skill_access
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.service_shared import sync_skill_fields_from_frontmatter
from app.skills.validators import structural_validate


CLI_SESSION_DAYS = 30
LOGIN_INTENT_TTL_SECONDS = 600
RUN_TOKEN_TTL_SECONDS = 1800
POLL_REDIRECT_URI = "urn:skillforge:codex:poll"
DEFAULT_DEBUG_LIMITS = {"max_tool_calls": 50, "timeout_seconds": 300}
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_FILE_BYTES = 5 * 1024 * 1024
STDOUT_TAIL_LIMIT = 64 * 1024

ALLOWED_PACKAGE_EXACT = {
    "SKILL.md",
    "contract.json",
    "skillforge.yaml",
    "policy_pack.yaml",
    "policy.yaml",
    "intent.md",
    "source_contract.yaml",
    "metric_registry.yaml",
}
ALLOWED_PACKAGE_DIRS = (
    "scripts/",
    "tests/",
    "fixtures/",
    "references/",
    "assets/",
    "prompts/",
)
DENIED_PACKAGE_PREFIXES = (
    ".git/",
    ".venv/",
    "venv/",
    "node_modules/",
    "__pycache__/",
    "skills-repo/",
)
DENIED_PACKAGE_EXACT = {".env", ".env.local", ".env.production"}
FULL_GIT_OBJECT_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
ORG_MCP_USER_LIMIT = 100
ORG_MCP_PUSH_LIMIT = 50
PLATFORM_AI_MCP_MODEL = "deepseek-v4-pro"
PLATFORM_AI_MCP_FLASH_MODEL = "deepseek-v4-flash"
PLATFORM_AI_MCP_MAX_CONTEXT_TOKENS = 1_000_000
PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS = 16_384
RAW_DATA_QUERY_LIMIT = 100
RAW_DATA_TEXT_LIMIT = 12_000
DINGTALK_CONTACT_DEPARTMENT_SCAN_LIMIT = 1000
DINGTALK_CONTACT_USER_SCAN_LIMIT = 10000
DATA_ARTIFACT_DEFAULT_READ_BYTES = 1024 * 1024
DATA_ARTIFACT_MAX_READ_BYTES = 10 * 1024 * 1024
BUILTIN_MCP_TOOL_NAMES = {
    "skillforge_data_artifact_get",
    "skillforge_data_capability_latest",
    "skillforge_data_capability_list",
    "skillforge_samplebrand_cloud_video_data_get",
    "skillforge_samplebrand_cloud_video_data_latest",
    "skillforge_samplebrand_cloud_video_daily_analysis_input",
    "skillforge_execution_artifact_latest",
    "skillforge_execution_artifact_summary",
    "skillforge_org_search_users",
    "skillforge_org_list_members",
    "skillforge_dingtalk_send_work_notice",
    "skillforge_qianchuan_video_content_analysis",
    "skillforge_agent_coverage",
    "skillforge_ai_analyze",
    "skillforge_cloud_video_accounts",
    "skillforge_cloud_video_ad_report",
    "skillforge_cloud_video_audit_rejects",
    "skillforge_cloud_video_categories",
    "skillforge_cloud_video_daily_person_video_report",
    "skillforge_cloud_video_material_report",
    "skillforge_cloud_video_session",
    "skillforge_cloud_video_tags",
    "skillforge_cloud_video_video_usage_report",
    "skillforge_cloud_video_visual_analysis",
    "skillforge_cloud_video_videos",
    "skillforge_raw_data_query",
    "skillforge_run_analyze",
    "skillforge_sf_data_get",
    "skillforge_sf_data_list",
    "skillforge_sf_data_write",
    "skillforge_tmall_link_decline_data_get",
    "skillforge_tmall_link_decline_data_latest",
    "skillforge_yuyidata_customer_service_data_get",
    "skillforge_yuyidata_customer_service_data_latest",
}


@dataclass(frozen=True)
class DataCapability:
    key: str
    platform: str
    data_scope: str
    display_name: str
    description: str
    source_skill_ids: tuple[str, ...]
    default_kind: str = "raw-output"
    aliases: tuple[str, ...] = ()


DATA_CAPABILITIES: dict[str, DataCapability] = {
    "cloud_video.samplebrand_weekly.raw_collection": DataCapability(
        key="cloud_video.samplebrand_weekly.raw_collection",
        platform="cloud_video",
        data_scope="cloud_video.samplebrand_weekly.cached_artifacts",
        display_name="示例品牌云视频周度全量采集数据",
        description="读取 samplebrand-weekly-video-diagnosis 每日 08:00 自动采集后保存的 raw-output gzip JSON。",
        source_skill_ids=("samplebrand-weekly-video-diagnosis",),
        aliases=(
            "samplebrand",
            "samplebrand_cloud_video",
            "samplebrand-weekly-video",
            "samplebrand_weekly_video",
            "cloud_video_samplebrand",
            "示例品牌",
            "示例品牌云视频",
        ),
    ),
    "tmall.link_decline.raw_collection": DataCapability(
        key="tmall.link_decline.raw_collection",
        platform="tmall",
        data_scope="tmall.link_decline.cached_artifacts",
        display_name="天猫链接下滑每日原始采集数据",
        description="读取 tmall-link-decline-collector-v1 每日执行后由平台保存的 raw-output gzip JSON。",
        source_skill_ids=("tmall-link-decline-collector-v1",),
        aliases=(
            "tmall",
            "tmall_link_decline",
            "tmall-link-decline",
            "tmail",
            "tmail_link_decline",
            "tmail-link-decline",
            "天猫链接下滑",
        ),
    ),
    "yuyidata.customer_service.raw_collection": DataCapability(
        key="yuyidata.customer_service.raw_collection",
        platform="yuyidata",
        data_scope="yuyidata.customer_service.cached_artifacts",
        display_name="语艺客服会话每日原始数据",
        description="读取语艺客服会话采集/日报类 Skill 执行后由平台保存的 raw-output gzip JSON。",
        source_skill_ids=(
            "yuyidata-customer-service-collector-v1",
            "yuyidata-daily-user-need-analysis-v1",
            "yuyidata-customer-need-analysis-v1",
        ),
        aliases=(
            "yuyi",
            "yuyi_customer_service",
            "yuyidata",
            "yuyidata_customer_service",
            "yuyidata-customer-service",
            "语艺",
            "语艺客服",
        ),
    ),
}

DATA_CAPABILITY_ALIASES: dict[str, str] = {}
for _capability in DATA_CAPABILITIES.values():
    DATA_CAPABILITY_ALIASES[_capability.key] = _capability.key
    DATA_CAPABILITY_ALIASES[_capability.key.lower()] = _capability.key
    for _alias in _capability.aliases:
        DATA_CAPABILITY_ALIASES[_alias] = _capability.key
        DATA_CAPABILITY_ALIASES[_alias.lower()] = _capability.key


def _org_search_users_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "按姓名、账号、平台用户 ID、钉钉 userId 或部门模糊搜索",
            },
            "department": {"type": "string", "description": "按部门名称模糊过滤"},
            "org_unit_id": {"type": "string", "description": "按组织单元 ID 过滤"},
            "include_subtree": {
                "type": "boolean",
                "description": "org_unit_id 存在时是否包含子组织，默认 true",
            },
            "role": {"type": "string", "description": "按角色过滤"},
            "state": {"type": "string", "description": "active/disabled/pending，默认 active"},
            "include_dingtalk_contacts": {
                "type": "boolean",
                "description": "平台用户未命中时是否兜底查询钉钉通讯录，默认 true",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": ORG_MCP_USER_LIMIT},
        },
    }


def _org_list_members_schema() -> dict[str, Any]:
    schema = _org_search_users_schema()
    schema["properties"] = {
        key: value
        for key, value in schema["properties"].items()
        if key not in {"query", "role", "include_dingtalk_contacts"}
    }
    schema["properties"]["include_org"] = {
        "type": "boolean",
        "description": "返回匹配组织单元摘要，默认 true",
    }
    return schema


def _dingtalk_work_notice_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "工作通知标题"},
            "markdown": {"type": "string", "description": "工作通知 Markdown 正文"},
            "content": {"type": "string", "description": "markdown 的兼容别名"},
            "buttons": {
                "type": "array",
                "description": "可选按钮，最多 5 个",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
            },
            "user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "平台用户 ID 列表",
            },
            "dingtalk_user_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "钉钉 userId 列表",
            },
            "query": {"type": "string", "description": "按组织成员搜索并推送"},
            "department": {"type": "string", "description": "按部门成员推送"},
            "org_unit_id": {"type": "string", "description": "按组织单元成员推送"},
            "include_subtree": {"type": "boolean", "description": "org_unit_id 存在时是否包含子组织，默认 true"},
            "limit": {"type": "integer", "minimum": 1, "maximum": ORG_MCP_PUSH_LIMIT},
            "dryRun": {"type": "boolean", "description": "兼容 MCP stdio 参数；默认由网关 dry_run 控制"},
            "idempotencyKey": {"type": "string", "description": "真实推送的幂等键，兼容 idempotency_key"},
            "idempotency_key": {"type": "string", "description": "真实推送的幂等键"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 9, "description": "Outbox 优先级，默认 5"},
        },
        "required": ["title"],
    }


def _platform_ai_analyze_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "prompt、question、context 或 context_pack 至少提供一个；服务端会继续做参数校验。",
        "properties": {
            "prompt": {"type": "string", "description": "要交给平台 AI 分析的问题或任务"},
            "question": {"type": "string", "description": "prompt 的兼容别名"},
            "system": {"type": "string", "description": "可选 system prompt；不传则使用平台默认分析提示"},
            "context": {"description": "可选上下文，支持字符串、对象或数组"},
            "context_pack": {"type": "object", "description": "可选结构化上下文包，会作为 1M 上下文输入的一部分"},
            "json_mode": {"type": "boolean", "description": "是否要求模型返回 JSON object，默认 false"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "max_output_tokens": {
                "type": "integer",
                "minimum": 1,
                "maximum": PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS,
                "description": "输出 token 上限，默认 4096",
            },
            "skill_id": {"type": "string", "description": "可选 Skill 归因；会校验当前用户读取权限"},
        },
    }


def _raw_data_query_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": [
                    "execution_runs",
                    "execution_steps",
                    "decision_logs",
                    "collection_proofs",
                    "api_schema_snapshots",
                ],
                "description": "要查询的原始数据来源；默认 execution_runs",
            },
            "skill_id": {"type": "string", "description": "Skill ID；非全局管理员通常必须提供 skill_id、run_id 或 proof_id"},
            "run_id": {"type": "string", "description": "Skill 运行后的 execution_run id"},
            "execution_run_id": {"type": "string", "description": "run_id 的兼容别名"},
            "proof_id": {"type": "string", "description": "collection_proofs 的 proof_id"},
            "platform": {"type": "string"},
            "shop_id": {"type": "string"},
            "data_scope": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": RAW_DATA_QUERY_LIMIT},
            "include_payload": {
                "type": "boolean",
                "description": "是否返回 input/output/metadata 等载荷；敏感字段会脱敏，默认 true",
            },
            "include_endpoint": {
                "type": "boolean",
                "description": "api_schema_snapshots 是否返回脱敏 endpoint，默认 false",
            },
        },
    }


def _execution_artifact_summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "执行 run_id"},
            "kind": {
                "type": "string",
                "description": "artifact 类型，默认返回全部；常用 raw-input/raw-output",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        "required": ["run_id"],
    }


def _execution_artifact_latest_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "skill_id": {"type": "string", "description": "Skill ID"},
            "kind": {
                "type": "string",
                "description": "artifact 类型，默认 raw-output",
                "default": "raw-output",
            },
            "schema": {"type": "string", "description": "可选 schema_name 过滤"},
            "status": {"type": "string", "description": "执行状态过滤，默认 completed"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
        },
        "required": ["skill_id"],
    }


def _data_capability_list_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "description": "可选平台过滤，如 tmall/yuyidata",
            },
            "include_unavailable": {
                "type": "boolean",
                "description": "是否返回当前账号不可读或尚无可读 source Skill 的能力，默认 false",
            },
        },
    }


def _sf_data_write_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "数据命名空间，如 project.short_video.inputs"},
            "title": {"type": "string", "description": "可读标题"},
            "content_type": {"type": "string", "enum": ["json", "text", "table", "binary"], "description": "数据类型；不传则由 payload 推断"},
            "data": {"description": "JSON 对象、数组或标量数据"},
            "content": {"description": "兼容字段，等同 data"},
            "text": {"type": "string", "description": "文本内容"},
            "rows": {"type": "array", "description": "表格行数组，通常为对象数组"},
            "base64": {"type": "string", "description": "二进制内容的 base64 字符串；大型内容转 artifact 引用"},
            "filename": {"type": "string"},
            "metadata": {"type": "object", "description": "业务元数据"},
            "schema": {"type": "object", "description": "可选数据 schema 摘要"},
            "source": {"type": "string", "description": "来源，如 codex_cli/project/api"},
            "source_tool": {"type": "string", "description": "写入来源工具"},
            "source_ref": {"type": "string", "description": "外部来源引用"},
            "skill_id": {"type": "string"},
            "run_id": {"type": "string"},
            "visibility": {"type": "string", "enum": ["global", "department", "private"], "description": "默认 global"},
            "idempotency_key": {"type": "string", "description": "真实写入幂等键，也可通过 sf mcp call --idempotency-key 传入"},
        },
        "required": ["namespace"],
    }


def _sf_data_list_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "namespace": {"type": "string"},
            "content_type": {"type": "string"},
            "skill_id": {"type": "string"},
            "run_id": {"type": "string"},
            "source": {"type": "string"},
            "q": {"type": "string", "description": "关键词搜索"},
        },
    }


def _sf_data_get_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "sf_data_records.id"},
            "record_id": {"type": "string", "description": "兼容字段，等同 id"},
        },
    }


def _run_analyze_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "要分析的 Skill execution_run id"},
            "execution_run_id": {"type": "string", "description": "run_id 的兼容别名"},
            "skill_id": {"type": "string", "description": "可选 Skill ID；会校验读取权限"},
            "prompt": {"type": "string", "description": "可选分析任务；不传则使用运行复盘默认提示"},
            "json_mode": {"type": "boolean", "description": "是否要求模型返回 JSON object，默认 false"},
            "max_output_tokens": {
                "type": "integer",
                "minimum": 1,
                "maximum": PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS,
                "description": "输出 token 上限，默认 4096",
            },
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "step_limit": {"type": "integer", "minimum": 1, "maximum": RAW_DATA_QUERY_LIMIT},
            "decision_limit": {"type": "integer", "minimum": 1, "maximum": RAW_DATA_QUERY_LIMIT},
            "proof_limit": {"type": "integer", "minimum": 1, "maximum": RAW_DATA_QUERY_LIMIT},
            "snapshot_limit": {"type": "integer", "minimum": 1, "maximum": RAW_DATA_QUERY_LIMIT},
            "include_raw": {
                "type": "boolean",
                "description": "是否在 MCP 响应中返回本次送入 AI 的脱敏原始数据，默认 false",
            },
        },
        "required": ["run_id"],
    }


def _agent_coverage_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "department": {
                "type": "string",
                "description": "可选部门名称；管理员可按部门过滤，普通账号仍只返回本部门可见覆盖度",
            },
            "status": {
                "type": "string",
                "enum": ["ready", "fallback", "missing"],
                "description": "可选覆盖状态过滤",
            },
            "include_agents": {
                "type": "boolean",
                "description": "是否返回脱敏 Agent 摘要；默认 true",
            },
        },
    }


def _cloud_video_session_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "force_refresh": {"type": "boolean", "description": "是否强制重新登录云视频账号，默认 false"},
        },
    }


def _cloud_video_accounts_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "include_tree": {"type": "boolean", "description": "是否返回团队-分组-人员树，默认 false"},
            "max_people": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
        },
    }


def _cloud_video_categories_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "category_id": {"type": "string", "description": "可选父分类 ID；空值返回根分类"},
            "include_children": {"type": "boolean", "description": "是否保留子分类树，默认 true"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
        },
    }


def _cloud_video_tags_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "category_ids": {"type": "array", "items": {"type": "string"}, "description": "素材分类 ID 列表"},
            "parent_type_ids": {"type": "array", "items": {"type": "string"}, "description": "根分类 ID 列表；不传时由分类树推断"},
            "include_system": {"type": "boolean", "description": "是否返回系统标签节点，默认 false"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
        },
    }


def _cloud_video_videos_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 60, "default": 24},
            "search": {"type": "string", "description": "按视频名搜索"},
            "category_ids": {"type": "array", "items": {"type": "string"}, "description": "素材分类 ID 列表"},
            "tag_ids": {"type": "array", "items": {"type": "string"}, "description": "标签 ID 列表"},
            "exclude_tag_ids": {"type": "array", "items": {"type": "string"}, "description": "排除标签 ID 列表"},
            "search_type": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4],
                "description": "上传维度筛选：0=全部，1=上传人，2=上传分组，3=上传团队，4=素材分类维度",
            },
            "search_ids": {"type": "array", "items": {"type": "string"}, "description": "search_type 对应的账号、分组、团队或分类 ID"},
            "system_auto_label_type": {"type": "string", "description": "系统自动标签筛选；1=千川卡审，6=AD卡审，9=ADQ卡审，11=预审通过，12=预审卡审"},
            "video_state": {"type": "string", "description": "视频状态筛选，按云视频前端 videoState 透传"},
            "video_type": {"type": "integer", "default": 0, "description": "视频分区类型，默认 0=成片；1=素材，2=三方，3=图片，5=音频，6=脚本"},
            "date_mode": {"type": "string", "enum": ["upload", "shoot", "cost"], "description": "日期筛选口径，默认 upload"},
            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            "sort_order": {"type": "integer", "description": "云视频素材库排序值，默认 1"},
            "include_raw": {"type": "boolean", "description": "是否返回脱敏 raw 字段，默认 false"},
            "auto_page": {"type": "boolean", "description": "是否由平台 MCP 在单次调用内自动翻页聚合，默认 false"},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "auto_page=true 时最多读取页数"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 300, "description": "auto_page=true 时最多返回素材条数"},
        },
    }


def _cloud_video_visual_analysis_schema() -> dict[str, Any]:
    video_item = {
        "type": "object",
        "properties": {
            "video_id": {"type": "string", "description": "云视频 videoId"},
            "video_name": {"type": "string", "description": "视频标题"},
            "topic_key": {"type": "string", "description": "同主题 key，建议为 category_name + 爆款形式"},
            "category_name": {"type": "string"},
            "labels": {"type": "object"},
            "lifecycle_hint": {
                "type": "object",
                "description": "可选：千川点击生命周期摘要，仅用于提示视觉模型重点观察高点击秒点附近的画面触发因素。",
            },
        },
        "required": ["video_id", "topic_key"],
    }
    return {
        "type": "object",
        "properties": {
            "low_videos": {"type": "array", "items": video_item, "description": "当天低消耗视频列表"},
            "benchmark_videos": {"type": "array", "items": video_item, "description": "同主题高质量 benchmark 视频列表"},
            "videos": {"type": "array", "items": video_item, "description": "兼容字段：等同 low_videos"},
            "max_videos": {"type": "integer", "minimum": 1, "maximum": 30, "default": 20},
            "force_refresh": {"type": "boolean", "description": "是否忽略缓存重新分析，默认 false"},
            "cache_ttl_seconds": {"type": "integer", "minimum": 0, "description": "脱敏视觉结果缓存 TTL"},
        },
    }


def _cloud_video_material_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "消耗开始日期 YYYY-MM-DD，默认 2026-05-01"},
            "end_date": {"type": "string", "description": "消耗结束日期 YYYY-MM-DD，默认 2026-06-09"},
            "search_type": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4],
                "default": 0,
                "description": "素材统计维度：0=汇总，1=上传人，2=上传分组，3=上传团队，4=素材分类",
            },
            "search_ids": {"type": "array", "items": {"type": "string"}, "description": "筛选的上传人/分组/团队/分类 ID"},
            "type_ids": {"type": "array", "items": {"type": "string"}, "description": "素材分类筛选 ID"},
            "platform_type": {"type": "integer", "default": 2, "description": "广告平台类型，默认 2=巨量千川"},
            "dimension_type": {"type": "integer", "description": "上游报表 type；不传时按 search_type 复用前端默认规则"},
            "advertiser_id": {"type": "string", "description": "广告主 ID，可选"},
            "advertiser_name": {"type": "string", "description": "广告主名称，可选"},
        },
    }


def _cloud_video_video_usage_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "统计开始日期 YYYY-MM-DD，默认 2026-05-01"},
            "end_date": {"type": "string", "description": "统计结束日期 YYYY-MM-DD，默认 2026-06-09；单次最多 93 天"},
            "data_type": {
                "type": "integer",
                "enum": [1, 2, 3],
                "default": 3,
                "description": "统计维度：1=个人数据，2=分组数据，3=团队数据",
            },
            "top_type": {
                "type": "integer",
                "enum": [0, 1, 2],
                "default": 2,
                "description": "前端展示口径：0=Top5，1=Top10，2=汇总数据",
            },
            "metric_type": {"type": "integer", "default": 0, "description": "前端报表 type，默认 0=上传次数/上传条数口径"},
            "video_type": {"type": "integer", "default": 0, "description": "视频分区类型，默认 0=成片"},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "data_type 对应的人员/分组/团队 ID；不传为当前账号可见范围"},
            "type_ids": {"type": "array", "items": {"type": "string"}, "description": "素材分类 ID 筛选"},
            "label_ids": {"type": "array", "items": {"type": "string"}, "description": "素材标签 ID 筛选"},
            "query_type": {"type": "integer", "default": 0, "description": "前端 queryType，默认 0"},
        },
    }


def _cloud_video_audit_rejects_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "video_id": {"type": "string", "description": "云视频 videoId，必填"},
            "platform_type": {"type": "integer", "default": 2, "description": "审核平台：2=巨量千川，0=巨量广告，6=腾讯ADQ"},
            "state": {"type": "integer", "enum": [0, 1], "description": "卡审状态：0=卡审中，1=历史卡审；不传为全部"},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
        "required": ["video_id"],
    }


def _cloud_video_ad_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tab": {"type": "string", "enum": ["personal", "detail", "个人", "明细"], "description": "报表 tab，默认 personal"},
            "start_date": {"type": "string", "description": "消耗开始日期 YYYY-MM-DD，默认 2026-05-01"},
            "end_date": {"type": "string", "description": "消耗结束日期 YYYY-MM-DD，默认 2026-06-09"},
            "account_ids": {"type": "array", "items": {"type": "string"}, "description": "云视频账号树内的人员 ID；不传则使用账号可见全部人员"},
            "platform_type": {"type": "integer", "default": 2, "description": "投放平台类型，默认 2=巨量千川"},
            "platform_data_type": {"type": "integer", "default": 0, "description": "平台子类型，默认 0=千川汇总"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
        },
    }


def _cloud_video_daily_person_video_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "消耗开始日期 YYYY-MM-DD，默认 2026-05-01"},
            "end_date": {"type": "string", "description": "消耗结束日期 YYYY-MM-DD，默认 2026-06-09；单次最多 93 天"},
            "account_ids": {"type": "array", "items": {"type": "string"}, "description": "云视频账号树内的人员 ID；不传则使用账号可见全部人员"},
            "platform_type": {"type": "integer", "default": 2, "description": "投放平台类型，默认 2=巨量千川"},
            "platform_data_type": {"type": "integer", "default": 0, "description": "平台子类型，默认 0=千川汇总"},
            "max_video_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200000,
                "default": 200000,
                "description": "最多读取并用于人员摘要分析的视频明细行数",
            },
            "max_returned_video_rows": {
                "type": "integer",
                "minimum": 0,
                "maximum": 200000,
                "default": 50000,
                "description": "include_daily_rows=true 时最多返回的逐日视频明细行数；不影响人员摘要计算",
            },
            "max_person_rows": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 10000},
            "top_videos_per_person": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "low_videos_per_person": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "include_daily_rows": {"type": "boolean", "description": "是否返回逐日原始聚合行，默认 false；摘要里的每个视频仍保留 daily_breakdown"},
        },
    }


def _data_capability_latest_schema(*, require_capability: bool = True) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": "数据能力 key 或别名，如 tmall.link_decline.raw_collection",
            },
            "kind": {
                "type": "string",
                "description": "artifact 类型，默认使用能力定义，一般为 raw-output",
            },
            "schema": {"type": "string", "description": "可选 schema_name 过滤"},
            "status": {"type": "string", "description": "执行状态过滤，默认 completed"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
        },
    }
    if require_capability:
        schema["required"] = ["capability"]
    return schema


def _data_artifact_get_schema(*, require_capability: bool = False) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "artifact_id": {
                "type": "integer",
                "description": "execution_artifacts.id；优先使用该字段精确读取",
            },
            "run_id": {"type": "string", "description": "执行 run_id；未传 artifact_id 时可配合 kind 使用"},
            "capability": {
                "type": "string",
                "description": "数据能力 key 或别名；未传 artifact_id/run_id 时读取最近一次",
            },
            "kind": {
                "type": "string",
                "description": "artifact 类型，默认 raw-output",
            },
            "include_content": {
                "type": "boolean",
                "description": "是否返回解压后的 JSON 原文，默认 false",
            },
            "max_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": DATA_ARTIFACT_MAX_READ_BYTES,
                "default": DATA_ARTIFACT_DEFAULT_READ_BYTES,
            },
            "schema": {"type": "string", "description": "按 capability 读取最近一次时可选 schema_name 过滤"},
            "status": {"type": "string", "description": "按 capability 读取最近一次时执行状态过滤，默认 completed"},
        },
    }
    if require_capability:
        schema["required"] = ["capability"]
    return schema


def _samplebrand_daily_analysis_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "analysis_date": {
                "type": "string",
                "description": "要分析的北京时间自然日，YYYY-MM-DD；默认使用采集窗口 end_date。",
            },
            "run_id": {"type": "string", "description": "可选 collector run_id；不传读取最近一次成功采集。"},
            "artifact_id": {"type": "integer", "description": "可选 execution_artifacts.id，优先精确读取。"},
            "kind": {"type": "string", "description": "artifact 类型，默认 raw-output。"},
            "status": {"type": "string", "description": "最近一次查询的执行状态过滤，默认 completed。"},
            "schema": {"type": "string", "description": "最近一次查询时可选 schema_name 过滤。"},
            "include_zero_cost": {
                "type": "boolean",
                "description": "是否保留零消耗行，默认 false。",
            },
            "min_cost": {
                "type": "number",
                "description": "服务端预过滤最低消耗，默认 0；业务 Skill 可再用自己的阈值。",
            },
            "max_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50000,
                "default": 50000,
                "description": "最多返回目标日期视频消耗行数。",
            },
        },
    }


@dataclass
class CliPrincipal:
    user: User
    session: CodexCliSession


@dataclass
class RunPrincipal:
    user: User
    run: CodexDebugRun
    token: CodexRunToken
    session: CodexCliSession | None = None


@dataclass
class RuntimePrincipal:
    skill_id: str
    run_id: str
    run_mode: str
    user: User | None = None
    user_id: str = "runtime"
    claims: dict | None = None


def utc_safe_now():
    return now_bjt()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def new_secret() -> str:
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        str(token).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def pkce_challenge(verifier: str, method: str = "S256") -> str:
    if method.upper() == "PLAIN":
        return verifier
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_redirect_uri(uri: str) -> str:
    if uri == POLL_REDIRECT_URI:
        return uri
    parsed = urlparse(uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AppError("PARAM_INVALID", 400, {"detail": "redirect_uri 只允许本机 http callback"})
    if not parsed.port:
        raise AppError("PARAM_INVALID", 400, {"detail": "redirect_uri 必须包含随机本机端口"})
    return uri


def sanitize_device_name(value: str | None) -> str | None:
    text = "".join(ch for ch in str(value or "").strip() if ch.isprintable())
    return text[:64] or None


def assert_active_user(user: User) -> None:
    state = getattr(user, "state", None) or ("active" if getattr(user, "is_active", False) else "disabled")
    if state == "disabled" or not getattr(user, "is_active", True):
        raise AppError("USER_DISABLED", 403)
    if state != "active":
        raise AppError("AUTH_ACCOUNT_NOT_ACTIVE", 403)


async def create_login_intent(
    db: AsyncSession,
    *,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    code_challenge_method: str = "S256",
    nonce: str | None = None,
    device_name: str | None = None,
) -> CodexLoginIntent:
    method = (code_challenge_method or "S256").upper()
    if method not in {"S256", "PLAIN"}:
        raise AppError("PARAM_INVALID", 400, {"detail": "code_challenge_method 仅支持 S256/plain"})
    intent = CodexLoginIntent(
        id=new_id("login"),
        state=str(state or secrets.token_urlsafe(16))[:160],
        nonce=str(nonce or "")[:160] or None,
        redirect_uri=validate_redirect_uri(redirect_uri),
        code_challenge=str(code_challenge or "")[:160],
        code_challenge_method=method,
        device_name=sanitize_device_name(device_name),
        expires_at=utc_safe_now() + timedelta(seconds=LOGIN_INTENT_TTL_SECONDS),
    )
    if not intent.code_challenge:
        raise AppError("PARAM_INVALID", 400, {"detail": "缺少 code_challenge"})
    db.add(intent)
    await db.flush()
    return intent


async def authorize_login_intent(
    db: AsyncSession,
    *,
    login_intent_id: str,
    state: str,
    user: User,
) -> tuple[CodexLoginIntent, str]:
    intent = await db.get(CodexLoginIntent, login_intent_id)
    if not intent or intent.state != state:
        raise AppError("AUTH_REQUIRED", 401)
    if intent.used_at or intent.expires_at <= utc_safe_now():
        raise AppError("TOKEN_EXPIRED", 401)
    assert_active_user(user)
    code = new_secret()
    intent.user_id = user.id
    intent.auth_code_hash = token_hash(code)
    await db.flush()
    return intent, code


async def exchange_auth_code(
    db: AsyncSession,
    *,
    login_intent_id: str,
    auth_code: str,
    code_verifier: str,
) -> tuple[CodexCliSession, str, User]:
    intent = await db.get(CodexLoginIntent, login_intent_id)
    if not intent or intent.used_at or intent.expires_at <= utc_safe_now():
        raise AppError("TOKEN_EXPIRED", 401)
    expected = pkce_challenge(code_verifier, intent.code_challenge_method)
    if not hmac.compare_digest(expected, intent.code_challenge):
        raise AppError("AUTH_INVALID_CREDENTIALS", 401)
    if intent.redirect_uri == POLL_REDIRECT_URI:
        if not intent.user_id:
            raise AppError("TOKEN_PENDING", 401)
    elif not intent.auth_code_hash or not hmac.compare_digest(intent.auth_code_hash, token_hash(auth_code)):
        raise AppError("TOKEN_EXPIRED", 401)
    user = await db.get(User, intent.user_id)
    if not user:
        raise AppError("AUTH_REQUIRED", 401)
    assert_active_user(user)
    raw_token = new_secret()
    session = CodexCliSession(
        id=new_id("cli"),
        user_id=user.id,
        token_hash=token_hash(raw_token),
        device_name=intent.device_name,
        scopes_json={"source": "codex_cli"},
        permissions_rev_snapshot=int(getattr(user, "permissions_rev", 0) or 0),
        expires_at=utc_safe_now() + timedelta(days=CLI_SESSION_DAYS),
        last_seen_at=utc_safe_now(),
    )
    intent.used_at = utc_safe_now()
    db.add(session)
    await db.flush()
    await audit.log(user.id, "codex.auth.login", "codex_cli_session", session.id, detail={"device_name": session.device_name})
    return session, raw_token, user


async def authenticate_cli_session(db: AsyncSession, bearer_token: str) -> CliPrincipal:
    if not bearer_token:
        raise AppError("AUTH_REQUIRED", 401)
    row = (
        await db.execute(select(CodexCliSession).where(CodexCliSession.token_hash == token_hash(bearer_token)))
    ).scalar_one_or_none()
    if not row:
        raise AppError("AUTH_REQUIRED", 401)
    if row.revoked_at is not None:
        raise AppError("TOKEN_REVOKED", 401)
    if row.expires_at <= utc_safe_now():
        raise AppError("TOKEN_EXPIRED", 401)
    user = await db.get(User, row.user_id)
    if not user:
        raise AppError("AUTH_REQUIRED", 401)
    assert_active_user(user)
    if int(getattr(user, "permissions_rev", 0) or 0) != int(row.permissions_rev_snapshot or 0):
        row.revoked_at = utc_safe_now()
        row.revoked_reason = "permissions_rev_changed"
        await revoke_run_tokens_for_session(db, row.id)
        raise AppError("PERMISSION_REV_CHANGED", 403)
    row.last_seen_at = utc_safe_now()
    await db.flush()
    return CliPrincipal(user=user, session=row)


async def revoke_run_tokens_for_session(db: AsyncSession, cli_session_id: str) -> None:
    rows = (
        await db.execute(
            select(CodexRunToken).where(
                CodexRunToken.cli_session_id == cli_session_id,
                CodexRunToken.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    now = utc_safe_now()
    for row in rows:
        row.revoked_at = now
    await db.flush()


async def authenticate_run_token(db: AsyncSession, bearer_token: str) -> RunPrincipal:
    row = (
        await db.execute(select(CodexRunToken).where(CodexRunToken.token_hash == token_hash(bearer_token)))
    ).scalar_one_or_none()
    if not row:
        raise AppError("AUTH_REQUIRED", 401)
    if row.revoked_at is not None or row.expires_at <= utc_safe_now():
        raise AppError("DEBUG_RUN_EXPIRED", 401)
    run = await db.get(CodexDebugRun, row.debug_run_id)
    if not run or run.expires_at <= utc_safe_now() or run.status not in {"running", "completed"}:
        raise AppError("DEBUG_RUN_EXPIRED", 401)
    user = await db.get(User, row.user_id)
    if not user:
        raise AppError("AUTH_REQUIRED", 401)
    assert_active_user(user)
    session = await db.get(CodexCliSession, row.cli_session_id) if row.cli_session_id else None
    if session:
        if session.revoked_at is not None or session.expires_at <= utc_safe_now():
            row.revoked_at = utc_safe_now()
            raise AppError("DEBUG_RUN_EXPIRED", 401)
        if int(getattr(user, "permissions_rev", 0) or 0) != int(session.permissions_rev_snapshot or 0):
            row.revoked_at = utc_safe_now()
            session.revoked_at = utc_safe_now()
            session.revoked_reason = "permissions_rev_changed"
            raise AppError("PERMISSION_REV_CHANGED", 403)
    return RunPrincipal(user=user, run=run, token=row, session=session)


async def authenticate_runtime_run_token(db: AsyncSession, bearer_token: str) -> RuntimePrincipal:
    from app.execution.execution_service import verify_run_token
    from app.execution.models import ExecutionRun

    claims = verify_run_token(bearer_token)
    run_id = str(claims.get("run_id") or "")
    skill_id = str(claims.get("skill_id") or "")
    if not run_id or not skill_id:
        raise AppError("RUN_TOKEN_INVALID", 401)
    run = await db.get(ExecutionRun, run_id)
    if not run or str(run.skill_id or "") != skill_id or run.status != "running":
        raise AppError("RUN_TOKEN_INVALID", 401)
    user = await _resolve_runtime_skill_actor(db, skill_id=skill_id)
    return RuntimePrincipal(
        skill_id=skill_id,
        run_id=run_id,
        run_mode=str(run.run_mode or "scheduled_real"),
        user=user,
        user_id=user.id,
        claims=claims,
    )


async def _resolve_runtime_skill_actor(db: AsyncSession, *, skill_id: str) -> User:
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    owner_member_user_ids = (
        await db.execute(
            select(SkillMember.user_id)
            .where(SkillMember.skill_id == skill_id, SkillMember.role == "owner")
            .order_by(SkillMember.granted_at.asc())
        )
    ).scalars().all()
    for user_id in owner_member_user_ids:
        user = await db.get(User, user_id)
        if _is_active_runtime_user(user):
            return user

    owner_id = str(getattr(skill, "owner", None) or "").strip()
    if owner_id:
        user = await db.get(User, owner_id)
        if _is_active_runtime_user(user):
            return user

    raise AppError(
        "RUNTIME_ACTOR_NOT_FOUND",
        403,
        {
            "skill_id": skill_id,
            "detail": "Skill 运行态调用平台内置 MCP 需要一个 active 的 owner 成员或 Skill.owner 用户",
        },
    )


def _is_active_runtime_user(user: User | None) -> bool:
    if user is None:
        return False
    state = getattr(user, "state", None) or ("active" if getattr(user, "is_active", False) else "disabled")
    return state == "active" and bool(getattr(user, "is_active", True))


async def create_debug_run(
    db: AsyncSession,
    principal: CliPrincipal,
    *,
    skill_id: str,
    run_mode: str = "local_debug",
    package_hash: str | None = None,
    manifest: dict | None = None,
    input_json: dict | None = None,
    requested_tools: list[str] | None = None,
    limits: dict | None = None,
) -> tuple[CodexDebugRun, str]:
    limits = {**DEFAULT_DEBUG_LIMITS, **(limits or {})}
    run = CodexDebugRun(
        id=new_id("debug"),
        cli_session_id=principal.session.id,
        user_id=principal.user.id,
        skill_id=skill_id,
        run_mode=run_mode,
        package_hash=package_hash,
        manifest_json=manifest or {},
        input_json=input_json or {},
        requested_tools_json=requested_tools or [],
        output_mode="preview_only",
        limits_json=limits,
        status="running",
        expires_at=utc_safe_now() + timedelta(seconds=int(limits.get("timeout_seconds") or 300)),
    )
    raw_token = new_secret()
    run_token = CodexRunToken(
        id=new_id("run"),
        token_hash=token_hash(raw_token),
        cli_session_id=principal.session.id,
        debug_run_id=run.id,
        user_id=principal.user.id,
        skill_id=skill_id,
        scopes_json=list((manifest or {}).get("mcp_scopes") or []),
        shop_ids_json=[],
        expires_at=min(run.expires_at, principal.session.expires_at, utc_safe_now() + timedelta(seconds=RUN_TOKEN_TTL_SECONDS)),
    )
    db.add(run)
    db.add(run_token)
    await db.flush()
    return run, raw_token


async def create_implicit_mcp_run(db: AsyncSession, principal: CliPrincipal, *, skill_id: str | None) -> RunPrincipal:
    run, raw = await create_debug_run(
        db,
        principal,
        skill_id=skill_id or "codex-mcp-stdio",
        run_mode="mcp_session",
        manifest={},
        requested_tools=[],
    )
    return await authenticate_run_token(db, raw)


def _load_tool_registry() -> dict[str, Any]:
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import skillforge_mcp_runtime as runtime
    except Exception as exc:  # noqa: BLE001
        logger.warning("load mcp registry failed: {}", exc)
        return {}

    registry: dict[str, Any] = {}
    try:
        runtime.ensure_default_tools_registered()
        for module_name in ("yuyidata_mcp_server",):
            try:
                runtime._import_script_module(module_name)
            except Exception:
                logger.debug("codex mcp catalog skipped module {}", module_name)
        registry = {meta.tool_name: meta for meta in runtime.TOOL_REGISTRY.values()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("load script mcp registry failed: {}", exc)
    registry.update(_builtin_mcp_tool_registry(runtime.ToolMeta))
    return registry


def _builtin_mcp_tool_registry(tool_meta_cls) -> dict[str, Any]:
    tools = [
        tool_meta_cls(
            tool_name="skillforge_data_capability_list",
            platform="skillforge",
            data_scope="data.capabilities",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.capabilities",
            description="列出当前账号可读取的平台缓存数据能力，包括 Tmall 和语艺原始数据能力。",
            input_schema=_data_capability_list_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_data_capability_latest",
            platform="skillforge",
            data_scope="data.capabilities",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.capabilities",
            description="按数据能力 key 查询最近一次平台缓存的原始 JSON Artifact 摘要、大小、sha256 和 run 引用。",
            input_schema=_data_capability_latest_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_data_artifact_get",
            platform="skillforge",
            data_scope="data.artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.artifacts",
            description="受控读取平台缓存数据 Artifact；默认只返回摘要，include_content=true 且未超限时才返回解压 JSON。",
            input_schema=_data_artifact_get_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_sf_data_write",
            platform="skillforge",
            data_scope="sf.data_store",
            endpoint_family="sf_data_store",
            warning_group="sf_data_store",
            requires_shop_id=False,
            write=True,
            source_id="skillforge.sf_data.records",
            description="向 SkillForge 通用 SF 数据库写入 JSON、文本、表格或大型 artifact 引用；真实写入需要 dry_run=false 和 idempotency_key。",
            input_schema=_sf_data_write_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_sf_data_list",
            platform="skillforge",
            data_scope="sf.data_store",
            endpoint_family="sf_data_store",
            warning_group="sf_data_store",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.sf_data.records",
            description="分页查询已写入的通用 SF 数据记录，支持 namespace、Skill、Run、类型和关键词过滤。",
            input_schema=_sf_data_list_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_sf_data_get",
            platform="skillforge",
            data_scope="sf.data_store",
            endpoint_family="sf_data_store",
            warning_group="sf_data_store",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.sf_data.records",
            description="读取单条通用 SF 数据记录详情、预览、inline JSON 和 artifact 引用。",
            input_schema=_sf_data_get_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_samplebrand_cloud_video_data_latest",
            platform="cloud_video",
            data_scope="cloud_video.samplebrand_weekly.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.cloud_video.samplebrand_weekly",
            description="读取示例品牌云视频周度全量采集 Skill 最近一次平台缓存原始数据摘要。",
            input_schema=_data_capability_latest_schema(require_capability=False),
        ),
        tool_meta_cls(
            tool_name="skillforge_samplebrand_cloud_video_data_get",
            platform="cloud_video",
            data_scope="cloud_video.samplebrand_weekly.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.cloud_video.samplebrand_weekly",
            description="受控读取示例品牌云视频周度全量采集缓存 JSON；默认只返回摘要，显式 include_content 才返回原文。",
            input_schema=_data_artifact_get_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_samplebrand_cloud_video_daily_analysis_input",
            platform="cloud_video",
            data_scope="cloud_video.samplebrand_weekly.daily_analysis_input",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.cloud_video.samplebrand_weekly.daily_analysis_input",
            description="从示例品牌云视频周度全量采集缓存中按日期过滤出日诊断所需的视频消耗行和视频元数据，不重新采集外部数据。",
            input_schema=_samplebrand_daily_analysis_input_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_tmall_link_decline_data_latest",
            platform="tmall",
            data_scope="tmall.link_decline.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.tmall",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.tmall.link_decline",
            description="读取天猫链接下滑每日采集 Skill 最近一次平台缓存原始数据摘要。",
            input_schema=_data_capability_latest_schema(require_capability=False),
        ),
        tool_meta_cls(
            tool_name="skillforge_tmall_link_decline_data_get",
            platform="tmall",
            data_scope="tmall.link_decline.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.tmall",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.tmall.link_decline",
            description="受控读取天猫链接下滑每日采集缓存原始 JSON；默认只返回摘要，显式 include_content 才返回原文。",
            input_schema=_data_artifact_get_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_yuyidata_customer_service_data_latest",
            platform="yuyidata",
            data_scope="yuyidata.customer_service.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.yuyidata",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.yuyidata.customer_service",
            description="读取语艺客服会话每日数据 Skill 最近一次平台缓存原始数据摘要。",
            input_schema=_data_capability_latest_schema(require_capability=False),
        ),
        tool_meta_cls(
            tool_name="skillforge_yuyidata_customer_service_data_get",
            platform="yuyidata",
            data_scope="yuyidata.customer_service.cached_artifacts",
            endpoint_family="cached_data_artifact_read",
            warning_group="data_capability.yuyidata",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.data.yuyidata.customer_service",
            description="受控读取语艺客服会话每日缓存原始 JSON；默认只返回摘要，显式 include_content 才返回原文。",
            input_schema=_data_artifact_get_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_execution_artifact_summary",
            platform="skillforge",
            data_scope="execution.artifacts",
            endpoint_family="execution_artifact_read",
            warning_group="execution_artifacts",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.execution_artifacts",
            description="按 run_id 查询平台已保存的执行原始数据 Artifact 摘要、大小、sha256 和存储引用；默认不返回完整原文。",
            input_schema=_execution_artifact_summary_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_execution_artifact_latest",
            platform="skillforge",
            data_scope="execution.artifacts",
            endpoint_family="execution_artifact_read",
            warning_group="execution_artifacts",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.execution_artifacts",
            description="按 skill_id 查询最近一次成功执行保存的原始数据 Artifact 摘要、大小、sha256 和 run 引用。",
            input_schema=_execution_artifact_latest_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_org_search_users",
            platform="skillforge",
            data_scope="org.users",
            endpoint_family="org_user_search",
            warning_group="org_directory",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.org.users",
            description="查询组织成员；平台用户未命中时可按姓名或钉钉 userId 兜底查询钉钉通讯录。",
            input_schema=_org_search_users_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_org_list_members",
            platform="skillforge",
            data_scope="org.users",
            endpoint_family="org_members",
            warning_group="org_directory",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.org.members",
            description="列出部门或组织单元内当前账号可见的成员。",
            input_schema=_org_list_members_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_dingtalk_send_work_notice",
            platform="skillforge",
            data_scope="dingtalk.work_notice",
            endpoint_family="dingtalk_work_notice",
            warning_group="org_push",
            requires_shop_id=False,
            write=True,
            source_id="skillforge.dingtalk.outbox",
            description="向组织成员发送钉钉工作通知。真实推送需要 dry_run=false 且提供 idempotency_key。",
            input_schema=_dingtalk_work_notice_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_qianchuan_video_content_analysis",
            platform="qianchuan",
            data_scope="qianchuan.video_content_analysis",
            endpoint_family="qianchuan_video_content_analysis",
            warning_group="qianchuan",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.qianchuan.video_content_analysis",
            description="读取千川素材分析-视频素材-推商品-点击里的单视频内容分析：互动时序、整体点击次数、脚本文本、素材标签和 benchmark 标签缺口。",
            input_schema=qianchuan_video_content_analysis_input_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_agent_coverage",
            platform="skillforge",
            data_scope="platform.agent_coverage",
            endpoint_family="agent_department_coverage",
            warning_group="agent_coverage",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.agent.coverage",
            description="查询当前账号可见部门的执行/分析/训练 Agent 覆盖度、在线状态和平台兜底状态。",
            input_schema=_agent_coverage_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_ai_analyze",
            platform="skillforge",
            data_scope="platform.ai",
            endpoint_family="platform_ai_analyze",
            warning_group="platform_ai",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.ai.deepseek_v4_pro_1m",
            description="通过平台后台 AI 配置调用 DeepSeek V4 Pro 1M 上下文模型做分析，不向本地暴露模型密钥。",
            input_schema=_platform_ai_analyze_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_raw_data_query",
            platform="skillforge",
            data_scope="platform.raw_data",
            endpoint_family="skill_run_raw_data",
            warning_group="platform_raw_data",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.raw_data.query",
            description="按权限查询 Skill 运行后的原始记录，包括 execution_runs、steps、decision_log、collection proof 和 schema snapshot。",
            input_schema=_raw_data_query_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_run_analyze",
            platform="skillforge",
            data_scope="platform.run_analysis",
            endpoint_family="skill_run_analysis",
            warning_group="platform_ai",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.run.analyze",
            description="一键读取某次 Skill 运行后的脱敏原始数据，并调用平台 DeepSeek V4 Pro 1M 上下文模型生成运行复盘。",
            input_schema=_run_analyze_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_session",
            platform="cloud_video",
            data_scope="cloud_video.session",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.session",
            description="检查并刷新云视频服务端登录态；账号凭据只保存在 SkillForge 服务端配置中。",
            input_schema=_cloud_video_session_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_accounts",
            platform="cloud_video",
            data_scope="cloud_video.accounts",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.accounts",
            description="读取云视频团队-分组-人员树，用于报表 idStr 和按人分析。",
            input_schema=_cloud_video_accounts_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_categories",
            platform="cloud_video",
            data_scope="cloud_video.categories",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.categories",
            description="读取云视频素材库分类树，用于定位示例品牌、产品线和素材类型。",
            input_schema=_cloud_video_categories_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_tags",
            platform="cloud_video",
            data_scope="cloud_video.tags",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.tags",
            description="按云视频分类读取标签树，用于素材筛选和分析维度补齐。",
            input_schema=_cloud_video_tags_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_videos",
            platform="cloud_video",
            data_scope="cloud_video.videos",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.videos",
            description="搜索或分页读取云视频素材元数据，默认不返回视频/封面原始 URL。",
            input_schema=_cloud_video_videos_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_visual_analysis",
            platform="cloud_video",
            data_scope="cloud_video.visual_analysis",
            endpoint_family="cloud_video_visual_analysis",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.visual_analysis",
            description="平台服务端按云视频 videoId 拉取同主题低消耗/高质量素材，调用视觉模型生成脱敏诊断；高质量同主题样本按 topic_key+video_id 缓存复用。",
            input_schema=_cloud_video_visual_analysis_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_ad_report",
            platform="cloud_video",
            data_scope="cloud_video.ad_report",
            endpoint_family="cloud_video_report_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.ad_report",
            description="读取云视频投放报表的个人或明细页，默认查询 2026-05-01 至 2026-06-09 巨量千川汇总。",
            input_schema=_cloud_video_ad_report_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_material_report",
            platform="cloud_video",
            data_scope="cloud_video.material_report",
            endpoint_family="cloud_video_report_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.material_report",
            description="读取云视频广告平台分析页素材统计，按团队/分组/上传人/分类返回素材数、消耗和标签统计。",
            input_schema=_cloud_video_material_report_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_video_usage_report",
            platform="cloud_video",
            data_scope="cloud_video.video_usage_report",
            endpoint_family="cloud_video_report_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.video_usage_report",
            description="读取云视频视频统计报表，按团队/分组/个人输出上传条数、下载、推送、剪映和爆款等使用统计。",
            input_schema=_cloud_video_video_usage_report_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_audit_rejects",
            platform="cloud_video",
            data_scope="cloud_video.audit_rejects",
            endpoint_family="cloud_video_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.audit_rejects",
            description="按视频读取云视频平台审核拒因，支持巨量千川/巨量广告/腾讯ADQ和卡审中/历史卡审状态。",
            input_schema=_cloud_video_audit_rejects_schema(),
        ),
        tool_meta_cls(
            tool_name="skillforge_cloud_video_daily_person_video_report",
            platform="cloud_video",
            data_scope="cloud_video.daily_person_video_report",
            endpoint_family="cloud_video_report_read",
            warning_group="cloud_video",
            requires_shop_id=False,
            write=False,
            source_id="skillforge.cloud_video.daily_person_video_report",
            description="按天读取并聚合云视频人员消耗和视频消耗明细，输出可供短视频项目生成个人改进方案的数据集。",
            input_schema=_cloud_video_daily_person_video_report_schema(),
        ),
    ]
    return {meta.tool_name: meta for meta in tools}


def _generic_mcp_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shop_id": {"type": "string"},
            "limit": {"type": "integer"},
            "dateType": {"type": "string"},
        },
    }


def mcp_catalog_for_user(user: User | None) -> dict:
    tools = []
    for meta in sorted(_load_tool_registry().values(), key=lambda item: item.tool_name):
        tools.append(
            {
                "name": meta.tool_name,
                "description": meta.description or meta.tool_name,
                "inputSchema": getattr(meta, "input_schema", None) or _generic_mcp_input_schema(),
                "meta": {
                    "platform": meta.platform,
                    "data_scope": meta.data_scope,
                    "endpoint_family": meta.endpoint_family,
                    "warning_group": meta.warning_group,
                    "requires_shop_id": meta.requires_shop_id,
                    "write": bool(getattr(meta, "write", False)),
                },
            }
        )
    return {"servers": [{"name": "skillforge", "tools": tools}]}


def mcp_catalog_snapshot() -> dict:
    return mcp_catalog_for_user(None)


async def list_visible_skills(db: AsyncSession, user: User, scope: str) -> list[dict]:
    access_filter = await build_skill_access_filter(db, user, "read")
    rows = (
        await db.execute(
            select(Skill)
            .where(access_filter)
            .where(Skill.status != "deprecated")
            .order_by(Skill.updated_at.desc())
            .limit(500)
        )
    ).scalars().all()
    department_scope = await _codex_department_scope(db, user) if scope == "department" else None
    items = []
    for skill in rows:
        perms = await get_skill_permissions(db, skill, user)
        if scope == "editable" and not perms.get("edit"):
            continue
        if scope == "publishable" and not (perms.get("publish") or perms.get("review")):
            continue
        if scope == "mine" and skill.owner != user.id and not perms.get("edit"):
            continue
        if scope == "department" and department_scope is not None:
            if not _skill_matches_codex_department_scope(skill, *department_scope):
                continue
        items.append(
            {
                "skill_id": skill.id,
                "name": skill.name,
                "department": skill.department,
                "visibility": skill.visibility,
                "can_edit": bool(perms.get("edit")),
                "can_submit": bool(perms.get("edit") or perms.get("review") or perms.get("publish")),
                "can_publish": bool(perms.get("publish")),
                "git_commit_full": skill.git_commit,
                "latest_review": None,
                "updated_at": isoformat_bjt(skill.updated_at),
            }
        )
    return items


async def _codex_department_scope(db: AsyncSession, user: User) -> tuple[set[str], set[str], bool]:
    if role_matches_any(user, ("admin",)) or bool(getattr(user, "can_view_all", False)):
        return set(), set(), True
    accessible = await get_accessible_departments(db, user)
    if accessible is None:
        return set(), set(), True
    org_ids = {item for item in accessible if isinstance(item, str) and item}
    department_names = await _org_names_for_ids(db, org_ids)
    user_department = str(getattr(user, "department", "") or "").strip()
    if user_department:
        department_names.add(user_department)
    return org_ids, department_names, False


def _skill_matches_codex_department_scope(
    skill: Skill,
    org_ids: set[str],
    department_names: set[str],
    read_all: bool,
) -> bool:
    if read_all:
        return True
    skill_org_id = str(getattr(skill, "org_unit_id", "") or "").strip()
    if skill_org_id and skill_org_id in org_ids:
        return True
    skill_department = str(getattr(skill, "department", "") or "").strip()
    return bool(skill_department and skill_department in department_names)


def _bool_arg(args: dict, *names: str, default: bool = False) -> bool:
    for name in names:
        if name in args:
            value = args.get(name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return bool(value)
    return default


def _list_arg(args: dict, *names: str) -> list[str]:
    raw = None
    for name in names:
        if name in args:
            raw = args.get(name)
            break
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw).split(",")
    return [str(item).strip() for item in values if str(item).strip()]


def _text_arg(args: dict, *names: str) -> str:
    for name in names:
        if name in args and args.get(name) is not None:
            return str(args.get(name)).strip()
    return ""


def _int_arg(args: dict, name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(args.get(name) or default)
    except (TypeError, ValueError):
        raise AppError("PARAM_INVALID", 400, {"field": name, "reason": "must be integer"})
    return min(max(value, minimum), maximum)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _org_ids_for_filter(db: AsyncSession, org_unit_id: str, include_subtree: bool) -> set[str]:
    org = await db.get(OrgUnit, org_unit_id)
    if not org:
        return set()
    ids = {org.id}
    if include_subtree and org.path:
        safe_path = _escape_like(org.path.rstrip("/"))
        rows = (
            await db.execute(
                select(OrgUnit.id).where(
                    or_(OrgUnit.id == org.id, OrgUnit.path.like(f"{safe_path}/%", escape="\\"))
                )
            )
        ).scalars().all()
        ids.update(rows)
    return ids


async def _org_names_for_ids(db: AsyncSession, org_ids: set[str]) -> set[str]:
    if not org_ids:
        return set()
    return set((await db.execute(select(OrgUnit.name).where(OrgUnit.id.in_(org_ids)))).scalars().all())


async def _visible_user_scope_conditions(db: AsyncSession, user: User) -> list[Any]:
    if role_matches_any(user, ("admin",)) or bool(getattr(user, "can_view_all", False)):
        return []

    accessible = await get_accessible_departments(db, user)
    conditions = []
    if accessible:
        conditions.append(
            User.id.in_(
                select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id.in_(list(accessible)))
            )
        )
        org_names = await _org_names_for_ids(db, accessible)
        if org_names:
            conditions.append(User.department.in_(list(org_names)))
    if getattr(user, "department", None):
        conditions.append(User.department == user.department)
    conditions.append(User.id == user.id)
    return [or_(*conditions)]


async def _member_orgs_for_users(db: AsyncSession, users: list[User]) -> dict[str, list[dict]]:
    user_ids = [row.id for row in users]
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(
                UserOrgMembership.user_id,
                UserOrgMembership.membership_type,
                UserOrgMembership.is_manager,
                OrgUnit.id,
                OrgUnit.name,
                OrgUnit.type,
            )
            .join(OrgUnit, OrgUnit.id == UserOrgMembership.org_unit_id)
            .where(UserOrgMembership.user_id.in_(user_ids))
            .order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc())
        )
    ).all()
    by_user: dict[str, list[dict]] = {}
    for user_id, membership_type, is_manager, org_id, org_name, org_type in rows:
        by_user.setdefault(user_id, []).append(
            {
                "id": org_id,
                "name": org_name,
                "type": org_type,
                "membership_type": membership_type,
                "is_manager": bool(is_manager),
            }
        )
    return by_user


def _serialize_org_user(user: User, orgs_by_user: dict[str, list[dict]]) -> dict:
    orgs = orgs_by_user.get(user.id) or []
    return {
        "user_id": user.id,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "department": user.department or (orgs[0]["name"] if orgs else None),
        "org_units": orgs,
        "dingtalk_user_id": user.dingtalk_user_id,
        "can_receive_dingtalk": bool(user.dingtalk_user_id),
    }


def _normalize_dingtalk_dept_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, int)):
        raw_values = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _dingtalk_contact_matches_query(contact: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    needle = query.lower()
    raw = contact.get("raw") if isinstance(contact.get("raw"), dict) else {}
    haystack = [
        contact.get("user_id"),
        contact.get("name"),
        contact.get("email"),
        contact.get("mobile"),
        raw.get("title"),
        raw.get("job_number"),
        raw.get("jobNumber"),
        raw.get("unionid"),
        raw.get("unionId"),
    ]
    return any(needle in str(item or "").lower() for item in haystack)


def _serialize_dingtalk_contact(contact: dict[str, Any], dept_names: dict[str, str]) -> dict:
    dingtalk_user_id = str(contact.get("user_id") or "").strip()
    raw = contact.get("raw") if isinstance(contact.get("raw"), dict) else {}
    dept_ids = _normalize_dingtalk_dept_ids(
        contact.get("dept_id_list")
        or contact.get("department_ids")
        or raw.get("dept_id_list")
        or raw.get("deptIdList")
        or raw.get("department")
    )
    org_units = [
        {
            "id": dept_id,
            "name": dept_names.get(dept_id) or dept_id,
            "type": "department",
            "membership_type": "dingtalk",
            "is_manager": False,
        }
        for dept_id in dept_ids
    ]
    department = org_units[0]["name"] if org_units else None
    return {
        "user_id": f"dingtalk:{dingtalk_user_id}",
        "name": str(contact.get("name") or dingtalk_user_id),
        "username": f"dingtalk_{dingtalk_user_id}",
        "role": "dingtalk_contact",
        "department": department,
        "org_units": org_units,
        "dingtalk_user_id": dingtalk_user_id,
        "can_receive_dingtalk": bool(dingtalk_user_id),
        "source": "dingtalk_contact",
    }


def _serialize_cached_dingtalk_contact(user: User, orgs_by_user: dict[str, list[dict]]) -> dict | None:
    dingtalk_user_id = str(user.dingtalk_user_id or "").strip()
    if not dingtalk_user_id:
        return None
    orgs = orgs_by_user.get(user.id) or []
    return {
        "user_id": f"dingtalk:{dingtalk_user_id}",
        "name": user.name or dingtalk_user_id,
        "username": f"dingtalk_{dingtalk_user_id}",
        "role": "dingtalk_contact",
        "department": user.department or (orgs[0]["name"] if orgs else None),
        "org_units": orgs,
        "dingtalk_user_id": dingtalk_user_id,
        "can_receive_dingtalk": True,
        "source": "dingtalk_contact_cache",
    }


def _int_arg(args: dict, key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(args.get(key) if args.get(key) is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _serialize_execution_artifact(row: ExecutionArtifact, run: ExecutionRun | None = None) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "skill_id": row.skill_id,
        "decision_log_id": row.decision_log_id,
        "kind": row.kind,
        "schema": row.schema_name,
        "storage_backend": row.storage_backend,
        "storage_path": row.storage_path,
        "media_type": row.media_type,
        "encoding": row.encoding,
        "size_bytes": row.size_bytes,
        "uncompressed_size_bytes": row.uncompressed_size_bytes,
        "sha256": row.sha256,
        "summary": row.summary_json or {},
        "run_status": getattr(run, "status", None) if run else None,
        "run_started_at": isoformat_bjt(run.started_at) if run and run.started_at else None,
        "run_completed_at": isoformat_bjt(run.completed_at) if run and run.completed_at else None,
        "created_at": isoformat_bjt(row.created_at) if row.created_at else None,
    }


def _resolve_data_capability_key(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        raise AppError("PARAM_INVALID", 400, {"detail": "capability is required"})
    key = DATA_CAPABILITY_ALIASES.get(text) or DATA_CAPABILITY_ALIASES.get(text.lower())
    if not key:
        raise AppError("DATA_CAPABILITY_NOT_FOUND", 404, {"capability": text})
    return key


def _serialize_data_capability(capability: DataCapability, *, readable_skill_ids: list[str] | None = None) -> dict:
    source_skill_ids = list(capability.source_skill_ids)
    readable = readable_skill_ids if readable_skill_ids is not None else source_skill_ids
    return {
        "capability": capability.key,
        "platform": capability.platform,
        "data_scope": capability.data_scope,
        "display_name": capability.display_name,
        "description": capability.description,
        "default_kind": capability.default_kind,
        "source_skill_ids": source_skill_ids,
        "readable_source_skill_ids": readable,
        "available": bool(readable),
        "aliases": list(capability.aliases),
    }


async def _readable_capability_skill_ids(db: AsyncSession, user: User, capability: DataCapability) -> list[str]:
    readable: list[str] = []
    for skill_id in capability.source_skill_ids:
        skill = await db.get(Skill, skill_id)
        if skill is None:
            continue
        perms = await get_skill_permissions(db, skill, user)
        if perms.get("read") or perms.get("execute") or perms.get("edit") or perms.get("publish") or perms.get("review"):
            readable.append(skill_id)
    return readable


async def _require_capability_readable(db: AsyncSession, user: User, capability: DataCapability) -> list[str]:
    readable = await _readable_capability_skill_ids(db, user, capability)
    if not readable:
        raise AppError(
            "DATA_CAPABILITY_ACCESS_DENIED",
            403,
            {
                "capability": capability.key,
                "source_skill_ids": list(capability.source_skill_ids),
            },
        )
    return readable


async def _latest_artifact_rows_for_capability(
    db: AsyncSession,
    user: User,
    capability: DataCapability,
    args: dict,
    *,
    limit: int | None = None,
) -> tuple[list[tuple[ExecutionArtifact, ExecutionRun]], list[str], str]:
    readable_skill_ids = await _require_capability_readable(db, user, capability)
    kind = _text_arg(args, "kind") or capability.default_kind
    schema = _text_arg(args, "schema", "schema_name", "schemaName")
    status = _text_arg(args, "status") or "completed"
    effective_limit = limit if limit is not None else _int_arg(args, "limit", 1, minimum=1, maximum=20)
    stmt = (
        select(ExecutionArtifact, ExecutionRun)
        .join(ExecutionRun, ExecutionRun.id == ExecutionArtifact.run_id)
        .where(ExecutionArtifact.skill_id.in_(readable_skill_ids))
        .where(ExecutionArtifact.kind == kind)
        .order_by(ExecutionRun.completed_at.desc().nullslast(), ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
        .limit(effective_limit)
    )
    if schema:
        stmt = stmt.where(ExecutionArtifact.schema_name == schema)
    if status:
        stmt = stmt.where(ExecutionRun.status == status)
    rows = (await db.execute(stmt)).all()
    return rows, readable_skill_ids, kind


async def _builtin_data_capability_list(db: AsyncSession, user: User, args: dict) -> dict:
    platform = _text_arg(args, "platform")
    include_unavailable = _bool_arg(args, "include_unavailable", "includeUnavailable", default=False)
    items = []
    for capability in DATA_CAPABILITIES.values():
        if platform and capability.platform != platform:
            continue
        readable = await _readable_capability_skill_ids(db, user, capability)
        if not include_unavailable and not readable:
            continue
        items.append(_serialize_data_capability(capability, readable_skill_ids=readable))
    return {
        "count": len(items),
        "items": items,
    }


async def _builtin_data_capability_latest(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    capability_key: str | None = None,
) -> dict:
    key = capability_key or _resolve_data_capability_key(_text_arg(args, "capability", "data_capability", "dataCapability"))
    capability = DATA_CAPABILITIES[key]
    rows, readable_skill_ids, kind = await _latest_artifact_rows_for_capability(db, user, capability, args)
    return {
        **_serialize_data_capability(capability, readable_skill_ids=readable_skill_ids),
        "kind": kind,
        "count": len(rows),
        "items": [_serialize_execution_artifact(artifact, run) for artifact, run in rows],
    }


async def _artifact_by_id_or_run(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    capability: DataCapability | None = None,
) -> tuple[ExecutionArtifact, ExecutionRun | None, DataCapability | None, list[str]]:
    artifact_id_raw = args.get("artifact_id") if args.get("artifact_id") is not None else args.get("artifactId")
    readable_skill_ids: list[str] = []
    if artifact_id_raw is not None:
        try:
            artifact_id = int(artifact_id_raw)
        except (TypeError, ValueError):
            raise AppError("PARAM_INVALID", 400, {"detail": "artifact_id must be integer"}) from None
        row = await db.get(ExecutionArtifact, artifact_id)
        if row is None:
            raise AppError("NOT_FOUND", 404, {"artifact_id": artifact_id})
        if capability is not None:
            readable_skill_ids = await _require_capability_readable(db, user, capability)
            if row.skill_id not in readable_skill_ids:
                raise AppError("DATA_CAPABILITY_ACCESS_DENIED", 403, {"capability": capability.key})
        else:
            await _assert_artifact_skill_readable(db, user, row.skill_id)
        run = await db.get(ExecutionRun, row.run_id)
        return row, run, capability, readable_skill_ids

    run_id = _text_arg(args, "run_id", "runId")
    if run_id:
        run = await db.get(ExecutionRun, run_id)
        if run is None or not run.skill_id:
            raise AppError("NOT_FOUND", 404, {"run_id": run_id})
        if capability is not None:
            readable_skill_ids = await _require_capability_readable(db, user, capability)
            if run.skill_id not in readable_skill_ids:
                raise AppError("DATA_CAPABILITY_ACCESS_DENIED", 403, {"capability": capability.key})
        else:
            await _assert_artifact_skill_readable(db, user, run.skill_id)
        kind = _text_arg(args, "kind") or (capability.default_kind if capability else "raw-output")
        row = (
            await db.execute(
                select(ExecutionArtifact)
                .where(ExecutionArtifact.run_id == run_id)
                .where(ExecutionArtifact.kind == kind)
                .order_by(ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AppError("NOT_FOUND", 404, {"run_id": run_id, "kind": kind})
        return row, run, capability, readable_skill_ids

    if capability is None:
        key = _resolve_data_capability_key(_text_arg(args, "capability", "data_capability", "dataCapability"))
        capability = DATA_CAPABILITIES[key]
    rows, readable_skill_ids, _kind = await _latest_artifact_rows_for_capability(db, user, capability, args, limit=1)
    if not rows:
        raise AppError("NOT_FOUND", 404, {"capability": capability.key})
    row, run = rows[0]
    return row, run, capability, readable_skill_ids


def _safe_artifact_path(storage_path: str) -> Path:
    rel = PurePosixPath(str(storage_path or ""))
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise AppError("ARTIFACT_STORAGE_PATH_INVALID", 500)
    path = artifact_abs_path(str(rel))
    return path


def _read_artifact_json_content(row: ExecutionArtifact, *, max_bytes: int) -> tuple[Any, bool, str | None]:
    if row.storage_backend != "local":
        return None, True, "storage_backend_not_supported"
    if row.uncompressed_size_bytes and row.uncompressed_size_bytes > max_bytes:
        return None, True, "uncompressed_size_exceeds_max_bytes"
    path = _safe_artifact_path(row.storage_path)
    if not path.is_file():
        return None, True, "artifact_file_missing"
    raw = path.read_bytes()
    if row.encoding == "gzip" or str(row.media_type or "").endswith("+gzip"):
        raw = gzip.decompress(raw)
    if len(raw) > max_bytes:
        return None, True, "content_exceeds_max_bytes"
    try:
        return json.loads(raw.decode("utf-8")), False, None
    except Exception:  # noqa: BLE001
        return raw.decode("utf-8", errors="replace"), False, None


async def _builtin_data_artifact_get(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    capability_key: str | None = None,
) -> dict:
    if not capability_key:
        requested_capability = _text_arg(args, "capability", "data_capability", "dataCapability")
        capability_key = _resolve_data_capability_key(requested_capability) if requested_capability else None
    capability = DATA_CAPABILITIES[capability_key] if capability_key else None
    row, run, capability, readable_skill_ids = await _artifact_by_id_or_run(db, user, args, capability=capability)
    include_content = _bool_arg(args, "include_content", "includeContent", default=False)
    max_bytes = _int_arg(
        args,
        "max_bytes",
        DATA_ARTIFACT_DEFAULT_READ_BYTES,
        minimum=1,
        maximum=DATA_ARTIFACT_MAX_READ_BYTES,
    )
    artifact = _serialize_execution_artifact(row, run)
    data: dict[str, Any] = {
        "artifact": artifact,
        "include_content": include_content,
        "max_bytes": max_bytes,
    }
    if capability is not None:
        if not readable_skill_ids:
            readable_skill_ids = await _readable_capability_skill_ids(db, user, capability)
        data["capability"] = _serialize_data_capability(capability, readable_skill_ids=readable_skill_ids)
    if include_content:
        content, omitted, reason = _read_artifact_json_content(row, max_bytes=max_bytes)
        data["content_omitted"] = omitted
        if reason:
            data["content_omitted_reason"] = reason
        if not omitted:
            if isinstance(content, (dict, list)):
                data["content_json"] = content
            else:
                data["content_text"] = str(content)
    else:
        data["content_omitted"] = True
        data["content_omitted_reason"] = "include_content_false"
    return data


def _parse_iso_date_arg(value: str | None, *, field: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise AppError("PARAM_INVALID", 400, {"detail": f"{field} must be YYYY-MM-DD"}) from exc


def _video_id_from_daily_row(row: dict[str, Any]) -> str:
    for key in ("video_id", "videoId", "material_id", "materialId", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _qianchuan_lifecycle_video_id(item: dict[str, Any]) -> str:
    cloud_video = item.get("cloud_video") if isinstance(item.get("cloud_video"), dict) else {}
    return str(
        item.get("video_id")
        or item.get("cloud_video_id")
        or cloud_video.get("video_id")
        or ""
    ).strip()


def _qianchuan_lifecycle_topic(item: dict[str, Any]) -> str:
    cloud_video = item.get("cloud_video") if isinstance(item.get("cloud_video"), dict) else {}
    return str(
        item.get("topic_key")
        or item.get("theme_name")
        or cloud_video.get("theme_name")
        or cloud_video.get("topic_key")
        or ""
    ).strip()


def _qianchuan_lifecycle_cloud_metrics(item: dict[str, Any]) -> dict[str, Any]:
    cloud_video = item.get("cloud_video") if isinstance(item.get("cloud_video"), dict) else {}
    metrics = cloud_video.get("metrics") if isinstance(cloud_video.get("metrics"), dict) else {}

    def number(*keys: str) -> float:
        for key in keys:
            value = cloud_video.get(key)
            if value is None:
                value = metrics.get(key)
            try:
                if value is None or value == "":
                    continue
                return float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return 0.0

    return {
        "cost": number("stat_cost", "statCost", "cost"),
        "clicks": number("click_count", "clickCnt", "clicks"),
        "show_count": number("showCnt", "show_count"),
        "click_rate": number("clickRate", "click_rate"),
        "average_click_price": number("averageClickPrice", "average_click_price"),
        "total_play": number("totalPlay", "total_play"),
        "play_3s_rate": number("playDuration3sRate", "play_3s_rate"),
        "play_over_rate": number("playOverRate", "play_over_rate"),
        "roi": number("roi"),
        "pay_amount": number("pay_amount", "payOrderAmount", "payOrderAmountAndPayOrderCouponAmount"),
    }


def _qianchuan_lifecycle_failure_reason(item: dict[str, Any]) -> str:
    qianchuan = item.get("qianchuan") if isinstance(item.get("qianchuan"), dict) else {}
    warnings = qianchuan.get("warnings") if isinstance(qianchuan.get("warnings"), list) else []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        message = str(warning.get("message") or "").strip()
        endpoint = str(warning.get("endpoint") or "").strip()
        if message:
            return f"{endpoint}: {message}" if endpoint else message
    error_code = str(qianchuan.get("error_code") or "").strip()
    if error_code:
        return error_code
    if item.get("lifecycle_available") is False:
        return "千川内容分析未返回可用互动生命周期"
    return ""


def _normalize_qianchuan_lifecycle_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = _qianchuan_lifecycle_video_id(item)
        if not video_id:
            continue
        cloud_video = item.get("cloud_video") if isinstance(item.get("cloud_video"), dict) else {}
        qianchuan = item.get("qianchuan") if isinstance(item.get("qianchuan"), dict) else {}
        qianchuan_summary = qianchuan.get("summary") if isinstance(qianchuan.get("summary"), dict) else {}
        available = bool(
            item.get("lifecycle_available")
            or qianchuan.get("ok") is True
            or qianchuan.get("interaction_lifecycle")
            or qianchuan_summary.get("click_total")
            or qianchuan_summary.get("series_points")
        )
        normalized_item = {
            **item,
            "video_id": video_id,
            "cloud_video_id": str(item.get("cloud_video_id") or video_id),
            "topic_key": _qianchuan_lifecycle_topic(item),
            "lifecycle_available": available,
            "lifecycle_status": "available" if available else "unavailable",
            "lifecycle_failure_reason": "" if available else _qianchuan_lifecycle_failure_reason(item),
            "cloud_video_proxy_metrics": _qianchuan_lifecycle_cloud_metrics(item),
            "manager_value": (
                "千川秒级点击生命周期可用，可用来定位高消耗视频真正触发点击的秒点。"
                if available
                else "千川秒级生命周期未命中；本次只能用云视频消耗、点击、点击率、3秒播放和转化指标判断高消耗参考是否具备放量价值。"
            ),
            "consumer_value": (
                "结合秒级峰值与画面内容判断消费者被哪一帧、卖点或价格理由触发。"
                if available
                else "无法直接还原消费者秒级点击峰值，只能从高消耗参考的标题主题、视觉证据和点击/转化代理指标推断可复用触发点。"
            ),
        }
        if qianchuan_summary:
            normalized_item["qianchuan_summary"] = {
                **qianchuan_summary,
                **(item.get("qianchuan_summary") if isinstance(item.get("qianchuan_summary"), dict) else {}),
            }
        elif isinstance(item.get("qianchuan_summary"), dict):
            normalized_item["qianchuan_summary"] = item.get("qianchuan_summary")
        if cloud_video:
            normalized_item["cloud_video"] = cloud_video
        normalized.append(normalized_item)
    return normalized


def _qianchuan_lifecycle_quality(items: list[dict[str, Any]]) -> dict[str, Any]:
    attempted = len(items)
    available = sum(1 for item in items if item.get("lifecycle_available") is True)
    failed = attempted - available
    failure_reasons: dict[str, int] = {}
    proxy_metric_rows = 0
    for item in items:
        reason = str(item.get("lifecycle_failure_reason") or "").strip()
        if reason:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        metrics = item.get("cloud_video_proxy_metrics")
        if isinstance(metrics, dict) and any(float(metrics.get(key) or 0) > 0 for key in ("cost", "clicks", "click_rate", "roi", "pay_amount")):
            proxy_metric_rows += 1
    return {
        "attempted": attempted,
        "available": available,
        "failed": failed,
        "availability_ratio": round(available / attempted, 4) if attempted else 0,
        "cloud_video_proxy_metric_rows": proxy_metric_rows,
        "failure_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(failure_reasons.items(), key=lambda row: (-row[1], row[0]))[:5]
        ],
        "manager_value": (
            "高消耗参考视频已尝试读取千川内容分析；可用时优先按秒级点击峰值解释内容触发。"
            if available
            else "高消耗参考视频已尝试读取千川内容分析，但素材列表未命中或接口无响应；管理者应把千川素材 ID 映射/账号口径作为补查项。"
        ),
        "consumer_value": (
            "生命周期可用样本可说明消费者在哪个秒点被利益点、场景或 CTA 触发。"
            if available
            else "消费者秒级触发点暂不能直接量化，需结合完整视频画面和点击/转化代理指标判断可复用卖点。"
        ),
    }


def _numeric_metric(row: dict[str, Any], *keys: str) -> float:
    metric_bag = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in keys:
        value = row.get(key)
        if value is None:
            value = metric_bag.get(key)
        try:
            if value is None or value == "":
                continue
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return 0.0


async def _builtin_samplebrand_daily_analysis_input(
    db: AsyncSession,
    user: User,
    args: dict,
) -> dict:
    capability = DATA_CAPABILITIES["cloud_video.samplebrand_weekly.raw_collection"]
    row, run, _capability, readable_skill_ids = await _artifact_by_id_or_run(db, user, args, capability=capability)
    content, omitted, reason = _read_artifact_json_content(
        row,
        max_bytes=max(int(row.uncompressed_size_bytes or 0), DATA_ARTIFACT_MAX_READ_BYTES),
    )
    if omitted or not isinstance(content, dict):
        raise AppError(
            "DATA_ARTIFACT_CONTENT_UNAVAILABLE",
            422,
            {
                "reason": reason or "content_not_json_object",
                "artifact_id": row.id,
                "run_id": row.run_id,
            },
        )

    raw_data = content.get("raw_data") if isinstance(content.get("raw_data"), dict) else content
    if not isinstance(raw_data, dict):
        raise AppError("DATA_ARTIFACT_CONTENT_INVALID", 422, {"detail": "raw_data must be object"})

    date_range = raw_data.get("date_range") if isinstance(raw_data.get("date_range"), dict) else {}
    requested_date = _parse_iso_date_arg(_text_arg(args, "analysis_date", "analysisDate", "date"), field="analysis_date")
    analysis_date = requested_date or _parse_iso_date_arg(str(date_range.get("end_date") or ""), field="date_range.end_date")
    if not analysis_date:
        raise AppError("PARAM_INVALID", 400, {"detail": "analysis_date is required when artifact has no date_range.end_date"})

    daily_report = raw_data.get("daily_report") if isinstance(raw_data.get("daily_report"), dict) else {}
    daily_rows_raw = daily_report.get("daily_video_rows") if isinstance(daily_report.get("daily_video_rows"), list) else []
    include_zero_cost = _bool_arg(args, "include_zero_cost", "includeZeroCost", default=False)
    min_cost_raw = args.get("min_cost") if args.get("min_cost") is not None else args.get("minCost")
    try:
        min_cost = float(min_cost_raw) if min_cost_raw is not None and str(min_cost_raw).strip() != "" else 0.0
    except (TypeError, ValueError):
        min_cost = 0.0
    max_rows = _int_arg(args, "max_rows", 50000, minimum=1, maximum=50000)

    daily_video_rows: list[dict[str, Any]] = []
    excluded_zero_cost = 0
    excluded_below_min_cost = 0
    total_date_rows = 0
    for item in daily_rows_raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("date") or item.get("stat_date") or item.get("statDate") or "").strip() != analysis_date:
            continue
        total_date_rows += 1
        cost = _numeric_metric(item, "statCost", "stat_cost", "cost")
        if not include_zero_cost and cost <= 0:
            excluded_zero_cost += 1
            continue
        if min_cost and cost < min_cost:
            excluded_below_min_cost += 1
            continue
        daily_video_rows.append(item)
        if len(daily_video_rows) >= max_rows:
            break

    video_ids = {_video_id_from_daily_row(item) for item in daily_video_rows}
    video_ids.discard("")
    videos_raw = raw_data.get("videos") if isinstance(raw_data.get("videos"), list) else []
    videos_by_id = {
        str(item.get("id") or item.get("video_id") or "").strip(): item
        for item in videos_raw
        if isinstance(item, dict) and str(item.get("id") or item.get("video_id") or "").strip() in video_ids
    }
    qianchuan_lifecycle_raw = (
        raw_data.get("qianchuan_high_consumption_lifecycle")
        if isinstance(raw_data.get("qianchuan_high_consumption_lifecycle"), list)
        else []
    )
    qianchuan_lifecycle = _normalize_qianchuan_lifecycle_items([
        item
        for item in qianchuan_lifecycle_raw
        if isinstance(item, dict)
    ])
    qianchuan_by_video_id = {_qianchuan_lifecycle_video_id(item): item for item in qianchuan_lifecycle}
    qianchuan_by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in qianchuan_lifecycle:
        topic = _qianchuan_lifecycle_topic(item)
        if topic:
            qianchuan_by_topic.setdefault(topic, []).append(item)
    qianchuan_quality = _qianchuan_lifecycle_quality(qianchuan_lifecycle)

    data: dict[str, Any] = {
        "ok": True,
        "analysis_date": analysis_date,
        "department": raw_data.get("department"),
        "team_id": raw_data.get("team_id"),
        "team_name": raw_data.get("team_name"),
        "date_range": date_range,
        "daily_video_rows": daily_video_rows,
        "videos": list(videos_by_id.values()),
        "videos_by_id": videos_by_id,
        "qianchuan_high_consumption_lifecycle": qianchuan_lifecycle,
        "qianchuan_lifecycle_by_video_id": qianchuan_by_video_id,
        "qianchuan_lifecycle_by_topic": qianchuan_by_topic,
        "qianchuan_lifecycle_quality": qianchuan_quality,
        "source_counts": {
            "total_daily_video_rows": len(daily_rows_raw),
            "target_date_rows": total_date_rows,
            "returned_daily_video_rows": len(daily_video_rows),
            "returned_videos": len(videos_by_id),
            "qianchuan_lifecycle_rows": len(qianchuan_lifecycle),
            "qianchuan_lifecycle_available_rows": qianchuan_quality["available"],
            "qianchuan_lifecycle_failed_rows": qianchuan_quality["failed"],
            "excluded_zero_cost_rows": excluded_zero_cost,
            "excluded_below_min_cost_rows": excluded_below_min_cost,
            "truncated": total_date_rows > len(daily_video_rows) + excluded_zero_cost + excluded_below_min_cost,
            "max_rows": max_rows,
        },
        "source_artifact": _serialize_execution_artifact(row, run),
    }
    if _capability is not None:
        data["capability"] = _serialize_data_capability(_capability, readable_skill_ids=readable_skill_ids)
    return data


async def _assert_artifact_skill_readable(db: AsyncSession, user: User, skill_id: str) -> Skill:
    skill = await db.get(Skill, skill_id)
    if skill is None:
        raise AppError("SKILL_NOT_FOUND", 404, {"skill_id": skill_id})
    perms = await get_skill_permissions(db, skill, user)
    if not (perms.get("read") or perms.get("execute") or perms.get("edit") or perms.get("publish") or perms.get("review")):
        raise AppError("SKILL_ACCESS_DENIED", 403)
    return skill


async def _builtin_execution_artifact_summary(db: AsyncSession, user: User, args: dict) -> dict:
    run_id = _text_arg(args, "run_id", "runId")
    if not run_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "run_id is required"})
    run = await db.get(ExecutionRun, run_id)
    if run is None or not run.skill_id:
        raise AppError("NOT_FOUND", 404, {"run_id": run_id})
    await _assert_artifact_skill_readable(db, user, run.skill_id)

    limit = _int_arg(args, "limit", 10, minimum=1, maximum=20)
    kind = _text_arg(args, "kind")
    stmt = (
        select(ExecutionArtifact)
        .where(ExecutionArtifact.run_id == run_id)
        .order_by(ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
        .limit(limit)
    )
    if kind:
        stmt = stmt.where(ExecutionArtifact.kind == kind)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "run_id": run_id,
        "skill_id": run.skill_id,
        "count": len(rows),
        "items": [_serialize_execution_artifact(row, run) for row in rows],
    }


async def _builtin_execution_artifact_latest(db: AsyncSession, user: User, args: dict) -> dict:
    skill_id = _text_arg(args, "skill_id", "skillId")
    if not skill_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "skill_id is required"})
    await _assert_artifact_skill_readable(db, user, skill_id)
    limit = _int_arg(args, "limit", 1, minimum=1, maximum=20)
    kind = _text_arg(args, "kind") or "raw-output"
    schema = _text_arg(args, "schema", "schema_name", "schemaName")
    status = _text_arg(args, "status") or "completed"
    stmt = (
        select(ExecutionArtifact, ExecutionRun)
        .join(ExecutionRun, ExecutionRun.id == ExecutionArtifact.run_id)
        .where(ExecutionArtifact.skill_id == skill_id)
        .where(ExecutionArtifact.kind == kind)
        .order_by(ExecutionRun.completed_at.desc().nullslast(), ExecutionArtifact.created_at.desc(), ExecutionArtifact.id.desc())
        .limit(limit)
    )
    if schema:
        stmt = stmt.where(ExecutionArtifact.schema_name == schema)
    if status:
        stmt = stmt.where(ExecutionRun.status == status)
    rows = (await db.execute(stmt)).all()
    return {
        "skill_id": skill_id,
        "kind": kind,
        "count": len(rows),
        "items": [_serialize_execution_artifact(artifact, run) for artifact, run in rows],
    }


async def _search_cached_dingtalk_contacts(db: AsyncSession, args: dict, *, limit: int) -> list[dict]:
    query = _text_arg(args, "query", "q")
    department = _text_arg(args, "department")
    dingtalk_user_ids = _list_arg(args, "dingtalk_user_ids", "dingtalkUserIds")
    if not query and not department and not dingtalk_user_ids:
        return []
    state = _text_arg(args, "state") or "active"
    if state not in {"active", "disabled", "pending", "all"}:
        return []

    conditions = [
        User.dingtalk_user_id.is_not(None),
        User.dingtalk_user_id != "",
    ]
    if state != "all":
        conditions.append(User.state == state)
    if state == "active":
        conditions.append(User.is_active == True)  # noqa: E712

    if query:
        pattern = f"%{_escape_like(query)}%"
        conditions.append(
            or_(
                User.id.ilike(pattern, escape="\\"),
                User.name.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
                User.department.ilike(pattern, escape="\\"),
                User.dingtalk_user_id.ilike(pattern, escape="\\"),
            )
        )

    if dingtalk_user_ids:
        conditions.append(User.dingtalk_user_id.in_(dingtalk_user_ids))

    if department:
        pattern = f"%{_escape_like(department)}%"
        conditions.append(
            or_(
                User.department.ilike(pattern, escape="\\"),
                User.id.in_(
                    select(UserOrgMembership.user_id)
                    .join(OrgUnit, OrgUnit.id == UserOrgMembership.org_unit_id)
                    .where(
                        or_(
                            OrgUnit.name.ilike(pattern, escape="\\"),
                            OrgUnit.dingtalk_dept_id.ilike(pattern, escape="\\"),
                        )
                    )
                ),
            )
        )

    users = list(
        (
            await db.execute(
                select(User)
                .where(*conditions)
                .order_by(User.name.asc(), User.username.asc())
                .limit(limit)
            )
        ).scalars().all()
    )
    orgs_by_user = await _member_orgs_for_users(db, users)
    items = []
    for row in users:
        item = _serialize_cached_dingtalk_contact(row, orgs_by_user)
        if item:
            items.append(item)
    return items


async def _dingtalk_department_index() -> tuple[list[str], dict[str, str]]:
    dept_ids = ["1"]
    dept_names = {"1": "根部门"}
    queue = ["1"]
    seen = {"1"}
    while queue and len(dept_ids) < DINGTALK_CONTACT_DEPARTMENT_SCAN_LIMIT:
        dept_id = queue.pop(0)
        result = await dingtalk_client.list_sub_departments(dept_id)
        if not result.get("ok"):
            logger.info("dingtalk department lookup unavailable: {}", result.get("error") or result.get("data"))
            break
        for dept in result.get("data") or []:
            child_id = str(dept.get("dept_id") or "").strip()
            if not child_id or child_id in seen:
                continue
            seen.add(child_id)
            dept_ids.append(child_id)
            dept_names[child_id] = str(dept.get("name") or child_id)
            queue.append(child_id)
            if len(dept_ids) >= DINGTALK_CONTACT_DEPARTMENT_SCAN_LIMIT:
                break
    return dept_ids, dept_names


async def _search_dingtalk_contacts(args: dict, *, limit: int) -> list[dict]:
    query = _text_arg(args, "query", "q")
    department = _text_arg(args, "department")
    dingtalk_user_ids = _list_arg(args, "dingtalk_user_ids", "dingtalkUserIds")
    if not query and not department and not dingtalk_user_ids:
        return []

    contacts: list[dict] = []
    seen_dingtalk_ids: set[str] = set()
    dept_names: dict[str, str] = {}

    async def add_contact(contact: dict[str, Any]) -> None:
        dingtalk_user_id = str(contact.get("user_id") or "").strip()
        if not dingtalk_user_id or dingtalk_user_id in seen_dingtalk_ids:
            return
        if query and not _dingtalk_contact_matches_query(contact, query):
            return
        item = _serialize_dingtalk_contact(contact, dept_names)
        if department:
            org_names = [str(org.get("name") or "") for org in item.get("org_units") or []]
            if department not in str(item.get("department") or "") and not any(department in name for name in org_names):
                return
        seen_dingtalk_ids.add(dingtalk_user_id)
        contacts.append(item)

    exact_ids = set(dingtalk_user_ids)
    if query and len(query) >= 6 and re.search(r"\d", query):
        exact_ids.add(query)
    for dingtalk_user_id in sorted(exact_ids):
        try:
            detail = await dingtalk_client.get_user_detail(dingtalk_user_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("dingtalk user detail lookup failed user_id={} error={}", dingtalk_user_id, exc)
            continue
        if not detail.get("ok") or not isinstance(detail.get("data"), dict):
            continue
        await add_contact(detail["data"])
        if len(contacts) >= limit:
            return contacts[:limit]

    if dingtalk_user_ids and not query and not department:
        return contacts[:limit]
    if query and len(query) < 2 and not department:
        return contacts[:limit]

    try:
        dept_ids, dept_names = await _dingtalk_department_index()
    except Exception as exc:  # noqa: BLE001
        logger.info("dingtalk department scan failed: {}", exc)
        return contacts[:limit]

    scanned_users = 0
    for dept_id in dept_ids:
        if department and department not in dept_names.get(dept_id, ""):
            continue
        try:
            result = await dingtalk_client.list_department_users(dept_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("dingtalk department user lookup failed dept_id={} error={}", dept_id, exc)
            continue
        if not result.get("ok"):
            logger.info("dingtalk department user lookup unavailable dept_id={} error={}", dept_id, result.get("error"))
            continue
        for contact in result.get("data") or []:
            if not isinstance(contact, dict):
                continue
            dept_ids_for_contact = _normalize_dingtalk_dept_ids(contact.get("dept_id_list"))
            if not dept_ids_for_contact:
                contact = {**contact, "dept_id_list": [dept_id]}
            await add_contact(contact)
            scanned_users += 1
            if len(contacts) >= limit or scanned_users >= DINGTALK_CONTACT_USER_SCAN_LIMIT:
                return contacts[:limit]
    return contacts[:limit]


async def _serialize_dingtalk_recipient(db: AsyncSession, user: User, orgs_by_user: dict[str, list[dict]]) -> dict | None:
    canonical = await resolve_work_notice_user(db, user)
    if canonical is None or not canonical.dingtalk_user_id:
        return None
    if canonical.id == user.id:
        return _serialize_org_user(canonical, orgs_by_user)
    canonical_orgs = await _member_orgs_for_users(db, [canonical])
    item = _serialize_org_user(canonical, canonical_orgs)
    item["requested_user_id"] = user.id
    item["requested_dingtalk_user_id"] = user.dingtalk_user_id
    item["resolved_from_shadow_user"] = True
    return item


async def _query_org_users(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    default_limit: int = 20,
    max_limit: int = ORG_MCP_USER_LIMIT,
) -> tuple[list[User], dict[str, list[dict]], dict | None]:
    state = _text_arg(args, "state") or "active"
    if state not in {"active", "disabled", "pending", "all"}:
        raise AppError("PARAM_INVALID", 400, {"field": "state", "reason": "unsupported state"})
    limit = _int_arg(args, "limit", default=default_limit, minimum=1, maximum=max_limit)
    conditions = []
    if state != "all":
        conditions.append(User.state == state)
    if state == "active":
        conditions.append(User.is_active == True)  # noqa: E712

    query = _text_arg(args, "query", "q")
    if query:
        pattern = f"%{_escape_like(query)}%"
        conditions.append(
            or_(
                User.id.ilike(pattern, escape="\\"),
                User.name.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
                User.department.ilike(pattern, escape="\\"),
                User.dingtalk_user_id.ilike(pattern, escape="\\"),
            )
        )

    department = _text_arg(args, "department")
    if department:
        conditions.append(User.department.ilike(f"%{_escape_like(department)}%", escape="\\"))

    role = _text_arg(args, "role")
    if role:
        conditions.append(User.role == role)

    recipient_user_ids = _list_arg(args, "user_ids", "userIds", "recipient_user_ids", "recipientUserIds")
    dingtalk_user_ids = _list_arg(args, "dingtalk_user_ids", "dingtalkUserIds")
    recipient_conditions = []
    if recipient_user_ids:
        recipient_conditions.append(User.id.in_(recipient_user_ids))
    if dingtalk_user_ids:
        recipient_conditions.append(User.dingtalk_user_id.in_(dingtalk_user_ids))
    if recipient_conditions:
        conditions.append(or_(*recipient_conditions))

    org_summary = None
    org_unit_id = _text_arg(args, "org_unit_id", "orgUnitId")
    if org_unit_id:
        include_subtree = _bool_arg(args, "include_subtree", "includeSubtree", default=True)
        org_ids = await _org_ids_for_filter(db, org_unit_id, include_subtree)
        if not org_ids:
            return [], {}, None
        conditions.append(
            User.id.in_(
                select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id.in_(list(org_ids)))
            )
        )
        org = await db.get(OrgUnit, org_unit_id)
        org_summary = (
            {
                "id": org.id,
                "name": org.name,
                "type": org.type,
                "include_subtree": include_subtree,
                "matched_org_unit_ids": sorted(org_ids),
            }
            if org
            else None
        )

    conditions.extend(await _visible_user_scope_conditions(db, user))
    rows = (
        await db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.name.asc(), User.username.asc())
            .limit(limit)
        )
    ).scalars().all()
    users = list(rows)
    orgs_by_user = await _member_orgs_for_users(db, users)
    return users, orgs_by_user, org_summary


async def _resolve_org_users_for_display(
    db: AsyncSession,
    users: list[User],
) -> tuple[list[User], dict[str, list[dict]]]:
    display_users: list[User] = []
    seen_user_ids: set[str] = set()
    for row in users:
        canonical = await resolve_work_notice_user(db, row)
        display_user = canonical or row
        if display_user.id in seen_user_ids:
            continue
        seen_user_ids.add(display_user.id)
        display_users.append(display_user)
    orgs_by_user = await _member_orgs_for_users(db, display_users)
    return display_users, orgs_by_user


async def _builtin_org_search_users(db: AsyncSession, user: User, args: dict) -> dict:
    users, _orgs_by_user, org_summary = await _query_org_users(db, user, args)
    display_users, display_orgs_by_user = await _resolve_org_users_for_display(db, users)
    items = [_serialize_org_user(row, display_orgs_by_user) for row in display_users]
    limit = _int_arg(args, "limit", default=20, minimum=1, maximum=ORG_MCP_USER_LIMIT)
    include_dingtalk_contacts = _bool_arg(
        args,
        "include_dingtalk_contacts",
        "includeDingTalkContacts",
        default=True,
    )
    state = _text_arg(args, "state") or "active"
    can_use_contact_fallback = (
        include_dingtalk_contacts
        and state in {"active", "all"}
        and not _text_arg(args, "role")
        and not _text_arg(args, "org_unit_id", "orgUnitId")
        and (not items or bool(_list_arg(args, "dingtalk_user_ids", "dingtalkUserIds")))
    )
    if can_use_contact_fallback and len(items) < limit:
        seen_dingtalk_ids = {
            str(item.get("dingtalk_user_id") or "")
            for item in items
            if item.get("dingtalk_user_id")
        }
        try:
            contact_items = await _search_cached_dingtalk_contacts(db, args, limit=limit - len(items))
        except Exception as exc:  # noqa: BLE001
            logger.info("cached dingtalk contact fallback failed: {}", exc)
            contact_items = []
        for item in contact_items:
            dingtalk_user_id = str(item.get("dingtalk_user_id") or "")
            if not dingtalk_user_id or dingtalk_user_id in seen_dingtalk_ids:
                continue
            seen_dingtalk_ids.add(dingtalk_user_id)
            items.append(item)
            if len(items) >= limit:
                break
        try:
            contact_items = await _search_dingtalk_contacts(args, limit=max(0, limit - len(items)))
        except Exception as exc:  # noqa: BLE001
            logger.info("dingtalk contact fallback failed: {}", exc)
            contact_items = []
        for item in contact_items:
            dingtalk_user_id = str(item.get("dingtalk_user_id") or "")
            if not dingtalk_user_id or dingtalk_user_id in seen_dingtalk_ids:
                continue
            seen_dingtalk_ids.add(dingtalk_user_id)
            items.append(item)
            if len(items) >= limit:
                break
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "org": org_summary,
    }


async def _builtin_org_list_members(db: AsyncSession, user: User, args: dict) -> dict:
    users, _orgs_by_user, org_summary = await _query_org_users(db, user, args)
    display_users, display_orgs_by_user = await _resolve_org_users_for_display(db, users)
    include_org = _bool_arg(args, "include_org", "includeOrg", default=True)
    return {
        "ok": True,
        "items": [_serialize_org_user(row, display_orgs_by_user) for row in display_users],
        "count": len(display_users),
        "org": org_summary if include_org else None,
    }


def _clean_dingtalk_buttons(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    buttons = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:50]
        url = str(item.get("url") or "").strip()[:500]
        if title and url:
            buttons.append({"title": title, "url": url})
    return buttons


def _normalize_idempotency_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 50:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{text[:33]}:{digest}"


async def _existing_dingtalk_notice_rows(db: AsyncSession, idempotency_key: str) -> list[DingTalkOutbox]:
    return list(
        (
            await db.execute(
                select(DingTalkOutbox)
                .where(
                    DingTalkOutbox.related_type == "codex_mcp_dingtalk_notice",
                    DingTalkOutbox.related_id == idempotency_key,
                )
                .order_by(DingTalkOutbox.id.asc())
            )
        ).scalars().all()
    )


async def _builtin_dingtalk_send_work_notice(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    dry_run: bool,
    idempotency_key: str | None,
) -> dict:
    if not role_matches_any(user, ("admin", "dept_admin", "aibp", "ai_engineer", "biz_owner")):
        raise AppError("AUTH_PERMISSION_DENIED", 403)

    title = _text_arg(args, "title")[:100]
    markdown = (_text_arg(args, "markdown") or _text_arg(args, "content"))[:6000]
    if not title or not markdown:
        raise AppError("PARAM_INVALID", 400, {"detail": "title 和 markdown/content 必填"})

    has_selector = any(
        [
            _list_arg(args, "user_ids", "userIds", "recipient_user_ids", "recipientUserIds"),
            _list_arg(args, "dingtalk_user_ids", "dingtalkUserIds"),
            _text_arg(args, "query", "q"),
            _text_arg(args, "department"),
            _text_arg(args, "org_unit_id", "orgUnitId"),
        ]
    )
    if not has_selector:
        raise AppError("PARAM_INVALID", 400, {"detail": "必须指定 user_ids、dingtalk_user_ids、query、department 或 org_unit_id"})

    users, orgs_by_user, org_summary = await _query_org_users(
        db,
        user,
        {**args, "state": "active"},
        default_limit=min(_int_arg(args, "limit", default=20, minimum=1, maximum=ORG_MCP_PUSH_LIMIT), ORG_MCP_PUSH_LIMIT),
        max_limit=ORG_MCP_PUSH_LIMIT,
    )
    recipients = []
    seen_dingtalk_ids: set[str] = set()
    for row in users:
        recipient = await _serialize_dingtalk_recipient(db, row, orgs_by_user)
        if not recipient:
            continue
        dingtalk_user_id = str(recipient.get("dingtalk_user_id") or "")
        if dingtalk_user_id in seen_dingtalk_ids:
            continue
        seen_dingtalk_ids.add(dingtalk_user_id)
        recipients.append(recipient)
    missing_dingtalk = len(users) - len(recipients)
    direct_contact_args = {
        key: value
        for key, value in args.items()
        if key in {"query", "q", "department", "dingtalk_user_ids", "dingtalkUserIds"}
    }
    explicit_dingtalk_user_ids = bool(_list_arg(args, "dingtalk_user_ids", "dingtalkUserIds"))
    if direct_contact_args and (not recipients or explicit_dingtalk_user_ids):
        direct_contact_args["include_dingtalk_contacts"] = True
        try:
            contact_recipients = await _search_cached_dingtalk_contacts(
                db,
                direct_contact_args,
                limit=max(0, ORG_MCP_PUSH_LIMIT - len(recipients)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("cached dingtalk contact recipient fallback failed: {}", exc)
            contact_recipients = []
        for recipient in contact_recipients:
            dingtalk_user_id = str(recipient.get("dingtalk_user_id") or "")
            if not dingtalk_user_id or dingtalk_user_id in seen_dingtalk_ids:
                continue
            seen_dingtalk_ids.add(dingtalk_user_id)
            recipients.append(recipient)
            if len(recipients) >= ORG_MCP_PUSH_LIMIT:
                break
        try:
            contact_recipients = await _search_dingtalk_contacts(
                direct_contact_args,
                limit=max(0, ORG_MCP_PUSH_LIMIT - len(recipients)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("dingtalk contact recipient fallback failed: {}", exc)
            contact_recipients = []
        for recipient in contact_recipients:
            dingtalk_user_id = str(recipient.get("dingtalk_user_id") or "")
            if not dingtalk_user_id or dingtalk_user_id in seen_dingtalk_ids:
                continue
            seen_dingtalk_ids.add(dingtalk_user_id)
            recipients.append(recipient)
            if len(recipients) >= ORG_MCP_PUSH_LIMIT:
                break
    buttons = _clean_dingtalk_buttons(args.get("buttons"))
    payload = {"title": title, "markdown": markdown}
    if buttons:
        payload["buttons"] = buttons

    if dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "queued": 0,
            "recipient_count": len(recipients),
            "missing_dingtalk_user_id_count": missing_dingtalk,
            "recipients": recipients,
            "org": org_summary,
            "message": {"title": title, "markdown_preview": markdown[:300], "buttons": buttons},
        }

    if not idempotency_key:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", 400)
    existing = await _existing_dingtalk_notice_rows(db, idempotency_key)
    if existing:
        return {
            "ok": True,
            "dryRun": False,
            "deduped": True,
            "queued": 0,
            "outbox_ids": [row.id for row in existing],
            "recipient_count": len(existing),
        }

    priority = _int_arg(args, "priority", default=5, minimum=1, maximum=9)
    outbox_rows = []
    for recipient in recipients:
        row = DingTalkOutbox(
            message_type="work_notice",
            priority=priority,
            recipient_user_id=recipient["dingtalk_user_id"],
            payload=payload,
            related_type="codex_mcp_dingtalk_notice",
            related_id=idempotency_key,
        )
        db.add(row)
        outbox_rows.append(row)
    await db.flush()
    return {
        "ok": True,
        "dryRun": False,
        "deduped": False,
        "queued": len(outbox_rows),
        "outbox_ids": [row.id for row in outbox_rows],
        "recipient_count": len(recipients),
        "missing_dingtalk_user_id_count": missing_dingtalk,
        "recipients": recipients,
        "org": org_summary,
    }


def _iso_or_none(value: Any) -> str | None:
    return isoformat_bjt(value) if value is not None else None


def _can_query_platform_scope(user: User) -> bool:
    return bool(getattr(user, "can_view_all", False)) or role_matches_any(user, ("admin",))


def _can_call_platform_ai(user: User) -> bool:
    return _can_query_platform_scope(user) or role_matches_any(
        user,
        ("dept_admin", "aibp", "ai_engineer", "biz_owner", "operator"),
    )


def _sanitize_raw_data_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 200:
                cleaned["_truncated_keys"] = max(len(value) - index, 0)
                break
            key_text = str(key)
            if re.search(
                r"(?i)(authorization|cookie|token|secret|password|credential|api[_-]?key|access[_-]?key|private[_-]?key)",
                key_text,
            ):
                cleaned[key_text] = "[REDACTED]"
            else:
                cleaned[key_text] = _sanitize_raw_data_value(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize_raw_data_value(item, depth=depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        redacted = redact_secret_text(value, limit=RAW_DATA_TEXT_LIMIT)
        return redacted + ("...[TRUNCATED]" if len(value) > RAW_DATA_TEXT_LIMIT else "")
    return value


def _payload_if_allowed(value: Any, include_payload: bool) -> Any:
    if not include_payload:
        return None
    return _sanitize_raw_data_value(value)


async def _resolve_raw_scope(
    db: AsyncSession,
    user: User,
    *,
    skill_id: str,
    run_id: str,
    proof_id: str,
    source: str,
) -> str | None:
    if skill_id:
        try:
            await require_skill_access(db, skill_id, user, "read")
        except AppError as exc:
            if exc.code == "SKILL_NOT_FOUND" and _can_query_platform_scope(user):
                return skill_id
            raise
        return skill_id

    if run_id:
        run = await db.get(ExecutionRun, run_id)
        if run and run.skill_id:
            try:
                await require_skill_access(db, str(run.skill_id), user, "read")
            except AppError as exc:
                if exc.code == "SKILL_NOT_FOUND" and _can_query_platform_scope(user):
                    return str(run.skill_id)
                raise
            return str(run.skill_id)
        if run and _can_query_platform_scope(user):
            return None
        if source == "collection_proofs":
            proof_skill_ids = set(
                (
                    await db.execute(
                        select(CollectionProof.skill_id)
                        .where(CollectionProof.run_id == run_id)
                        .where(CollectionProof.skill_id.is_not(None))
                        .limit(RAW_DATA_QUERY_LIMIT)
                    )
                ).scalars().all()
            )
            if len(proof_skill_ids) == 1:
                resolved = next(iter(proof_skill_ids))
                await require_skill_access(db, resolved, user, "read")
                return resolved
        raise AppError("SKILL_ACCESS_DENIED", 403, {"detail": "run_id 无法映射到当前账号可读的 Skill"})

    if proof_id:
        proof = (
            await db.execute(select(CollectionProof).where(CollectionProof.proof_id == proof_id).limit(1))
        ).scalar_one_or_none()
        if proof and proof.skill_id:
            await require_skill_access(db, str(proof.skill_id), user, "read")
            return str(proof.skill_id)
        if proof and _can_query_platform_scope(user):
            return None
        raise AppError("SKILL_ACCESS_DENIED", 403, {"detail": "proof_id 无法映射到当前账号可读的 Skill"})

    if _can_query_platform_scope(user):
        return None
    raise AppError("PARAM_INVALID", 400, {"detail": "查询原始数据需要提供 skill_id、run_id 或 proof_id"})


async def _builtin_platform_ai_analyze(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    effective_skill_id: str | None,
    effective_run_id: str | None,
) -> dict:
    if not _can_call_platform_ai(user):
        raise AppError("AUTH_PERMISSION_DENIED", 403, {"detail": "当前账号无权调用平台 AI"})

    skill_id = _text_arg(args, "skill_id", "skillId") or (effective_skill_id or "")
    if skill_id and skill_id != "codex-mcp-stdio":
        try:
            await require_skill_access(db, skill_id, user, "read")
        except AppError as exc:
            if exc.code == "SKILL_NOT_FOUND" and _can_query_platform_scope(user):
                pass
            else:
                raise

    prompt = _text_arg(args, "prompt", "question", "query")
    context_pack = args.get("context_pack") or args.get("contextPack") or {}
    context = args.get("context")
    if not prompt and isinstance(context_pack, dict):
        prompt = str(
            context_pack.get("prompt")
            or context_pack.get("question")
            or context_pack.get("task")
            or ""
        ).strip()
    if not prompt and context is None and not context_pack:
        raise AppError("PARAM_INVALID", 400, {"detail": "prompt/question/context/context_pack 至少提供一个"})

    system_prompt = (
        _text_arg(args, "system")
        or "你是 SkillForge 平台 AI 分析助手。基于调用者提供的上下文做严谨分析；不要编造未给出的事实；输出直接可执行的结论。"
    )
    requested_model_profile = _text_arg(args, "model_profile", "modelProfile", "profile").lower().replace("_", "-")
    profile_aliases = {
        "": "",
        "default": "",
        "cheap": "cheap",
        "ai.cheap": "cheap",
        "flash": "flash",
        "v4f": "flash",
        "deepseek-v4-flash": "flash",
        "configured": "configured",
        "provider-default": "configured",
        "deepseek-chat": "configured",
    }
    if requested_model_profile not in profile_aliases:
        raise AppError("PARAM_INVALID", 400, {"field": "model_profile", "allowed": ["default", "cheap", "flash", "configured"]})
    model_profile = profile_aliases[requested_model_profile]
    if model_profile == "flash":
        model_override = PLATFORM_AI_MCP_FLASH_MODEL
        call_model_profile = ""
        effective_model = PLATFORM_AI_MCP_FLASH_MODEL
    elif model_profile == "configured":
        model_override = ""
        call_model_profile = ""
        effective_ai_config = await get_ai_profile_config(require_system_config=True)
        effective_model = str(effective_ai_config.get("ai.model") or "configured")
    elif model_profile:
        model_override = ""
        call_model_profile = model_profile
        effective_ai_config = await get_ai_profile_config(
            model_profile=model_profile,
            require_system_config=True,
        )
        effective_model = str(effective_ai_config.get("ai.model") or model_profile)
    else:
        model_override = PLATFORM_AI_MCP_MODEL
        call_model_profile = ""
        # The default Codex platform-AI MCP tool is contractually pinned to the
        # long-context platform model.  Let call_llm enforce server-side AI
        # credentials; this pre-step must not block tests or dry-run shims that
        # monkeypatch call_llm and never touch real credentials.
        effective_model = PLATFORM_AI_MCP_MODEL
    user_payload = {
        "prompt": prompt,
        "context": context,
        "context_pack": context_pack,
        "constraints": {
            "model": effective_model,
            "model_profile": model_profile or "default",
            "max_context_tokens": PLATFORM_AI_MCP_MAX_CONTEXT_TOKENS,
            "credential_location": "platform_only",
        },
    }
    user_text = json.dumps(user_payload, ensure_ascii=False, default=str)
    prompt_hash = hashlib.sha256(user_text.encode("utf-8")).hexdigest()
    json_mode = _bool_arg(args, "json_mode", "jsonMode", default=False)
    max_output_tokens = _int_arg(
        args,
        "max_output_tokens",
        default=4096,
        minimum=1,
        maximum=PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS,
    )
    try:
        temperature = float(args.get("temperature", 0.2) or 0.2)
    except (TypeError, ValueError):
        raise AppError("PARAM_INVALID", 400, {"field": "temperature", "reason": "must be number"})
    temperature = min(max(temperature, 0.0), 2.0)

    output = await call_llm(
        system_prompt,
        user_text,
        max_tokens=max_output_tokens,
        temperature=temperature,
        timeout=180,
        json_mode=json_mode,
        call_source="codex_mcp_platform_ai",
        model_override=model_override,
        cost_context={
            "user_id": user.id,
            "department": getattr(user, "department", None),
            "skill_id": skill_id or None,
            "conversation_id": effective_run_id,
            "prompt_hash": prompt_hash,
            "model_profile": model_profile or "default",
        },
        model_profile=call_model_profile,
        require_system_config=True,
    )
    if output is None:
        raise AppError("PLATFORM_AI_CALL_FAILED", 502, {"detail": "平台 AI 未返回有效结果"})

    return {
        "ok": True,
        "model": effective_model,
        "model_profile": model_profile or "default",
        "max_context_tokens": PLATFORM_AI_MCP_MAX_CONTEXT_TOKENS,
        "json_mode": json_mode,
        "output": output,
        "prompt_hash": prompt_hash,
        "credential_location": "platform_only",
    }


async def _query_execution_runs_raw(
    db: AsyncSession,
    *,
    skill_id: str | None,
    run_id: str,
    limit: int,
    include_payload: bool,
) -> list[dict]:
    conditions = []
    if skill_id:
        conditions.append(ExecutionRun.skill_id == skill_id)
    if run_id:
        conditions.append(ExecutionRun.id == run_id)
    rows = (
        await db.execute(
            select(ExecutionRun)
            .where(*conditions)
            .order_by(ExecutionRun.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "skill_id": row.skill_id,
            "run_mode": row.run_mode,
            "trigger_type": row.trigger_type,
            "status": row.status,
            "started_at": _iso_or_none(row.started_at),
            "completed_at": _iso_or_none(row.completed_at),
            "completed_steps": row.completed_steps,
            "total_steps": row.total_steps,
            "summary": row.summary,
            "business_ref_id": row.business_ref_id,
            "source_instance_id": row.source_instance_id,
            "metadata_json": _payload_if_allowed(row.metadata_json, include_payload),
        }
        for row in rows
    ]


async def _query_execution_steps_raw(
    db: AsyncSession,
    *,
    skill_id: str | None,
    run_id: str,
    limit: int,
    include_payload: bool,
) -> list[dict]:
    conditions = []
    if skill_id:
        conditions.append(ExecutionStep.skill_id == skill_id)
    if run_id:
        conditions.append(ExecutionStep.run_id == run_id)
    rows = (
        await db.execute(
            select(ExecutionStep)
            .where(*conditions)
            .order_by(ExecutionStep.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "skill_id": row.skill_id,
            "step_order": row.step_order,
            "status": row.status,
            "started_at": _iso_or_none(row.started_at),
            "completed_at": _iso_or_none(row.completed_at),
            "duration_ms": row.duration_ms,
            "input_data": _payload_if_allowed(row.input_data, include_payload),
            "output_data": _payload_if_allowed(row.output_data, include_payload),
            "error_message": redact_secret_text(row.error_message, limit=2000) if row.error_message else None,
        }
        for row in rows
    ]


async def _query_decision_logs_raw(
    db: AsyncSession,
    *,
    skill_id: str | None,
    run_id: str,
    limit: int,
    include_payload: bool,
) -> list[dict]:
    conditions = []
    if skill_id:
        conditions.append(DecisionLog.skill_id == skill_id)
    if run_id:
        conditions.append(DecisionLog.run_id == run_id)
    rows = (
        await db.execute(
            select(DecisionLog)
            .where(*conditions)
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "skill_id": row.skill_id,
            "approval_level": row.approval_level,
            "approval_status": row.approval_status,
            "approver": row.approver,
            "target_user": row.target_user,
            "user_action": row.user_action,
            "user_feedback": redact_secret_text(row.user_feedback, limit=4000) if row.user_feedback else None,
            "rating": row.rating,
            "feedback_type": row.feedback_type,
            "reject_reason": row.reject_reason,
            "created_at": _iso_or_none(row.created_at),
            "model_id": row.model_id,
            "token_count": row.token_count,
            "is_sandbox": bool(row.is_sandbox),
            "input_snapshot": _payload_if_allowed(row.input_snapshot, include_payload),
            "output_result": _payload_if_allowed(row.output_result, include_payload),
            "suggested_action": _payload_if_allowed(row.suggested_action, include_payload),
            "business_impact": _payload_if_allowed(row.business_impact, include_payload),
        }
        for row in rows
    ]


async def _query_collection_proofs_raw(
    db: AsyncSession,
    *,
    skill_id: str | None,
    run_id: str,
    proof_id: str,
    args: dict,
    limit: int,
) -> list[dict]:
    conditions = []
    if skill_id:
        conditions.append(CollectionProof.skill_id == skill_id)
    if run_id:
        conditions.append(CollectionProof.run_id == run_id)
    if proof_id:
        conditions.append(CollectionProof.proof_id == proof_id)
    for field in ("platform", "shop_id", "data_scope", "status"):
        value = _text_arg(args, field)
        if value:
            conditions.append(getattr(CollectionProof, field) == value)
    rows = (
        await db.execute(
            select(CollectionProof)
            .where(*conditions)
            .order_by(CollectionProof.created_at.desc(), CollectionProof.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "proof_id": row.proof_id,
            "run_id": row.run_id,
            "skill_id": row.skill_id,
            "mcp_tool_name": row.mcp_tool_name,
            "platform": row.platform,
            "shop_id": row.shop_id,
            "data_scope": row.data_scope,
            "endpoint_family": row.endpoint_family,
            "warning_group": row.warning_group,
            "credential_scope": row.credential_scope,
            "credential_alias": row.credential_alias,
            "browser_slot_id": row.browser_slot_id,
            "retry_of_proof_id": row.retry_of_proof_id,
            "status": row.status,
            "http_status": row.http_status,
            "error_code": row.error_code,
            "response_hash": row.response_hash,
            "data_keys": _sanitize_raw_data_value(row.data_keys),
            "row_count": row.row_count,
            "warning_signal": _sanitize_raw_data_value(row.warning_signal),
            "created_at": _iso_or_none(row.created_at),
        }
        for row in rows
    ]


async def _query_api_schema_snapshots_raw(
    db: AsyncSession,
    *,
    run_id: str,
    args: dict,
    limit: int,
) -> list[dict]:
    conditions = []
    if run_id:
        conditions.append(PlatformApiSchemaSnapshot.source_run_id == run_id)
    for field in ("platform", "shop_id", "endpoint_hash"):
        value = _text_arg(args, field)
        if value:
            conditions.append(getattr(PlatformApiSchemaSnapshot, field) == value)
    include_endpoint = _bool_arg(args, "include_endpoint", "includeEndpoint", default=False)
    rows = (
        await db.execute(
            select(PlatformApiSchemaSnapshot)
            .where(*conditions)
            .order_by(PlatformApiSchemaSnapshot.captured_at.desc(), PlatformApiSchemaSnapshot.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "platform": row.platform,
            "shop_id": row.shop_id,
            "endpoint_hash": row.endpoint_hash,
            "endpoint": redact_secret_text(row.endpoint, limit=2000) if include_endpoint else None,
            "response_keys": _sanitize_raw_data_value(row.response_keys),
            "row_count": row.row_count,
            "response_hash": row.response_hash,
            "source_run_id": row.source_run_id,
            "captured_at": _iso_or_none(row.captured_at),
        }
        for row in rows
    ]


async def _builtin_raw_data_query(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    effective_skill_id: str | None,
) -> dict:
    source_alias = (_text_arg(args, "source", "table") or "execution_runs").lower()
    source = {
        "runs": "execution_runs",
        "run": "execution_runs",
        "steps": "execution_steps",
        "step": "execution_steps",
        "decisions": "decision_logs",
        "decision": "decision_logs",
        "proofs": "collection_proofs",
        "proof": "collection_proofs",
        "schemas": "api_schema_snapshots",
        "schema": "api_schema_snapshots",
    }.get(source_alias, source_alias)
    if source not in {
        "execution_runs",
        "execution_steps",
        "decision_logs",
        "collection_proofs",
        "api_schema_snapshots",
    }:
        raise AppError("PARAM_INVALID", 400, {"field": "source", "reason": "unsupported source"})

    skill_id = _text_arg(args, "skill_id", "skillId") or (effective_skill_id or "")
    if skill_id == "codex-mcp-stdio":
        skill_id = ""
    run_id = _text_arg(args, "run_id", "runId", "execution_run_id", "executionRunId")
    proof_id = _text_arg(args, "proof_id", "proofId")
    limit = _int_arg(args, "limit", default=20, minimum=1, maximum=RAW_DATA_QUERY_LIMIT)
    include_payload = _bool_arg(args, "include_payload", "includePayload", default=True)
    scope_skill_id = await _resolve_raw_scope(
        db,
        user,
        skill_id=skill_id,
        run_id=run_id,
        proof_id=proof_id,
        source=source,
    )
    if source == "api_schema_snapshots" and not run_id and not _can_query_platform_scope(user):
        raise AppError("PARAM_INVALID", 400, {"detail": "api_schema_snapshots 普通账号必须指定 run_id"})

    if source == "execution_runs":
        items = await _query_execution_runs_raw(
            db,
            skill_id=scope_skill_id,
            run_id=run_id,
            limit=limit,
            include_payload=include_payload,
        )
    elif source == "execution_steps":
        items = await _query_execution_steps_raw(
            db,
            skill_id=scope_skill_id,
            run_id=run_id,
            limit=limit,
            include_payload=include_payload,
        )
    elif source == "decision_logs":
        items = await _query_decision_logs_raw(
            db,
            skill_id=scope_skill_id,
            run_id=run_id,
            limit=limit,
            include_payload=include_payload,
        )
    elif source == "collection_proofs":
        items = await _query_collection_proofs_raw(
            db,
            skill_id=scope_skill_id,
            run_id=run_id,
            proof_id=proof_id,
            args=args,
            limit=limit,
        )
    else:
        items = await _query_api_schema_snapshots_raw(db, run_id=run_id, args=args, limit=limit)

    return {
        "ok": True,
        "source": source,
        "items": items,
        "count": len(items),
        "limit": limit,
        "scope": {
            "skill_id": scope_skill_id,
            "run_id": run_id or None,
            "proof_id": proof_id or None,
            "payload_redacted": True,
        },
    }


def _run_analysis_prompt(run_id: str) -> str:
    return (
        f"请基于 Skill 运行 {run_id} 的脱敏原始数据做一次运行复盘。"
        "输出需覆盖：运行是否成功、关键输入输出摘要、异常或缺失数据、用户/审批反馈、"
        "采集 proof 和 schema 线索、可执行的下一步改进建议。不要编造上下文中不存在的事实。"
    )


async def _build_run_analysis_context(
    db: AsyncSession,
    *,
    skill_id: str | None,
    run_id: str,
    step_limit: int,
    decision_limit: int,
    proof_limit: int,
    snapshot_limit: int,
) -> dict[str, Any]:
    runs = await _query_execution_runs_raw(
        db,
        skill_id=skill_id,
        run_id=run_id,
        limit=1,
        include_payload=True,
    )
    if not runs:
        raise AppError("PARAM_INVALID", 404, {"detail": "未找到可访问的 execution_run"})
    steps = await _query_execution_steps_raw(
        db,
        skill_id=skill_id,
        run_id=run_id,
        limit=step_limit,
        include_payload=True,
    )
    decisions = await _query_decision_logs_raw(
        db,
        skill_id=skill_id,
        run_id=run_id,
        limit=decision_limit,
        include_payload=True,
    )
    proofs = await _query_collection_proofs_raw(
        db,
        skill_id=skill_id,
        run_id=run_id,
        proof_id="",
        args={},
        limit=proof_limit,
    )
    snapshots = await _query_api_schema_snapshots_raw(
        db,
        run_id=run_id,
        args={},
        limit=snapshot_limit,
    )
    return {
        "run": runs[0],
        "execution_steps": steps,
        "decision_logs": decisions,
        "collection_proofs": proofs,
        "api_schema_snapshots": snapshots,
        "counts": {
            "execution_steps": len(steps),
            "decision_logs": len(decisions),
            "collection_proofs": len(proofs),
            "api_schema_snapshots": len(snapshots),
        },
        "payload_redacted": True,
    }


async def _builtin_run_analyze(
    db: AsyncSession,
    user: User,
    args: dict,
    *,
    effective_skill_id: str | None,
) -> dict:
    run_id = _text_arg(args, "run_id", "runId", "execution_run_id", "executionRunId")
    if not run_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "run_id 必填"})
    skill_id = _text_arg(args, "skill_id", "skillId") or (effective_skill_id or "")
    if skill_id == "codex-mcp-stdio":
        skill_id = ""
    scope_skill_id = await _resolve_raw_scope(
        db,
        user,
        skill_id=skill_id,
        run_id=run_id,
        proof_id="",
        source="execution_runs",
    )
    context_pack = await _build_run_analysis_context(
        db,
        skill_id=scope_skill_id,
        run_id=run_id,
        step_limit=_int_arg(args, "step_limit", default=20, minimum=1, maximum=RAW_DATA_QUERY_LIMIT),
        decision_limit=_int_arg(args, "decision_limit", default=50, minimum=1, maximum=RAW_DATA_QUERY_LIMIT),
        proof_limit=_int_arg(args, "proof_limit", default=50, minimum=1, maximum=RAW_DATA_QUERY_LIMIT),
        snapshot_limit=_int_arg(args, "snapshot_limit", default=20, minimum=1, maximum=RAW_DATA_QUERY_LIMIT),
    )
    prompt = _text_arg(args, "prompt", "question", "query") or _run_analysis_prompt(run_id)
    ai_result = await _builtin_platform_ai_analyze(
        db,
        user,
        {
            "prompt": prompt,
            "context_pack": context_pack,
            "json_mode": _bool_arg(args, "json_mode", "jsonMode", default=False),
            "max_output_tokens": _int_arg(
                args,
                "max_output_tokens",
                default=4096,
                minimum=1,
                maximum=PLATFORM_AI_MCP_MAX_OUTPUT_TOKENS,
            ),
            "temperature": args.get("temperature", 0.2),
            "skill_id": scope_skill_id or "",
        },
        effective_skill_id=scope_skill_id,
        effective_run_id=run_id,
    )
    include_raw = _bool_arg(args, "include_raw", "includeRaw", default=False)
    return {
        "ok": True,
        "run_id": run_id,
        "skill_id": scope_skill_id,
        "analysis": ai_result["output"],
        "model": ai_result["model"],
        "max_context_tokens": ai_result["max_context_tokens"],
        "prompt_hash": ai_result["prompt_hash"],
        "raw_counts": context_pack["counts"],
        "raw_data": context_pack if include_raw else None,
        "credential_location": "platform_only",
    }


async def _builtin_qianchuan_video_content_analysis(args: dict) -> dict:
    from app.browser import service as browser_service

    async def _fetch(spec: dict[str, Any]) -> dict[str, Any]:
        return await browser_service.fetch_json(
            url=spec["url"],
            method=spec.get("method", "GET"),
            headers=spec.get("headers") or {},
            body=spec.get("body"),
            page_url=spec.get("page_url"),
        )

    return await collect_qianchuan_video_content_analysis_async(args, _fetch)


async def analyze_execution_run_with_platform_ai(
    db: AsyncSession,
    user: User,
    *,
    run_id: str,
    skill_id: str | None = None,
    prompt: str = "",
    include_raw: bool = False,
    json_mode: bool = False,
    max_output_tokens: int = 4096,
    temperature: float | None = None,
    step_limit: int = 20,
    decision_limit: int = 50,
    proof_limit: int = 50,
    snapshot_limit: int = 20,
) -> dict:
    args: dict[str, Any] = {
        "run_id": run_id,
        "skill_id": skill_id or "",
        "prompt": prompt or "",
        "include_raw": include_raw,
        "json_mode": json_mode,
        "max_output_tokens": max_output_tokens,
        "step_limit": step_limit,
        "decision_limit": decision_limit,
        "proof_limit": proof_limit,
        "snapshot_limit": snapshot_limit,
    }
    if temperature is not None:
        args["temperature"] = temperature
    return await _builtin_run_analyze(
        db,
        user,
        args,
        effective_skill_id=skill_id,
    )


async def _builtin_agent_coverage(
    db: AsyncSession,
    user: User,
    args: dict,
) -> dict:
    from app.aiclaw.router import get_agent_department_coverage

    data = await get_agent_department_coverage(db=db, current_user=user)
    department_filter = _text_arg(args, "department", "dept").lower()
    status_filter = _text_arg(args, "status").lower()
    include_agents = _bool_arg(args, "include_agents", "includeAgents", default=True)
    items = list(data.get("items") or [])
    if department_filter:
        items = [
            item
            for item in items
            if department_filter in str(item.get("department") or "").lower()
        ]
    if status_filter:
        items = [item for item in items if str(item.get("status") or "") == status_filter]
    if not include_agents:
        stripped = []
        for item in items:
            clone = dict(item)
            capabilities = {}
            for key, value in (clone.get("capabilities") or {}).items():
                if isinstance(value, dict):
                    capabilities[key] = {
                        inner_key: inner_value
                        for inner_key, inner_value in value.items()
                        if inner_key not in {"agents", "fallback_agents"}
                    }
            clone["capabilities"] = capabilities
            stripped.append(clone)
        items = stripped
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "summary": {
            "ready": sum(1 for item in items if item.get("status") == "ready"),
            "fallback": sum(1 for item in items if item.get("status") == "fallback"),
            "missing": sum(1 for item in items if item.get("status") == "missing"),
        },
        "scope": {
            "department": department_filter or None,
            "status": status_filter or None,
            "visible_only": True,
            "payload_redacted": True,
        },
    }


async def _call_builtin_mcp_tool(
    db: AsyncSession,
    *,
    user: User | None,
    tool: str,
    args: dict,
    dry_run: bool,
    idempotency_key: str | None,
    effective_skill_id: str | None = None,
    effective_run_id: str | None = None,
) -> dict:
    if user is None:
        raise AppError("AUTH_PERMISSION_DENIED", 403, {"detail": "内置 SkillForge MCP 工具只允许已登录 CLI 用户调用"})
    assert_active_user(user)
    if tool == "skillforge_data_capability_list":
        return await _builtin_data_capability_list(db, user, args)
    if tool == "skillforge_data_capability_latest":
        return await _builtin_data_capability_latest(db, user, args)
    if tool == "skillforge_data_artifact_get":
        return await _builtin_data_artifact_get(db, user, args)
    if tool == "skillforge_sf_data_write":
        return await sf_data_service.write_sf_data(
            db,
            user,
            args,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            source_tool=tool,
            source="mcp",
            skill_id=effective_skill_id,
            run_id=effective_run_id,
        )
    if tool == "skillforge_sf_data_list":
        return await sf_data_service.list_sf_data_records(
            db,
            user,
            page=int(args.get("page") or 1),
            page_size=int(args.get("page_size") or args.get("pageSize") or 50),
            namespace=_text_arg(args, "namespace"),
            content_type=_text_arg(args, "content_type", "contentType"),
            skill_id=_text_arg(args, "skill_id", "skillId") or effective_skill_id,
            run_id=_text_arg(args, "run_id", "runId"),
            source=_text_arg(args, "source"),
            q=_text_arg(args, "q", "query"),
        )
    if tool == "skillforge_sf_data_get":
        record_id = _text_arg(args, "id", "record_id", "recordId")
        if not record_id:
            raise AppError("PARAM_INVALID", 422, {"detail": "id is required"})
        return await sf_data_service.get_sf_data_record(db, user, record_id)
    if tool == "skillforge_samplebrand_cloud_video_data_latest":
        return await _builtin_data_capability_latest(
            db,
            user,
            args,
            capability_key="cloud_video.samplebrand_weekly.raw_collection",
        )
    if tool == "skillforge_samplebrand_cloud_video_data_get":
        return await _builtin_data_artifact_get(
            db,
            user,
            args,
            capability_key="cloud_video.samplebrand_weekly.raw_collection",
        )
    if tool == "skillforge_samplebrand_cloud_video_daily_analysis_input":
        return await _builtin_samplebrand_daily_analysis_input(db, user, args)
    if tool == "skillforge_tmall_link_decline_data_latest":
        return await _builtin_data_capability_latest(
            db,
            user,
            args,
            capability_key="tmall.link_decline.raw_collection",
        )
    if tool == "skillforge_tmall_link_decline_data_get":
        return await _builtin_data_artifact_get(
            db,
            user,
            args,
            capability_key="tmall.link_decline.raw_collection",
        )
    if tool == "skillforge_yuyidata_customer_service_data_latest":
        return await _builtin_data_capability_latest(
            db,
            user,
            args,
            capability_key="yuyidata.customer_service.raw_collection",
        )
    if tool == "skillforge_yuyidata_customer_service_data_get":
        return await _builtin_data_artifact_get(
            db,
            user,
            args,
            capability_key="yuyidata.customer_service.raw_collection",
        )
    if tool == "skillforge_execution_artifact_summary":
        return await _builtin_execution_artifact_summary(db, user, args)
    if tool == "skillforge_execution_artifact_latest":
        return await _builtin_execution_artifact_latest(db, user, args)
    if tool == "skillforge_org_search_users":
        return await _builtin_org_search_users(db, user, args)
    if tool == "skillforge_org_list_members":
        return await _builtin_org_list_members(db, user, args)
    if tool == "skillforge_dingtalk_send_work_notice":
        return await _builtin_dingtalk_send_work_notice(
            db,
            user,
            args,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
        )
    if tool == "skillforge_qianchuan_video_content_analysis":
        return await _builtin_qianchuan_video_content_analysis(args)
    if tool == "skillforge_agent_coverage":
        return await _builtin_agent_coverage(db, user, args)
    if tool == "skillforge_ai_analyze":
        return await _builtin_platform_ai_analyze(
            db,
            user,
            args,
            effective_skill_id=effective_skill_id,
            effective_run_id=effective_run_id,
        )
    if tool == "skillforge_raw_data_query":
        return await _builtin_raw_data_query(
            db,
            user,
            args,
            effective_skill_id=effective_skill_id,
        )
    if tool == "skillforge_run_analyze":
        return await _builtin_run_analyze(
            db,
            user,
            args,
            effective_skill_id=effective_skill_id,
        )
    if tool == "skillforge_cloud_video_session":
        return await codex_cloud_video.builtin_cloud_video_session(args)
    if tool == "skillforge_cloud_video_accounts":
        return await codex_cloud_video.builtin_cloud_video_accounts(args)
    if tool == "skillforge_cloud_video_categories":
        return await codex_cloud_video.builtin_cloud_video_categories(args)
    if tool == "skillforge_cloud_video_tags":
        return await codex_cloud_video.builtin_cloud_video_tags(args)
    if tool == "skillforge_cloud_video_videos":
        return await codex_cloud_video.builtin_cloud_video_videos(args)
    if tool == "skillforge_cloud_video_visual_analysis":
        return await codex_cloud_video.builtin_cloud_video_visual_analysis(args)
    if tool == "skillforge_cloud_video_ad_report":
        return await codex_cloud_video.builtin_cloud_video_ad_report(args)
    if tool == "skillforge_cloud_video_material_report":
        return await codex_cloud_video.builtin_cloud_video_material_report(args)
    if tool == "skillforge_cloud_video_video_usage_report":
        return await codex_cloud_video.builtin_cloud_video_video_usage_report(args)
    if tool == "skillforge_cloud_video_audit_rejects":
        return await codex_cloud_video.builtin_cloud_video_audit_rejects(args)
    if tool == "skillforge_cloud_video_daily_person_video_report":
        return await codex_cloud_video.builtin_cloud_video_daily_person_video_report(args)
    raise AppError("MCP_SCOPE_DENIED", 403, {"tool": tool})


def tool_script_for_name(tool: str) -> Path:
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if tool.startswith("tmall_"):
        return scripts_dir / "tmall_mcp_server.py"
    if tool.startswith("yuyidata_"):
        return scripts_dir / "yuyidata_mcp_server.py"
    raise AppError("MCP_SCOPE_DENIED", 403, {"tool": tool})


def _parse_mcp_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("MCP server returned empty stdout")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        last_obj = None
        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break
            if text[idx] != "{":
                next_start = text.find("{", idx + 1)
                if next_start < 0:
                    break
                idx = next_start
                continue
            try:
                last_obj, idx = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                next_start = text.find("{", idx + 1)
                if next_start < 0:
                    raise
                idx = next_start
        envelope = last_obj
    if not isinstance(envelope, dict):
        raise RuntimeError("MCP server returned invalid JSON-RPC envelope")
    return envelope


async def call_mcp_tool(
    db: AsyncSession,
    principal: CliPrincipal | RunPrincipal | RuntimePrincipal,
    *,
    server: str,
    tool: str,
    arguments: dict,
    skill_id: str | None,
    run_id: str | None,
    run_mode: str,
    dry_run: bool | None,
    idempotency_key: str | None,
) -> dict:
    if server != "skillforge":
        raise AppError("MCP_SCOPE_DENIED", 403, {"server": server})

    meta = _load_tool_registry().get(tool)
    if not meta:
        raise AppError("MCP_SCOPE_DENIED", 403, {"tool": tool})

    run: CodexDebugRun | RuntimePrincipal | None = None
    user = getattr(principal, "user", None)
    actor_user_id = getattr(user, "id", None) or getattr(principal, "user_id", "runtime")
    if isinstance(principal, RunPrincipal):
        run = principal.run
        if skill_id and skill_id != run.skill_id:
            raise AppError("SKILL_ACCESS_DENIED", 403)
    elif isinstance(principal, RuntimePrincipal):
        run = principal
        if skill_id and skill_id != run.skill_id:
            raise AppError("SKILL_ACCESS_DENIED", 403)
        if user is None:
            user = await _resolve_runtime_skill_actor(db, skill_id=run.skill_id)
            principal.user = user
            principal.user_id = user.id
            actor_user_id = user.id
    elif run_id:
        run = await db.get(CodexDebugRun, run_id)
        if not run or run.user_id != actor_user_id or run.expires_at <= utc_safe_now() or run.status != "running":
            raise AppError("DEBUG_RUN_EXPIRED", 401)
        if skill_id and skill_id != run.skill_id:
            raise AppError("SKILL_ACCESS_DENIED", 403)
    else:
        principal = await create_implicit_mcp_run(db, principal, skill_id=skill_id)
        run = principal.run

    if not run or getattr(run, "status", "running") != "running":
        raise AppError("DEBUG_RUN_EXPIRED", 401)
    limits = getattr(run, "limits_json", None) or DEFAULT_DEBUG_LIMITS
    if isinstance(run, CodexDebugRun) and run.tool_call_count >= int(limits.get("max_tool_calls") or 50):
        raise AppError("RATE_LIMITED", 429, {"retry_after_seconds": 60})

    effective_skill_id = run.skill_id if isinstance(principal, RunPrincipal) else (skill_id or run.skill_id)
    skill_department = None
    if effective_skill_id and effective_skill_id != "codex-mcp-stdio":
        skill = await db.get(Skill, effective_skill_id)
        skill_department = getattr(skill, "department", None) if skill else None
        if skill and user is not None:
            perms = await get_skill_permissions(db, skill, user)
            if tool in {"skillforge_ai_analyze", "skillforge_raw_data_query", "skillforge_run_analyze"}:
                allowed = bool(perms.get("read"))
            else:
                allowed = bool(perms.get("execute") or perms.get("edit") or perms.get("publish") or perms.get("review"))
            if not allowed:
                raise AppError("SKILL_ACCESS_DENIED", 403)

    request_id = new_id("req")
    proof_id = new_id("proof")
    call_source = "skill_runtime" if isinstance(principal, RuntimePrincipal) else "codex_cli"
    args = await inject_yuyidata_arguments(db, tool, dict(arguments or {}))
    shop_id = args.get("shop_id") or args.get("shopId")
    effective_dry_run = True if dry_run is None else bool(dry_run)
    idempotency_key = _normalize_idempotency_key(
        idempotency_key or _text_arg(args, "idempotency_key", "idempotencyKey") or None
    )
    if not effective_dry_run and idempotency_key is None and bool(getattr(meta, "write", False)):
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", 400)

    timeout_seconds = min(max(int(limits.get("timeout_seconds") or 300), 3), 1800)
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    effective_run_id = run.id if isinstance(run, CodexDebugRun) else run.run_id
    ok = False
    error_code = None
    try:
        if tool in BUILTIN_MCP_TOOL_NAMES:
            data = await _call_builtin_mcp_tool(
                db,
                user=user,
                tool=tool,
                args=args,
                dry_run=effective_dry_run,
                idempotency_key=idempotency_key,
                effective_skill_id=effective_skill_id,
                effective_run_id=effective_run_id,
            )
        else:
            script = tool_script_for_name(tool)
            env = os.environ.copy()
            if tool.startswith("yuyidata_"):
                env.update(await yuyidata_mcp_env(db))
            env["SKILLFORGE_PLATFORM_URL"] = str(settings.PUBLIC_BASE_URL).rstrip("/")
            env["SKILLFORGE_SKILL_ID"] = effective_skill_id or ""
            env["SKILLFORGE_RUN_ID"] = effective_run_id
            env["SKILLFORGE_RUN_MODE"] = run.run_mode
            effective_instance_id = (
                (principal.claims or {}).get("instance_id")
                if isinstance(principal, RuntimePrincipal)
                else None
            ) or "codex-local"
            env["SKILLFORGE_INSTANCE_ID"] = str(effective_instance_id)
            env["INSTANCE_ID"] = str(effective_instance_id)
            try:
                from app.execution.execution_service import issue_run_token

                nested_run_token = issue_run_token(
                    skill_id=effective_skill_id or "codex-mcp-stdio",
                    run_id=effective_run_id,
                    instance_id=str(effective_instance_id),
                    department=skill_department,
                    ttl_seconds=timeout_seconds + 60,
                )
                env["SKILLFORGE_RUN_TOKEN"] = nested_run_token
                env["RUN_TOKEN"] = nested_run_token
            except Exception as exc:  # noqa: BLE001
                logger.warning("issue nested MCP run token failed run={} err={}", effective_run_id, exc)
            if shop_id:
                env["SKILLFORGE_SHOP_ID"] = str(shop_id)
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(script)],
                input=json.dumps(req, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(script.parent.parent),
                env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:500])
            envelope = _parse_mcp_stdout(proc.stdout or "")
            result = envelope.get("result") or {}
            if result.get("isError"):
                content = result.get("content") or []
                text = content[0].get("text") if content and isinstance(content[0], dict) else str(result)
                raise RuntimeError(text[:500])
            content = result.get("content") or []
            text = content[0].get("text") if content and isinstance(content[0], dict) else "{}"
            data = json.loads(text) if isinstance(text, str) else text
            if not isinstance(data, (dict, list)):
                data = {"value": data}
        ok = True
    except AppError as exc:
        error_code = exc.code
        data = {"error": exc.message, "detail": exc.detail or {}}
        raise
    except Exception as exc:  # noqa: BLE001
        error_code = "MCP_CALL_FAILED"
        data = {"error": str(exc)[:500]}
    finally:
        if isinstance(run, CodexDebugRun):
            run.tool_call_count += 1
        audit_row = CodexMcpCallAudit(
            request_id=request_id,
            proof_id=proof_id,
            user_id=str(actor_user_id)[:50],
            skill_id=effective_skill_id,
            debug_run_id=effective_run_id,
            server=server,
            tool=tool,
            data_scope=meta.data_scope,
            shop_id=str(shop_id) if shop_id else None,
            run_mode=run_mode or run.run_mode,
            dry_run=effective_dry_run,
            ok=ok,
            error_code=error_code,
            detail_json={"idempotency_key": idempotency_key, "call_source": call_source},
        )
        db.add(audit_row)
        await audit.log(
            str(actor_user_id)[:50],
            "codex.mcp.call",
            "mcp_tool",
            tool,
            detail={
                "skill_id": effective_skill_id,
                "run_id": effective_run_id,
                "proof_id": proof_id,
                "data_scope": meta.data_scope,
                "shop_id": shop_id,
                "ok": ok,
                "dry_run": effective_dry_run,
            },
        )
        await db.flush()

    if not ok:
        raise AppError("MCP_CALL_FAILED", 502, {"tool": tool, "proof_id": proof_id, "error": data.get("error")})
    return {
        "ok": True,
        "tool": tool,
        "data": data,
        "proof": {
            "id": proof_id,
            "call_source": call_source,
            "run_mode": run.run_mode,
            "user_id": actor_user_id,
            "skill_id": effective_skill_id,
            "platform": meta.platform,
            "data_scope": meta.data_scope,
            "credential_location": "platform_only",
            "dry_run": effective_dry_run,
        },
    }


def normalize_package_path(path: str) -> str:
    path = str(path).replace("\\", "/").strip("/")
    pure = PurePosixPath(path)
    if path.startswith("/") or any(part in {"", ".", ".."} for part in pure.parts):
        raise AppError("PACKAGE_INVALID", 422, {"path": path, "reason": "invalid path"})
    return str(pure)


def is_allowed_package_path(path: str) -> bool:
    if path in DENIED_PACKAGE_EXACT:
        return False
    if any(path.startswith(prefix) for prefix in DENIED_PACKAGE_PREFIXES):
        return False
    return path in ALLOWED_PACKAGE_EXACT or any(path.startswith(prefix) for prefix in ALLOWED_PACKAGE_DIRS)


SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer\s+[a-z0-9._~+/=-]{20,})"),
    re.compile(r"(?i)(authorization\s*[:=]\s*[^\n]{12,})"),
    re.compile(r"(?i)(access_token|refresh_token|api_key|secret_key|client_secret)\s*[:=]\s*[\"']?[a-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |)?PRIVATE KEY-----"),
    re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
]


def scan_secret(path: str, data: bytes) -> str | None:
    if len(data) > MAX_PACKAGE_FILE_BYTES:
        return "file too large"
    text = data[:512 * 1024].decode("utf-8", errors="ignore")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return f"secret pattern matched: {pattern.pattern[:40]}"
    return None


def normalized_package_hash(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for path, data in sorted(files, key=lambda item: item[0]):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _tar_package_bytes(files: list[tuple[str, bytes]]) -> bytes:
    buffer = tempfile.SpooledTemporaryFile(max_size=MAX_PACKAGE_BYTES)
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for rel, data in sorted(files, key=lambda item: item[0]):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            tar.addfile(info, fileobj=io.BytesIO(data))
    buffer.seek(0)
    package = buffer.read()
    buffer.close()
    if len(package) > MAX_PACKAGE_BYTES:
        raise AppError("PACKAGE_INVALID", 422, {"reason": "package too large"})
    return package


def _skill_pull_metadata_yaml(skill: Skill, remote_head: str | None, existing: bytes | None = None) -> bytes:
    data: dict[str, Any] = {}
    if existing:
        try:
            parsed = yaml.safe_load(existing.decode("utf-8")) or {}
            if isinstance(parsed, dict):
                data.update(parsed)
        except Exception:
            data = {}
    data["skill_id"] = skill.id
    if remote_head:
        data["base_commit"] = remote_head
    if getattr(skill, "name", None):
        data.setdefault("name", skill.name)
    if getattr(skill, "department", None):
        data.setdefault("department", skill.department)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def build_skill_pull_package(skill: Skill) -> tuple[bytes, str, dict[str, Any]]:
    """Build a safe Codex pull package from the Skill Git workspace.

    This reuses the submission package whitelist and secret scanner. It never
    exposes `.git`, env files, hidden local state, or files outside the Skill
    package contract.
    """
    if not git_service.skill_exists(skill.id):
        raise AppError("SKILL_GIT_NOT_FOUND", 404, {"skill_id": skill.id})
    root = git_service.skill_dir(skill.id).resolve()
    remote_logs = git_service.log(skill_id=skill.id, max_count=1) if git_service.skill_exists(skill.id) else []
    remote_head = remote_logs[0]["hash_full"] if remote_logs else getattr(skill, "git_commit", None)
    files: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            rel = normalize_package_path(str(path.relative_to(root)))
        except AppError:
            continue
        if not is_allowed_package_path(rel):
            continue
        data = path.read_bytes()
        if len(data) > MAX_PACKAGE_FILE_BYTES:
            raise AppError("PACKAGE_INVALID", 422, {"path": rel, "reason": "file too large"})
        secret_reason = scan_secret(rel, data)
        if secret_reason:
            raise AppError("PACKAGE_INVALID", 422, {"path": rel, "reason": secret_reason})
        files.append((rel, data))
    if not any(rel == "SKILL.md" for rel, _ in files):
        raise AppError("PACKAGE_INVALID", 422, {"skill_id": skill.id, "reason": "missing SKILL.md"})
    existing_yaml = next((data for rel, data in files if rel == "skillforge.yaml"), None)
    generated_yaml = _skill_pull_metadata_yaml(skill, remote_head, existing_yaml)
    files = [(rel, data) for rel, data in files if rel != "skillforge.yaml"]
    files.append(("skillforge.yaml", generated_yaml))
    digest = normalized_package_hash(files)
    manifest = {
        "skill_id": skill.id,
        "name": skill.name,
        "department": skill.department,
        "git_commit": getattr(skill, "git_commit", None),
        "remote_head": remote_head,
        "base_commit": remote_head,
        "package_hash": digest,
        "file_count": len(files),
        "paths": [rel for rel, _ in sorted(files, key=lambda item: item[0])],
    }
    return _tar_package_bytes(files), digest, manifest


def extract_and_validate_package(package_bytes: bytes) -> tuple[Path, list[tuple[str, bytes]], dict]:
    if len(package_bytes) > MAX_PACKAGE_BYTES:
        raise AppError("PACKAGE_INVALID", 422, {"reason": "package too large"})
    tmpdir = Path(tempfile.mkdtemp(prefix="codex-skill-package-"))
    archive = tmpdir / "package.tar.gz"
    archive.write_bytes(package_bytes)
    files: list[tuple[str, bytes]] = []
    checks = {"secret_scan": "passed", "paths": [], "file_count": 0}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                if member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise AppError("PACKAGE_INVALID", 422, {"path": member.name, "reason": "unsupported tar entry"})
                rel = normalize_package_path(member.name)
                if not is_allowed_package_path(rel):
                    raise AppError("PACKAGE_INVALID", 422, {"path": rel, "reason": "path not allowed"})
                if int(member.size or 0) > MAX_PACKAGE_FILE_BYTES:
                    raise AppError("PACKAGE_INVALID", 422, {"path": rel, "reason": "file too large"})
                extracted = tar.extractfile(member)
                data = extracted.read() if extracted else b""
                secret_reason = scan_secret(rel, data)
                if secret_reason:
                    raise AppError("PACKAGE_INVALID", 422, {"path": rel, "reason": secret_reason})
                files.append((rel, data))
                checks["paths"].append(rel)
        if not files:
            raise AppError("PACKAGE_INVALID", 422, {"reason": "empty package"})
        checks["file_count"] = len(files)
        extract_dir = tmpdir / "extract"
        extract_dir.mkdir()
        for rel, data in files:
            dst = extract_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        return extract_dir, files, checks
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def parse_package_frontmatter(files: list[tuple[str, bytes]]) -> dict:
    skill_md = next((data for path, data in files if path == "SKILL.md"), None)
    if skill_md is None:
        raise AppError("PACKAGE_INVALID", 422, {"reason": "missing SKILL.md"})
    text = skill_md.decode("utf-8", errors="strict")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": {"errors": ["缺少 YAML frontmatter"]}})
    try:
        parsed = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {"detail": {"errors": [f"frontmatter YAML 解析失败: {exc}"]}},
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": {"errors": ["frontmatter 必须是字典"]}})
    return parsed


def _decode_package_text(files: list[tuple[str, bytes]], path: str) -> str | None:
    data = next((content for rel, content in files if rel == path), None)
    if data is None:
        return None
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": {"errors": [f"{path} 必须是 UTF-8"]}}) from exc


def _load_package_contract(files: list[tuple[str, bytes]]) -> dict:
    raw = _decode_package_text(files, "contract.json")
    if raw is None:
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {"detail": {"source": "contract_validation", "errors": ["缺少 contract.json"]}},
        )
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {"detail": {"source": "contract_validation", "errors": [f"contract.json 不是合法 JSON: {exc.msg}"]}},
        ) from exc
    if not isinstance(contract, dict):
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {"detail": {"source": "contract_validation", "errors": ["contract.json 顶层必须是对象"]}},
        )
    return contract


def _output_table_fields(contract: dict) -> set[str]:
    raw = contract.get("output_table")
    if raw is None and isinstance(contract.get("output"), dict):
        raw = (contract.get("output") or {}).get("output_table")
    if not isinstance(raw, list):
        return set()
    fields: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = str(item.get("name") or item.get("key") or item.get("field") or item.get("id") or "").strip()
        if field:
            fields.add(field)
    return fields


def validate_package_contract(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Validate the non-executable Codex package contract gate."""
    contract = _load_package_contract(files)
    errors: list[str] = []
    output_schema = get_output_schema(contract)
    if not isinstance(output_schema, dict):
        errors.append("contract.json 缺少 output_schema（JSON Schema Draft-07）")
        output_schema = None
    else:
        errors.extend(lint_output_schema(output_schema))

    platform_fields = {"todos", "reports", "诊断报告"}
    required = {
        str(item)
        for item in ((output_schema or {}).get("required") or [])
        if isinstance(item, str) and item not in platform_fields
    }

    output_table = _output_table_fields(contract)
    if output_table:
        missing_table = required - output_table
        extra_table = output_table - required
        if missing_table:
            errors.append(f"output_table 缺少 output_schema.required 字段: {sorted(missing_table)}")
        if extra_table:
            errors.append(f"output_table 声明了未进入 output_schema.required 的字段: {sorted(extra_table)}")

    skill_md = _decode_package_text(files, "SKILL.md") or ""
    skill_output_fields = set(parse_skill_md_output_fields(skill_md)) - platform_fields
    if skill_output_fields:
        missing_skill_md = required - skill_output_fields
        extra_skill_md = skill_output_fields - required
        if missing_skill_md:
            errors.append(f"SKILL.md §输出定义 缺少 output_schema.required 字段: {sorted(missing_skill_md)}")
        if extra_skill_md:
            errors.append(f"SKILL.md §输出定义 声明了未进入 output_schema.required 的字段: {sorted(extra_skill_md)}")

    return {
        "ok": not errors,
        "source": "contract_validation",
        "required": sorted(required),
        "output_table": sorted(output_table),
        "skill_md_output_fields": sorted(skill_output_fields),
        "errors": errors,
    }


def resolve_manifest_skill_id(manifest: dict, frontmatter: dict) -> str:
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    nested_manifest = manifest.get("manifest") if isinstance(manifest.get("manifest"), dict) else {}
    skill_id = str(
        manifest.get("skill_id")
        or nested_manifest.get("skill_id")
        or manifest.get("id")
        or metadata.get("skill_id")
        or frontmatter.get("name")
        or ""
    ).strip()
    if not skill_id:
        raise AppError("PACKAGE_INVALID", 422, {"reason": "missing skill_id"})
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$", skill_id):
        raise AppError("PACKAGE_INVALID", 422, {"reason": "invalid skill_id"})
    return skill_id


async def upsert_skill_metadata(
    db: AsyncSession,
    *,
    user: User,
    skill_id: str,
    frontmatter: dict,
    git_commit: str | None,
) -> Skill:
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    department = (
        frontmatter.get("department")
        or metadata.get("department")
        or getattr(user, "department", None)
        or "未分配"
    )
    owner_id = str(frontmatter.get("owner") or metadata.get("owner") or user.id)[:50]
    existing = await db.get(Skill, skill_id)
    if existing:
        await require_skill_access(db, skill_id, user, "edit")
        skill = existing
    else:
        if not role_matches_any(user, ("admin", "ai_engineer", "biz_owner", "aibp")):
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        skill = Skill(
            id=skill_id,
            name=str(frontmatter.get("name") or skill_id)[:100],
            description=frontmatter.get("description"),
            department=str(department)[:50],
            owner=owner_id,
            status="draft",
            visibility="department",
        )
        db.add(skill)

    sync_skill_fields_from_frontmatter(skill, frontmatter)
    if not skill.department or skill.department == "未分配":
        skill.department = str(department)[:50]
    if frontmatter.get("description") is not None:
        skill.description = str(frontmatter.get("description") or "")
    if frontmatter.get("owner") is not None or metadata.get("owner") is not None:
        skill.owner = owner_id
    if git_commit:
        skill.git_commit = git_commit[:40]
    await db.flush()
    owner_member = await db.get(SkillMember, {"skill_id": skill_id, "user_id": skill.owner})
    if not owner_member and skill.owner:
        db.add(SkillMember(skill_id=skill_id, user_id=skill.owner, role="owner", granted_by=user.id))
        await db.flush()
    return skill


async def create_or_update_skill_from_package(
    db: AsyncSession,
    *,
    user: User,
    package_bytes: bytes,
    manifest: dict,
    supplied_package_hash: str,
    base_commit: str | None,
    message: str,
    force_review_submit: bool = False,
    force_review_reason: str = "",
) -> CodexSkillSubmission:
    if force_review_submit and not str(force_review_reason or "").strip():
        raise AppError("PARAM_INVALID", 400, {"detail": "force_review_reason is required"})
    extract_dir, files, checks = extract_and_validate_package(package_bytes)
    try:
        actual_hash = normalized_package_hash(files)
        if supplied_package_hash and supplied_package_hash != actual_hash:
            raise AppError("PACKAGE_INVALID", 422, {"reason": "package_hash mismatch", "actual": actual_hash})

        validation = structural_validate(extract_dir)
        checks["structural_validation"] = validation.to_detail()
        if not validation.ok:
            raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": validation.to_detail()})

        contract_validation = validate_package_contract(files)
        checks["contract_validation"] = contract_validation
        if not contract_validation["ok"]:
            raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": contract_validation})

        frontmatter = parse_package_frontmatter(files)
        skill_id = resolve_manifest_skill_id(manifest, frontmatter)
        if base_commit and not FULL_GIT_OBJECT_RE.match(base_commit):
            raise AppError("PACKAGE_INVALID", 422, {"reason": "base_commit must be full git object id"})
        existing_skill = await db.get(Skill, skill_id)
        if existing_skill:
            await require_skill_access(db, skill_id, user, "edit")
        elif not role_matches_any(user, ("admin", "ai_engineer", "biz_owner", "aibp")):
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        submission = CodexSkillSubmission(
            id=new_id("sub"),
            user_id=user.id,
            skill_id=skill_id,
            package_hash=actual_hash,
            base_commit=base_commit or None,
            message=message,
            status="pending",
            checks_json=checks,
            manifest_json={**manifest, "frontmatter": frontmatter},
        )

        async with git_service.skill_advisory_lock(skill_id):
            logs = git_service.log(skill_id=skill_id, max_count=1) if git_service.skill_exists(skill_id) else []
            remote_head = logs[0]["hash_full"] if logs else None
            if remote_head and base_commit != remote_head:
                raise AppError("PACKAGE_CONFLICT", 409, {"remote_head": remote_head, "base_commit": base_commit})
            if not remote_head and base_commit:
                raise AppError("PACKAGE_CONFLICT", 409, {"remote_head": None, "base_commit": base_commit})
            target = git_service.skill_dir(skill_id)
            target.mkdir(parents=True, exist_ok=True)
            for existing in list(target.iterdir()):
                if existing.is_dir():
                    shutil.rmtree(existing)
                else:
                    existing.unlink()
            for rel, data in files:
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            commit = git_service.commit_all(
                message or f"codex submit {skill_id}",
                author=user.id,
                skill_id=skill_id,
                validate=False,
            )
            submission.git_commit = commit or remote_head

        await upsert_skill_metadata(
            db,
            user=user,
            skill_id=skill_id,
            frontmatter=frontmatter,
            git_commit=submission.git_commit,
        )

        try:
            from app.reviews import service as review_service

            review = await review_service.create_review(
                db,
                skill_id=skill_id,
                submitter=user.id,
                change_type="codex_submit",
                diff_summary=message or f"Codex 提交 {skill_id}",
                diff_content={"source": "codex_cli", "package_hash": actual_hash, "checks": checks},
                reason=message or "Codex 本地提交",
                force_submit=force_review_submit,
                force_reason=force_review_reason,
            )
            submission.review_id = review.get("id") or review.get("review_id")
            submission.status = "review_pending"
            if force_review_submit:
                await audit.log(
                    user.id,
                    "codex.skill.force_submit",
                    "skill",
                    skill_id,
                    detail={
                        "submission_id": submission.id,
                        "review_id": submission.review_id,
                        "package_hash": actual_hash,
                        "base_commit": base_commit or None,
                        "force_reason": force_review_reason,
                        "gate": review.get("force_submit_gate"),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None)
            logger.warning("codex submission review creation failed skill={} err={} detail={}", skill_id, exc, detail)
            submission.status = "committed"
            review_error = str(exc)[:300]
            if detail is not None:
                submission.checks_json = {
                    **checks,
                    "review_error": review_error,
                    "review_error_detail": detail,
                }
            else:
                submission.checks_json = {**checks, "review_error": review_error}

        db.add(submission)
        await db.flush()
        await audit.log(
            user.id,
            "codex.skill.submit",
            "skill",
            skill_id,
            detail={"submission_id": submission.id, "package_hash": actual_hash},
        )
        return submission
    finally:
        shutil.rmtree(extract_dir.parent, ignore_errors=True)


def safe_tail(value: Any) -> str:
    text = str(value or "")
    return text[-STDOUT_TAIL_LIMIT:]
