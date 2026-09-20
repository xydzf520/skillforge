"""
数据源管理服务：CSV上传+解析+质量校验+API拉取。
"""

import asyncio
import contextlib
import csv
import hashlib
import io
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from loguru import logger
from sqlalchemy import Integer, and_, cast, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import expand_org_lineage
from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt, to_bjt_naive
from app.common.models import SystemConfig
from app.config import settings
from app.datasources.models import (
    ConnectorApiKey,
    DataAccessGrant,
    DataAccessRequest,
    DataIngestionLog,
    DataSource,
    PlatformApiDriftAck,
    PlatformCookieAudit,
    PlatformCookiePool,
)
from app.org.models import OrgUnit, UserOrgMembership

# ── push_cookies → docker Chrome 自动同步（节流 + fire-and-forget）─────
# 扩展每分钟 push 一次，没节流的话每次都会开 CDP WebSocket，噪音大；
# 按 source_id 维度做 30s 最短间隔节流。
_COOKIE_SYNC_THROTTLE_SECONDS = 30.0
_COOKIE_VERIFY_THROTTLE_SECONDS = 60.0
_last_cookie_sync_ts: dict[str, float] = {}
_last_cookie_verify_ts: dict[str, float] = {}
# 保持 task 引用防 GC（asyncio.create_task 的惯用 pattern）
_cookie_sync_tasks: set[asyncio.Task[Any]] = set()
_cookie_verify_tasks: set[asyncio.Task[Any]] = set()


def _validate_source_id(source_id: str) -> None:
    """校验source_id格式：仅允许字母/数字/短横线/下划线，最多50字符，防止路径遍历"""
    if not source_id or len(source_id) > 50:
        raise AppError("INVALID_SOURCE_ID", 400,
                       detail={"reason": "source_id长度必须在1-50之间"})
    if not re.match(r"^[a-zA-Z0-9_\-]+$", source_id):
        raise AppError("INVALID_SOURCE_ID", 400,
                       detail={"reason": "source_id只能包含字母、数字、短横线和下划线"})


