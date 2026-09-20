"""
Skill管理服务：核心CRUD + 文件操作 + 数据库同步。
业务逻辑在这里，router.py只做路由分发。

拆分说明：
- 影子运行相关 → shadow_service.py
- 校验/参数对比 → validation_service.py
- AI周报生成   → generation_service.py
"""

import asyncio
import json
import re as _re
from datetime import datetime

import yaml
from loguru import logger


def _ensure_skillforge_frontmatter(
    skill_md: str,
    *,
    trigger_type: str,
    risk_level: str,
) -> str:
    """如果 SKILL.md frontmatter 里缺 trigger_type / risk_level, 用参数回填。

    LLM 生成的 SKILL.md 经常漏填 SkillForge 扩展字段 (它只认 Anthropic 标准
    name/description)。走 quick_validate 前兜底一次, 避免 422 把用户打回去。
    """
    if not skill_md:
        return skill_md
    m = _re.match(r"^---\r?\n(.*?)\r?\n---", skill_md, _re.DOTALL)
    if not m:
        # 没有 frontmatter - 让 quick_validate 自己报错
        return skill_md
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return skill_md
    if not isinstance(fm, dict):
        return skill_md

    changed = False
    if not fm.get("trigger_type") and trigger_type:
        fm["trigger_type"] = trigger_type
        changed = True
    if not fm.get("risk_level") and risk_level:
        fm["risk_level"] = risk_level
        changed = True
    if not changed:
        return skill_md

    new_fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{new_fm_yaml}---{skill_md[m.end():]}"
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.skills.core.git_service import git_service
from app.skills.tooling.manifest_service import build_skill_manifest_from_dir
from app.skills.core.models import Skill
from app.skills.core.parser import (
    Antipattern,
    Branch,
    DataInput,
    DecisionStep,
    OutputItem,
    SkillStructured,
    TestCase,
    skill_parser,
)
from app.skills.service_files import (
    acquire_lock,
    create_file,
    delete_file,
    get_lock_status,
    list_scripts,
    release_lock,
    rename_file,
    run_script,
    save_file,
)
from app.skills.lifecycle.service_runtime import (
    deprecate_skill,
    delete_skill,
    get_skill_diff,
    get_skill_history,
    rollback_skill,
    update_params,
)
from app.skills.tooling.service_quality import (
    get_cross_skill_conflicts_scoped,
    get_param_usages_grouped,
    get_skill_conflicts_with_access,
    save_skill_structured,
)
from app.skills.core.service_shared import parse_approval_level as _parse_approval_level, sync_skill_fields_from_frontmatter as _sync_skill_fields_from_frontmatter, validate_skill_id


