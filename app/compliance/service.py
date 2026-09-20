"""
合规规则管理服务：规则CRUD + 内容合规检查 + 批量导入导出。
"""

import csv
import io
import re
from datetime import date

from loguru import logger
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.common.models import ComplianceRule, ComplianceRuleVersion


async def list_rules(
    db: AsyncSession,
    platform: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """列出合规规则（分页+筛选）"""
    conditions = []
    if platform:
        conditions.append(ComplianceRule.platform == platform)
    if category:
        conditions.append(ComplianceRule.category_scope.ilike(f"%{category}%"))

    where_clause = and_(*conditions) if conditions else True

    # 查询总数
    count_stmt = select(func.count(ComplianceRule.id)).where(where_clause)
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页查询
    offset = (page - 1) * page_size
    stmt = (
        select(ComplianceRule)
        .where(where_clause)
        .order_by(ComplianceRule.severity, ComplianceRule.id)
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rules = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_rule_to_dict(r) for r in rules],
    }


async def get_rule(db: AsyncSession, rule_id: str) -> dict:
    """获取规则详情"""
    result = await db.execute(
        select(ComplianceRule).where(ComplianceRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise AppError("COMPLIANCE_RULE_NOT_FOUND", 404)
    return _rule_to_dict(rule)


async def _get_rule_for_update(db: AsyncSession, rule_id: str) -> ComplianceRule | None:
    result = await db.execute(
        select(ComplianceRule)
        .where(ComplianceRule.id == rule_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _next_rule_version_no(db: AsyncSession, rule_id: str) -> int:
    current_max = (
        await db.execute(
            select(func.coalesce(func.max(ComplianceRuleVersion.version_no), 0)).where(
                ComplianceRuleVersion.rule_id == rule_id
            )
        )
    ).scalar_one()
    return int(current_max or 0) + 1


async def create_rule(
    db: AsyncSession,
    rule_id: str,
    platform: str,
    surface: str,
    category_scope: str | None,
    trigger_type: str,
    pattern_value: str,
    severity: str,
    decision: str,
    rewrite_suggestion: str | None = None,
    required_evidence: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    source_url: str | None = None,
    owner: str | None = None,
    user_id: str = "system",
) -> dict:
    """创建合规规则"""
    # 检查ID是否已存在
    existing = await db.execute(
        select(ComplianceRule).where(ComplianceRule.id == rule_id)
    )
    if existing.scalar_one_or_none():
        raise AppError("COMPLIANCE_RULE_EXISTS", 409)

    # 如果是regex类型，验证正则表达式合法性
    if trigger_type == "regex":
        try:
            re.compile(pattern_value)
        except re.error as e:
            raise AppError("COMPLIANCE_INVALID_REGEX", 400,
                           detail={"pattern": pattern_value, "error": str(e)})

    rule = ComplianceRule(
        id=rule_id,
        platform=platform,
        surface=surface,
        category_scope=category_scope,
        trigger_type=trigger_type,
        pattern_value=pattern_value,
        severity=severity,
        decision=decision,
        rewrite_suggestion=rewrite_suggestion,
        required_evidence=required_evidence,
        effective_from=effective_from,
        effective_to=effective_to,
        source_url=source_url,
        owner=owner,
    )
    db.add(rule)
    await db.flush()

    await audit.log(user_id, "compliance.create", "compliance_rule", rule_id)
    return _rule_to_dict(rule)


async def update_rule(
    db: AsyncSession,
    rule_id: str,
    user_id: str = "system",
    **fields,
) -> dict:
    """更新合规规则"""
    change_reason = fields.pop("_change_reason", None)
    rule = await _get_rule_for_update(db, rule_id)
    if not rule:
        raise AppError("COMPLIANCE_RULE_NOT_FOUND", 404)

    # 允许更新的字段白名单
    allowed_fields = {
        "platform", "surface", "category_scope", "trigger_type",
        "pattern_value", "severity", "decision", "rewrite_suggestion",
        "required_evidence", "effective_from", "effective_to",
        "source_url", "owner",
    }

    # 更新前先拍 snapshot（用于回滚 / 历史展示）
    snapshot_before = _rule_to_dict(rule)

    updated = []
    for key, value in fields.items():
        if key in allowed_fields and value is not None:
            # 如果更新了trigger_type或pattern_value，验证正则
            if key == "pattern_value" or key == "trigger_type":
                trigger = fields.get("trigger_type", rule.trigger_type)
                pattern = fields.get("pattern_value", rule.pattern_value)
                if trigger == "regex":
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        raise AppError("COMPLIANCE_INVALID_REGEX", 400,
                                       detail={"pattern": pattern, "error": str(e)})
            setattr(rule, key, value)
            updated.append(key)

    await db.flush()

    # 锁住规则行后用 max(version_no)+1 生成新版本号，避免并发 update/rollback 分配重复号。
    if updated:
        version = ComplianceRuleVersion(
            rule_id=rule_id,
            version_no=await _next_rule_version_no(db, rule_id),
            snapshot=snapshot_before,
            author=user_id,
            reason=change_reason,
        )
        db.add(version)
        await db.flush()

    await audit.log(user_id, "compliance.update", "compliance_rule", rule_id,
                    detail={"updated_fields": updated})
    return _rule_to_dict(rule)


async def list_rule_versions(db: AsyncSession, rule_id: str) -> dict:
    """列出某规则的历史版本，按时间倒序"""
    result = await db.execute(
        select(ComplianceRuleVersion)
        .where(ComplianceRuleVersion.rule_id == rule_id)
        .order_by(ComplianceRuleVersion.created_at.desc())
    )
    versions = result.scalars().all()
    return {
        "items": [
            {
                "id": v.id,
                "rule_id": v.rule_id,
                "version_no": v.version_no,
                "snapshot": v.snapshot,
                "author": v.author,
                "reason": v.reason,
                "created_at": isoformat_bjt(v.created_at),
            }
            for v in versions
        ]
    }


async def rollback_rule(db: AsyncSession, rule_id: str, version_no: int, user_id: str = "system") -> dict:
    """把规则回滚到指定历史版本（会产生一条新的 version_no）"""
    result = await db.execute(
        select(ComplianceRuleVersion)
        .where(ComplianceRuleVersion.rule_id == rule_id)
        .where(ComplianceRuleVersion.version_no == version_no)
    )
    version = result.scalar_one_or_none()
    if not version:
        raise AppError("COMPLIANCE_VERSION_NOT_FOUND", 404)

    snapshot = version.snapshot or {}
    # 用 snapshot 调用 update_rule，会自动产生一个新版本
    return await update_rule(
        db,
        rule_id=rule_id,
        user_id=user_id,
        **{k: v for k, v in snapshot.items() if k not in ("id", "created_at", "updated_at")},
        _change_reason=f"rollback to v{version_no}",
    )


async def delete_rule(db: AsyncSession, rule_id: str, user_id: str = "system") -> dict:
    """删除合规规则"""
    result = await db.execute(
        select(ComplianceRule).where(ComplianceRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise AppError("COMPLIANCE_RULE_NOT_FOUND", 404)

    await db.delete(rule)
    await db.flush()

    await audit.log(user_id, "compliance.delete", "compliance_rule", rule_id)
    return {"deleted": rule_id}


async def check_content(
    db: AsyncSession,
    content: str,
    platform: str | None = None,
) -> list[dict]:
    """
    检查内容是否违反合规规则。
    对每条激活规则检查关键词或正则匹配，返回所有违反的规则列表。
    """
    today = date.today()

    # 查询激活的规则（在有效期内或无有效期限制）
    conditions = []
    if platform:
        # 匹配指定平台或 "all"（全平台通用规则）
        conditions.append(
            ComplianceRule.platform.in_([platform, "all"])
        )

    where_clause = and_(*conditions) if conditions else True
    result = await db.execute(
        select(ComplianceRule).where(where_clause).order_by(ComplianceRule.severity)
    )
    rules = result.scalars().all()

    violations = []
    for rule in rules:
        # 检查有效期
        if rule.effective_from and today < rule.effective_from:
            continue
        if rule.effective_to and today > rule.effective_to:
            continue

        matched = False

        if rule.trigger_type == "keyword":
            # 关键词匹配（不区分大小写）
            if rule.pattern_value.lower() in content.lower():
                matched = True
        elif rule.trigger_type == "regex":
            # 正则匹配
            try:
                if re.search(rule.pattern_value, content, re.IGNORECASE):
                    matched = True
            except re.error:
                # 跳过无效正则表达式
                logger.warning(f"合规规则 {rule.id} 正则表达式无效: {rule.pattern_value}")
                continue
        elif rule.trigger_type == "image_label":
            # image_label 需要图像识别服务，文本检查跳过（Phase 4 接入多模态 LLM）
            continue

        if matched:
            violations.append(_rule_to_dict(rule))

    return violations


async def import_from_csv(
    db: AsyncSession, file_content: bytes, user_id: str = "system"
) -> dict:
    """
    批量导入合规规则（CSV/Excel格式）。

    支持的列名: id, platform, surface, category_scope, trigger_type,
               pattern_value, severity, decision, rewrite_suggestion,
               required_evidence, effective_from, effective_to, source_url, owner

    1. 解析CSV（支持UTF-8 BOM编码）
    2. 校验每行必填字段
    3. 跳过重复项（相同platform+pattern_value）
    4. 插入新规则
    5. 返回 {imported: N, skipped: N, errors: [...]}
    """
    # 必填列（id可以自动生成，所以不强制）
    REQUIRED_FIELDS = {"platform", "surface", "trigger_type", "pattern_value",
                       "severity", "decision"}
    # 合法值校验
    VALID_PLATFORMS = {"tmall", "jd", "douyin", "pdd", "meituan", "eleme", "all"}
    VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}
    VALID_DECISIONS = {"block", "rewrite", "escalate", "pass_with_log"}
    VALID_TRIGGER_TYPES = {"keyword", "regex", "image_label"}

    # 解码文件内容，处理UTF-8 BOM
    try:
        text = file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_content.decode("gbk")
        except UnicodeDecodeError:
            raise AppError("COMPLIANCE_IMPORT_ENCODING_ERROR", 400)

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise AppError("COMPLIANCE_IMPORT_EMPTY_FILE", 400)

    # 列名映射：支持中英文和带/不带前缀的列名
    COLUMN_ALIAS = {
        # 英文别名 → 标准字段名
        "category": "category_scope",
        "pattern": "pattern_value",
        "trigger": "trigger_type",
        "rewrite": "rewrite_suggestion",
        "evidence": "required_evidence",
        "source": "source_url",
        # 中文别名
        "平台": "platform",
        "检查面": "surface",
        "类别": "category_scope",
        "类别范围": "category_scope",
        "匹配方式": "trigger_type",
        "关键词": "pattern_value",
        "模式": "pattern_value",
        "严重程度": "severity",
        "处置": "decision",
        "处置方式": "decision",
        "改写建议": "rewrite_suggestion",
        "来源": "source_url",
        "负责人": "owner",
        "描述": "rewrite_suggestion",
        "规则ID": "id",
    }

    def _normalize_column(col: str) -> str:
        """列名归一化：去空格，查别名映射"""
        col = col.strip()
        return COLUMN_ALIAS.get(col, col)

    # 预查询所有已有规则的 (platform, pattern_value) 组合用于去重
    existing_result = await db.execute(
        select(ComplianceRule.platform, ComplianceRule.pattern_value)
    )
    existing_keys = {
        (row.platform, row.pattern_value) for row in existing_result.all()
    }
    # 也收集已有的ID
    id_result = await db.execute(select(ComplianceRule.id))
    existing_ids = {row[0] for row in id_result.all()}

    imported = 0
    skipped = 0
    errors = []
    auto_id_counter = 1

    for row_num, raw_row in enumerate(reader, start=2):
        # 归一化列名
        row = {_normalize_column(k): v.strip() if v else ""
               for k, v in raw_row.items() if k}

        # 检查必填字段
        missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
        if missing:
            errors.append(f"第{row_num}行: 缺少必填字段 {', '.join(missing)}")
            continue

        platform = row["platform"].lower()
        surface = row["surface"].lower()
        trigger_type = row["trigger_type"].lower()
        pattern_value = row["pattern_value"]
        severity = row["severity"].upper()
        decision = row["decision"].lower()

        # 值域校验
        row_errors = []
        if platform not in VALID_PLATFORMS:
            row_errors.append(f"platform '{platform}' 不合法")
        if severity not in VALID_SEVERITIES:
            row_errors.append(f"severity '{severity}' 不合法")
        if decision not in VALID_DECISIONS:
            row_errors.append(f"decision '{decision}' 不合法")
        if trigger_type not in VALID_TRIGGER_TYPES:
            row_errors.append(f"trigger_type '{trigger_type}' 不合法")

        # 正则表达式合法性校验
        if trigger_type == "regex":
            try:
                re.compile(pattern_value)
            except re.error as e:
                row_errors.append(f"正则表达式无效: {e}")

        if row_errors:
            errors.append(f"第{row_num}行: {'; '.join(row_errors)}")
            continue

        # 跳过重复项（相同 platform + pattern_value）
        key = (platform, pattern_value)
        if key in existing_keys:
            skipped += 1
            continue

        # 生成或使用提供的ID
        rule_id = row.get("id", "").strip()
        if not rule_id:
            # 自动生成ID
            while f"IMP-{auto_id_counter:04d}" in existing_ids:
                auto_id_counter += 1
            rule_id = f"IMP-{auto_id_counter:04d}"
            auto_id_counter += 1
        elif rule_id in existing_ids:
            skipped += 1
            continue

        # 解析日期字段
        effective_from = _parse_date(row.get("effective_from", ""))
        effective_to = _parse_date(row.get("effective_to", ""))

        rule = ComplianceRule(
            id=rule_id,
            platform=platform,
            surface=surface,
            category_scope=row.get("category_scope") or None,
            trigger_type=trigger_type,
            pattern_value=pattern_value,
            severity=severity,
            decision=decision,
            rewrite_suggestion=row.get("rewrite_suggestion") or None,
            required_evidence=row.get("required_evidence") or None,
            effective_from=effective_from,
            effective_to=effective_to,
            source_url=row.get("source_url") or None,
            owner=row.get("owner") or None,
        )
        db.add(rule)
        existing_keys.add(key)
        existing_ids.add(rule_id)
        imported += 1

    await db.flush()
    await audit.log(
        user_id, "compliance.import", "compliance_rule", None,
        detail={"imported": imported, "skipped": skipped, "error_count": len(errors)},
    )

    logger.info(f"合规规则导入完成: 导入{imported}条, 跳过{skipped}条, 错误{len(errors)}条")
    return {"imported": imported, "skipped": skipped, "errors": errors}


async def export_csv(
    db: AsyncSession, platform: str | None = None
) -> str:
    """
    导出合规规则为CSV字符串。

    1. 查询规则（可按平台筛选）
    2. 输出含BOM头的CSV（兼容Excel直接打开）
    3. 返回CSV字符串
    """
    conditions = []
    if platform:
        conditions.append(ComplianceRule.platform == platform)
    where_clause = and_(*conditions) if conditions else True

    result = await db.execute(
        select(ComplianceRule)
        .where(where_clause)
        .order_by(ComplianceRule.severity, ComplianceRule.id)
    )
    rules = result.scalars().all()

    # CSV列定义
    fieldnames = [
        "id", "platform", "surface", "category_scope", "trigger_type",
        "pattern_value", "severity", "decision", "rewrite_suggestion",
        "required_evidence", "effective_from", "effective_to",
        "source_url", "owner",
    ]

    output = io.StringIO()
    # 写入UTF-8 BOM，让Excel自动识别编码
    output.write("\ufeff")

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for rule in rules:
        writer.writerow({
            "id": rule.id,
            "platform": rule.platform,
            "surface": rule.surface,
            "category_scope": rule.category_scope or "",
            "trigger_type": rule.trigger_type,
            "pattern_value": rule.pattern_value,
            "severity": rule.severity,
            "decision": rule.decision,
            "rewrite_suggestion": rule.rewrite_suggestion or "",
            "required_evidence": rule.required_evidence or "",
            "effective_from": rule.effective_from.isoformat() if rule.effective_from else "",
            "effective_to": rule.effective_to.isoformat() if rule.effective_to else "",
            "source_url": rule.source_url or "",
            "owner": rule.owner or "",
        })

    return output.getvalue()


def _parse_date(value: str) -> date | None:
    """解析日期字符串，支持 YYYY-MM-DD 格式，无效则返回 None"""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _rule_to_dict(rule: ComplianceRule) -> dict:
    """将ORM对象转为字典"""
    return {
        "id": rule.id,
        "platform": rule.platform,
        "surface": rule.surface,
        "category_scope": rule.category_scope,
        "trigger_type": rule.trigger_type,
        "pattern_value": rule.pattern_value,
        "severity": rule.severity,
        "decision": rule.decision,
        "rewrite_suggestion": rule.rewrite_suggestion,
        "required_evidence": rule.required_evidence,
        "effective_from": rule.effective_from.isoformat() if rule.effective_from else None,
        "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
        "source_url": rule.source_url,
        "owner": rule.owner,
        "created_at": isoformat_bjt(rule.created_at),
        "updated_at": isoformat_bjt(rule.updated_at),
    }