async def list_sources(
    db: AsyncSession,
    department: str | None = None,
    source_type: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """列出数据源（支持分页 + 筛选）。"""
    stmt = select(DataSource)
    count_stmt = select(func.count()).select_from(DataSource)
    if department:
        stmt = stmt.where(DataSource.department == department)
        count_stmt = count_stmt.where(DataSource.department == department)
    if source_type:
        normalized = source_type.strip().lower()
        variants = {
            "csv": ("csv", "csv_upload"),
            "api": ("api", "api_pull"),
            "platform_cookies": ("platform_cookies",),
        }.get(normalized, (normalized,))
        if len(variants) == 1:
            stmt = stmt.where(DataSource.source_type == variants[0])
            count_stmt = count_stmt.where(DataSource.source_type == variants[0])
        else:
            stmt = stmt.where(DataSource.source_type.in_(variants))
            count_stmt = count_stmt.where(DataSource.source_type.in_(variants))
    if is_active is not None:
        stmt = stmt.where(DataSource.is_active == is_active)  # noqa: E712
        count_stmt = count_stmt.where(DataSource.is_active == is_active)  # noqa: E712

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = (
        stmt
        .order_by(DataSource.department, DataSource.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(stmt)
    sources = result.scalars().all()

    source_ids = [s.id for s in sources]
    latest_log_by_source: dict[str, DataIngestionLog] = {}
    if source_ids:
        latest_created = (
            select(
                DataIngestionLog.source_id.label("source_id"),
                func.max(DataIngestionLog.created_at).label("created_at"),
            )
            .where(DataIngestionLog.source_id.in_(source_ids))
            .where(DataIngestionLog.status == "success")
            .group_by(DataIngestionLog.source_id)
            .subquery()
        )
        latest_logs = await db.execute(
            select(DataIngestionLog)
            .join(
                latest_created,
                and_(
                    DataIngestionLog.source_id == latest_created.c.source_id,
                    DataIngestionLog.created_at == latest_created.c.created_at,
                ),
            )
            .order_by(DataIngestionLog.source_id, DataIngestionLog.id.desc())
        )
        for log in latest_logs.scalars().all():
            latest_log_by_source.setdefault(log.source_id, log)

    # 批量查询每个数据源的最后上传时间，避免列表页 N+1。
    items = []
    for s in sources:
        last = latest_log_by_source.get(s.id)

        # config 中去掉加密 cookies（不暴露给前端）
        safe_config = {
            k: v for k, v in (s.config or {}).items()
            if k not in ("encrypted_cookies", "encrypted_cookie_details")
        }

        items.append({
            "id": s.id,
            "name": s.name,
            "department": s.department,
            "source_type": s.source_type,
            "config": safe_config,
            "schedule": s.schedule,
            "is_active": s.is_active,
            "stale_threshold_hours": s.stale_threshold_hours,
            "related_skills": s.related_skills or [],
            "last_updated": isoformat_bjt(last.created_at) if last else None,
            "last_row_count": last.row_count if last else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_source(db: AsyncSession, source_id: str) -> dict:
    """获取数据源详情"""
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 最近上传记录
    logs_result = await db.execute(
        select(DataIngestionLog)
        .where(DataIngestionLog.source_id == source_id)
        .order_by(DataIngestionLog.created_at.desc())
        .limit(10)
    )
    logs = logs_result.scalars().all()

    return {
        "id": source.id,
        "name": source.name,
        "department": source.department,
        "source_type": source.source_type,
        "config": {
            k: v for k, v in (source.config or {}).items()
            if k not in ("encrypted_cookies", "encrypted_cookie_details")
        },
        "schedule": source.schedule,
        "stale_threshold_hours": source.stale_threshold_hours,
        "quality_rules": source.quality_rules,
        "related_skills": source.related_skills or [],
        "is_active": source.is_active,
        "created_by": source.created_by,
        "history": [
            {
                "id": l.id,
                "type": l.ingestion_type,
                "uploaded_by": l.uploaded_by,
                "file_name": l.file_name,
                "row_count": l.row_count,
                "status": l.status,
                "quality_report": l.quality_report,
                "created_at": isoformat_bjt(l.created_at),
            }
            for l in logs
        ],
    }


ALLOWED_VISIBILITIES = {"company", "department", "private"}


async def create_source(
    db: AsyncSession,
    source_id: str,
    name: str,
    department: str,
    source_type: str,
    config: dict,
    quality_rules: dict | None = None,
    related_skills: list | None = None,
    user_id: str = "system",
    visibility: str = "department",
    description: str | None = None,
    usage_hint: str | None = None,
    owner_contact: str | None = None,
    **_legacy_kwargs: Any,  # 兼容 router 旧字段 sensitivity / owner_org_unit_id（尚未落库）
) -> dict:
    """创建数据源。

    v2.7 大厅 v3：新增 visibility/description/usage_hint/owner_contact 四个可选入参。
    owner 创建时自选 visibility（默认 department）。
    """
    if visibility not in ALLOWED_VISIBILITIES:
        raise AppError(
            "VISIBILITY_INVALID",
            400,
            detail={"allowed": sorted(ALLOWED_VISIBILITIES), "given": visibility},
        )
    source = DataSource(
        id=source_id,
        name=name,
        department=department,
        source_type=source_type,
        config=config,
        quality_rules=quality_rules or {},
        related_skills=related_skills,
        created_by=user_id,
        visibility=visibility,
        description=description,
        usage_hint=usage_hint,
        owner_contact=owner_contact or user_id,  # 默认 owner = 创建人
    )
    db.add(source)
    await db.flush()

    await audit.log(
        user_id,
        "datasource.create",
        "datasource",
        source_id,
        detail={"visibility": visibility},
    )
    return {"source_id": source_id, "visibility": visibility}


async def upload_csv(
    db: AsyncSession,
    source_id: str,
    file_content: bytes,
    file_name: str,
    user_id: str = "system",
) -> dict:
    """
    上传CSV文件：解析 + 质量校验 + 记录日志。
    """
    # 检查数据源存在
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    if not file_content:
        raise AppError("DATASOURCE_UPLOAD_EMPTY", 400)

    # 保存原始文件
    _validate_source_id(source_id)
    raw_dir = Path("data/raw") / source_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    date_str = now_bjt().strftime("%Y%m%d_%H%M%S")
    raw_path = (raw_dir / f"{date_str}_{file_name}").resolve()
    # 路径遍历防护：确保解析后的路径仍然在 data/raw/source_id 下
    if not str(raw_path).startswith(str(raw_dir.resolve())):
        raise AppError("INVALID_SOURCE_ID", 400, detail={"reason": "非法路径"})
    raw_path.write_bytes(file_content)

    # 解析文件（支持 CSV 和 Excel）
    is_excel = file_name.lower().endswith((".xlsx", ".xls"))
    if is_excel:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            header = [str(c or f"col_{i}") for i, c in enumerate(next(rows_iter, []))]
            columns = header
            rows = []
            for row_vals in rows_iter:
                row_dict = {}
                for i, val in enumerate(row_vals):
                    if i < len(header):
                        row_dict[header[i]] = str(val) if val is not None else ""
                rows.append(row_dict)
            wb.close()
        except Exception as e:
            raise AppError("DATASOURCE_PARSE_ERROR", 422, detail={"reason": f"Excel解析失败: {e}"})
    else:
        try:
            text = file_content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = file_content.decode("gbk")
            except UnicodeDecodeError:
                raise AppError("DATASOURCE_PARSE_ERROR", 422, detail={"reason": "编码不支持"})

        try:
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            columns = reader.fieldnames or []
        except Exception as e:
            raise AppError("DATASOURCE_PARSE_ERROR", 422, detail={"reason": str(e)})

    row_count = len(rows)

    # 获取上次成功上传的行数（用于delta检查）
    prev_row_count = await _get_previous_row_count(db, source_id)

    # 质量校验
    quality_report = _run_quality_checks(
        rows, columns, source.quality_rules or {},
        prev_row_count=prev_row_count,
    )

    # 记录日志
    status = "failed" if quality_report["has_errors"] else ("warning" if quality_report["has_warnings"] else "success")
    log_entry = DataIngestionLog(
        source_id=source_id,
        ingestion_type="upload",
        uploaded_by=user_id,
        file_name=file_name,
        row_count=row_count,
        status=status,
        quality_report=quality_report,
        storage_path=str(raw_path),
    )
    db.add(log_entry)
    await db.flush()

    await audit.log(user_id, "datasource.upload", "datasource", source_id,
                    detail={"file": file_name, "rows": row_count, "status": status})

    # Phase 2: 解析后写入 PostgreSQL 动态表 ds_{source_id}
    if status != "failed" and rows:
        try:
            await _persist_rows_to_table(source_id, columns, rows)
            logger.info(f"数据已写入 PostgreSQL 表 ds_{source_id}: {row_count}行")
        except Exception as e:
            logger.error(f"写入动态表 ds_{source_id} 失败: {e}")
            # 不影响上传结果，仅记录错误

    return {
        "status": status,
        "row_count": row_count,
        "columns": columns,
        "quality_report": quality_report,
    }


async def _persist_rows_to_table(source_id: str, columns: list[str], rows: list[dict]):
    """
    Phase 2: 将解析后的数据写入 PostgreSQL 动态表 ds_{source_id}。
    策略：DROP + CREATE（全量替换），避免 UPSERT 的主键问题。
    列类型统一为 TEXT（CSV 无类型信息），Skill 通过 SQL CAST 自行转换。
    """
    from sqlalchemy import text

    _validate_source_id(source_id)
    table_name = f"ds_{source_id}"
    # 清洗列名：只保留字母数字下划线，防注入
    safe_columns = []
    col_map = {}  # 原始列名 → 安全列名
    for col in columns:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", col).strip("_")[:63]
        if not safe:
            safe = f"col_{len(safe_columns)}"
        # 避免重复
        base = safe
        idx = 1
        while safe in col_map.values():
            safe = f"{base}_{idx}"
            idx += 1
        safe_columns.append(safe)
        col_map[col] = safe

    if not safe_columns:
        return

    def _sf():
        from app.database import async_session_factory
        return async_session_factory

    async with _sf()() as session:
        # 删除旧表（如果存在）
        await session.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))

        # 建表：所有列为 TEXT + _ingested_at 时间戳
        col_defs = ", ".join(f'"{c}" TEXT' for c in safe_columns)
        create_sql = f'CREATE TABLE "{table_name}" ({col_defs}, _ingested_at TIMESTAMP DEFAULT NOW())'
        await session.execute(text(create_sql))

        # 批量插入（每 500 行一批）
        if rows:
            placeholders = ", ".join(f":c{i}" for i in range(len(safe_columns)))
            col_parts = ", ".join(f'"{c}"' for c in safe_columns)
            insert_sql = f'INSERT INTO "{table_name}" ({col_parts}) VALUES ({placeholders})'

            batch_size = 500
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start:batch_start + batch_size]
                params_list = []
                for row in batch:
                    params = {}
                    for i, orig_col in enumerate(columns):
                        val = row.get(orig_col, "")
                        params[f"c{i}"] = str(val) if val is not None else None
                    params_list.append(params)

                for p in params_list:
                    await session.execute(text(insert_sql), p)

        await session.commit()


async def pull_api_source(
    db: AsyncSession,
    source_id: str,
    user_id: str = "scheduler",
) -> dict:
    """
    API拉取：调用外部API获取数据 → 字段映射 → 质量校验 → 记录日志。
    config示例：
    {
      "api_url": "https://api.example.com/data",
      "auth_type": "bearer",          # bearer / api_key / none
      "auth_token": "xxx",
      "params": {"date": "{{yesterday}}"},
      "json_paths": ["$.data[*]"],    # 从响应中提取数据的路径
      "field_mapping": {"原始列名": "目标列名"},
      "timeout": 30
    }
    """
    # 加载数据源配置
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    if source.source_type != "api_pull":
        raise AppError("DATASOURCE_PARSE_ERROR", 422,
                        detail={"reason": f"数据源类型为{source.source_type}，不支持API拉取"})

    config = source.config or {}
    api_url = config.get("api_url")
    if not api_url:
        raise AppError("DATASOURCE_PARSE_ERROR", 422,
                        detail={"reason": "数据源配置缺少api_url"})

    # 变量替换：{{yesterday}} → 昨天日期，{{today}} → 今天日期
    params = _substitute_variables(config.get("params", {}))
    timeout = config.get("timeout", 30)

    # 构建HTTP请求头（认证）
    headers = _build_auth_headers(config)

    # 调用外部API
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(api_url, params=params, headers=headers)
            response.raise_for_status()
            response_json = response.json()
    except httpx.TimeoutException:
        # 记录失败日志
        log_entry = DataIngestionLog(
            source_id=source_id,
            ingestion_type="api_pull",
            uploaded_by=user_id,
            status="failed",
            error_message="API请求超时",
        )
        db.add(log_entry)
        await db.flush()
        raise AppError("DATASOURCE_PARSE_ERROR", 422, detail={"reason": "API请求超时"})
    except httpx.HTTPStatusError as e:
        log_entry = DataIngestionLog(
            source_id=source_id,
            ingestion_type="api_pull",
            uploaded_by=user_id,
            status="failed",
            error_message=f"API返回HTTP {e.response.status_code}",
        )
        db.add(log_entry)
        await db.flush()
        raise AppError("DATASOURCE_PARSE_ERROR", 422,
                        detail={"reason": f"API返回HTTP {e.response.status_code}"})
    except Exception as e:
        log_entry = DataIngestionLog(
            source_id=source_id,
            ingestion_type="api_pull",
            uploaded_by=user_id,
            status="failed",
            error_message=str(e),
        )
        db.add(log_entry)
        await db.flush()
        raise AppError("DATASOURCE_PARSE_ERROR", 422, detail={"reason": f"API请求异常: {e}"})

    # 从JSON响应中提取数据
    json_paths = config.get("json_paths", [])
    rows = _extract_json_data(response_json, json_paths)

    if not rows:
        logger.warning(f"API拉取数据为空: source={source_id} url={api_url}")

    # 字段映射
    field_mapping = config.get("field_mapping", {})
    if field_mapping and rows:
        rows = _apply_field_mapping(rows, field_mapping)

    columns = list(rows[0].keys()) if rows else []
    row_count = len(rows)

    # 获取上次成功拉取的行数（用于delta检查）
    prev_row_count = await _get_previous_row_count(db, source_id)

    # 质量校验
    quality_report = _run_quality_checks(
        rows, columns, source.quality_rules or {},
        prev_row_count=prev_row_count,
    )

    # 记录日志
    status = "failed" if quality_report["has_errors"] else (
        "warning" if quality_report["has_warnings"] else "success"
    )
    log_entry = DataIngestionLog(
        source_id=source_id,
        ingestion_type="api_pull",
        uploaded_by=user_id,
        row_count=row_count,
        status=status,
        quality_report=quality_report,
    )
    db.add(log_entry)
    await db.flush()

    await audit.log(
        user_id, "datasource.api_pull", "datasource", source_id,
        detail={"url": api_url, "rows": row_count, "status": status},
    )

    return {
        "status": status,
        "row_count": row_count,
        "columns": columns,
        "quality_report": quality_report,
    }


def _substitute_variables(params: dict) -> dict:
    """替换参数中的模板变量：{{yesterday}} → 昨天日期，{{today}} → 今天日期"""
    today = now_bjt().date()
    yesterday = today - timedelta(days=1)

    substitutions = {
        "{{today}}": today.isoformat(),
        "{{yesterday}}": yesterday.isoformat(),
    }

    result = {}
    for key, value in params.items():
        if isinstance(value, str):
            for pattern, replacement in substitutions.items():
                value = value.replace(pattern, replacement)
        result[key] = value
    return result


def _build_auth_headers(config: dict) -> dict:
    """根据认证类型构建HTTP请求头（不记录token到日志）"""
    headers = {}
    auth_type = config.get("auth_type", "none")
    auth_token = config.get("auth_token", "")

    if auth_type == "bearer" and auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif auth_type == "api_key" and auth_token:
        headers["X-API-Key"] = auth_token

    return headers


def _extract_json_data(response_json: dict | list, json_paths: list[str]) -> list[dict]:
    """
    从JSON响应中提取数据。支持简单的jsonpath语法：
    - "$.data[*]" → response["data"]（列表中的每个元素）
    - "$.result.items[*]" → response["result"]["items"]
    - "$.data" → response["data"]（如果本身是列表）
    如果json_paths为空，尝试直接使用响应（如果是列表）。
    """
    if not json_paths:
        # 没有指定路径时，尝试直接使用响应
        if isinstance(response_json, list):
            return [row for row in response_json if isinstance(row, dict)]
        # 尝试常见的数据字段
        for key in ("data", "items", "result", "records", "rows"):
            if key in response_json and isinstance(response_json[key], list):
                return [row for row in response_json[key] if isinstance(row, dict)]
        return []

    all_rows = []
    for path in json_paths:
        extracted = _resolve_json_path(response_json, path)
        if isinstance(extracted, list):
            all_rows.extend(row for row in extracted if isinstance(row, dict))
        elif isinstance(extracted, dict):
            all_rows.append(extracted)

    return all_rows


def _resolve_json_path(data: dict | list, path: str):
    """
    解析简单jsonpath：$.key1.key2[*].key3
    支持 $ 开头、点号分割、[*] 表示展开列表、[N] 表示取索引。
    """
    # 去掉 $ 前缀
    path = path.strip()
    if path.startswith("$"):
        path = path[1:]
    if path.startswith("."):
        path = path[1:]
    if not path:
        return data

    # 分段解析
    segments = _parse_path_segments(path)
    current = data

    for seg in segments:
        if current is None:
            return None

        if seg == "[*]":
            # 展开列表，后续路径不再处理（返回列表本身）
            if isinstance(current, list):
                return current
            return None
        elif seg.startswith("[") and seg.endswith("]"):
            # 索引访问
            try:
                idx = int(seg[1:-1])
                current = current[idx] if isinstance(current, list) else None
            except (ValueError, IndexError):
                return None
        else:
            # 字典键访问
            if isinstance(current, dict):
                current = current.get(seg)
            else:
                return None

    return current


def _parse_path_segments(path: str) -> list[str]:
    """将jsonpath字符串拆分成段：data[*].items → ["data", "[*]", "items"]"""
    segments = []
    current = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if current:
                segments.append(current)
                current = ""
        elif ch == "[":
            if current:
                segments.append(current)
                current = ""
            # 找到对应的 ]
            j = path.index("]", i)
            segments.append(path[i:j + 1])
            i = j
        else:
            current += ch
        i += 1
    if current:
        segments.append(current)
    return segments


def _apply_field_mapping(rows: list[dict], mapping: dict) -> list[dict]:
    """应用字段映射：将原始列名重命名为目标列名"""
    mapped_rows = []
    for row in rows:
        new_row = {}
        for src_key, value in row.items():
            target_key = mapping.get(src_key, src_key)
            new_row[target_key] = value
        mapped_rows.append(new_row)
    return mapped_rows


async def _get_previous_row_count(db: AsyncSession, source_id: str) -> int | None:
    """获取上次成功导入的行数（用于delta比较）"""
    result = await db.execute(
        select(DataIngestionLog.row_count)
        .where(DataIngestionLog.source_id == source_id)
        .where(DataIngestionLog.status == "success")
        .order_by(DataIngestionLog.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _run_quality_checks(rows: list[dict], columns: list[str], rules: dict,
                        prev_row_count: int | None = None) -> dict:
    """执行质量校验"""
    report: dict = {"checks": [], "has_errors": False, "has_warnings": False}

    if not rows:
        report["checks"].append({"rule": "数据不能为空", "status": "error", "detail": "0行数据"})
        report["has_errors"] = True
        return report

    # 必填字段检查
    required = rules.get("required_columns", [])
    for col in required:
        if col not in columns:
            report["checks"].append({"rule": f"必填列 {col}", "status": "error", "detail": f"缺少列 {col}"})
            report["has_errors"] = True
        else:
            null_count = sum(1 for r in rows if not r.get(col))
            if null_count > 0:
                report["checks"].append({"rule": f"{col}不能为空", "status": "error", "detail": f"{null_count}行为空"})
                report["has_errors"] = True
            else:
                report["checks"].append({"rule": f"{col}不能为空", "status": "pass"})

    # 非负数值检查
    for col in rules.get("non_negative_columns", []):
        if col in columns:
            neg_count = 0
            for r in rows:
                try:
                    if float(r.get(col, 0)) < 0:
                        neg_count += 1
                except (ValueError, TypeError):
                    pass
            if neg_count > 0:
                report["checks"].append({"rule": f"{col}不能为负", "status": "error", "detail": f"{neg_count}行为负"})
                report["has_errors"] = True

    # 最少行数检查
    min_rows = rules.get("min_rows")
    if min_rows and len(rows) < min_rows:
        report["checks"].append({"rule": f"至少{min_rows}行", "status": "warning", "detail": f"实际{len(rows)}行"})
        report["has_warnings"] = True

    # Delta比较：当前行数与上次相差超过50%时发出警告
    if prev_row_count is not None and prev_row_count > 0:
        current_count = len(rows)
        change_ratio = abs(current_count - prev_row_count) / prev_row_count
        if change_ratio > 0.5:
            direction = "增加" if current_count > prev_row_count else "减少"
            pct = round(change_ratio * 100)
            report["checks"].append({
                "rule": "行数波动检查",
                "status": "warning",
                "detail": f"行数{direction}{pct}%（上次{prev_row_count}行 → 本次{current_count}行）",
            })
            report["has_warnings"] = True
        else:
            report["checks"].append({"rule": "行数波动检查", "status": "pass"})

    # 日期格式校验：检查指定列的值是否符合预期的日期格式
    for date_rule in rules.get("date_columns", []):
        # date_rule可以是字符串（列名，默认YYYY-MM-DD）或dict（含column和format）
        if isinstance(date_rule, str):
            col_name = date_rule
            date_format = "%Y-%m-%d"
        elif isinstance(date_rule, dict):
            col_name = date_rule.get("column", "")
            date_format = date_rule.get("format", "%Y-%m-%d")
        else:
            continue

        if col_name not in columns:
            continue

        invalid_count = 0
        for r in rows:
            val = r.get(col_name, "")
            if not val:
                continue
            try:
                datetime.strptime(str(val).strip(), date_format)
            except ValueError:
                invalid_count += 1

        if invalid_count > 0:
            report["checks"].append({
                "rule": f"{col_name}日期格式",
                "status": "warning",
                "detail": f"{invalid_count}行日期格式不符合{date_format}",
            })
            report["has_warnings"] = True
        else:
            report["checks"].append({"rule": f"{col_name}日期格式", "status": "pass"})

    if not report["checks"]:
        report["checks"].append({"rule": "基础检查", "status": "pass"})

    return report


# ===== 缺口补齐：编辑/预览/历史/质量报告 =====


async def update_source(
    db: AsyncSession,
    source_id: str,
    name: str | None = None,
    config: dict | None = None,
    quality_rules: dict | None = None,
    related_skills: list | None = None,
    schedule: str | None = None,
    stale_threshold_hours: int | None = None,
    user_id: str = "system",
    user_role: str = "",
    visibility: str | None = None,
    description: str | None = None,
    usage_hint: str | None = None,
    owner_contact: str | None = None,
    **_legacy_kwargs: Any,  # 吸收 sensitivity / owner_org_unit_id 等 router 旧字段
) -> dict:
    """编辑数据源配置。

    v2.7 大厅 v3：支持改 visibility/description/usage_hint/owner_contact。

    **visibility 提权规则**：
    - department → company 必须 admin 或 dept_admin（防止误提权到全公司）
    - 其他变更（降级 / 横向）owner 即可
    """
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    changes = {}
    if name is not None:
        source.name = name
        changes["name"] = name
    if config is not None:
        # 默认 merge，避免平台连接这种大 config 被前端局部 patch 覆盖丢字段
        source.config = {**(source.config or {}), **config}
        changes["config_updated"] = True
    if quality_rules is not None:
        source.quality_rules = quality_rules
        changes["quality_rules_updated"] = True
    if related_skills is not None:
        source.related_skills = related_skills
        changes["related_skills"] = related_skills
    if schedule is not None:
        source.schedule = schedule
        changes["schedule"] = schedule
    if stale_threshold_hours is not None:
        source.stale_threshold_hours = stale_threshold_hours
        changes["stale_threshold_hours"] = stale_threshold_hours

    # v2.7 visibility 提权守门
    if visibility is not None:
        if visibility not in ALLOWED_VISIBILITIES:
            raise AppError(
                "VISIBILITY_INVALID",
                400,
                detail={"allowed": sorted(ALLOWED_VISIBILITIES), "given": visibility},
            )
        # 提权到 company 必须 admin / dept_admin
        if (
            source.visibility != "company"
            and visibility == "company"
            and user_role not in ("admin", "dept_admin")
        ):
            raise AppError(
                "PERMISSION_DENIED",
                403,
                detail={"reason": "提升 visibility 到 company 需要 admin / dept_admin 权限"},
            )
        if source.visibility != visibility:
            changes["visibility"] = {"from": source.visibility, "to": visibility}
            source.visibility = visibility

    if description is not None:
        source.description = description
        changes["description_updated"] = True
    if usage_hint is not None:
        source.usage_hint = usage_hint
        changes["usage_hint_updated"] = True
    if owner_contact is not None:
        source.owner_contact = owner_contact
        changes["owner_contact"] = owner_contact

    source.updated_at = now_bjt()
    await db.flush()

    await audit.log(user_id, "datasource.update", "datasource", source_id, detail=changes)
    return {"source_id": source_id, "updated": list(changes.keys())}


async def preview_latest(
    db: AsyncSession,
    source_id: str,
    limit: int = 50,
) -> dict:
    """预览最新上传的数据（读取最近成功上传的CSV文件前N行）"""
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 查找最近成功上传
    log_result = await db.execute(
        select(DataIngestionLog)
        .where(DataIngestionLog.source_id == source_id)
        .where(DataIngestionLog.status.in_(["success", "warning"]))
        .order_by(DataIngestionLog.created_at.desc())
        .limit(1)
    )
    log_entry = log_result.scalar_one_or_none()

    if not log_entry or not log_entry.storage_path:
        return {"rows": [], "columns": [], "total_rows": 0, "message": "暂无数据"}

    # 读取文件
    file_path = Path(log_entry.storage_path)
    if not file_path.exists():
        return {"rows": [], "columns": [], "total_rows": 0, "message": "文件已清理"}

    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = file_path.read_text(encoding="gbk")

    reader = csv.DictReader(io.StringIO(text))
    columns = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        if i >= limit:
            break
        rows.append(row)

    return {
        "columns": columns,
        "rows": rows,
        "total_rows": log_entry.row_count or len(rows),
        "file_name": log_entry.file_name,
        "uploaded_at": isoformat_bjt(log_entry.created_at),
    }


async def get_ingestion_history(
    db: AsyncSession,
    source_id: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """上传/拉取历史记录（分页）"""
    # 检查数据源存在
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    if not result.scalar_one_or_none():
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 总数
    total = (await db.execute(
        select(func.count()).select_from(DataIngestionLog)
        .where(DataIngestionLog.source_id == source_id)
    )).scalar() or 0

    # 分页查询
    logs_result = await db.execute(
        select(DataIngestionLog)
        .where(DataIngestionLog.source_id == source_id)
        .order_by(DataIngestionLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = logs_result.scalars().all()

    return {
        "total": total,
        "page": page,
        "items": [
            {
                "id": l.id,
                "type": l.ingestion_type,
                "uploaded_by": l.uploaded_by,
                "file_name": l.file_name,
                "row_count": l.row_count,
                "status": l.status,
                "quality_report": l.quality_report,
                "error_message": l.error_message,
                "created_at": isoformat_bjt(l.created_at),
            }
            for l in logs
        ],
    }


async def get_latest_quality_report(
    db: AsyncSession,
    source_id: str,
) -> dict:
    """获取最近一次上传的质量校验报告"""
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    log_result = await db.execute(
        select(DataIngestionLog)
        .where(DataIngestionLog.source_id == source_id)
        .where(DataIngestionLog.quality_report.isnot(None))
        .order_by(DataIngestionLog.created_at.desc())
        .limit(1)
    )
    log_entry = log_result.scalar_one_or_none()

    if not log_entry:
        return {"source_id": source_id, "message": "暂无质量校验记录"}

    return {
        "source_id": source_id,
        "ingestion_id": log_entry.id,
        "status": log_entry.status,
        "file_name": log_entry.file_name,
        "row_count": log_entry.row_count,
        "quality_report": log_entry.quality_report,
        "checked_at": isoformat_bjt(log_entry.created_at),
    }


# ═══════════════════════════════════════════════════════════════
# 平台连接 Cookie 管理
# ═══════════════════════════════════════════════════════════════

CONNECTOR_AUTH_SOURCE_NEW = "connector_api_key"
CONNECTOR_AUTH_SOURCE_LEGACY = "legacy_connector_api_key"
CONNECTOR_AUTH_SOURCE_SESSION = "session"
CONNECTOR_REQUIRED_PUSH_SCOPE = "cookies:push"
CONNECTOR_SYNC_BUNDLE_SCOPE = "sync:bundle"
MIN_COOKIE_DISABLE_REASON_LEN = 10
DEFAULT_COOKIE_POOL_SHOP_ID = "default"


@dataclass(slots=True)
class ConnectorAuthContext:
    auth_source: str
    key_id: str | None = None
    source_id: str | None = None
    owner_user_id: str | None = None
    device_label: str | None = None
    scopes: list[str] | None = None
    allowed_platforms: list[str] | None = None
    allowed_shop_ids: list[str] | None = None
    legacy_owner_user_id: str | None = None

    @property
    def is_new_key(self) -> bool:
        return self.auth_source == CONNECTOR_AUTH_SOURCE_NEW

    @property
    def actor_user_id(self) -> str:
        return self.owner_user_id or self.legacy_owner_user_id or "chrome_extension"

    def has_scope(self, scope: str) -> bool:
        return scope in set(self.scopes or [])


PLATFORM_CONNECTION_DEFAULTS: dict[str, tuple[str, str]] = {
    "platform-taobao": ("taobao", "淘宝/天猫千牛连接"),
    "platform-sycm": ("sycm", "生意参谋连接"),
    "platform-alimama": ("alimama", "阿里妈妈连接"),
    "platform-douyin": ("douyin", "抖店/抖音连接"),
    "platform-jd": ("jd", "京东连接"),
    "platform-pdd": ("pdd", "拼多多连接"),
}

_cookie_cipher_cache = None

def _get_cookie_cipher():
    """Cookie 加密器（Fernet 对称加密）。COOKIE_ENCRYPT_KEY 必须在 .env 中预配置。"""
    global _cookie_cipher_cache
    if _cookie_cipher_cache is not None:
        return _cookie_cipher_cache

    from cryptography.fernet import Fernet
    from app.config import settings

    key = settings.COOKIE_ENCRYPT_KEY
    if not key:
        raise AppError(
            "CONFIG_MISSING", 500,
            {"detail": "COOKIE_ENCRYPT_KEY 未配置，请在 .env 中设置（生成命令: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"},
        )
    _cookie_cipher_cache = Fernet(key.encode() if isinstance(key, str) else key)
    return _cookie_cipher_cache


async def create_or_update_platform_connection(
    db: AsyncSession,
    *,
    source_id: str,
    name: str,
    department: str,
    platform: str,
    user_id: str,
) -> dict:
    """创建或更新平台连接类型的数据源。"""
    existing = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()

    now = now_bjt()
    if existing:
        existing.name = name
        existing.config = {**(existing.config or {}), "platform": platform}
        existing.updated_at = now
    else:
        db.add(DataSource(
            id=source_id,
            name=name,
            department=department,
            source_type="platform_cookies",
            config={"platform": platform, "connected": False},
            is_active=True,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        ))
    await db.commit()

    await audit.log(user_id, "datasource.platform_connect", "datasource", source_id,
                    {"platform": platform})

    return {"source_id": source_id, "platform": platform, "name": name, "created": not existing}


async def _sync_cookies_to_browser_background(source_id: str) -> None:
    """[2026-04-14] fire-and-forget：把最新 cookies 推到 docker Chrome。

    触发条件：push_cookies 成功 commit 后；按 source_id 维度 30s 节流。
    - 浏览器容器未运行：inject 内部会直接返回 `{"error": "浏览器未运行"}`，静默跳过
    - CDP 失败：只记 warn，不影响扩展的 push 响应
    """
    try:
        # 延迟 import 避免 datasources ↔ browser 模块循环依赖
        from app.browser.service import inject_stored_cookies
        from app.database import async_session_factory

        async with async_session_factory() as session:
            result = await inject_stored_cookies(session)
        if "error" in result:
            logger.debug("[cookies→browser] 跳过 source={}: {}", source_id, result)
        else:
            status = result.get(source_id, "(其他 source 都处理)")
            logger.info("[cookies→browser] source={} 同步完成: {}", source_id, status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cookies→browser] 后台同步异常 source={}: {}", source_id, exc, exc_info=True)


async def _verify_cookies_background(source_id: str) -> None:
    """Cookie 推送后自动跑一次已接入平台的登态心跳，避免页面状态长时间停在旧值。"""
    try:
        from app.browser.service import PLATFORM_LOGIN_PROBE, verify_login
        from app.database import async_session_factory

        if source_id not in PLATFORM_LOGIN_PROBE:
            return
        async with async_session_factory() as session:
            # verify_login 会优先走源 cookie 直连验证，必要时再自行注入浏览器。
            # 这里不要预先同步到 runtime Chrome，避免旧 cookie 与新 credential 混杂。
            result = await verify_login(session, source_id=source_id, user_id="chrome_extension")
        logger.info(
            "[cookies→verify] source={} status={} verified={}",
            source_id, result.get("status"), result.get("verified"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cookies→verify] 后台验证异常 source={}: {}", source_id, exc, exc_info=True)


def _schedule_cookie_sync_to_browser(source_id: str) -> None:
    """按 source_id 节流；在最短间隔内忽略重复触发。"""
    now = time.monotonic()
    last = _last_cookie_sync_ts.get(source_id, 0.0)
    if now - last < _COOKIE_SYNC_THROTTLE_SECONDS:
        return
    _last_cookie_sync_ts[source_id] = now
    try:
        task = asyncio.create_task(
            _sync_cookies_to_browser_background(source_id),
            name=f"cookie_sync:{source_id}",
        )
    except RuntimeError:
        # 没跑 event loop（纯脚本 / 测试夹具）—— 静默跳过
        return
    _cookie_sync_tasks.add(task)
    task.add_done_callback(_cookie_sync_tasks.discard)


def _schedule_cookie_verify(source_id: str) -> None:
    """按 source_id 节流；Cookie 更新后自动刷新登录态判断。"""
    now = time.monotonic()
    last = _last_cookie_verify_ts.get(source_id, 0.0)
    if now - last < _COOKIE_VERIFY_THROTTLE_SECONDS:
        return
    _last_cookie_verify_ts[source_id] = now
    try:
        task = asyncio.create_task(
            _verify_cookies_background(source_id),
            name=f"cookie_verify:{source_id}",
        )
    except RuntimeError:
        return
    _cookie_verify_tasks.add(task)
    task.add_done_callback(_cookie_verify_tasks.discard)


def _platform_from_source_id(source_id: str) -> str | None:
    if source_id.startswith("platform-"):
        return source_id.removeprefix("platform-")
    return None


def _mark_platform_connection_synced(
    ds: DataSource,
    *,
    platform: str | None,
    domain: str,
    user_agent: str,
    pushed_by: str,
    pushed_at: str,
    auth_source: str,
    shop_id: str | None = None,
    legacy_owner_user_id: str | None = None,
) -> None:
    """Update only non-secret connection metadata used by the admin UI."""
    config = dict(ds.config or {})
    if platform:
        config["platform"] = platform
    config["connected"] = True
    config["domain"] = domain
    config["user_agent"] = user_agent
    config["pushed_by"] = pushed_by
    config["pushed_at"] = pushed_at
    config["auth_source"] = auth_source
    if shop_id:
        config["shop_id"] = shop_id
    if legacy_owner_user_id:
        config["legacy_owner_user_id"] = legacy_owner_user_id
    ds.config = config
    ds.updated_at = now_bjt()


async def _ensure_platform_cookie_source(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: str,
    platform: str | None = None,
) -> DataSource:
    ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
    if ds:
        return ds

    platform_default = PLATFORM_CONNECTION_DEFAULTS.get(source_id)
    if not platform_default:
        raise AppError("NOT_FOUND", 404, {"detail": f"数据源 {source_id} 不存在，请先在管理界面创建"})

    default_platform, name = platform_default
    ds = DataSource(
        id=source_id,
        name=name,
        department="EC",
        source_type="platform_cookies",
        config={"platform": (platform or default_platform), "connected": False},
        is_active=True,
        created_by=user_id or "chrome_extension",
        created_at=now_bjt(),
        updated_at=now_bjt(),
    )
    db.add(ds)
    await db.flush()
    return ds


def _validate_new_cookie_context(
    *,
    source_id: str,
    ds: DataSource,
    platform: str | None,
    shop_id: str | None,
    auth_context: ConnectorAuthContext,
) -> tuple[str, str]:
    source_platform = _platform_from_source_id(source_id)
    normalized_platform = str(platform or source_platform or "").strip().lower()
    if not normalized_platform:
        raise AppError("MISSING_SHOP_CONTEXT", 400, {"detail": "new connector key requires platform"})
    allowed_shop_ids = _normalize_str_list(auth_context.allowed_shop_ids)
    fallback_shop_id = allowed_shop_ids[0] if len(allowed_shop_ids) == 1 else DEFAULT_COOKIE_POOL_SHOP_ID
    normalized_shop_id = str(shop_id or fallback_shop_id).strip()
    if auth_context.source_id and auth_context.source_id != source_id:
        raise AppError("CONNECTOR_SOURCE_MISMATCH", 400, {
            "expected_source_id": auth_context.source_id,
            "actual_source_id": source_id,
        })

    configured_platform = str((ds.config or {}).get("platform") or source_platform or "").lower()
    if source_platform and source_platform != normalized_platform:
        raise AppError("CONNECTOR_SOURCE_MISMATCH", 400, {
            "source_id": source_id,
            "platform": normalized_platform,
        })
    if configured_platform and configured_platform != normalized_platform:
        raise AppError("CONNECTOR_SOURCE_MISMATCH", 400, {
            "source_id": source_id,
            "configured_platform": configured_platform,
            "platform": normalized_platform,
        })

    scopes = set(auth_context.scopes or [])
    if "*" not in scopes and CONNECTOR_REQUIRED_PUSH_SCOPE not in scopes:
        raise AppError("CONNECTOR_SCOPE_DENIED", 403, {"required_scope": CONNECTOR_REQUIRED_PUSH_SCOPE})

    allowed_platforms = set(auth_context.allowed_platforms or [])
    if allowed_platforms and normalized_platform not in allowed_platforms:
        raise AppError("CONNECTOR_SCOPE_DENIED", 403, {
            "platform": normalized_platform,
            "allowed_platforms": sorted(allowed_platforms),
        })

    allowed_shop_ids = set(allowed_shop_ids)
    if allowed_shop_ids and normalized_shop_id not in allowed_shop_ids:
        raise AppError("CONNECTOR_SCOPE_DENIED", 403, {
            "shop_id": normalized_shop_id,
            "allowed_shop_ids": sorted(allowed_shop_ids),
        })

    if not auth_context.owner_user_id:
        raise AppError("CONNECTOR_SCOPE_DENIED", 403, {"detail": "connector key has no owner_user_id"})

    return normalized_platform, normalized_shop_id


async def _get_active_cookie_pool_row(
    db: AsyncSession,
    *,
    source_id: str,
    platform: str,
    shop_id: str,
    owner_user_id: str,
    connector_key_id: str | None = None,
) -> PlatformCookiePool | None:
    stmt = select(PlatformCookiePool).where(
        PlatformCookiePool.source_id == source_id,
        PlatformCookiePool.platform == platform.strip().lower(),
        PlatformCookiePool.shop_id == shop_id.strip(),
        PlatformCookiePool.owner_user_id == owner_user_id,
        PlatformCookiePool.is_active == True,  # noqa: E712
    )
    if connector_key_id:
        stmt = stmt.where(PlatformCookiePool.connector_key_id == connector_key_id)
    return (await db.execute(
        stmt.order_by(PlatformCookiePool.priority.desc(), PlatformCookiePool.pushed_at.desc())
    )).scalar_one_or_none()


def _add_cookie_audit(
    db: AsyncSession,
    row: PlatformCookiePool,
    *,
    action: str,
    actor_user_id: str | None,
    detail: dict | None = None,
) -> None:
    db.add(PlatformCookieAudit(
        cookie_pool_id=row.id,
        source_id=row.source_id,
        platform=row.platform,
        shop_id=row.shop_id,
        owner_user_id=row.owner_user_id,
        connector_key_id=row.connector_key_id,
        action=action,
        auth_source=row.auth_source,
        actor_user_id=actor_user_id,
        detail=detail or {},
    ))


def _apply_cookie_pool_verify_result(
    row: PlatformCookiePool,
    *,
    new_status: str,
    detail: str,
) -> dict[str, Any]:
    previous = {
        "verification_status": row.verification_status,
        "health_score": int(row.health_score or 0),
        "last_error_code": row.last_error_code,
    }
    row.last_verified_at = now_bjt()
    if row.is_active and row.status == "active" and new_status == "valid":
        row.verification_status = "active"
        row.health_score = 100
        row.last_error_code = None
    elif new_status == "expired":
        row.verification_status = "failed"
        row.health_score = 0
        row.last_error_code = "LOGIN_EXPIRED"
    elif row.is_active and row.status == "active":
        capability = dict(row.capability_json or {})
        capability["last_verify_unknown_detail"] = (detail or "VERIFY_UNKNOWN")[:200]
        row.capability_json = capability
        if previous["verification_status"] != "active" or previous["health_score"] <= 0:
            row.verification_status = "unknown"
            row.health_score = max(previous["health_score"], 50)
            row.last_error_code = (detail or "VERIFY_UNKNOWN")[:80]
        else:
            row.verification_status = str(previous["verification_status"] or "")
            row.health_score = int(previous["health_score"])
            row.last_error_code = previous["last_error_code"]
    else:
        row.verification_status = "disabled"
        row.health_score = 0
        row.last_error_code = "COOKIE_DISABLED"
    row.updated_at = now_bjt()
    return previous


async def _direct_verify_sycm_cookie_pool(row: PlatformCookiePool) -> dict[str, Any] | None:
    if row.source_id != "platform-sycm" and row.platform != "sycm":
        return None
    try:
        cipher = _get_cookie_cipher()
        cookie_header = cipher.decrypt(row.encrypted_cookies.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cookies] sycm direct verify decrypt failed pool={}: {}", row.id, exc)
        return {
            "status": "unknown",
            "logged_in": False,
            "detail": f"cookie 解密失败: {exc}",
            "transport": "direct_cookie",
        }

    from app.browser.collectors import sycm

    result = await sycm.verify_sycm_cookie_header(
        cookie_header,
        user_agent=row.user_agent,
    )
    result["transport"] = "direct_cookie"
    return result


async def _push_cookies_to_pool(
    db: AsyncSession,
    *,
    source_id: str,
    ds: DataSource,
    cookies: str,
    domain: str,
    user_agent: str,
    cookie_details: list[dict] | None,
    platform: str | None,
    shop_id: str | None,
    account_login: str | None,
    auth_context: ConnectorAuthContext,
) -> dict:
    normalized_platform, normalized_shop_id = _validate_new_cookie_context(
        source_id=source_id,
        ds=ds,
        platform=platform,
        shop_id=shop_id,
        auth_context=auth_context,
    )

    cipher = _get_cookie_cipher()
    encrypted_cookies = cipher.encrypt(cookies.encode("utf-8")).decode("utf-8")
    encrypted_details = None
    if cookie_details:
        encrypted_details = cipher.encrypt(
            json.dumps(cookie_details, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")

    owner_user_id = auth_context.owner_user_id or ""
    display_label = account_login or auth_context.device_label or auth_context.key_id
    now = now_bjt()
    row = (await db.execute(
        select(PlatformCookiePool).where(
            PlatformCookiePool.source_id == source_id,
            PlatformCookiePool.platform == normalized_platform,
            PlatformCookiePool.shop_id == normalized_shop_id,
            PlatformCookiePool.owner_user_id == owner_user_id,
            PlatformCookiePool.connector_key_id == auth_context.key_id,
        )
    )).scalar_one_or_none()

    if row:
        row.connector_key_id = auth_context.key_id
        row.device_label = display_label
        row.auth_source = auth_context.auth_source
        row.encrypted_cookies = encrypted_cookies
        row.encrypted_cookie_details = encrypted_details
        row.domain = domain
        row.user_agent = user_agent
        row.account_login = account_login
        row.status = "active"
        row.is_active = True
        row.health_score = max(int(row.health_score or 0), 80)
        row.verification_status = "pending"
        row.capability_json = {**(row.capability_json or {}), "cookie_detail_count": len(cookie_details or [])}
        row.last_error_code = None
        row.pushed_at = now
        row.disabled_at = None
        row.disabled_by = None
        row.disable_reason = None
        row.updated_at = now
    else:
        row = PlatformCookiePool(
            source_id=source_id,
            platform=normalized_platform,
            shop_id=normalized_shop_id,
            account_login=account_login,
            owner_user_id=owner_user_id,
            connector_key_id=auth_context.key_id,
            device_label=display_label,
            auth_source=auth_context.auth_source,
            encrypted_cookies=encrypted_cookies,
            encrypted_cookie_details=encrypted_details,
            domain=domain,
            user_agent=user_agent,
            priority=50,
            health_score=100,
            verification_status="pending",
            capability_json={"cookie_detail_count": len(cookie_details or [])},
            status="active",
            is_active=True,
            pushed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.flush()

    stale_rows = (await db.execute(
        select(PlatformCookiePool).where(
            PlatformCookiePool.source_id == source_id,
            PlatformCookiePool.platform == normalized_platform,
            PlatformCookiePool.owner_user_id == owner_user_id,
            PlatformCookiePool.is_active == True,  # noqa: E712
            PlatformCookiePool.id != row.id,
        )
    )).scalars().all()
    for stale in stale_rows:
        stale_key = await db.get(ConnectorApiKey, stale.connector_key_id) if stale.connector_key_id else None
        stale_key_revoked = bool(stale_key and (stale_key.status != "active" or stale_key.revoked_at))
        if not stale_key_revoked:
            continue
        stale.is_active = False
        stale.status = "disabled"
        stale.health_score = 0
        stale.verification_status = "disabled"
        stale.last_error_code = "SUPERSEDED_BY_NEW_COOKIE"
        stale.disabled_at = now
        stale.disabled_by = auth_context.actor_user_id
        stale.disable_reason = f"superseded by cookie_pool_id={row.id}"
        stale.updated_at = now
        _add_cookie_audit(
            db,
            stale,
            action="disable",
            actor_user_id=auth_context.actor_user_id,
            detail={
                "reason": "superseded_by_new_cookie",
                "stale_connector_key_status": stale_key.status if stale_key else None,
                "replacement_cookie_pool_id": row.id,
                "replacement_shop_id": row.shop_id,
            },
        )

    _add_cookie_audit(
        db,
        row,
        action="push",
        actor_user_id=auth_context.actor_user_id,
        detail={
            "domain": domain,
            "account_login": account_login,
            "device_label": display_label,
            "cookie_detail_count": len(cookie_details or []),
        },
    )

    return {
        "source_id": source_id,
        "status": "ok",
        "message": "Cookies 已写入授权池",
        "pool_id": row.id,
        "platform": normalized_platform,
        "shop_id": normalized_shop_id,
        "owner_user_id": owner_user_id,
        "account_login": account_login,
        "auth_source": auth_context.auth_source,
        "pushed_at": isoformat_bjt(now),
    }


async def push_cookies(
    db: AsyncSession,
    *,
    source_id: str,
    cookies: str,
    domain: str = "",
    user_agent: str = "",
    user_id: str = "",
    cookie_details: list[dict] | None = None,
    platform: str | None = None,
    shop_id: str | None = None,
    account_login: str | None = None,
    auth_context: ConnectorAuthContext | None = None,
) -> dict:
    """接收并加密存储 cookies（Chrome 扩展调用）。"""
    if not re.fullmatch(r"platform-[a-z]{2,20}", source_id):
        raise AppError("PARAM_INVALID", 400, {"detail": f"无效的 source_id: {source_id}"})

    effective_user_id = user_id or "chrome_extension"
    ds = await _ensure_platform_cookie_source(
        db,
        source_id=source_id,
        user_id=auth_context.actor_user_id if auth_context else effective_user_id,
        platform=platform,
    )

    if auth_context and auth_context.is_new_key:
        result = await _push_cookies_to_pool(
            db,
            source_id=source_id,
            ds=ds,
            cookies=cookies,
            domain=domain,
            user_agent=user_agent,
            cookie_details=cookie_details,
            platform=platform,
            shop_id=shop_id,
            account_login=account_login,
            auth_context=auth_context,
        )
        _mark_platform_connection_synced(
            ds,
            platform=result.get("platform") or platform,
            domain=domain,
            user_agent=user_agent,
            pushed_by=auth_context.actor_user_id,
            pushed_at=result["pushed_at"],
            auth_source=auth_context.auth_source,
            shop_id=result.get("shop_id"),
        )
        await db.commit()
        logger.info(
            "[cookies] pooled push source={} owner={} platform={} shop={}",
            source_id,
            auth_context.owner_user_id,
            result.get("platform"),
            result.get("shop_id"),
        )
        _schedule_cookie_sync_to_browser(source_id)
        _schedule_cookie_verify(source_id)
        return result

    cipher = _get_cookie_cipher()
    encrypted = cipher.encrypt(cookies.encode("utf-8")).decode("utf-8")

    pushed_at = isoformat_bjt(now_bjt())
    config = {**(ds.config or {}), "encrypted_cookies": encrypted}
    if cookie_details:
        config["encrypted_cookie_details"] = cipher.encrypt(
            json.dumps(cookie_details, ensure_ascii=False).encode("utf-8")
        ).decode("utf-8")
    ds.config = config
    _mark_platform_connection_synced(
        ds,
        platform=platform or _platform_from_source_id(source_id) or config.get("platform"),
        domain=domain,
        user_agent=user_agent,
        pushed_by=effective_user_id,
        pushed_at=pushed_at,
        auth_source=auth_context.auth_source if auth_context else CONNECTOR_AUTH_SOURCE_SESSION,
        legacy_owner_user_id=auth_context.legacy_owner_user_id
        if auth_context and auth_context.auth_source == CONNECTOR_AUTH_SOURCE_LEGACY
        else None,
    )
    await db.commit()

    logger.info("[cookies] pushed for source={} user={} domain={}", source_id, effective_user_id, domain)

    # [2026-04-14] fire-and-forget 触发 docker Chrome 同步（30s 节流）
    _schedule_cookie_sync_to_browser(source_id)
    # 同步后自动判断已接入平台的登态 / 业务接口状态（60s 节流）
    _schedule_cookie_verify(source_id)

    return {
        "source_id": source_id,
        "status": "ok",
        "message": "Cookies 已更新",
        "pushed_at": pushed_at,
        "auth_source": auth_context.auth_source if auth_context else CONNECTOR_AUTH_SOURCE_SESSION,
    }


async def get_cookie_details(
    db: AsyncSession,
    source_id: str,
    *,
    platform: str | None = None,
    shop_id: str | None = None,
    owner_user_id: str | None = None,
    connector_key_id: str | None = None,
) -> list[dict]:
    """获取解密后的完整 cookie 元数据，用于按原 domain/path 注入浏览器。"""
    if platform and shop_id and owner_user_id:
        pool = await _get_active_cookie_pool_row(
            db,
            source_id=source_id,
            platform=platform,
            shop_id=shop_id,
            owner_user_id=owner_user_id,
            connector_key_id=connector_key_id,
        )
        encrypted = pool.encrypted_cookie_details if pool else None
        if not encrypted:
            return []
        try:
            cipher = _get_cookie_cipher()
            raw = cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("[cookies] pooled detail decrypt failed source={}: {}", source_id, e)
            return []

    ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
    if not ds or ds.source_type != "platform_cookies":
        return []

    encrypted = (ds.config or {}).get("encrypted_cookie_details")
    if not encrypted:
        return []

    try:
        cipher = _get_cookie_cipher()
        raw = cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("[cookies] detail decrypt failed source={}: {}", source_id, e)
        return []


async def get_cookies(
    db: AsyncSession,
    source_id: str,
    *,
    platform: str | None = None,
    shop_id: str | None = None,
    owner_user_id: str | None = None,
    connector_key_id: str | None = None,
) -> str | None:
    """获取解密后的 cookies（Skill 执行时调用）。"""
    if platform and shop_id and owner_user_id:
        pool = await _get_active_cookie_pool_row(
            db,
            source_id=source_id,
            platform=platform,
            shop_id=shop_id,
            owner_user_id=owner_user_id,
            connector_key_id=connector_key_id,
        )
        encrypted = pool.encrypted_cookies if pool else None
        if not encrypted:
            return None
        try:
            cipher = _get_cookie_cipher()
            return cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.warning("[cookies] pooled decrypt failed source={}: {}", source_id, e)
            return None

    ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
    if not ds or ds.source_type != "platform_cookies":
        return None

    encrypted = (ds.config or {}).get("encrypted_cookies")
    if not encrypted:
        return None

    try:
        cipher = _get_cookie_cipher()
        return cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning("[cookies] decrypt failed source={}: {}", source_id, e)
        return None


def _connector_key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _generate_connector_key_material() -> tuple[str, str]:
    key_id = secrets.token_hex(8)
    api_key = f"sfck_{key_id}_{secrets.token_urlsafe(32)}"
    return key_id, api_key


def _encrypt_connector_api_key(api_key: str) -> str:
    cipher = _get_cookie_cipher()
    return cipher.encrypt(api_key.encode("utf-8")).decode("utf-8")


def _decrypt_connector_api_key(row: ConnectorApiKey) -> str:
    encrypted = getattr(row, "encrypted_key", None)
    if not encrypted:
        return ""
    try:
        cipher = _get_cookie_cipher()
        return cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[connector-key] decrypt failed key_id={}: {}", row.id, exc)
        return ""


def _normalize_str_list(value: Any, *, lowercase: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    items: list[str] = []
    for item in raw_items:
        text = str(item).strip()
        if not text:
            continue
        items.append(text.lower() if lowercase else text)
    return items


def _system_config_value_as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("value", "api_key", "owner_user_id"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value)


async def _get_legacy_connector_owner_user_id(db: AsyncSession) -> str:
    from app.common.models import SystemConfig

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.key == "connector_keys.legacy_owner_user_id")
    )).scalar_one_or_none()
    owner = _system_config_value_as_str(row.value) if row else ""
    return owner or "legacy_connector"


async def _legacy_connector_grace_active(db: AsyncSession) -> bool:
    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.key == "connector_keys.legacy_grace_until")
    )).scalar_one_or_none()
    raw = _system_config_value_as_str(row.value) if row else ""
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        deadline = to_bjt_naive(deadline)
    except (TypeError, ValueError):
        logger.warning("connector_keys.legacy_grace_until 配置非法: {}", raw)
        return False
    return deadline >= now_bjt()


async def verify_connector_api_key(db: AsyncSession, api_key: str) -> ConnectorAuthContext | None:
    """验证 Chrome 扩展 API Key，并返回认证上下文。"""
    from app.common.models import SystemConfig

    if not api_key:
        return None

    key_hash = _connector_key_hash(api_key)
    key_row = (await db.execute(
        select(ConnectorApiKey).where(ConnectorApiKey.key_hash == key_hash)
    )).scalar_one_or_none()
    if key_row:
        if key_row.status != "active":
            raise AppError("CONNECTOR_KEY_REVOKED", 401, {"key_id": key_row.id})
        key_row.last_used_at = now_bjt()
        key_row.updated_at = now_bjt()
        await db.flush()
        return ConnectorAuthContext(
            auth_source=CONNECTOR_AUTH_SOURCE_NEW,
            key_id=key_row.id,
            source_id=key_row.source_id,
            owner_user_id=key_row.owner_user_id,
            device_label=key_row.name,
            scopes=_normalize_str_list(key_row.scopes),
            allowed_platforms=_normalize_str_list(key_row.allowed_platforms, lowercase=True),
            allowed_shop_ids=_normalize_str_list(key_row.allowed_shop_ids),
        )

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.key == "connector.api_key")
    )).scalar_one_or_none()
    if not row:
        return None
    legacy_key = _system_config_value_as_str(row.value)
    if not legacy_key or not secrets.compare_digest(legacy_key, api_key):
        return None
    if not await _legacy_connector_grace_active(db):
        raise AppError("LEGACY_BLOCKED", 401)
    legacy_owner = await _get_legacy_connector_owner_user_id(db)
    return ConnectorAuthContext(
        auth_source=CONNECTOR_AUTH_SOURCE_LEGACY,
        owner_user_id=legacy_owner,
        scopes=[CONNECTOR_REQUIRED_PUSH_SCOPE],
        legacy_owner_user_id=legacy_owner,
    )


def _serialize_connector_key(row: ConnectorApiKey, *, include_plaintext: bool = False) -> dict:
    payload = {
        "id": row.id,
        "name": row.name,
        "device_label": row.name,
        "source_id": row.source_id,
        "owner_user_id": row.owner_user_id,
        "scopes": row.scopes or [],
        "platforms": row.allowed_platforms or [],
        "shop_ids": row.allowed_shop_ids or [],
        "key_prefix": row.key_prefix,
        "key_last4": row.key_last4,
        "status": row.status,
        "created_by": row.created_by,
        "rotated_from_key_id": row.rotated_from_key_id,
        "last_used_at": isoformat_bjt(row.last_used_at) if row.last_used_at else None,
        "revoked_at": isoformat_bjt(row.revoked_at) if row.revoked_at else None,
        "revoked_by": row.revoked_by,
        "revoke_reason": row.revoke_reason,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }
    if include_plaintext:
        payload["api_key"] = _decrypt_connector_api_key(row)
    return payload


def _credential_alias_for_pool(row: PlatformCookiePool) -> str:
    """Return a stable, non-sensitive alias for UI/proofs."""

    raw = f"{row.id}:{row.platform}:{row.shop_id}:{row.owner_user_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{row.platform}-{row.shop_id}-{digest}"


def _user_role(user: User | Any | None) -> str:
    return str(getattr(user, "role", "") or "")


def _can_cross_department_datasource_scope(user: User | Any | None) -> bool:
    role = _user_role(user)
    return bool(getattr(user, "can_view_all", False)) or role in {
        "admin",
        "system_admin",
        "aibp",
        "ai_engineer",
    }


def _can_see_sensitive_cookie_ids(user: User | Any | None) -> bool:
    role = _user_role(user)
    return bool(getattr(user, "can_view_all", False)) or role in {"admin", "system_admin"}


def _cookie_pool_scope_filter(user: User | Any | None):
    if user is None or _can_cross_department_datasource_scope(user):
        return None
    if _user_role(user) == "dept_admin" and getattr(user, "department", None):
        return DataSource.department == user.department
    return PlatformCookiePool.owner_user_id == getattr(user, "id", "")


def _connector_key_scope_filter(user: User | Any | None):
    if user is None or _can_cross_department_datasource_scope(user):
        return None
    if _user_role(user) == "dept_admin" and getattr(user, "department", None):
        return or_(
            DataSource.department == user.department,
            ConnectorApiKey.owner_user_id == getattr(user, "id", ""),
        )
    return ConnectorApiKey.owner_user_id == getattr(user, "id", "")


def _cookie_audit_scope_filter(user: User | Any | None):
    if user is None or _can_cross_department_datasource_scope(user):
        return None
    if _user_role(user) == "dept_admin" and getattr(user, "department", None):
        return DataSource.department == user.department
    return PlatformCookieAudit.owner_user_id == getattr(user, "id", "")


def _redact_cookie_pool_payload(item: dict, *, can_see_sensitive_ids: bool) -> dict:
    if can_see_sensitive_ids:
        return item
    redacted = dict(item)
    redacted["id"] = redacted.get("credential_alias") or f"{redacted.get('platform')}:{redacted.get('shop_id')}"
    redacted["owner_user_id"] = None
    redacted["connector_key_id"] = None
    return redacted


def _serialize_cookie_audit_row(row: PlatformCookieAudit, *, can_see_sensitive_ids: bool) -> dict:
    return {
        "id": row.id,
        "cookie_pool_id": row.cookie_pool_id if can_see_sensitive_ids else None,
        "source_id": row.source_id,
        "platform": row.platform,
        "shop_id": row.shop_id,
        "owner_user_id": row.owner_user_id if can_see_sensitive_ids else None,
        "connector_key_id": row.connector_key_id if can_see_sensitive_ids else None,
        "action": row.action,
        "auth_source": row.auth_source,
        "actor_user_id": row.actor_user_id,
        "detail": row.detail or {},
        "created_at": isoformat_bjt(row.created_at) if row.created_at else None,
    }


async def create_connector_api_key(
    db: AsyncSession,
    *,
    name: str = "",
    source_id: str | None = None,
    owner_user_id: str,
    scopes: list[str] | str | None = None,
    platforms: list[str] | str | None = None,
    shop_ids: list[str] | str | None = None,
    created_by: str,
) -> dict:
    """创建新的 connector key。"""
    if source_id:
        _validate_source_id(source_id)
    key_id, api_key = _generate_connector_key_material()
    scope_list = (
        _normalize_str_list(scopes)
        if scopes is not None
        else [CONNECTOR_REQUIRED_PUSH_SCOPE]
    )
    row = ConnectorApiKey(
        id=key_id,
        name=name.strip() or f"Connector Key {key_id[-6:]}",
        key_hash=_connector_key_hash(api_key),
        encrypted_key=_encrypt_connector_api_key(api_key),
        key_prefix=api_key[:16],
        key_last4=api_key[-4:],
        source_id=source_id or None,
        owner_user_id=owner_user_id,
        scopes=scope_list,
        allowed_platforms=_normalize_str_list(platforms, lowercase=True),
        allowed_shop_ids=_normalize_str_list(shop_ids),
        status="active",
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    return {**_serialize_connector_key(row, include_plaintext=True), "api_key": api_key}


async def list_connector_api_keys(
    db: AsyncSession,
    *,
    status: str | None = None,
    owner_user_id: str | None = None,
    current_user: User | Any | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    stmt = select(ConnectorApiKey).outerjoin(DataSource, ConnectorApiKey.source_id == DataSource.id)
    count_stmt = (
        select(func.count())
        .select_from(ConnectorApiKey)
        .outerjoin(DataSource, ConnectorApiKey.source_id == DataSource.id)
    )
    if status:
        stmt = stmt.where(ConnectorApiKey.status == status)
        count_stmt = count_stmt.where(ConnectorApiKey.status == status)
    if current_user is not None:
        scope_filter = _connector_key_scope_filter(current_user)
        if scope_filter is not None:
            stmt = stmt.where(scope_filter)
            count_stmt = count_stmt.where(scope_filter)
    elif owner_user_id:
        stmt = stmt.where(ConnectorApiKey.owner_user_id == owner_user_id)
        count_stmt = count_stmt.where(ConnectorApiKey.owner_user_id == owner_user_id)

    total = (await db.execute(count_stmt)).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(ConnectorApiKey.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return {
        "items": [_serialize_connector_key(row, include_plaintext=True) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def rotate_connector_api_key_record(
    db: AsyncSession,
    *,
    key_id: str,
    actor_user_id: str,
) -> dict:
    old = (await db.execute(
        select(ConnectorApiKey).where(ConnectorApiKey.id == key_id)
    )).scalar_one_or_none()
    if not old:
        raise AppError("NOT_FOUND", 404, {"detail": "connector key 不存在"})
    if old.status != "active":
        raise AppError("CONNECTOR_KEY_REVOKED", 400, {"key_id": key_id})

    new_id, api_key = _generate_connector_key_material()
    now = now_bjt()
    old.status = "revoked"
    old.revoked_at = now
    old.revoked_by = actor_user_id
    old.revoke_reason = "rotated"
    old.updated_at = now
    new_row = ConnectorApiKey(
        id=new_id,
        name=old.name,
        key_hash=_connector_key_hash(api_key),
        encrypted_key=_encrypt_connector_api_key(api_key),
        key_prefix=api_key[:16],
        key_last4=api_key[-4:],
        source_id=old.source_id,
        owner_user_id=old.owner_user_id,
        scopes=list(old.scopes or []),
        allowed_platforms=list(old.allowed_platforms or []),
        allowed_shop_ids=list(old.allowed_shop_ids or []),
        status="active",
        created_by=actor_user_id,
        rotated_from_key_id=old.id,
        created_at=now,
        updated_at=now,
    )
    db.add(new_row)
    await db.commit()
    return {**_serialize_connector_key(new_row, include_plaintext=True), "api_key": api_key, "rotated_from_key_id": old.id}


async def revoke_connector_api_key(
    db: AsyncSession,
    *,
    key_id: str,
    actor_user_id: str,
    reason: str = "manual",
) -> dict:
    row = (await db.execute(
        select(ConnectorApiKey).where(ConnectorApiKey.id == key_id)
    )).scalar_one_or_none()
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "connector key 不存在"})

    if row.status == "active":
        row.status = "revoked"
        row.revoked_at = now_bjt()
        row.revoked_by = actor_user_id
        row.revoke_reason = reason
        row.updated_at = now_bjt()
    disabled = await revoke_and_disable_cookies(
        db,
        connector_key_id=key_id,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await db.commit()
    return {"id": key_id, "status": "revoked", "disabled_cookies": disabled}


async def revoke_and_disable_cookies(
    db: AsyncSession,
    *,
    connector_key_id: str,
    actor_user_id: str,
    reason: str = "connector_key_revoked",
) -> int:
    """Disable active cookie pool rows created by a connector key."""
    rows = (await db.execute(
        select(PlatformCookiePool).where(
            PlatformCookiePool.connector_key_id == connector_key_id,
            PlatformCookiePool.is_active == True,  # noqa: E712
        )
    )).scalars().all()
    now = now_bjt()
    for row in rows:
        _add_cookie_audit(
            db,
            row,
            action="revoke",
            actor_user_id=actor_user_id,
            detail={"reason": reason},
        )
        row.is_active = False
        row.status = "disabled"
        row.disabled_at = now
        row.disabled_by = actor_user_id
        row.disable_reason = reason
        row.updated_at = now
        _add_cookie_audit(
            db,
            row,
            action="disable",
            actor_user_id=actor_user_id,
            detail={"reason": reason},
        )
    return len(rows)


async def cascade_disable_by_user_disabled(
    db: AsyncSession,
    *,
    user_id: str,
    actor_user_id: str,
    reason: str = "cascade_disable_by_user_disabled",
) -> dict[str, int]:
    """Revoke a disabled user's connector keys and disable their active cookies."""
    now = now_bjt()
    key_rows = (await db.execute(
        select(ConnectorApiKey).where(
            ConnectorApiKey.owner_user_id == user_id,
            ConnectorApiKey.status == "active",
        )
    )).scalars().all()
    for key in key_rows:
        key.status = "revoked"
        key.revoked_at = now
        key.revoked_by = actor_user_id
        key.revoke_reason = reason
        key.updated_at = now

    pool_rows = (await db.execute(
        select(PlatformCookiePool).where(
            PlatformCookiePool.owner_user_id == user_id,
            PlatformCookiePool.is_active == True,  # noqa: E712
        )
    )).scalars().all()
    for row in pool_rows:
        row.is_active = False
        row.status = "disabled"
        row.health_score = 0
        row.verification_status = "disabled"
        row.last_error_code = "USER_DISABLED"
        row.disabled_at = now
        row.disabled_by = actor_user_id
        row.disable_reason = reason
        row.updated_at = now
        _add_cookie_audit(
            db,
            row,
            action="user_disabled",
            actor_user_id=actor_user_id,
            detail={"reason": reason, "disabled_user_id": user_id},
        )
    await db.flush()
    return {"revoked_connector_keys": len(key_rows), "disabled_cookies": len(pool_rows)}


def _serialize_cookie_pool_row(row: PlatformCookiePool) -> dict:
    health_score = int(getattr(row, "health_score", 100 if row.is_active else 0) or 0)
    priority = int(getattr(row, "priority", 50) or 50)
    capability = getattr(row, "capability_json", None) or {}
    return {
        "id": str(row.id),
        "source_id": row.source_id,
        "platform": row.platform,
        "shop_id": row.shop_id,
        "account_login": row.account_login,
        "owner_user_id": row.owner_user_id,
        "credential_alias": _credential_alias_for_pool(row),
        "connector_key_id": row.connector_key_id,
        "device_label": getattr(row, "device_label", None),
        "auth_source": row.auth_source,
        "status": row.status,
        "is_active": bool(row.is_active),
        "health": round(max(min(health_score, 100), 0) / 100, 4),
        "health_score": health_score,
        "verification_status": getattr(row, "verification_status", None) or ("active" if row.is_active and row.status == "active" else "disabled"),
        "capability_summary": capability.get("summary") or ["cookies:push"],
        "capability_json": capability,
        "priority": priority,
        "domain": row.domain,
        "last_used_at": isoformat_bjt(row.last_used_at) if getattr(row, "last_used_at", None) else None,
        "last_verified_at": isoformat_bjt(row.last_verified_at) if getattr(row, "last_verified_at", None) else None,
        "last_error_code": getattr(row, "last_error_code", None),
        "latest_sync_at": isoformat_bjt(row.pushed_at) if row.pushed_at else None,
        "pushed_at": isoformat_bjt(row.pushed_at) if row.pushed_at else None,
        "disabled_at": isoformat_bjt(row.disabled_at) if row.disabled_at else None,
        "disable_reason": row.disable_reason,
        "created_at": isoformat_bjt(row.created_at) if row.created_at else None,
        "updated_at": isoformat_bjt(row.updated_at) if row.updated_at else None,
    }


async def _cookie_owner_map(db: AsyncSession, rows: list[PlatformCookiePool]) -> dict[str, User]:
    owner_ids = sorted({row.owner_user_id for row in rows if row.owner_user_id})
    if not owner_ids:
        return {}
    owners = (await db.execute(select(User).where(User.id.in_(owner_ids)))).scalars().all()
    return {owner.id: owner for owner in owners}


def _attach_cookie_owner_display(item: dict, row: PlatformCookiePool, owner: User | None) -> dict:
    result = dict(item)
    if owner:
        owner_name = owner.name or owner.username or owner.id
        inactive = getattr(owner, "state", None) == "disabled" or not bool(getattr(owner, "is_active", True))
        result["owner_name"] = f"已离职：原 {owner_name}" if inactive else owner_name
        result["owner_department"] = owner.department
        result["owner_status"] = "revoked" if inactive else (getattr(owner, "state", None) or "active")
    else:
        result["owner_name"] = row.owner_user_id or "未知授权人"
        result["owner_department"] = None
        result["owner_status"] = "unknown"
    return result


async def list_cookie_pool_summary(
    db: AsyncSession,
    *,
    platform: str | None = None,
    shop_id: str | None = None,
    current_user: User | Any | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List platform/shop cookie-pool summaries without exposing sensitive IDs."""
    stmt = (
        select(
            PlatformCookiePool.platform,
            PlatformCookiePool.shop_id,
            func.count(PlatformCookiePool.id).label("total_count"),
            func.sum(cast(PlatformCookiePool.is_active == True, Integer)).label("active_count"),  # noqa: E712
            func.sum(cast(and_(PlatformCookiePool.is_active == True, PlatformCookiePool.priority > 50), Integer)).label("standby_count"),  # noqa: E712
            func.avg(PlatformCookiePool.health_score)
            .filter(PlatformCookiePool.is_active == True)  # noqa: E712
            .label("avg_health_score"),
            func.max(PlatformCookiePool.pushed_at).label("latest_sync_at"),
        )
        .select_from(PlatformCookiePool)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
    )
    count_base = (
        select(PlatformCookiePool.platform, PlatformCookiePool.shop_id)
        .select_from(PlatformCookiePool)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
    )
    filters = []
    if platform:
        filters.append(PlatformCookiePool.platform == platform.strip().lower())
    if shop_id:
        filters.append(PlatformCookiePool.shop_id == shop_id.strip())
    scope_filter = _cookie_pool_scope_filter(current_user)
    if scope_filter is not None:
        filters.append(scope_filter)
    if filters:
        stmt = stmt.where(*filters)
        count_base = count_base.where(*filters)
    count_stmt = select(func.count()).select_from(
        count_base.group_by(PlatformCookiePool.platform, PlatformCookiePool.shop_id).subquery()
    )
    stmt = (
        stmt.group_by(PlatformCookiePool.platform, PlatformCookiePool.shop_id)
        .order_by(func.max(PlatformCookiePool.pushed_at).desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = (await db.execute(count_stmt)).scalar() or 0
    rows = (await db.execute(stmt)).all()
    items = []
    for row in rows:
        active_count = int(row.active_count or 0)
        standby_count = int(row.standby_count or 0)
        raw_total_count = int(row.total_count or 0)
        disabled_count = max(raw_total_count - active_count, 0)
        total_count = active_count
        avg_health = round(float(row.avg_health_score or 0) / 100, 4) if active_count else 0
        label_stmt = (
            select(PlatformCookiePool.device_label, PlatformCookiePool.account_login)
            .where(PlatformCookiePool.platform == row.platform)
            .where(PlatformCookiePool.shop_id == row.shop_id)
            .order_by(PlatformCookiePool.is_active.desc(), PlatformCookiePool.pushed_at.desc())
            .limit(1)
        )
        label_row = (await db.execute(label_stmt)).first()
        display_name = (label_row[0] or label_row[1]) if label_row else None
        items.append({
            "id": f"{row.platform}:{row.shop_id}",
            "platform": row.platform,
            "shop_id": row.shop_id,
            "shop_name": display_name or row.shop_id,
            "active_count": active_count,
            "standby_count": standby_count,
            "disabled_count": disabled_count,
            "total_count": total_count,
            "verify_pass_rate": round(active_count / total_count, 4) if total_count else 0,
            "mixed_credentials_ratio": 1.0 if active_count > 1 else 0.0,
            "avg_health": avg_health,
            "latest_sync_at": isoformat_bjt(row.latest_sync_at) if row.latest_sync_at else None,
            "warning_count": disabled_count,
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": {
            "platform_shop_count": total,
            "active_credential_count": sum(item["active_count"] for item in items),
        },
    }


async def get_cookie_pool_detail(
    db: AsyncSession,
    *,
    platform: str,
    shop_id: str,
    include_audit: bool = False,
    current_user: User | Any | None = None,
) -> dict:
    platform_norm = platform.strip().lower()
    shop_norm = shop_id.strip()
    stmt = (
        select(PlatformCookiePool)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
        .where(PlatformCookiePool.platform == platform_norm)
        .where(PlatformCookiePool.shop_id == shop_norm)
        .order_by(PlatformCookiePool.is_active.desc(), PlatformCookiePool.pushed_at.desc())
    )
    scope_filter = _cookie_pool_scope_filter(current_user)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)
    rows = (await db.execute(stmt)).scalars().all()
    can_see_sensitive_ids = _can_see_sensitive_cookie_ids(current_user)
    owner_map = await _cookie_owner_map(db, rows)
    credentials = [
        _redact_cookie_pool_payload(
            _attach_cookie_owner_display(
                _serialize_cookie_pool_row(row),
                row,
                owner_map.get(row.owner_user_id),
            ),
            can_see_sensitive_ids=can_see_sensitive_ids,
        )
        for row in rows
    ]
    summary = {
        "id": f"{platform_norm}:{shop_norm}",
        "platform": platform_norm,
        "shop_id": shop_norm,
        "shop_name": next((row.device_label or row.account_login for row in rows if row.device_label or row.account_login), shop_norm),
        "active_count": sum(1 for row in rows if row.is_active),
        "standby_count": 0,
        "disabled_count": sum(1 for row in rows if not row.is_active),
        "total_count": len(rows),
        "latest_sync_at": max(
            [isoformat_bjt(row.pushed_at) for row in rows if row.pushed_at],
            default=None,
        ),
    }
    audit_items: list[dict] = []
    if include_audit:
        audit_cutoff = now_bjt() - timedelta(days=30)
        audit_stmt = (
            select(PlatformCookieAudit)
            .outerjoin(DataSource, PlatformCookieAudit.source_id == DataSource.id)
            .where(PlatformCookieAudit.platform == platform_norm)
            .where(PlatformCookieAudit.shop_id == shop_norm)
            .where(PlatformCookieAudit.created_at >= audit_cutoff)
            .order_by(PlatformCookieAudit.created_at.desc())
            .limit(100)
        )
        audit_scope_filter = _cookie_audit_scope_filter(current_user)
        if audit_scope_filter is not None:
            audit_stmt = audit_stmt.where(audit_scope_filter)
        audit_rows = (await db.execute(audit_stmt)).scalars().all()
        audit_items = [
            _serialize_cookie_audit_row(row, can_see_sensitive_ids=can_see_sensitive_ids)
            for row in audit_rows
        ]
    return {"summary": summary, "credentials": credentials, "items": credentials, "audit": audit_items}


async def list_cookie_pool_audit(
    db: AsyncSession,
    *,
    platform: str | None = None,
    shop_id: str | None = None,
    limit: int = 100,
    current_user: User | Any | None = None,
) -> dict:
    audit_cutoff = now_bjt() - timedelta(days=30)
    stmt = (
        select(PlatformCookieAudit)
        .outerjoin(DataSource, PlatformCookieAudit.source_id == DataSource.id)
        .where(PlatformCookieAudit.created_at >= audit_cutoff)
    )
    if platform:
        stmt = stmt.where(PlatformCookieAudit.platform == platform.strip().lower())
    if shop_id:
        stmt = stmt.where(PlatformCookieAudit.shop_id == shop_id.strip())
    scope_filter = _cookie_audit_scope_filter(current_user)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)
    rows = (await db.execute(
        stmt.order_by(PlatformCookieAudit.created_at.desc()).limit(limit)
    )).scalars().all()
    can_see_sensitive_ids = _can_see_sensitive_cookie_ids(current_user)
    return {
        "items": [
            _serialize_cookie_audit_row(row, can_see_sensitive_ids=can_see_sensitive_ids)
            for row in rows
        ],
        "total": len(rows),
    }


async def collection_health_overview(
    db: AsyncSession,
    *,
    platform: str | None = None,
    shop_id: str | None = None,
    current_user: User | Any | None = None,
) -> dict:
    """Return shop-level collection health for the admin health board."""

    from app.collection.models import CollectionProof

    pool_summary = await list_cookie_pool_summary(
        db,
        platform=platform,
        shop_id=shop_id,
        current_user=current_user,
        page=1,
        page_size=500,
    )
    items: list[dict] = []
    for row in pool_summary.get("items", []):
        proof_stmt = select(CollectionProof).where(
            CollectionProof.platform == row["platform"],
            CollectionProof.shop_id == row["shop_id"],
        )
        proof_rows = (await db.execute(
            proof_stmt.order_by(CollectionProof.created_at.desc()).limit(200)
        )).scalars().all()
        success = sum(1 for proof in proof_rows if proof.status == "success")
        total = len(proof_rows)
        missing_scopes = sorted({
            proof.data_scope
            for proof in proof_rows
            if proof.status != "success" and proof.data_scope
        })[:3]
        warning_groups = sorted({
            proof.warning_group
            for proof in proof_rows
            if proof.warning_group and proof.status != "success"
        })
        items.append({
            **row,
            "data_health_ratio": round(success / total, 4) if total else row.get("avg_health", 0),
            "proof_count": total,
            "warning_groups": warning_groups,
            "circuit_state": "open" if warning_groups else "closed",
            "missing_data_scopes": missing_scopes,
        })
    return {
        "items": items,
        "total": len(items),
        "stats": {
            "shop_count": len(items),
            "active_credential_count": sum(int(item.get("active_count") or 0) for item in items),
            "avg_data_health_ratio": round(
                sum(float(item.get("data_health_ratio") or 0) for item in items) / len(items),
                4,
            ) if items else 0,
        },
    }


async def list_platform_api_drift_alerts(
    db: AsyncSession,
    *,
    platform: str | None = None,
    limit: int = 100,
    current_user: User | Any | None = None,
) -> dict:
    """Build lightweight schema/row-count drift alerts from API snapshots."""

    from app.collection.models import PlatformApiSchemaSnapshot

    stmt = select(PlatformApiSchemaSnapshot)
    if platform:
        stmt = stmt.where(PlatformApiSchemaSnapshot.platform == platform.strip().lower())
    visible_pairs = await _visible_cookie_platform_shop_pairs(db, current_user)
    if visible_pairs is not None:
        if not visible_pairs:
            return {"items": [], "total": 0}
        stmt = stmt.where(or_(*[
            and_(
                PlatformApiSchemaSnapshot.platform == pair_platform,
                PlatformApiSchemaSnapshot.shop_id == pair_shop,
            )
            for pair_platform, pair_shop in visible_pairs
        ]))
    rows = (await db.execute(
        stmt.order_by(PlatformApiSchemaSnapshot.captured_at.desc()).limit(max(limit * 4, limit))
    )).scalars().all()
    by_endpoint: dict[tuple[str, str, str], list] = {}
    for row in rows:
        by_endpoint.setdefault((row.platform, row.shop_id or "", row.endpoint_hash), []).append(row)

    alerts: list[dict] = []
    for (row_platform, row_shop, endpoint_hash), snapshots in by_endpoint.items():
        if len(alerts) >= limit:
            break
        latest = snapshots[0]
        previous = snapshots[1] if len(snapshots) > 1 else None
        if not previous:
            continue
        latest_schema = _normalize_snapshot_schema(latest.response_keys)
        previous_schema = _normalize_snapshot_schema(previous.response_keys)
        latest_keys = set(latest_schema)
        previous_keys = set(previous_schema)
        missing = sorted(previous_keys - latest_keys)
        added = sorted(latest_keys - previous_keys)
        type_changes = [
            {
                "field": key,
                "before": previous_schema[key],
                "after": latest_schema[key],
            }
            for key in sorted(previous_keys & latest_keys)
            if previous_schema.get(key)
            and latest_schema.get(key)
            and previous_schema.get(key) != latest_schema.get(key)
        ]
        row_count_change = None
        if previous and previous.row_count not in (None, 0) and latest.row_count is not None:
            row_count_change = round((latest.row_count - previous.row_count) / max(previous.row_count, 1), 4)
        change_type = None
        if missing:
            change_type = "field_missing"
        elif type_changes:
            change_type = "type_change"
        elif row_count_change is not None and abs(row_count_change) >= 0.5:
            change_type = "row_count_spike"
        if not change_type:
            continue
        alert_id = f"{row_platform}:{row_shop}:{endpoint_hash}"
        alerts.append({
            "id": alert_id,
            "platform": row_platform,
            "shop_id": row_shop,
            "endpoint": latest.endpoint,
            "endpoint_hash": endpoint_hash,
            "change_type": change_type,
            "first_seen_at": isoformat_bjt(latest.captured_at) if latest.captured_at else None,
            "latest_snapshot_at": isoformat_bjt(latest.captured_at) if latest.captured_at else None,
            "acknowledged": False,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "status": "open",
            "review_task_id": None,
            "before_keys": sorted(previous_keys),
            "after_keys": sorted(latest_keys),
            "missing_fields": missing,
            "added_fields": added,
            "type_changes": type_changes,
            "row_count": latest.row_count,
            "previous_row_count": previous.row_count if previous else None,
            "row_count_change": row_count_change,
            "affected_skills": [],
        })
    alert_ids = [item["id"] for item in alerts]
    if alert_ids:
        ack_rows = (await db.execute(
            select(PlatformApiDriftAck).where(PlatformApiDriftAck.alert_id.in_(alert_ids))
        )).scalars().all()
        ack_by_id = {row.alert_id: row for row in ack_rows}
        for item in alerts:
            ack = ack_by_id.get(item["id"])
            if not ack:
                continue
            item["acknowledged"] = True
            item["acknowledged_by"] = ack.acknowledged_by
            item["acknowledged_at"] = isoformat_bjt(ack.acknowledged_at) if ack.acknowledged_at else None
            item["status"] = ack.status
            item["review_task_id"] = ack.review_task_id
    return {"items": alerts[:limit], "total": len(alerts)}


def _normalize_snapshot_schema(raw: Any) -> dict[str, str | None]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        result: dict[str, str | None] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                result[str(key)] = value
            elif isinstance(value, dict):
                result[str(key)] = str(value.get("type") or value.get("kind") or "") or None
            else:
                result[str(key)] = type(value).__name__ if value is not None else None
        return result
    if isinstance(raw, list):
        result = {}
        for item in raw:
            if isinstance(item, str):
                result[item] = None
            elif isinstance(item, dict):
                key = item.get("name") or item.get("key") or item.get("field")
                if key:
                    result[str(key)] = str(item.get("type") or item.get("kind") or "") or None
        return result
    return {}


async def _visible_cookie_platform_shop_pairs(
    db: AsyncSession,
    user: User | Any | None,
) -> set[tuple[str, str]] | None:
    if user is None or _can_cross_department_datasource_scope(user):
        return None
    stmt = (
        select(PlatformCookiePool.platform, PlatformCookiePool.shop_id)
        .select_from(PlatformCookiePool)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
    )
    scope_filter = _cookie_pool_scope_filter(user)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)
    return {
        (platform, shop_id)
        for platform, shop_id in (await db.execute(stmt)).all()
    }


async def _ensure_platform_api_drift_alert_access(
    db: AsyncSession,
    *,
    alert_id: str,
    current_user: User | Any,
) -> None:
    if _can_cross_department_datasource_scope(current_user):
        return
    parts = alert_id.split(":", 2)
    if len(parts) != 3:
        raise AppError("PARAM_INVALID", 400, {"detail": "alert_id 格式无效"})
    visible_pairs = await _visible_cookie_platform_shop_pairs(db, current_user)
    if visible_pairs is None:
        return
    if (parts[0], parts[1]) not in visible_pairs:
        raise AppError("AUTH_PERMISSION_DENIED", 403)


async def ack_platform_api_drift_alert(
    db: AsyncSession,
    *,
    alert_id: str,
    current_user: User | Any,
    status: str = "acknowledged",
) -> dict:
    await _ensure_platform_api_drift_alert_access(
        db,
        alert_id=alert_id,
        current_user=current_user,
    )
    now = now_bjt()
    ack = await db.get(PlatformApiDriftAck, alert_id)
    review_task_id = ack.review_task_id if ack else None
    if not review_task_id:
        review_task_id = await _create_or_get_platform_api_drift_review_task(
            db,
            alert_id=alert_id,
            actor_user_id=current_user.id,
        )
    if ack:
        ack.acknowledged_by = current_user.id
        ack.acknowledged_at = now
        ack.status = status
        ack.review_task_id = review_task_id
        ack.updated_at = now
    else:
        ack = PlatformApiDriftAck(
            alert_id=alert_id,
            acknowledged_by=current_user.id,
            acknowledged_at=now,
            status=status,
            review_task_id=review_task_id,
            created_at=now,
            updated_at=now,
        )
        db.add(ack)
    await db.commit()
    return {
        "ok": True,
        "alert_id": alert_id,
        "acknowledged": True,
        "acknowledged_by": current_user.id,
        "acknowledged_at": isoformat_bjt(now),
        "status": status,
        "review_task_id": review_task_id,
    }


async def _create_or_get_platform_api_drift_review_task(
    db: AsyncSession,
    *,
    alert_id: str,
    actor_user_id: str,
) -> str:
    from app.todos.models import AITodo, DecisionRequest

    source_id = "api-drift-" + hashlib.sha256(alert_id.encode("utf-8")).hexdigest()[:24]
    existing = (await db.execute(
        select(DecisionRequest)
        .where(DecisionRequest.source_type == "platform_api_drift")
        .where(DecisionRequest.source_id == source_id)
        .where(DecisionRequest.kind == "review")
        .where(DecisionRequest.skill_id == "platform_api_drift")
    )).scalar_one_or_none()
    if existing:
        return existing.id

    request = DecisionRequest(
        id=f"dr-{uuid4().hex}",
        source_type="platform_api_drift",
        source_id=source_id,
        skill_id="platform_api_drift",
        kind="review",
        title="平台 API Drift 需要处理",
        summary=f"Drift alert {alert_id} 已 ACK，需确认采集契约或接口变化。",
        payload={"alert_id": alert_id},
        decision_mode="any_of",
        aggregate_status="pending",
        sla_at=now_bjt() + timedelta(hours=24),
    )
    db.add(request)
    await db.flush()
    db.add(AITodo(request_id=request.id, kind="review", assignee=actor_user_id))
    await db.flush()
    return request.id


async def disable_cookie_pool_credential(
    db: AsyncSession,
    *,
    credential_id: str,
    actor_user_id: str,
    reason: str = "manual_disable",
    current_user: User | Any | None = None,
) -> dict:
    normalized_reason = str(reason or "").strip()
    if len(normalized_reason) < MIN_COOKIE_DISABLE_REASON_LEN:
        raise AppError(
            "COOKIE_REASON_REQUIRED",
            400,
            {"min_length": MIN_COOKIE_DISABLE_REASON_LEN, "given": len(normalized_reason)},
        )
    row = await _get_cookie_pool_row_for_user(db, credential_id=credential_id, current_user=current_user)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "cookie credential 不存在"})
    if row.is_active:
        now = now_bjt()
        row.is_active = False
        row.status = "disabled"
        row.health_score = 0
        row.verification_status = "disabled"
        row.last_error_code = "MANUAL_DISABLED"
        row.disabled_at = now
        row.disabled_by = actor_user_id
        row.disable_reason = normalized_reason
        row.updated_at = now
        _add_cookie_audit(db, row, action="disable", actor_user_id=actor_user_id, detail={"reason": normalized_reason})
        await db.commit()
    return _redact_cookie_pool_payload(
        _serialize_cookie_pool_row(row),
        can_see_sensitive_ids=_can_see_sensitive_cookie_ids(current_user),
    )


async def update_cookie_pool_credential_priority(
    db: AsyncSession,
    *,
    credential_id: str,
    priority: int,
    actor_user_id: str,
    current_user: User | Any | None = None,
) -> dict:
    row = await _get_cookie_pool_row_for_user(db, credential_id=credential_id, current_user=current_user)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "cookie credential 不存在"})
    row.priority = max(1, min(int(priority), 100))
    row.updated_at = now_bjt()
    _add_cookie_audit(
        db,
        row,
        action="priority",
        actor_user_id=actor_user_id,
        detail={"priority": row.priority},
    )
    await db.commit()
    return _redact_cookie_pool_payload(
        _serialize_cookie_pool_row(row),
        can_see_sensitive_ids=_can_see_sensitive_cookie_ids(current_user),
    )


async def verify_cookie_pool_credential(
    db: AsyncSession,
    *,
    credential_id: str,
    current_user: User | Any | None = None,
) -> dict:
    row = await _get_cookie_pool_row_for_user(db, credential_id=credential_id, current_user=current_user)
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": "cookie credential 不存在"})

    if not (row.is_active and row.status == "active"):
        row.last_verified_at = now_bjt()
        row.verification_status = "disabled"
        row.health_score = 0
        row.last_error_code = "COOKIE_DISABLED"
        _add_cookie_audit(
            db,
            row,
            action="verify",
            actor_user_id=getattr(current_user, "id", None),
            detail={
                "verification_status": row.verification_status,
                "probe_status": "disabled",
                "logged_in": False,
                "detail": "credential 已停用，未注入浏览器",
            },
        )
        await db.commit()
        return {
            **_redact_cookie_pool_payload(
                _serialize_cookie_pool_row(row),
                can_see_sensitive_ids=_can_see_sensitive_cookie_ids(current_user),
            ),
            "verification_status": row.verification_status,
            "verified": False,
            "status": "disabled",
            "detail": "credential 已停用，未注入浏览器",
        }

    direct_result = await _direct_verify_sycm_cookie_pool(row)
    if direct_result and direct_result.get("status") in {"valid", "expired"}:
        new_status = str(direct_result.get("status") or "")
        detail = str(direct_result.get("detail") or "")
        logged_in = bool(direct_result.get("logged_in"))
        previous = _apply_cookie_pool_verify_result(row, new_status=new_status, detail=detail)
        _add_cookie_audit(
            db,
            row,
            action="verify",
            actor_user_id=getattr(current_user, "id", None),
            detail={
                "verification_status": row.verification_status,
                "previous_verification_status": previous["verification_status"],
                "previous_health_score": previous["health_score"],
                "previous_last_error_code": previous["last_error_code"],
                "probe_status": new_status,
                "logged_in": logged_in,
                "detail": detail,
                "cdp_role": "direct_cookie",
                "transport": direct_result.get("transport") or "direct_cookie",
            },
        )
        await db.commit()
        return {
            **_redact_cookie_pool_payload(
                _serialize_cookie_pool_row(row),
                can_see_sensitive_ids=_can_see_sensitive_cookie_ids(current_user),
            ),
            "verification_status": row.verification_status,
            "verified": logged_in,
            "status": new_status,
            "detail": detail,
        }

    from app.browser import service as browser_service

    probe = browser_service.PLATFORM_LOGIN_PROBE.get(row.source_id)
    if not probe:
        raise AppError("LOGIN_PROBE_UNSUPPORTED", 400, {"source_id": row.source_id})
    platform, task_type = probe
    verify_lock = await browser_service.get_cookie_verify_lock(
        source_id=row.source_id,
        platform=row.platform,
        shop_id=row.shop_id,
        credential_id=row.id,
    )
    async with verify_lock:
        cdp_url, cdp_role = await browser_service.select_verify_cdp_url()

        async def _run_verify() -> dict:
            await browser_service.inject_cookie_pool_to_browser(
                db,
                row,
                cdp_url=cdp_url,
                clear_existing=True,
            )
            return await browser_service.run_collection(
                db,
                platform=platform,
                task_type=task_type,
                params={},
                user_id=getattr(current_user, "id", "") or "credential_verify",
                cdp_url=cdp_url,
                force_new_page=cdp_role == "verify",
            )

        async with browser_service._VERIFY_BROWSER_USE_LOCK:
            result = await _run_verify()
    if result.get("status") != "success":
        new_status = "unknown"
        detail = result.get("error") or "采集失败"
        logged_in = False
    else:
        payload = result.get("result") or {}
        payload_status = payload.get("status")
        if payload_status in ("valid", "expired", "unknown"):
            new_status = payload_status
        else:
            new_status = "valid" if payload.get("logged_in") else "expired"
        detail = payload.get("detail") or payload.get("current_url") or ""
        logged_in = bool(payload.get("logged_in"))
    previous = _apply_cookie_pool_verify_result(row, new_status=new_status, detail=detail)
    _add_cookie_audit(
        db,
        row,
        action="verify",
        actor_user_id=getattr(current_user, "id", None),
        detail={
            "verification_status": row.verification_status,
            "previous_verification_status": previous["verification_status"],
            "previous_health_score": previous["health_score"],
            "previous_last_error_code": previous["last_error_code"],
            "probe_status": new_status,
            "logged_in": logged_in,
            "detail": detail,
            "task_id": result.get("task_id"),
            "cdp_role": cdp_role,
        },
    )
    await db.commit()
    return {
        **_redact_cookie_pool_payload(
            _serialize_cookie_pool_row(row),
            can_see_sensitive_ids=_can_see_sensitive_cookie_ids(current_user),
        ),
        "verification_status": row.verification_status,
        "verified": logged_in,
        "status": new_status,
        "detail": detail,
    }


async def _get_cookie_pool_row_for_user(
    db: AsyncSession,
    *,
    credential_id: str,
    current_user: User | Any | None,
) -> PlatformCookiePool | None:
    stmt = (
        select(PlatformCookiePool)
        .outerjoin(DataSource, PlatformCookiePool.source_id == DataSource.id)
    )
    scope_filter = _cookie_pool_scope_filter(current_user)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)
    try:
        row_id = int(credential_id)
    except (TypeError, ValueError):
        row_id = None
    if row_id is not None:
        return (await db.execute(stmt.where(PlatformCookiePool.id == row_id))).scalar_one_or_none()
    rows = (await db.execute(stmt.limit(500))).scalars().all()
    return next((row for row in rows if _credential_alias_for_pool(row) == credential_id), None)


async def get_or_create_connector_api_key(db: AsyncSession) -> str:
    """获取或生成 Chrome 扩展用的 API Key。"""
    from app.common.models import SystemConfig

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.key == "connector.api_key")
    )).scalar_one_or_none()
    if row:
        return _system_config_value_as_str(row.value)

    key = secrets.token_urlsafe(32)
    db.add(SystemConfig(key="connector.api_key", value=key))
    await db.commit()
    logger.info("[cookies] generated connector API key: {}...", key[:8])
    return key


async def rotate_connector_api_key(db: AsyncSession) -> str:
    """强制重新生成 Chrome 扩展 API Key。"""
    import secrets

    row = (await db.execute(
        select(SystemConfig).where(SystemConfig.key == "connector.api_key")
    )).scalar_one_or_none()

    key = secrets.token_urlsafe(32)
    if row:
        row.value = key
        row.updated_at = now_bjt()
    else:
        db.add(SystemConfig(key="connector.api_key", value=key))
    await db.commit()
    logger.info("[cookies] rotated connector API key: {}...", key[:8])
    return key


async def disconnect_platform_connection(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: str,
) -> dict:
    """断开平台连接：清除 cookies 相关信息，但保留平台元数据。"""
    ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
    if not ds:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    config = dict(ds.config or {})
    for key in (
        "encrypted_cookies",
        "encrypted_cookie_details",
        "domain",
        "user_agent",
        "pushed_by",
        "pushed_at",
    ):
        config.pop(key, None)
    config["connected"] = False
    config["disconnected_by"] = user_id
    config["disconnected_at"] = isoformat_bjt(now_bjt())
    ds.config = config
    ds.updated_at = now_bjt()
    await db.commit()

    await audit.log(user_id, "datasource.platform_disconnect", "datasource", source_id)
    return {"source_id": source_id, "disconnected": True}


async def test_platform_connection(
    db: AsyncSession,
    *,
    source_id: str,
) -> dict:
    """测试平台 cookies 是否存在，只返回元信息，不返回明文 cookies。"""
    ds = (await db.execute(select(DataSource).where(DataSource.id == source_id))).scalar_one_or_none()
    if not ds or ds.source_type != "platform_cookies":
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    cookies = await get_cookies(db, source_id)
    config = ds.config or {}
    cookie_count = 0
    if cookies:
        cookie_count = len([part for part in cookies.split(";") if part.strip()])

    return {
        "source_id": source_id,
        "connected": bool(cookies),
        "cookie_count": cookie_count,
        "domain": config.get("domain"),
        "pushed_at": config.get("pushed_at"),
    }


async def list_api_discoveries(
    db: AsyncSession,
    *,
    domain: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """列出 Chrome 扩展上报的 API 发现记录。"""
    stmt = select(SystemConfig).where(SystemConfig.key.like("api_discovery.%"))
    count_stmt = select(func.count()).select_from(SystemConfig).where(SystemConfig.key.like("api_discovery.%"))

    if domain:
        key = f"api_discovery.{domain}"
        stmt = stmt.where(SystemConfig.key == key)
        count_stmt = count_stmt.where(SystemConfig.key == key)

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = (
        stmt
        .order_by(SystemConfig.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for row in rows:
        value = row.value or {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = {}
        apis = value.get("apis") or []
        items.append({
            "domain": row.key.removeprefix("api_discovery."),
            "page_url": value.get("page_url") or "",
            "page_title": value.get("page_title") or "",
            "reported_at": value.get("reported_at") or None,
            "api_count": len(apis),
            "apis": apis,
            "updated_at": isoformat_bjt(row.updated_at),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ═══ 数据访问授权（data_access_grants） ═══


async def list_grants(
    db: AsyncSession,
    source_id: str | None = None,
    grantee_type: str | None = None,
    grantee_id: str | None = None,
) -> list[dict]:
    """查询数据访问授权列表。

    支持按 source_id、grantee_type（user/org_unit）、grantee_id 筛选。
    grantee_type + grantee_id 是 spec 兼容的计算列，可以直接用于查询。
    """
    stmt = select(DataAccessGrant)
    if source_id:
        stmt = stmt.where(DataAccessGrant.source_id == source_id)
    if grantee_type:
        stmt = stmt.where(DataAccessGrant.grantee_type == grantee_type)
    if grantee_id:
        stmt = stmt.where(DataAccessGrant.grantee_id == grantee_id)
    stmt = stmt.order_by(DataAccessGrant.created_at.desc())

    result = await db.execute(stmt)
    grants = result.scalars().all()

    return [_serialize_grant(g) for g in grants]


async def list_data_access_grants(
    db: AsyncSession,
    source_id: str,
) -> dict:
    """兼容 `/data-sources/{id}/access-grants` 旧路由返回格式：`{items, total}`。"""
    items = await list_grants(db, source_id=source_id)
    return {"items": items, "total": len(items)}


async def create_grant(
    db: AsyncSession,
    source_id: str,
    grantee_user_id: str | None = None,
    grantee_org_unit_id: str | None = None,
    permission: str = "read",
    granted_by: str = "system",
) -> dict:
    """创建数据访问授权。至少指定 grantee_user_id 或 grantee_org_unit_id。"""
    if not grantee_user_id and not grantee_org_unit_id:
        raise AppError("PARAM_INVALID", 400,
                        detail={"reason": "至少指定 grantee_user_id 或 grantee_org_unit_id"})

    # 验证数据源存在
    source = (await db.execute(
        select(DataSource).where(DataSource.id == source_id)
    )).scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    grant = DataAccessGrant(
        source_id=source_id,
        grantee_user_id=grantee_user_id,
        grantee_org_unit_id=grantee_org_unit_id,
        permission=permission,
        granted_by=granted_by,
    )
    db.add(grant)
    await db.flush()
    await db.refresh(grant)

    await audit.log(granted_by, "datasource.grant_create", "datasource", source_id,
                    detail={"grant_id": grant.id, "grantee_type": grant.grantee_type,
                            "grantee_id": grant.grantee_id, "permission": permission})

    return {
        "id": grant.id,
        "source_id": grant.source_id,
        "grantee_type": grant.grantee_type,
        "grantee_id": grant.grantee_id,
        "permission": grant.permission,
    }


async def revoke_grant(
    db: AsyncSession,
    grant_id: int,
    revoked_by: str = "system",
) -> dict:
    """撤销数据访问授权。"""
    grant = (await db.execute(
        select(DataAccessGrant).where(DataAccessGrant.id == grant_id)
    )).scalar_one_or_none()
    if not grant:
        raise AppError("GRANT_NOT_FOUND", 404)

    source_id = grant.source_id
    await db.delete(grant)
    await db.flush()

    await audit.log(revoked_by, "datasource.grant_revoke", "datasource", source_id,
                    detail={"grant_id": grant_id})

    return {"status": "revoked", "grant_id": grant_id}


async def check_access(
    db: AsyncSession,
    source_id: str,
    user_id: str | None = None,
    org_unit_id: str | None = None,
) -> bool:
    """检查用户或组织单元是否有某数据源的访问权限。

    同时支持原始字段查询和 spec 兼容字段查询。
    """
    from sqlalchemy import or_

    conditions = [DataAccessGrant.source_id == source_id]
    grantee_conds = []
    if user_id:
        grantee_conds.append(DataAccessGrant.grantee_user_id == user_id)
    if org_unit_id:
        grantee_conds.append(DataAccessGrant.grantee_org_unit_id == org_unit_id)

    if not grantee_conds:
        return False

    conditions.append(or_(*grantee_conds))

    stmt = select(func.count()).select_from(DataAccessGrant).where(*conditions)
    count = (await db.execute(stmt)).scalar() or 0
    return count > 0


async def has_data_access(
    db: AsyncSession,
    source_id: str | None = None,
    *,
    user_id: str,
    user_department: str | None = None,
    can_view_all: bool = False,
) -> bool:
    """v2.7 大厅 v3 · 便捷包装：当前用户是否有权访问某数据源。

    优先级：can_view_all > owner_contact == user > DataAccessGrant（未过期） > 同部门兜底。
    router 里已有多处调用此函数但本体缺失（pre-existing bug），本次补齐。
    """
    if can_view_all:
        return True
    if not source_id:
        return False
    src = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if not src:
        return False
    if src.owner_contact and src.owner_contact == user_id:
        return True
    # 检查有效 grant
    now = now_bjt()
    grant = (
        await db.execute(
            select(DataAccessGrant.id).where(
                DataAccessGrant.source_id == source_id,
                DataAccessGrant.grantee_user_id == user_id,
                or_(DataAccessGrant.expires_at.is_(None), DataAccessGrant.expires_at > now),
            )
        )
    ).first()
    if grant:
        return True
    visible_departments = await _get_user_visible_departments(db, user_id)
    # 同部门兜底（visibility=department 且本部门）
    if (
        src.visibility == "department"
        and (
            (user_department and src.department == user_department)
            or src.department in visible_departments
        )
    ):
        return True
    # company 可见性的只给"元数据可见"，preview / download 仍需明确 grant
    return False


async def can_skill_access_source(
    db: AsyncSession,
    *,
    source_id: str,
    skill_id: str,
    skill_department: str | None = None,
) -> bool:
    """Return whether a runtime Skill may read a governed data source."""
    if not source_id or not skill_id:
        return False
    src = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if not src or not src.is_active:
        return False
    related_skills = src.related_skills or []
    if isinstance(related_skills, list) and skill_id in related_skills:
        return True
    return bool(skill_department and src.department == skill_department)


# ═══════════════════════════════════════════════════════════
# v2.7 大厅 v3 · 申请访问审批流
# ═══════════════════════════════════════════════════════════

MIN_REASON_LEN = 20
DEFAULT_GRANT_TTL_DAYS = 90
REQUEST_EXPIRE_DAYS = 14


async def _get_user_visible_departments(
    db: AsyncSession, user_id: str
) -> set[str]:
    """把用户 org membership 展开成可与 DataSource.department 比较的名称集合。"""
    org_ids = set(
        (
            await db.execute(
                select(UserOrgMembership.org_unit_id).where(
                    UserOrgMembership.user_id == user_id
                )
            )
        ).scalars().all()
    )
    if not org_ids:
        return set()

    expanded_org_ids = await expand_org_lineage(db, org_ids)
    if not expanded_org_ids:
        return set()

    return set(
        name
        for name in (
            await db.execute(select(OrgUnit.name).where(OrgUnit.id.in_(expanded_org_ids)))
        ).scalars().all()
        if name
    )


def _normalize_naive_bjt(value: datetime | None) -> datetime | None:
    """将 offset-aware 时间统一归一到 naive 北京时间；naive 输入按北京时间处理。"""
    return to_bjt_naive(value)


async def request_data_access(
    db: AsyncSession,
    *,
    source_id: str,
    user_id: str,
    user_department: str | None = None,
    reason: str,
) -> dict:
    """用户在大厅发起数据访问申请。幂等：同 (source × user) 已有 pending 返回现有 id。

    流程：校验 → 幂等查 → 写 DataAccessRequest(pending) → outbox 推 owner_contact → 返回 id。
    """
    from app.auth.models import User
    from app.dingtalk.outbox import outbox

    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise AppError(
            "REASON_TOO_SHORT",
            400,
            detail={"min_length": MIN_REASON_LEN, "given": len(reason.strip()) if reason else 0},
        )

    source = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 幂等：同 user×source 有 pending 直接返回
    existing = (
        await db.execute(
            select(DataAccessRequest).where(
                DataAccessRequest.source_id == source_id,
                DataAccessRequest.requester_id == user_id,
                DataAccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {
            "id": existing.id,
            "status": "pending",
            "idempotent": True,
            "created_at": isoformat_bjt(existing.created_at),
        }

    # 已有有效 grant 的不再重复申请
    now = now_bjt()
    has_grant = await has_data_access(db, user_id=user_id, source_id=source_id)
    if has_grant:
        return {"id": 0, "status": "granted", "message": "已有授权，无需申请"}

    req = DataAccessRequest(
        source_id=source_id,
        requester_id=user_id,
        reason=reason.strip(),
        requested_permission="read",
        status="pending",
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)

    # 推 owner_contact（若有），否则 fallback 给 admin 兜底
    recipient_user_id = source.owner_contact
    if recipient_user_id:
        recipient_user = (
            await db.execute(
                select(User.dingtalk_user_id, User.name)
                .where(User.id == recipient_user_id)
            )
        ).first()
        recipient_ding = recipient_user[0] if recipient_user else None
    else:
        recipient_ding = None

    if recipient_ding:
        action_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/admin/data-requests/{req.id}"
        payload = {
            "kind": "data_access_request",
            "title": f"数据访问申请：{source.name}",
            "message": (
                f"{user_id}（{user_department or '-'}）申请访问数据源 "
                f"{source.name}（{source_id}）\n理由：{reason.strip()[:200]}"
            ),
            "action_url": action_url,
            "request_id": req.id,
            "source_id": source.id,
            "source_name": source.name,
            "requester": user_id,
            "requester_department": user_department,
        }
        await outbox.enqueue(
            message_type="work_notice",
            recipient=recipient_ding,
            payload=payload,
            priority=1,
            related_type="data_access_request",
            related_id=str(req.id),
            session=db,
        )
        logger.info(
            f"数据访问申请已入队: req={req.id} source={source_id} to={recipient_ding}"
        )
    else:
        logger.warning(
            f"数据访问申请 req={req.id} source={source_id} 无 owner_contact，钉钉未推；需 admin 在 /admin/data-requests 审批"
        )

    await audit.log(
        user_id,
        "datasource.request_access",
        "datasource",
        source_id,
        detail={"request_id": req.id, "reason_len": len(reason.strip())},
    )

    return {
        "id": req.id,
        "status": "pending",
        "idempotent": False,
        "created_at": isoformat_bjt(req.created_at),
    }


async def list_data_access_requests(
    db: AsyncSession,
    *,
    source_id: str,
    current_user_id: str,
    current_user_role: str,
    is_privileged: bool = False,
) -> list[dict]:
    """列出某数据源的申请单。owner_contact / admin 看全量；其他人仅自己的。"""
    source = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    stmt = select(DataAccessRequest).where(DataAccessRequest.source_id == source_id)
    if not is_privileged and source.owner_contact != current_user_id:
        stmt = stmt.where(DataAccessRequest.requester_id == current_user_id)
    stmt = stmt.order_by(DataAccessRequest.created_at.desc())

    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_request(r) for r in rows]


async def list_my_pending_requests(
    db: AsyncSession, *, current_user_id: str
) -> list[dict]:
    """历史命名保留；返回当前用户发起的全部申请状态。"""
    stmt = (
        select(DataAccessRequest)
        .where(DataAccessRequest.requester_id == current_user_id)
        .order_by(DataAccessRequest.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_request(r) for r in rows]


async def list_pending_requests_for_owner(
    db: AsyncSession, *, owner_user_id: str, is_privileged: bool = False
) -> list[dict]:
    """列出当前用户作为 owner_contact 需要审批的 pending 请求（admin 返回全量）。"""
    from app.auth.models import User

    stmt = (
        select(DataAccessRequest, DataSource, User.name, User.username)
        .join(DataSource, DataSource.id == DataAccessRequest.source_id)
        .outerjoin(User, User.id == DataAccessRequest.requester_id)
        .where(DataAccessRequest.status == "pending")
    )
    if not is_privileged:
        stmt = stmt.where(DataSource.owner_contact == owner_user_id)
    stmt = stmt.order_by(DataAccessRequest.created_at.desc())
    rows = (await db.execute(stmt)).all()
    return [
        {
            **_serialize_request(req),
            "source_name": source.name,
            "source_department": source.department,
            "requester_name": requester_name or requester_username or req.requester_id,
        }
        for req, source, requester_name, requester_username in rows
    ]


async def approve_data_access_request(
    db: AsyncSession,
    *,
    request_id: int,
    actor_user_id: str,
    actor_role: str,
    expires_at: datetime | None = None,
    comment: str | None = None,
) -> dict:
    """审批通过：写 DataAccessGrant + 更新 req 状态 + 通知申请人。"""
    from app.auth.models import User
    from app.dingtalk.outbox import outbox

    req = (
        await db.execute(
            select(DataAccessRequest).where(DataAccessRequest.id == request_id)
        )
    ).scalar_one_or_none()
    if not req:
        raise AppError("REQUEST_NOT_FOUND", 404)
    if req.status != "pending":
        raise AppError(
            "REQUEST_STATE_INVALID",
            409,
            detail={"current_status": req.status, "expected": "pending"},
        )

    source = (
        await db.execute(select(DataSource).where(DataSource.id == req.source_id))
    ).scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    # 权限：owner_contact 或 admin
    is_admin = actor_role in {"admin", "system_admin"}
    if source.owner_contact != actor_user_id and not is_admin:
        raise AppError(
            "PERMISSION_DENIED",
            403,
            detail={"reason": "仅数据源 owner_contact 或 admin 可审批"},
        )

    # 默认 90 天过期（审批人可覆盖；None = 永久，本轮 UI 不暴露"永久"选项）
    final_expires = _normalize_naive_bjt(expires_at)
    if final_expires is None:
        final_expires = now_bjt() + timedelta(days=DEFAULT_GRANT_TTL_DAYS)

    # 写 grant（复用 create_grant）
    grant = await create_grant(
        db,
        source_id=req.source_id,
        grantee_user_id=req.requester_id,
        permission=req.requested_permission,
        granted_by=actor_user_id,
    )
    # 手工补 expires_at（create_grant 未暴露此参数）
    await db.execute(
        DataAccessGrant.__table__.update()
        .where(DataAccessGrant.id == grant["id"])
        .values(expires_at=final_expires)
    )

    req.status = "approved"
    req.decided_by = actor_user_id
    req.decided_at = now_bjt()
    req.decision_comment = comment
    req.approved_expires_at = final_expires
    await db.flush()

    # 通知申请人
    requester = (
        await db.execute(
            select(User.dingtalk_user_id).where(User.id == req.requester_id)
        )
    ).first()
    if requester and requester[0]:
        payload = {
            "kind": "data_access_decision",
            "decision": "approved",
            "title": f"数据访问申请已通过：{source.name}",
            "message": (
                f"你对数据源 {source.name} 的访问申请已通过。"
                f"授权有效期至：{final_expires.strftime('%Y-%m-%d')}"
                + (f"\n审批意见：{comment}" if comment else "")
            ),
            "action_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/skills/hall?tab=data",
            "source_id": source.id,
            "source_name": source.name,
            "expires_at": isoformat_bjt(final_expires),
        }
        await outbox.enqueue(
            message_type="work_notice",
            recipient=requester[0],
            payload=payload,
            priority=3,
            related_type="data_access_request",
            related_id=str(req.id),
            session=db,
        )

    await audit.log(
        actor_user_id,
        "datasource.request_approve",
        "datasource",
        req.source_id,
        detail={"request_id": req.id, "grant_id": grant["id"], "expires_at": isoformat_bjt(final_expires)},
    )

    return {
        "status": "approved",
        "request_id": req.id,
        "grant_id": grant["id"],
        "expires_at": isoformat_bjt(final_expires),
    }


async def reject_data_access_request(
    db: AsyncSession,
    *,
    request_id: int,
    actor_user_id: str,
    actor_role: str,
    comment: str,
) -> dict:
    """审批驳回：更新 req 状态 + 通知申请人（必填 comment）。"""
    from app.auth.models import User
    from app.dingtalk.outbox import outbox

    if not comment or not comment.strip():
        raise AppError("COMMENT_REQUIRED", 400)

    req = (
        await db.execute(
            select(DataAccessRequest).where(DataAccessRequest.id == request_id)
        )
    ).scalar_one_or_none()
    if not req:
        raise AppError("REQUEST_NOT_FOUND", 404)
    if req.status != "pending":
        raise AppError(
            "REQUEST_STATE_INVALID",
            409,
            detail={"current_status": req.status, "expected": "pending"},
        )

    source = (
        await db.execute(select(DataSource).where(DataSource.id == req.source_id))
    ).scalar_one_or_none()
    if not source:
        raise AppError("DATASOURCE_NOT_FOUND", 404)

    is_admin = actor_role == "admin"
    if source.owner_contact != actor_user_id and not is_admin:
        raise AppError("PERMISSION_DENIED", 403)

    req.status = "rejected"
    req.decided_by = actor_user_id
    req.decided_at = now_bjt()
    req.decision_comment = comment.strip()
    await db.flush()

    # 通知申请人
    requester = (
        await db.execute(
            select(User.dingtalk_user_id).where(User.id == req.requester_id)
        )
    ).first()
    if requester and requester[0]:
        payload = {
            "kind": "data_access_decision",
            "decision": "rejected",
            "title": f"数据访问申请被驳回：{source.name}",
            "message": f"你对数据源 {source.name} 的申请被驳回。\n理由：{comment.strip()[:200]}",
            "action_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/skills/hall?tab=data",
            "source_id": source.id,
            "source_name": source.name,
        }
        await outbox.enqueue(
            message_type="work_notice",
            recipient=requester[0],
            payload=payload,
            priority=3,
            related_type="data_access_request",
            related_id=str(req.id),
            session=db,
        )

    await audit.log(
        actor_user_id,
        "datasource.request_reject",
        "datasource",
        req.source_id,
        detail={"request_id": req.id, "comment_len": len(comment.strip())},
    )

    return {"status": "rejected", "request_id": req.id}


async def expire_stale_access_requests(db: AsyncSession) -> int:
    """cron 任务：把 pending 超过 14 天的申请置 expired。返回影响行数。

    注：超时不发钉钉通知（避免给申请人二次打扰）；申请人下次打开大厅会看到状态变化。
    """
    cutoff = now_bjt() - timedelta(days=REQUEST_EXPIRE_DAYS)
    rows = (
        await db.execute(
            select(DataAccessRequest).where(
                DataAccessRequest.status == "pending",
                DataAccessRequest.created_at < cutoff,
            )
        )
    ).scalars().all()
    for req in rows:
        req.status = "expired"
        req.decided_at = now_bjt()
    await db.flush()
    return len(rows)


def _serialize_request(req: DataAccessRequest) -> dict:
    return {
        "id": req.id,
        "source_id": req.source_id,
        "requester_id": req.requester_id,
        "reason": req.reason,
        "requested_permission": req.requested_permission,
        "status": req.status,
        "decided_by": req.decided_by,
        "decided_at": isoformat_bjt(req.decided_at),
        "decision_comment": req.decision_comment,
        "approved_expires_at": (
            isoformat_bjt(req.approved_expires_at)
        ),
        "created_at": isoformat_bjt(req.created_at),
        "updated_at": isoformat_bjt(req.updated_at),
    }


def _serialize_grant(grant: DataAccessGrant) -> dict:
    return {
        "id": grant.id,
        "source_id": grant.source_id,
        "grantee_user_id": grant.grantee_user_id,
        "grantee_org_unit_id": grant.grantee_org_unit_id,
        "grantee_type": grant.grantee_type,
        "grantee_id": grant.grantee_id,
        "permission": grant.permission,
        "granted_by": grant.granted_by,
        "created_at": isoformat_bjt(grant.created_at),
        "expires_at": isoformat_bjt(grant.expires_at),
    }