def _latest_skill_git_commit(skill_id: str) -> dict | None:
    """Return the latest Git commit that touched this skill directory.

    The database stores the commit produced by SkillForge save flows, but AI
    workers or maintenance scripts can also commit directly to skills-repo.
    Page metadata should reflect the repository truth even when the DB pointer
    is stale.
    """
    try:
        logs = git_service.log(skill_id=skill_id, max_count=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 skill git head 失败 skill={}: {}", skill_id, exc)
        return None
    return logs[0] if logs else None


def _effective_skill_git_commit(skill: Skill) -> tuple[str | None, dict | None, bool]:
    latest = _latest_skill_git_commit(skill.id)
    latest_full = latest.get("hash_full") if latest else None
    effective = latest_full or skill.git_commit
    synced = bool(effective and skill.git_commit and effective == skill.git_commit)
    return effective, latest, synced


def _load_creation_contract(files: dict[str, str]) -> dict:
    """从创建输入中提取 contract/task-contract。

    允许只传其中一种；若存在但不是合法 JSON / 对象，直接阻断创建。
    """
    for key in ("contract.json", "task-contract.json"):
        raw = (files or {}).get(key)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError(
                "SKILL_VALIDATION_FAILED",
                422,
                {
                    "detail": f"{key} 不是合法 JSON",
                    "errors": [f"{key}: {exc.msg}"],
                },
            ) from exc
        if not isinstance(parsed, dict):
            raise AppError(
                "SKILL_VALIDATION_FAILED",
                422,
                {
                    "detail": f"{key} 必须是 JSON 对象",
                    "errors": [f"{key} 不是 JSON object"],
                },
            )
        return parsed
    return {}


async def _preflight_creation_files(
    *,
    skill_id: str,
    files: dict[str, str],
    contract: dict,
) -> tuple[dict[str, str], list[str]]:
    """创建前门禁：静态 lint + 轻量预演。

    返回归一化后的 files 和错误列表；调用方在写盘/落库前统一拦截。
    """
    from app.common.contract_schema import (
        build_verified_preview,
        get_preview_input,
        lint_placeholder_implementation,
        lint_runtime_data_acquisition,
        normalize_skill_bundle_files,
    )

    normalized_files = normalize_skill_bundle_files(files, contract)
    main_py_text = normalized_files.get("scripts/main.py") or ""
    skill_md_text = normalized_files.get("SKILL.md") or ""

    errors: list[str] = []
    errors.extend(lint_placeholder_implementation(main_py_text))
    errors.extend(
        lint_runtime_data_acquisition(
            contract,
            main_py_text,
            skill_md_text=skill_md_text,
        )
    )
    if errors:
        return normalized_files, errors

    preview_files = dict(normalized_files)
    preview_input = get_preview_input(contract, normalized_files)
    has_sample_input = bool(preview_files.get("fixtures/sample_input.json")) or bool(
        contract.get("sample_input")
    ) or bool(contract.get("fixtures"))
    if has_sample_input and isinstance(preview_input, dict):
        preview_files.setdefault(
            "fixtures/sample_input.json",
            json.dumps(preview_input, ensure_ascii=False),
        )
        preview = await build_verified_preview(
            contract,
            preview_files,
            cache_key=f"create_skill_from_files:{skill_id}",
        )
        if preview.get("success") is False:
            preview_errors = preview.get("schema_errors") or []
            if isinstance(preview_errors, list):
                errors.extend(str(item) for item in preview_errors if item)
            elif preview_errors:
                errors.append(str(preview_errors))
            sandbox_summary = preview.get("sandbox_summary")
            if sandbox_summary and not preview_errors:
                errors.append(str(sandbox_summary))

    return normalized_files, errors


async def list_skills(
    db: AsyncSession,
    current_user=None,
    department: str | None = None,
    status: str | None = None,
    include_deprecated: bool = False,
    q: str | None = None,
    risk_level: str | None = None,
    trigger_type: str | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 50,
    include_health: bool = False,
    view_filter: str | None = None,
) -> dict:
    """分页查询 Skill 列表，支持搜索/筛选/排序/状态计数

    include_health=True 时为每条记录附 health_score (0-100, 1 位小数)；
    view_filter 可把左侧"我创建 / 我负责 / 收藏 / 不健康"做成服务端分页筛选。
    """
    from sqlalchemy import func, or_
    from app.skills.core.access import SkillMember, build_skill_access_filter, get_skill_permissions
    from app.skills.core.models import UserSkillPin

    # 基础条件（不含 status，因为 status_counts 需要全量统计）
    base_conditions = []
    if current_user is not None:
        base_conditions.append(await build_skill_access_filter(db, current_user, "read"))
    if department:
        base_conditions.append(Skill.department == department)
    if risk_level:
        base_conditions.append(Skill.risk_level == risk_level)
    if trigger_type:
        base_conditions.append(Skill.trigger_type == trigger_type)
    if q:
        search = f"%{q}%"
        base_conditions.append(or_(Skill.id.ilike(search), Skill.name.ilike(search)))
    normalized_view_filter = view_filter if view_filter in {
        "mine_created",
        "mine_owned",
        "favorited",
        "unhealthy",
    } else None
    user_id = str(getattr(current_user, "id", "") or "")

    # 状态计数（基于 base_conditions，不含 status 筛选）
    count_stmt = select(Skill.status, func.count(Skill.id)).group_by(Skill.status)
    for cond in base_conditions:
        count_stmt = count_stmt.where(cond)
    count_result = await db.execute(count_stmt)
    counts = {row[0]: row[1] for row in count_result}
    visible_all_count = sum(
        count for item_status, count in counts.items()
        if include_deprecated or item_status != "deprecated"
    )
    status_counts = {
        "all": visible_all_count,
        "active": counts.get("active", 0),
        "shadow": counts.get("shadow", 0),
        "draft": counts.get("draft", 0),
        "deprecated": counts.get("deprecated", 0),
    }

    # 列表查询（含 status 筛选）
    all_conditions = list(base_conditions)
    normalized_status = status if status and status != "all" else None
    if normalized_status:
        all_conditions.append(Skill.status == normalized_status)
    elif not include_deprecated:
        all_conditions.append(Skill.status != "deprecated")
    view_base_conditions = list(all_conditions)
    view_counts: dict[str, int | None] = {
        "mine_created": None,
        "mine_owned": None,
        "favorited": None,
        "unhealthy": None,
    }
    if user_id:
        owner_member_skill_ids = (
            select(SkillMember.skill_id)
            .where(SkillMember.user_id == user_id)
            .where(SkillMember.role == "owner")
        )
        favorited_skill_ids = (
            select(UserSkillPin.skill_id)
            .where(UserSkillPin.user_id == user_id)
        )
        if normalized_view_filter == "mine_created":
            all_conditions.append(Skill.owner == user_id)
        elif normalized_view_filter == "mine_owned":
            all_conditions.append(or_(Skill.owner == user_id, Skill.id.in_(owner_member_skill_ids)))
        elif normalized_view_filter == "favorited":
            all_conditions.append(Skill.id.in_(favorited_skill_ids))
        view_count_filters = {
            "mine_created": Skill.owner == user_id,
            "mine_owned": or_(Skill.owner == user_id, Skill.id.in_(owner_member_skill_ids)),
            "favorited": Skill.id.in_(favorited_skill_ids),
        }
        for key, cond in view_count_filters.items():
            stmt_count = select(func.count(Skill.id))
            for base_cond in view_base_conditions:
                stmt_count = stmt_count.where(base_cond)
            view_counts[key] = int((await db.execute(stmt_count.where(cond))).scalar() or 0)

    stmt = select(Skill)
    total_stmt = select(func.count(Skill.id))
    for cond in all_conditions:
        stmt = stmt.where(cond)
        total_stmt = total_stmt.where(cond)

    # 排序
    allowed_sorts = {"updated_at", "name", "status", "risk_level", "department", "id", "created_at"}
    col = getattr(Skill, sort_by if sort_by in allowed_sorts else "updated_at")
    stmt = stmt.order_by(col.asc() if sort_order == "asc" else col.desc())

    health_map: dict[str, float | None] = {}
    if normalized_view_filter == "unhealthy":
        candidate_result = await db.execute(stmt)
        candidate_skills = candidate_result.scalars().all()
        health_map = await _collect_health_scores(db, candidate_skills)
        unhealthy_skills = [
            skill for skill in candidate_skills
            if isinstance(health_map.get(skill.id), (int, float)) and float(health_map[skill.id]) < 70
        ]
        view_counts["unhealthy"] = len(unhealthy_skills)
        total = len(unhealthy_skills)
        start = (page - 1) * page_size
        skills = unhealthy_skills[start:start + page_size]
    else:
        if include_health:
            health_candidate_stmt = select(Skill)
            for cond in view_base_conditions:
                health_candidate_stmt = health_candidate_stmt.where(cond)
            health_candidates = (await db.execute(health_candidate_stmt)).scalars().all()
            health_count_map = await _collect_health_scores(db, health_candidates)
            view_counts["unhealthy"] = sum(
                1 for score in health_count_map.values()
                if isinstance(score, (int, float)) and float(score) < 70
            )
        # 分页
        total = (await db.execute(total_stmt)).scalar() or 0
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(stmt)
        skills = result.scalars().all()

    # 批量聚合：owner_name / last_run_at / usage_count
    # （用实时聚合，不依赖 Skill 表上可能过时的 last_run_at / usage_count 持久列）
    owner_name_map, run_stats_map = await _collect_skill_list_extras(db, skills)

    if include_health and skills:
        missing_health_skills = [skill for skill in skills if skill.id not in health_map]
        health_map.update(await _collect_health_scores(db, missing_health_skills))

    items = []
    for s in skills:
        effective_commit, latest_commit, git_commit_synced = _effective_skill_git_commit(s)
        stats = run_stats_map.get(s.id) or {}
        permissions = (
            await get_skill_permissions(db, s, current_user)
            if current_user is not None else None
        )
        item = {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "department": s.department,
            "role": s.role,
            "status": s.status,
            "trigger_type": s.trigger_type,
            "risk_level": s.risk_level,
            "owner": s.owner,
            "owner_name": owner_name_map.get(s.owner) if s.owner else None,
            "current_version": s.current_version,
            "git_commit": effective_commit[:8] if effective_commit else None,
            "git_commit_full": effective_commit,
            "db_git_commit": s.git_commit,
            "git_head_commit": latest_commit,
            "git_commit_synced": git_commit_synced,
            "visibility": s.visibility,
            "permissions": permissions,
            "updated_at": isoformat_bjt(s.updated_at),
            "last_run_at": stats.get("last_run_at"),
            "usage_count": stats.get("usage_count", 0),
            "usage_today": stats.get("usage_today", 0),
            "usage_trend": stats.get("usage_trend", []),
        }
        if include_health:
            item["health_score"] = health_map.get(s.id)
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_counts": status_counts,
        "view_counts": view_counts,
    }


