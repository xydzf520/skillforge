"""todos 模块共用的权限判断 helper。

收件中心的角色匹配涉及 v2 主角色 + legacy 角色 + can_view_all 三套口径，旧代码散落
`role in ("admin","ai_engineer","director")` 各处不一致，且 v2 的 `system_admin` 反而被
漏判。本文件提供唯一入口：

- ``is_global_inbox_reader(user)``：是否拥有"看遍全公司收件箱 / 待办"特权
- ``is_global_inbox_actor(user)``：是否可以越部门改写 / 转派 / 延期任意 todo（口径与
  reader 暂时一致；保留独立函数以便后续审计写口径放宽 / 收紧）
- ``assert_can_modify_todo(user, todo, request, skill)``：服务层调用，
  非全局 + 非本部门 → 抛 ``AppError("AUTH_PERMISSION_DENIED", 403)``

注意：assignee 自己**不**能用本 helper 自动放行 SLA / 转派操作；assignee 自我延期/
自我转派的合理性由 spec inbox §6.2 决定（默认禁止），需要时由调用点显式判断。
"""

from __future__ import annotations

from app.auth.models import User
from app.common.exceptions import AppError
from app.skills.core.models import Skill
from app.todos.models import AITodo, DecisionRequest


# 全局可见 / 全局可写角色集合：v2 主角色 system_admin + legacy admin/ai_engineer/director
# - system_admin：v2 主角色（spec role-matrix-v2 §3.1）—— 必须全局
# - admin：legacy 主角色，仍兼容
# - ai_engineer / director：legacy 全局可见角色，等迁移收口后再移除
_GLOBAL_ROLES: frozenset[str] = frozenset(
    {"system_admin", "admin", "ai_engineer", "director"}
)


def is_global_inbox_reader(user: User | None) -> bool:
    """是否拥有跨部门读取收件中心 / 待办的特权。"""
    if user is None:
        return False
    if bool(getattr(user, "can_view_all", False)):
        return True
    role = (getattr(user, "role", "") or "").lower()
    return role in _GLOBAL_ROLES


def is_global_inbox_actor(user: User | None) -> bool:
    """是否可以越部门写入（延期 SLA / 批量转派 / 代审批）任意 todo。

    当前与 reader 完全一致；保留独立函数避免后续要收紧写权时改 30+ 调用点。
    """
    return is_global_inbox_reader(user)


def assert_can_modify_todo(
    *,
    user: User,
    todo: AITodo,
    request: DecisionRequest,
    skill: Skill | None,
) -> None:
    """SLA 延期 / 转派 / 批量改写操作前的统一权限闸门。

    放行规则（满足任一即可）：
    1. ``is_global_inbox_actor(user)`` —— admin / system_admin / can_view_all
    2. ``user.role == "dept_admin"`` 且 todo 所属 Skill 部门在 user 关联部门中
       —— 走 ``require_department_access`` sync 兜底（org_tree 的 async 查询交由
       调用方在需要时自行做更细判定）

    全部不满足 → ``AppError("AUTH_PERMISSION_DENIED", 403)``。
    assignee 自己**不**自动放行：SLA 延期 / 转派属于管理操作，需要 dept_admin 以上权限。
    """
    if is_global_inbox_actor(user):
        return

    # 仅 dept_admin（v2 主角色）有"在本部门内代审 / 改 SLA / 转派"的权力。
    # 注意：legacy `biz_owner` 在 v2 已经 normalize 为 `dept_admin`（见 role_matrix.ROLE_ALIASES），
    # 但仍存在尚未迁移的 `biz_owner` 记录—— **不**自动放行，避免普通业务用户越权。
    role = (getattr(user, "role", "") or "").lower()
    if role == "dept_admin":
        # 优先用 skill.department；orphan skill（skill_id 已不在 skills 表）
        # 不允许 dept_admin 操作（防止跨部门越权）
        if skill is not None and skill.department:
            from app.auth.dependencies import require_department_access

            if require_department_access(skill.department, user):
                return

    raise AppError("AUTH_PERMISSION_DENIED", 403)


__all__ = [
    "is_global_inbox_reader",
    "is_global_inbox_actor",
    "assert_can_modify_todo",
]