async def _collect_health_scores(
    db: AsyncSession,
    skills: list[Skill],
) -> dict[str, float | None]:
    """批量计算 health_score；串行使用同一个 AsyncSession，避免并发会话访问。"""
    if not skills:
        return {}
    from app.dashboard.service import get_skill_health_score

    scores: dict[str, float | None] = {}
    for skill in skills:
        try:
            payload = await get_skill_health_score(db, skill.id)
            raw_score = payload.get("score")
            scores[skill.id] = float(raw_score) if raw_score is not None else None
        except Exception:
            scores[skill.id] = None
    return scores


async def _collect_skill_list_extras(
    db: AsyncSession,
    skills: list[Skill],
) -> tuple[dict[str, str], dict[str, dict]]:
    """为 list_skills 批量聚合 owner_name / 执行统计。

    返回：
        owner_name_map: { user_id -> user.name }
        run_stats_map:  { skill_id -> {
            "last_run_at": ISO str | None,
            "usage_count": int,
            "usage_today": int,
            "usage_trend": [{"date": "YYYY-MM-DD", "count": int}, ...],
        } }

    设计：
    - 单次 IN 批量查 User.name，避免 N+1
    - 单次聚合查 ExecutionRun(COUNT, MAX(started_at))，避免 N+1
    - skills 列表为空时直接返回空 dict，避免空 IN 查询
    """
    from datetime import timedelta

    from sqlalchemy import func

    from app.auth.models import User
    from app.execution.models import ExecutionRun
    from app.common.time_utils import now_bjt

    owner_name_map: dict[str, str] = {}
    run_stats_map: dict[str, dict] = {}

    if not skills:
        return owner_name_map, run_stats_map

    # --- owner_name: batch fetch by owner IDs ---
    owner_ids = {s.owner for s in skills if s.owner}
    if owner_ids:
        name_rows = (await db.execute(
            select(User.id, User.name).where(User.id.in_(owner_ids))
        )).all()
        owner_name_map = {row.id: row.name for row in name_rows}

    # --- execution stats: COUNT(*) + MAX(started_at) grouped by skill_id ---
    skill_ids = [s.id for s in skills]
    stats_rows = (await db.execute(
        select(
            ExecutionRun.skill_id,
            func.count().label("usage_count"),
            func.max(ExecutionRun.started_at).label("last_run_at"),
        )
        .where(ExecutionRun.skill_id.in_(skill_ids))
        .group_by(ExecutionRun.skill_id)
    )).all()
    for row in stats_rows:
        run_stats_map[row.skill_id] = {
            "usage_count": int(row.usage_count or 0),
            "last_run_at": isoformat_bjt(row.last_run_at),
        }

    # --- 7-day usage trend: fixed window from oldest -> newest, zero-filled ---
    today = now_bjt().date()
    trend_days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    trend_start = datetime.combine(trend_days[0], datetime.min.time())
    trend_end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    day_expr = func.date(ExecutionRun.started_at)
    trend_rows = (await db.execute(
        select(
            ExecutionRun.skill_id,
            day_expr.label("day"),
            func.count().label("run_count"),
        )
        .where(ExecutionRun.skill_id.in_(skill_ids))
        .where(ExecutionRun.started_at >= trend_start)
        .where(ExecutionRun.started_at < trend_end)
        .group_by(ExecutionRun.skill_id, day_expr)
    )).all()
    trend_counts: dict[str, dict[str, int]] = {}
    for row in trend_rows:
        day = str(row.day)
        trend_counts.setdefault(row.skill_id, {})[day] = int(row.run_count or 0)
    for skill_id in skill_ids:
        stats = run_stats_map.setdefault(skill_id, {"usage_count": 0, "last_run_at": None})
        day_counts = trend_counts.get(skill_id, {})
        stats["usage_today"] = day_counts.get(today.isoformat(), 0)
        stats["usage_trend"] = [
            {"date": day.isoformat(), "count": day_counts.get(day.isoformat(), 0)}
            for day in trend_days
        ]

    return owner_name_map, run_stats_map


async def get_skill(db: AsyncSession, skill_id: str) -> dict:
    """获取Skill详情：数据库元数据 + Git文件内容 + 结构化解析"""
    validate_skill_id(skill_id)

    # 数据库元数据
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    effective_commit, latest_commit, git_commit_synced = _effective_skill_git_commit(skill)

    # 从Git读取文件内容
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    policy_pack_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""

    # 列出并读取所有文件
    # 容错读 (errors='replace'): skill 目录可能含 PNG / 字体 / PDF 等二进制,
    # strict 模式会抛 UnicodeDecodeError → 整个 GET /api/skills/{id} 500.
    # 二进制文件本来就不该 text 展示, replace 模式让 API 不崩, 前端拿到的是
    # 含 replacement char 的字符串, 二进制文件依然不可用但其他文件正常返回。
    all_file_paths = git_service.list_skill_files(skill_id)
    scripts = {}
    references = {}
    other_files = {}
    for fp in all_file_paths:
        if fp in ("SKILL.md", "policy_pack.yaml"):
            continue
        try:
            content = git_service.read_file(skill_id, fp, errors="replace") or ""
        except Exception as e:
            # 极端兜底 (read_text I/O 错误等), 不影响其他文件返回
            logger.warning(f"读取 skill 文件失败 skill={skill_id} path={fp}: {e}")
            content = ""
        if fp.startswith("scripts/"):
            scripts[fp] = content
        elif fp.startswith("references/"):
            references[fp] = content
        else:
            other_files[fp] = content

    # 解析SKILL.md
    parsed = skill_parser.parse(skill_md) if skill_md else None

    # 解析policy_pack.yaml
    try:
        policy_pack = yaml.safe_load(policy_pack_raw) or {}
    except yaml.YAMLError:
        policy_pack = {}

    try:
        task_contract = json.loads(other_files["task-contract.json"]) if other_files.get("task-contract.json") else None
    except (json.JSONDecodeError, TypeError):
        task_contract = None
    try:
        task_review_state = json.loads(other_files["task-review-state.json"]) if other_files.get("task-review-state.json") else None
    except (json.JSONDecodeError, TypeError):
        task_review_state = None

    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "department": skill.department,
        "role": skill.role,
        "status": skill.status,
        "trigger_type": skill.trigger_type,
        "trigger_expression": skill.trigger_expression,
        "risk_level": skill.risk_level,
        "approval_level": skill.approval_level,
        "visibility": skill.visibility,
        "owner": skill.owner,
        "current_version": skill.current_version,
        "git_commit": effective_commit,
        "git_commit_full": effective_commit,
        "db_git_commit": skill.git_commit,
        "git_head_commit": latest_commit,
        "git_commit_synced": git_commit_synced,
        "forked_from": skill.forked_from,
        "fork_type": skill.fork_type,
        "parent_version": skill.parent_version,
        "created_at": isoformat_bjt(skill.created_at),
        "updated_at": isoformat_bjt(skill.updated_at),
        # 文件内容
        "skill_md": skill_md,
        "policy_pack": policy_pack,
        "policy_pack_raw": policy_pack_raw,
        "script_content": scripts.get("scripts/main.py", next(iter(scripts.values()), "")),
        "scripts": scripts,
        "references": references,
        "other_files": other_files,
        "manifest": build_skill_manifest_from_dir(git_service.skill_dir(skill_id), skill_id=skill_id),
        "task_contract": task_contract,
        "task_review_state": task_review_state,
        "all_files": all_file_paths,
        "file_tree": git_service.list_skill_tree(skill_id),
        # 结构化解析
        "parsed": {
            "frontmatter": parsed.frontmatter if parsed else {},
            "purpose": parsed.purpose if parsed else "",
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "branches": [
                        {"condition": b.condition, "conclusion": b.conclusion,
                         "action": b.action, "next_step": b.next_step}
                        for b in s.branches
                    ],
                }
                for s in (parsed.steps if parsed else [])
            ],
            "antipatterns": [
                {"scenario": a.scenario, "correct_action": a.correct_action, "source": a.source}
                for a in (parsed.antipatterns if parsed else [])
            ],
            "output_definition": [
                {"name": o.name, "format": o.format, "recipient": o.recipient,
                 "approval_level": o.approval_level}
                for o in (parsed.output_definition if parsed else [])
            ],
            "data_inputs": [
                {"name": d.name, "source": d.source, "frequency": d.frequency}
                for d in (parsed.data_inputs if parsed else [])
            ],
            "test_cases": [
                {"name": tc.name, "input_data": tc.input_data,
                 "expected_output": tc.expected_output, "assert_rules": tc.assert_rules}
                for tc in (parsed.test_cases if parsed else [])
            ],
            "raw_sections": parsed.raw_sections if parsed else {},
            "custom_sections": parsed.custom_sections if parsed else {},
        },
    }


async def create_skill(
    db: AsyncSession,
    skill_id: str,
    name: str,
    department: str,
    role: str,
    trigger_type: str = "manual",
    trigger_expression: str = "",
    risk_level: str = "R2",
    approval_level: int = 1,
    skill_md: str = "",
    policy_pack: dict | None = None,
    user_id: str = "system",
) -> dict:
    """新建Skill：写数据库 + 创建文件 + Git commit"""
    validate_skill_id(skill_id)

    # W1-E: 拒绝空/占位部门，防止脏数据入库（配合前端 O14 双层校验）
    dept_clean = (department or "").strip()
    if not dept_clean or dept_clean == "未指定":
        raise AppError("PARAM_INVALID", 422, {"detail": "部门不能为空或'未指定'，请选择真实部门"})

    # W4-B: risk_level 必须是 R1-R4 之一；空字符串等非法值拒绝，强制用户选一次
    if risk_level not in ("R1", "R2", "R3", "R4"):
        raise AppError("PARAM_INVALID", 422, {"detail": f"风险等级必须是 R1-R4 之一，当前：{risk_level!r}"})

    # 检查是否已存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    if result.scalar_one_or_none():
        raise AppError("SKILL_ALREADY_EXISTS", 409)

    # 生成SKILL.md（调 LLM 生成真实内容，失败时 fallback 到静态模板）
    if not skill_md or skill_md.strip() == "":
        skill_md = await _generate_skill_with_llm(
            skill_id=skill_id, name=name, department=department,
            role=role, trigger_type=trigger_type,
            trigger_expression=trigger_expression, risk_level=risk_level,
        )

    # LLM 生成的 frontmatter 可能缺 SkillForge 必填扩展字段 (trigger_type / risk_level);
    # 此处用 router 已校验过的参数回填, 防止后续 quick_validate 报"缺必填字段"
    skill_md = _ensure_skillforge_frontmatter(
        skill_md,
        trigger_type=trigger_type,
        risk_level=risk_level,
    )

    # tripleyak 风格的快速校验: frontmatter 必填字段 + 命名 + 长度
    from app.skills.validators import quick_validate
    qv = quick_validate(skill_md)
    if not qv.ok:
        raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": qv.to_detail()})

    # 生成 policy_pack.yaml
    policy_yaml = yaml.dump(
        policy_pack.get("params", policy_pack) if policy_pack else {},
        allow_unicode=True, default_flow_style=False,
    )

    # 生成 scripts/main.py 草稿入口。空白创建不伪造成功结果，避免后续运行时
    # 被误认为是已实现 Skill；真正的 AI 生成流程走 create_skill_from_files。
    script_content = f'''#!/usr/bin/env python3
"""
{name} — Skill 执行脚本
空白草稿入口。请在 Skill Studio / AI 助手中补齐 main(payload) 逻辑。
"""
import json
import sys
import yaml
from pathlib import Path
from app.common.time_utils import now_bjt

SKILL_DIR = Path(__file__).resolve().parent.parent


def load_params():
    p = SKILL_DIR / "policy_pack.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {{}}


def main(payload: dict) -> dict:
    _params = load_params()
    raise RuntimeError(
        "Skill 还是空白草稿：请先在 scripts/main.py 中实现 main(payload)，再运行。"
    )


def _read_payload() -> dict:
    raw = sys.stdin.read().strip()
    if raw:
        return json.loads(raw)
    if len(sys.argv) > 1:
        return json.loads(sys.argv[1])
    return {{}}


if __name__ == "__main__":
    result = main(_read_payload())
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''

    # 创建文件
    git_service.create_skill_dir(skill_id)
    git_service.write_file(skill_id, "SKILL.md", skill_md)
    git_service.write_file(skill_id, "policy_pack.yaml", policy_yaml)
    git_service.write_file(skill_id, "scripts/main.py", script_content)
    git_service.write_file(skill_id, "tests/test_main.py", "# 在此编写测试用例\n")

    # 生成 skillforge.yaml（平台管理元数据）
    sf_meta = {
        "display_name": name,
        "department": department,
        "trigger_type": trigger_type,
        "trigger_expression": trigger_expression or "",
        "risk_level": risk_level,
        "approval_level": approval_level,
        "owner": user_id,
    }
    git_service.write_file(
        skill_id, "skillforge.yaml",
        yaml.dump(sf_meta, allow_unicode=True, default_flow_style=False),
    )

    # Git commit
    commit_sha = git_service.commit_all(f"新建Skill: {skill_id} ({name})", user_id, skill_id=skill_id)

    # 写数据库
    skill = Skill(
        id=skill_id,
        name=name,
        description=f"{name}",
        department=department,
        role=role,
        trigger_type=trigger_type,
        trigger_expression=trigger_expression,
        risk_level=risk_level,
        approval_level=approval_level,
        status="draft",
        owner=user_id,
        git_commit=commit_sha,
    )
    db.add(skill)
    try:
        await db.flush()
    except IntegrityError as exc:
        # 并发场景下两个请求都通过 309 行的存在性检查，DB 唯一约束会拦下第二个
        # → 转成结构化 409，避免用户看到 500 内部错误
        await db.rollback()
        logger.info(f"create_skill 并发竞态命中 DB 约束，skill_id={skill_id}: {exc}")
        raise AppError("SKILL_ALREADY_EXISTS", 409) from exc

    # 审计日志
    await audit.log(user_id, "skill.create", "skill", skill_id)

    # 设备下发不再走本机 AICLAW_SKILLS_DIR 硬拷贝；创建完成后的调用方按账号/部门
    # 通过 sync_service.push_skill_to_targets 下发到对应 Agent 终端。

    quality = _score_skill_md(skill_md)
    return {
        "skill_id": skill_id,
        "git_commit": commit_sha,
        "quality_score": quality["score"],
    }


async def create_skill_from_files(
    db: AsyncSession,
    *,
    skill_id: str,
    name: str,
    department: str,
    files: dict[str, str],
    user_id: str = "system",
    trigger_type: str = "manual",
    trigger_expression: str = "",
    risk_level: str = "R2",
    approval_level: int = 1,
    current_version: str | None = None,
) -> dict:
    """
    新建 Skill：直接用预生成的文件内容（一步到位 aiclaw 流程用）。

    跟 create_skill 的区别：
    - 不调 LLM 生成 skill_md / scripts 骨架
    - 不重新构造 policy_pack.yaml
    - 直接把 files dict 里的所有文件原样写到 skill_repo/<skill_id>/
    - 仍然写数据库 + git commit + 推 AIClaw

    files 必须包含的最小集合：SKILL.md
    可选 / 推荐：intent.md / policy.yaml / scripts/main.py / tests/test_main.py / 等
    """
    validate_skill_id(skill_id)

    # W1-E: 同 create_skill 拒绝空/占位部门
    dept_clean = (department or "").strip()
    if not dept_clean or dept_clean == "未指定":
        raise AppError("PARAM_INVALID", 422, {"detail": "部门不能为空或'未指定'，请选择真实部门"})

    # W4-B: 同 create_skill 校验 risk_level
    if risk_level not in ("R1", "R2", "R3", "R4"):
        raise AppError("PARAM_INVALID", 422, {"detail": f"风险等级必须是 R1-R4 之一，当前：{risk_level!r}"})

    if "SKILL.md" not in files:
        raise AppError("SKILL_VALIDATION_FAILED", 422,
                       {"detail": "files 必须包含 SKILL.md"})

    contract = _load_creation_contract(files)
    files, gate_errors = await _preflight_creation_files(
        skill_id=skill_id,
        files=files,
        contract=contract,
    )
    if gate_errors:
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {
                "detail": "create_skill_from_files 校验失败",
                "errors": gate_errors,
            },
        )

    skill_md = files["SKILL.md"]

    # 检查重复
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    if result.scalar_one_or_none():
        raise AppError("SKILL_ALREADY_EXISTS", 409)

    # tripleyak 风格的快速校验
    from app.skills.validators import quick_validate
    qv = quick_validate(skill_md)
    if not qv.ok:
        raise AppError(
            "SKILL_VALIDATION_FAILED",
            422,
            {
                "detail": "SKILL.md 快速校验失败",
                "errors": [f"SKILL.md: {err}" for err in qv.errors],
                "warnings": qv.warnings,
            },
        )

    # 创建目录 + 写所有文件
    git_service.create_skill_dir(skill_id)
    skill_base = git_service.skill_dir(skill_id).resolve()
    for rel_path, content in files.items():
        # P0 #7: 路径穿越防御 — 公共校验函数统一处理
        from app.common.path_validator import validate_file_path
        validate_file_path(rel_path, skill_base)
        git_service.write_file(skill_id, rel_path, content)

    # 生成 skillforge.yaml（平台元数据，与现有 create_skill 一致）
    sf_meta = {
        "display_name": name,
        "department": department,
        "trigger_type": trigger_type,
        "trigger_expression": trigger_expression or "",
        "risk_level": risk_level,
        "approval_level": approval_level,
        "owner": user_id,
    }
    git_service.write_file(
        skill_id, "skillforge.yaml",
        yaml.dump(sf_meta, allow_unicode=True, default_flow_style=False),
    )

    commit_sha = git_service.commit_all(
        f"新建Skill (一步到位): {skill_id} ({name})", user_id, skill_id=skill_id,
    )

    skill = Skill(
        id=skill_id,
        name=name,
        description=f"{name}",
        department=department,
        role="",
        trigger_type=trigger_type,
        trigger_expression=trigger_expression,
        risk_level=risk_level,
        approval_level=approval_level,
        current_version=current_version,
        status="draft",
        owner=user_id,
        git_commit=commit_sha,
    )
    db.add(skill)
    await db.flush()

    await audit.log(user_id, "skill.create_from_files", "skill", skill_id,
                    {"file_count": len(files), "via": "aiclaw"})

    # 自动推送 AIClaw（与 create_skill 一致）
    if settings.AICLAW_SKILLS_DIR:
        try:
            import shutil
            src = f"{settings.SKILL_REPO_PATH}/{skill_id}"
            dst = f"{settings.AICLAW_SKILLS_DIR}/{skill_id}"
            shutil.copytree(src, dst, dirs_exist_ok=True)
            logger.info(f"Skill {skill_id} 已推送到 AIClaw: {dst}")
            try:
                from app.execution.openclaw_client import default_client
                reload_result = await default_client.reload_skills()
                logger.info(f"AIClaw reload: {reload_result}")
            except Exception as re:
                logger.debug(f"AIClaw reload 失败（非阻塞）: {re}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"推送 AIClaw 失败: {e}")

    quality = _score_skill_md(skill_md)
    return {
        "skill_id": skill_id,
        "git_commit": commit_sha,
        "quality_score": quality["score"],
        "current_version": current_version,
    }


async def scan_repo(
    db: AsyncSession,
    user_id: str = "system",
    dry_run: bool = False,
    skill_ids: list[str] | None = None,
) -> dict:
    """
    扫描 skills-repo 目录，把"文件存在但 DB 没记录"的 Skill 自动注册。

    与 scripts/seed_skills.py 等价，但走 Web API + SQLAlchemy + git_service，
    并自动 commit 未跟踪的文件，确保 git 历史完整。

    返回：
      {
        "found": int,            # 文件系统中的 Skill 总数
        "imported": list[dict],  # 本次新建的 Skill 列表 [{id, name, dept, ...}]
        "skipped": list[str],    # 已存在的 Skill ID 列表
        "errors": list[dict],    # 解析失败的 [{id, error}]
        "commit_sha": str | None,# 自动 commit 的 SHA（若有新文件）
        "dry_run": bool,
        "requested": list[str],
      }
    """
    repo_path = git_service.repo_path
    if not repo_path.exists():
        raise AppError("SKILL_REPO_NOT_FOUND", 500,
                       detail={"path": str(repo_path)})

    requested_ids = set()
    for raw_skill_id in skill_ids or []:
        skill_id = str(raw_skill_id or "").strip()
        if not skill_id:
            continue
        validate_skill_id(skill_id)
        requested_ids.add(skill_id)

    # 1. 扫描所有含 SKILL.md 的目录
    skill_dirs = []
    for d in sorted(repo_path.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").exists():
            if requested_ids and d.name not in requested_ids:
                continue
            skill_dirs.append(d)
    found_ids = {d.name for d in skill_dirs}
    missing_ids = sorted(requested_ids - found_ids)

    # 2. 查询 DB 已有的 Skill ID
    result = await db.execute(select(Skill.id))
    existing_ids = {row[0] for row in result.all()}

    imported: list[dict] = []
    skipped: list[str] = []
    errors: list[dict] = []
    for missing_id in missing_ids:
        errors.append({"id": missing_id, "error": "SKILL.md 不存在"})

    for d in skill_dirs:
        skill_id = d.name

        # 校验 ID 格式（防路径穿越 + 防奇怪命名）
        try:
            validate_skill_id(skill_id)
        except AppError as e:
            errors.append({"id": skill_id, "error": f"ID 不合法: {e.code}"})
            continue

        if skill_id in existing_ids:
            skipped.append(skill_id)
            continue

        # 解析 SKILL.md frontmatter
        try:
            skill_md = (d / "SKILL.md").read_text(encoding="utf-8")
        except Exception as e:
            errors.append({"id": skill_id, "error": f"读取 SKILL.md 失败: {e}"})
            continue

        frontmatter: dict = {}
        if skill_md.startswith("---"):
            parts = skill_md.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError as e:
                    errors.append({"id": skill_id, "error": f"frontmatter 解析失败: {e}"})
                    continue

        # 同时尝试读 skillforge.yaml（平台元数据），优先级高于 SKILL.md frontmatter
        sf_meta: dict = {}
        sf_path = d / "skillforge.yaml"
        if sf_path.exists():
            try:
                sf_meta = yaml.safe_load(sf_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                pass

        def pick(*keys, default=""):
            """从 sf_meta / frontmatter 取值，sf_meta 优先"""
            for src in (sf_meta, frontmatter):
                for k in keys:
                    if k in src and src[k] not in (None, ""):
                        return src[k]
            return default

        name = pick("display_name", "name", default=skill_id)
        # description 用于列表展示，从 SKILL.md frontmatter 拿
        description = frontmatter.get("description", "") or sf_meta.get("description", "")
        # W1-E: default 从 "未指定" 改 ""；下面的 validator 会拒绝空字符串并给出明确错误
        department = pick("department", default="")
        role = pick("role", default="")
        trigger_type = pick("trigger_type", "trigger", default="manual")
        trigger_expression = pick("trigger_expression", default="")
        risk_level = pick("risk_level", "risk-level", default="R2")
        approval_level_raw = pick("approval_level", default=1)
        try:
            approval_level = int(approval_level_raw) if approval_level_raw != "" else 1
        except (TypeError, ValueError):
            approval_level = 1

        imported.append({
            "id": skill_id,
            "name": str(name),
            "description": str(description)[:200],
            "department": str(department),
            "trigger_type": str(trigger_type),
            "risk_level": str(risk_level),
        })

        if not dry_run:
            now = now_bjt()
            skill = Skill(
                id=skill_id,
                name=str(name),
                description=str(description),
                department=str(department),
                role=str(role),
                trigger_type=str(trigger_type),
                trigger_expression=str(trigger_expression),
                risk_level=str(risk_level),
                approval_level=approval_level,
                status="draft",
                owner=user_id,
                created_at=now,
                updated_at=now,
            )
            db.add(skill)

    # 3. flush DB（让后续审计能拿到 commit）
    if imported and not dry_run:
        await db.flush()

    # 4. 自动 commit 未跟踪的 Skill 文件（保证 git 历史完整）
    commit_sha: str | None = None
    if imported and not dry_run:
        try:
            # 注意：只提交本次导入的目录，避免 scan-repo 把仓库里其它删除/修改
            # 一起带进历史。这里传 paths 而不是 skill_id，故不会跑结构校验；
            # 手动放入的历史 Skill 可能不完全符合当前结构标准。
            summary = ", ".join(s["id"] for s in imported[:5])
            if len(imported) > 5:
                summary += f" 等 {len(imported)} 个"
            commit_sha = git_service.commit_all(
                f"scan-repo: 注册 {summary}",
                user_id,
                paths=[s["id"] for s in imported],
            )
        except Exception as e:
            logger.warning(f"scan-repo 自动 commit 失败（不影响入库）: {e}")

    # 5. 审计日志
    if not dry_run:
        await audit.log(
            user_id, "skill.scan_repo", "skill", None,
            detail={
                "found": len(skill_dirs),
                "imported_count": len(imported),
                "skipped_count": len(skipped),
                "error_count": len(errors),
                "commit_sha": commit_sha,
            },
        )

    return {
        "found": len(skill_dirs),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "commit_sha": commit_sha,
        "dry_run": dry_run,
        "requested": sorted(requested_ids),
    }


async def save_skill_content(
    db: AsyncSession,
    skill_id: str,
    skill_md: str | None = None,
    policy_pack_raw: str | None = None,
    script_content: str | None = None,
    user_id: str = "system",
) -> dict:
    """保存Skill内容（草稿）：写文件 + Git commit + 更新数据库

    [H8] 内容 hash 短路: 若新内容与已有内容相同, 则不调 git_service.write_file,
    并在返回值里通过 skill_md_changed 字段告知 router 是否需要清缓存。
    避免"保存空内容也清掉参数索引和冲突缓存"的浪费。
    """
    validate_skill_id(skill_id)

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    changes = []
    skill_md_changed = False  # [H8] 仅在 SKILL.md 真实变化时为 True
    policy_pack_changed = False
    script_content_changed = False

    if skill_md is not None:
        # tripleyak 风格的快速校验, frontmatter / 命名 / 长度不合规直接拒绝
        from app.skills.validators import quick_validate
        qv = quick_validate(skill_md)
        if not qv.ok:
            raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": qv.to_detail()})

        # [H8] 对比新旧内容
        old_md = git_service.read_file(skill_id, "SKILL.md") or ""
        if old_md != skill_md:
            git_service.write_file(skill_id, "SKILL.md", skill_md)
            changes.append("SKILL.md")
            skill_md_changed = True
            # 从新的SKILL.md同步frontmatter到数据库
            parsed = skill_parser.parse(skill_md)
            fm = parsed.frontmatter
            _sync_skill_fields_from_frontmatter(skill, fm)

    if policy_pack_raw is not None:
        old_policy = git_service.read_file(skill_id, "policy_pack.yaml") or ""
        if old_policy != policy_pack_raw:
            git_service.write_file(skill_id, "policy_pack.yaml", policy_pack_raw)
            changes.append("policy_pack.yaml")
            policy_pack_changed = True

    if script_content is not None:
        old_script = git_service.read_file(skill_id, "scripts/main.py") or ""
        if old_script != script_content:
            git_service.write_file(skill_id, "scripts/main.py", script_content)
            changes.append("scripts/main.py")
            script_content_changed = True

    # 没有任何文件实际变化 → 跳过 git commit / db flush / audit, 直接返回
    if not changes:
        return {
            "skill_id": skill_id,
            "git_commit": skill.git_commit,
            "changes": [],
            "skill_md_changed": False,
            "policy_pack_changed": False,
            "script_content_changed": False,
            "noop": True,
        }

    # Git commit
    commit_sha = git_service.commit_all(
        f"更新 {', '.join(changes)}", user_id, skill_id=skill_id
    )

    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()

    await audit.log(user_id, "skill.edit", "skill", skill_id,
                    detail={"changes": changes})

    return {
        "skill_id": skill_id,
        "git_commit": commit_sha,
        "changes": changes,
        "skill_md_changed": skill_md_changed,
        "policy_pack_changed": policy_pack_changed,
        "script_content_changed": script_content_changed,
        "noop": False,
    }


async def _call_llm_with_retry(prompt: str, system_prompt: str, *, max_retries: int = 2) -> dict | None:
    """纯 LLM 调用 + 重试逻辑。

    按 temperature 阶梯重试，遇到可恢复错误（非 dict、校验失败）继续；
    遇到不可恢复错误（限流/鉴权/超时）直接抛出 AppError。
    返回 LLM 的 JSON dict，或重试耗尽后返回 None。
    """
    from app.common.ai import call_llm

    temperatures = [0.3, 0.5][:max_retries]
    for attempt, temp in enumerate(temperatures):
        try:
            result = await call_llm(
                system=system_prompt,
                user=prompt,
                max_tokens=2500,
                temperature=temp,
                json_mode=True,
                timeout=60,
            )

            if not result or not isinstance(result, dict):
                logger.warning(f"LLM 返回非 dict (attempt {attempt+1}): {type(result)}")
                continue

            return result

        except asyncio.TimeoutError as e:
            logger.warning(f"LLM 调用超时 (attempt {attempt+1}): {e}")
            # 第二次超时不再重试，直接抛出
            if attempt >= max_retries - 1:
                raise AppError("LLM_TIMEOUT", 504, {"detail": "LLM 生成超时"}) from e
        except Exception as e:
            from app.common.ai import LLMRateLimitError, LLMQuotaExceededError, LLMAuthError
            if isinstance(e, LLMRateLimitError):
                logger.error(f"LLM 限流 (attempt {attempt+1}): {e}")
                raise AppError("LLM_QUOTA_EXCEEDED", 429, {"detail": "LLM 限流"}) from e
            if isinstance(e, LLMQuotaExceededError):
                logger.error(f"LLM 配额耗尽 (attempt {attempt+1}): {e}")
                raise AppError("LLM_QUOTA_EXCEEDED", 429, {"detail": "LLM 配额已用完"}) from e
            if isinstance(e, LLMAuthError):
                logger.error(f"LLM 鉴权失败 (attempt {attempt+1}): {e}")
                raise AppError("LLM_API_ERROR", 502, {"detail": "LLM 鉴权失败"}) from e
            err_type = type(e).__name__
            logger.warning(f"LLM 调用失败 (attempt {attempt+1}, type={err_type}): {e}")

    return None


async def _generate_skill_with_llm(
    skill_id: str, name: str, department: str, role: str,
    trigger_type: str, trigger_expression: str, risk_level: str,
) -> str:
    """两阶段生成: LLM 出 JSON → 代码确定性渲染 SKILL.md。

    流程编排：
      1. 构建 prompt，调 _call_llm_with_retry 获取结构化 JSON
      2. 解析 triage / description / body 字段
      3. 调 _render_skill_md 确定性渲染
      4. 校验通过返回；失败则降级到静态模板
    """
    import re
    from app.skills.core.methodology import ENHANCED_SYSTEM_PROMPT

    slug = re.sub(r'[^a-z0-9\-]', '-', skill_id.lower())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')[:64]

    # 阶段 1: LLM 输出 JSON（结构化内容，不涉及 YAML 格式）
    user_prompt = (
        f"Skill 名称: {name}\n"
        f"部门: {department}\n"
        f"角色: {role}\n"
        f"触发方式: {trigger_type}\n"
        f"触发表达式: {trigger_expression or '无'}\n"
        f"风险等级: {risk_level}\n"
        "\n"
        "请按 SkillForge 4 阶段方法论生成此 Skill。"
    )

    result = await _call_llm_with_retry(user_prompt, ENHANCED_SYSTEM_PROMPT, max_retries=2)

    if result is not None:
        # Phase 0 triage 结果记录到日志
        triage_decision = str(result.get("triage_decision", "CREATE_NEW")).strip().upper()
        triage_reason = str(result.get("triage_reason", "")).strip()
        if triage_decision in {"USE_EXISTING", "IMPROVE", "COMPOSE"}:
            logger.warning(
                "Skill {} Phase 0 triage 建议 {}: {}",
                slug, triage_decision, triage_reason,
            )
        else:
            logger.info(
                "Skill {} Phase 0 triage = CREATE_NEW: {}",
                slug, triage_reason or "无说明",
            )

        desc = str(result.get("description", f"{name}。Use when: 需要{name}时。"))
        compat = str(result.get("compatibility", "Requires curl"))
        body = str(result.get("body", f"# {name}\n\n请补充内容。"))

        # triage 不是 CREATE_NEW 时，在 description 头部注入提醒
        if triage_decision != "CREATE_NEW" and triage_reason:
            desc = f"[Phase 0 建议: {triage_decision}] {desc}"

        # 阶段 2: 代码确定性渲染（不依赖 LLM 输出 YAML）
        md = _render_skill_md(
            slug=slug, description=desc, compatibility=compat,
            department=department, trigger_type=trigger_type, risk_level=risk_level, body=body,
        )

        # 阶段 3: 校验
        if _validate_generated_md(md, slug):
            logger.info(f"Skill {slug} LLM 生成成功")
            return md
        else:
            logger.warning(f"Skill {slug} LLM 结果校验失败，降级到静态模板")

    # fallback 到静态模板
    logger.warning(f"Skill {slug} LLM 生成失败，使用静态模板")
    return _generate_skill_template(
        skill_id=skill_id, name=name, department=department,
        role=role, trigger_type=trigger_type,
        trigger_expression=trigger_expression, risk_level=risk_level,
    )


def _render_skill_md(
    slug: str, description: str, compatibility: str,
    department: str, risk_level: str, body: str, trigger_type: str = "manual",
) -> str:
    """确定性渲染：JSON 内容 → 标准 SKILL.md（不可能格式错）。

    P0-8: 用 yaml.safe_dump 处理 frontmatter, 而不是手写 replace('"', '\\\\"').
    这样 description / compatibility 中的反斜杠、换行、控制字符、多字节字符
    都会被 PyYAML 正确转义, 从根上消除 prompt injection / YAML 注入风险。
    """
    fm_dict = {
        "name": slug,
        "description": description,
        "compatibility": compatibility,
        "trigger_type": trigger_type,
        "risk_level": risk_level,
        "metadata": {
            "author": "skillforge",
            "department": department,
            "risk-level": risk_level,
        },
    }
    fm_yaml = yaml.safe_dump(
        fm_dict,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    frontmatter = f"---\n{fm_yaml}\n---"

    # body 清理：去掉可能的 frontmatter 残留
    body = body.strip()
    if body.startswith('---'):
        # LLM 可能在 body 里也输出了 frontmatter，去掉
        parts = body.split('---', 2)
        if len(parts) >= 3:
            body = parts[2].strip()

    # 确保 body 以 # 标题开头
    if not body.startswith('#'):
        body = f"# {slug}\n\n{body}"

    return f"{frontmatter}\n\n{body}\n"


def _validate_generated_md(md: str, expected_slug: str) -> bool:
    """校验生成的 SKILL.md 是否合法"""
    try:
        # YAML frontmatter 可解析
        parts = md.split('---', 2)
        if len(parts) < 3:
            return False
        fm = yaml.safe_load(parts[1])
        if not isinstance(fm, dict):
            return False
        # name 正确
        if fm.get('name') != expected_slug:
            return False
        # description 非空
        if not fm.get('description'):
            return False
        # body 非空且有内容
        body = parts[2].strip()
        if len(body) < 50:
            return False
        # parser 能解析
        parsed = skill_parser.parse(md)
        if not parsed.purpose and not parsed.steps:
            # 允许没有 steps（纯指令型 skill），但至少要有内容
            pass
        return True
    except Exception as e:
        logger.debug(f"校验失败: {e}")
        return False


def _score_skill_md(md: str) -> dict:
    """对生成的 SKILL.md 打质量分（0-100）"""
    score = 0
    details = {}

    try:
        parts = md.split('---', 2)
        fm = yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
        body = parts[2] if len(parts) >= 3 else md

        # 格式合规 (30分)
        fmt = 0
        if isinstance(fm, dict) and fm.get('name'): fmt += 10
        if fm.get('description') and 'when' in str(fm.get('description', '')).lower(): fmt += 10
        if fm.get('metadata'): fmt += 5
        if fm.get('compatibility'): fmt += 5
        score += fmt
        details['format'] = fmt

        # 内容可执行 (30分)
        exe = 0
        if '```bash' in body or '```shell' in body: exe += 15
        if 'curl' in body: exe += 10
        if 'http' in body: exe += 5
        score += exe
        details['executable'] = exe

        # 完整性 (20分)
        comp = 0
        if '##' in body: comp += 5
        if '步骤' in body or 'Step' in body: comp += 5
        if '输出' in body or '格式' in body: comp += 5
        if '|' in body: comp += 5  # 有表格
        score += comp
        details['completeness'] = comp

        # 行数适中 (10分)
        lines = len(body.strip().split('\n'))
        if 30 <= lines <= 300: details['length'] = 10
        elif 15 <= lines: details['length'] = 5
        else: details['length'] = 0
        score += details['length']

        # 可读性 (10分)
        read = 0
        if body.count('#') >= 3: read += 5
        if '-' in body or '*' in body: read += 5
        score += read
        details['readability'] = read

    except Exception as e:
        logger.warning("Skill 质量评分异常: {}", e)

    return {'score': min(score, 100), 'details': details}


def _generate_skill_template(
    skill_id: str, name: str, department: str, role: str,
    trigger_type: str, trigger_expression: str, risk_level: str,
) -> str:
    """生成标准格式的 SKILL.md 模板"""
    import re
    slug = re.sub(r'[^a-z0-9\-]', '-', skill_id.lower())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')[:64]

    desc = f"{name}。Use when: 需要{name}相关的决策建议。"

    return f"""---
name: {slug}
description: "{desc}"
compatibility: "Requires Python 3.12"
trigger_type: {trigger_type}
risk_level: {risk_level}
metadata:
  author: skillforge
  department: {department}
  risk-level: {risk_level}
---

# {name}

## 目的

{name}的决策逻辑。请补充详细描述。

## 判断逻辑

### step_1: 主要判断
  ├─ 条件A满足 → 绿灯
      动作: 建议执行
  └─ 其他 → 红灯
      动作: 建议暂停

## 反例

1. **误判场景**: 请补充典型误判场景
   **正确做法**: 请补充正确处理方式

2. **误判场景**: 请补充第二个误判场景
   **正确做法**: 请补充正确处理方式

## 输出定义

| 名称 | 格式 | 接收人 | 审批级别 |
| :--- | :--- | :--- | :--- |
| 决策报告 | JSON | {role or '负责人'} | L1 |

## 数据输入

| 名称 | 来源 | 频率 |
| :--- | :--- | :--- |
| 业务数据 | 待配置 | 每日 |

## 测试用例

### 用例_正常通过
```yaml
input:
  key: value
expected_output:
  conclusion: "绿灯"
```

### 用例_异常拒绝
```yaml
input:
  key: bad_value
expected_output:
  conclusion: "红灯"
```

### 用例_边界情况
```yaml
input:
  key: boundary_value
expected_output:
  conclusion: "黄灯"
```
"""


# ═══════════════════════════════════════════════════════
# [H7] v1.10.0 三个新端点的 service 层 — router 业务逻辑下沉
# 原版本把部门过滤和聚合都写在 router, 违反 CLAUDE.md "router 只做分发"
# ═══════════════════════════════════════════════════════
