# SkillForge 权限体系 v2 — 角色矩阵与实施规格

> 日期：2026-04-17
> 版本：v2.2（detailed supplement，可直接按本文实施）
> 目的：把现有 6 角色 + 三套散落权限判定收敛为 4 角色 + `UserOrgMembership` / `Skill.org_unit_id` 真源 + `SkillMember` 对象级权限
> 实施顺序：先完成身份真源 / 会话 / 兼容双写，再迁业务域，最后替换前端

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [核心决策汇总](#2-核心决策汇总)
3. [角色定义](#3-角色定义)
4. [数据模型](#4-数据模型)
5. [能力矩阵](#5-能力矩阵)
6. [审批链规则](#6-审批链规则)
7. [对象级权限 SkillMember](#7-对象级权限-skillmember)
8. [钉钉集成](#8-钉钉集成)
9. [用户生命周期](#9-用户生命周期)
10. [Session 立刻失效机制](#10-session-立刻失效机制)
11. [组织变更](#11-组织变更)
12. [审计与 Service ID](#12-审计与-service-id)
13. [API Token 策略](#13-api-token-策略)
14. [老角色迁移映射表](#14-老角色迁移映射表)
15. [前端改动清单](#15-前端改动清单)
16. [后端改动清单](#16-后端改动清单)
17. [API 接口规格](#17-api-接口规格)
18. [错误码清单](#18-错误码清单)
19. [Codex 实施指引 Phase & Commit 级 checklist](#19-codex-实施指引)
20. [测试场景矩阵](#20-测试场景矩阵)
21. [Edge case 与踩坑提示](#21-edge-case-与踩坑提示)
22. [边界决策（本版一次定死）](#22-边界决策本版一次定死)
23. [文档变更记录](#23-文档变更记录)

---

## 1. 背景与目标

### 1.1 现状问题

1. **角色过多且语义重合**：`admin` / `ai_engineer` / `biz_owner` / `operator` / `aibp` / `director` 六种。`ai_engineer` 与 `director` 跨部门权限等价；`biz_owner` 与 `director` 审批职责重合；`operator` 是钉钉首登默认值但几乎什么都做不了。
2. **数据归属双真源**：`User.department` 单值 vs `UserOrgMembership` 多对多，权限判定只读前者。
3. **三套权限判定互不通气**：路由级 `if user.role in (...)` 硬编码 + 对象级 `SkillMember` + 策略级 `abac_policies` 表。
4. **新钉钉用户体验差**：`app/auth/dingtalk_oauth.py:159` 首登默认 `operator`，看到几乎空白首页。
5. **离职 / 组织变更无规则**：资产没转交机制；部门合并靠手工。

### 1.2 目标

- 4 种角色覆盖 95% 场景
- 权限真源唯一：人员归属 = `UserOrgMembership`；资源归属 = `Skill.org_unit_id` / `Playbook.department_id`
- `pending → active → disabled` 状态机显式管理 onboarding
- `dept_admin` 承担部门内部自治，减轻 `system_admin` 负担
- 跨部门规则明确（Fork、Playbook 引用、部门合并）

---

## 2. 核心决策汇总

| 项 | 决策 |
|---|------|
| 角色 | `system_admin` / `dept_admin` / `aibp` / `observer` |
| 附加字段 | `can_view_all` (bool)、`state` (pending/active/disabled)、`permissions_rev` (int) |
| 数据归属 | 人员归属以 `UserOrgMembership` 为真源；资源归属以 `Skill.org_unit_id` / `Playbook.department_id` 为真源；`User.department` / `Skill.department` / `Playbook.department` 均降级为展示/兼容字段 |
| 代码编写权 | aibp 及以上 |
| 对象级权限 | `SkillMember` opt-in（不配 = 部门全员可编辑） |
| 钉钉首登 | `state=pending`，所有菜单隐藏 |
| 钉钉 `is_manager` | 只进 `UserOrgMembership`，**不自动**提 `dept_admin` |
| pending 激活 | dept_admin 激活本部门候选人 |
| dept_admin 本部门权力 | 加人 / 改 role (aibp↔observer) / 禁用 / 移除 / 重置密码 |
| 离职转交 | 禁用时弹窗要求指定接手人 |
| 最后 system_admin | 硬锁禁删、禁禁用、禁改 role |
| 权限变更 | `permissions_rev += 1`，旧 session 立刻失效 |
| Skill Fork | 完全断开，原作者不保权限 |
| Playbook 跨部门 | 归创建人部门审批 |
| Playbook 元数据 | frontmatter 双写 `department_id`(真源) + `department`(展示) + `creator_user_id` / `owner_user_id` |
| 组织合并 | 自动迁移 Skill / Membership / 进行中 todo |
| 审批链 | L1 / L2 到 dept_admin，**不升到 system_admin** |
| 审计 | 仅 system_admin 可查 |
| API Token | 不开放 |
| Service Account | 本需求内不引入独立模型；统一使用固定 `svc_xxx` 审计 ID，禁止隐式 `"system"` |
| 实施策略 | 必须按“身份真源与会话 → 业务域迁移 → 前端替换”三段推进，禁止一次性全仓直改 |

---

## 3. 角色定义

### 3.1 system_admin — 系统管理员

**定位**：平台整体负责人。

**典型用户**：SkillForge 运维 / 平台 Owner。

**独家能力**：
- 组织架构管理（`org_units` CRUD、钉钉组织同步）
- 跨部门用户管理（建人、改 role 到任何角色、跨部门转移）
- 系统配置 / Prompt 管理 / AI 成本报表 / 代理设备管理
- 审计日志查询与导出
- 部门合并 / 拆分 / 改名
- 提拔任何人到 `dept_admin`

**约束**：
- 系统中**至少有一名** system_admin，最后一个不允许禁用、删除或改 role（硬锁，见 §9.6）

### 3.2 dept_admin — 部门管理员（子管理员）

**定位**：某一个或多个部门的内部管理者。

**典型用户**：部门 leader、业务线 leader。

**管辖部门 = `SELECT org_unit_id FROM user_org_memberships WHERE user_id = ? AND is_manager = TRUE`**

**读范围规则（统一约定）**：
- 管理范围仍只等于 `is_manager=True` 的**直接部门**，不因角色自动获得子部门写权。
- 读范围单独由 read-scope helper 计算：`dept_admin` 对“本人所有关联部门”有直读权；其中属于自己管理范围的部门，再向下展开到整棵子树。
- 因此 `dept_admin` 在 Dashboard / Tasktree / Datasource / Execution / Skill / Playbook 读侧看到的是“关联部门直读 + 管辖部门子树”，但写操作、审批归属、成员管理仍只按直接管辖部门判定。

**能力**：
- 管辖部门的 Skill CRUD / 编辑 / 发布 / 删除
- 管辖部门的 Playbook CRUD
- 写代码（进 Skill 工作台、Playbook 编辑器）
- 审批管辖部门 Skill/Playbook 的 L1 / L2
- 本部门成员管理：加人（激活 pending）/ 改 role（aibp ↔ observer，**不能提到 dept_admin**）/ 禁用 / 移除 / 重置密码
- 读侧可见范围 = 关联部门直读；对管辖部门展开到下级子树（Tasktree / Dashboard / Datasource / Execution / Skill / Playbook）

**不能做**：
- 提拔本部门成员到 `dept_admin`（防止私相授受）
- 跨管辖范围操作
- 管理 `org_units` 结构
- 查看 Audit Log
- 创建非钉钉本地账号（仅 system_admin 可从 `scripts/manage_users.py` 做）

### 3.3 aibp — AI 业务伙伴

**定位**：日常写 Skill / 维护 Skill 的业务技术工作者。

**关联部门 = `SELECT org_unit_id FROM user_org_memberships WHERE user_id = ?`（不要求 is_manager）**

**能力**：
- 关联部门内 Skill 的建 / 编辑（进入 SkillMember 受控模式时要求 SkillMember.role ∈ {owner, editor}）
- 关联部门 Skill 的**发布权** —— 仅当 `SkillMember.role=owner` 时才能按"发布"
- 关联部门内 Playbook 的新建 / 编辑 / 提审（要求 `owner_user_id=自己` 或由 `dept_admin` 指定为 owner）
- 写代码（进 Skill 工作台、Playbook 编辑器）
- 发起审核（提交给 dept_admin）
- 关联部门 Tasktree / Dashboard

**约束**：
- 发布权在对象级，避免同部门互相乱发布
- 没有组织级管理能力

### 3.4 observer — 观察员

**定位**：只读角色，服务 onboarding、合规、上级查看。

**默认可见范围**：
- 自己关联部门 + **所有下级子树**（按 `org_units.path` 前缀匹配）
- 叠加 `can_view_all=True` 时跨所有部门

**能力**：
- Skill Hall / Skill 详情 / Playbook 详情 / Tasktree / Dashboard（默认可见范围内）
- 明确指派给自己的 Todo / Dispatch 回执（仅限本人 assignee 的任务回填，不含审批 / 转派 / 新建）
- **其余按钮**隐藏或置灰：编辑 / 运行 / 保存 / 审核 / 发布 / Fork / 删除 / 新建

**不能做**：
- 除本人 assignee 的 Todo 回执外，不能做任何写操作
- `/admin/**` 后台
- Audit Log
- 工作台 / Playbook 编辑器
- 触发任何 LLM 调用（Skill 执行按钮不可见）

### 3.5 can_view_all 标志

**含义**：布尔字段，独立于 role。`True` 时跨所有部门只读穿透。

**典型使用**：
- `observer + can_view_all=True` = 原 `director` 角色（高管跨部门看板）
- `aibp + can_view_all=True` = 特殊 AIBP 跨部门协作（需谨慎授予）
- `system_admin` 本身全局，此标志对它无影响

**谁能设置**：仅 `system_admin`。

### 3.6 state 状态机

```
     创建/首登
         ↓
     [pending] ─────activate───→ [active]
                  (dept_admin       │
                   or system_admin) │ disable
                                    ↓
                               [disabled]
                                    │
                                    │ reactivate
                                    ↓
                                 [active]
```

| 状态 | 含义 | 能登录 | 能做事 |
|------|------|:---:|:---:|
| `pending` | 已创建（通常是钉钉首登），待审批 | ✓ | ✗（看到 `/pending` 挂起页） |
| `active` | 正常使用 | ✓ | 按 role 决定 |
| `disabled` | 被禁用（离职 / 封禁） | ✗ | ✗ |

---

## 4. 数据模型

### 4.1 User 表变更

**文件**：`app/auth/models.py:11-30`

**当前代码**（第 11-30 行）：
```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    can_view_all: Mapped[bool] = mapped_column(Boolean, default=False)
    department: Mapped[str | None] = mapped_column(String(50))
    dingtalk_user_id: Mapped[str | None] = mapped_column(String(100))
    dingtalk_union_id: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

**目标代码**（在现有基础上新增两个字段，注释 `role` 值域）：
```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # v2: 4 种角色 — system_admin / dept_admin / aibp / observer
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    can_view_all: Mapped[bool] = mapped_column(Boolean, default=False)

    # v2: 降级为主部门展示/兼容字段，存主部门显示名；权限真源为 UserOrgMembership
    department: Mapped[str | None] = mapped_column(String(50))

    dingtalk_user_id: Mapped[str | None] = mapped_column(String(100))
    dingtalk_union_id: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))

    # v2: is_active 保留但不再作为业务状态真源，由 state 驱动
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 【新增】v2 状态机 — pending / active / disabled
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # 【新增】v2 权限 revision — 每次 role/state/can_view_all/membership 变更 +1，
    # session token 里快照此值；验证失败即踢回 /login
    permissions_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 4.2 UserOrgMembership 语义升级（表结构不变）

**文件**：`app/org/models.py:30-43`

- 结构**不变**
- 语义升级见 §2 和 §3
- `is_manager=True` 记录某人是该部门的 manager（来自钉钉或手工），但**不自动**等于 `dept_admin`

### 4.2.1 部门标识正规化与兼容双写

- `org_units.id` 是**唯一 canonical department id**。有钉钉 `dept_id` 时直接用其字符串；没有时才人工生成稳定 id。
- `org_units.name` 是部门展示名；允许改名，但 `id` 不变。
- `user_org_memberships.org_unit_id`、`skills.org_unit_id`、`playbooks.department_id` 一律存 canonical id。
- `users.department`、`skills.department`、`playbooks.department` 保留为**展示/兼容字段**，统一存部门展示名，不再作为权限真源。
- 任一 create / update / sync / merge / rename 路径都必须双写：
  - 真源字段：`org_unit_id` / `department_id`
  - 兼容字段：`department`（展示名）
- 旧代码尚未迁完前，禁止把 `users.department` 或 `skills.department` 改成 org id；否则现存大量 `department == current_user.department` 比较会直接失真。

### 4.3 SkillMember 语义明确化（表结构不变）

**文件**：`app/skills/members.py:11-18`

- 结构**不变**
- 新语义：**opt-in 精细化**
  - skill_members 表对某 Skill 无记录 → 部门内 aibp 及以上全员可编辑
  - 有至少一条 → 进入受控模式，只有成员按 role 操作

### 4.4 索引新增

```sql
CREATE INDEX IF NOT EXISTS idx_user_state_role ON users(state, role);
CREATE INDEX IF NOT EXISTS idx_user_permissions_rev ON users(permissions_rev);
CREATE INDEX IF NOT EXISTS idx_membership_manager ON user_org_memberships(user_id, is_manager);
CREATE INDEX IF NOT EXISTS idx_orgunit_path_prefix ON org_units(path text_pattern_ops);
-- 子树查询统一写成：path = :base_path OR path LIKE :base_path || '/%'
-- 禁止裸用 LIKE base_path || '%'，否则 /EC 会误匹配 /ECOM
```

### 4.5 Alembic 迁移脚本（完整可跑）

**文件**：`migrations/versions/NNNN_v2_role_matrix.py`（N 由 `alembic revision --autogenerate -m "v2_role_matrix"` 自动生成，参考现有最后一条 `cd49132` 相关的迁移号）

```python
"""v2 role matrix migration

Revision ID: <AUTOGEN>
Revises: <LATEST_EXISTING>
Create Date: 2026-04-17
"""
from alembic import op
import sqlalchemy as sa


revision = "<AUTOGEN>"
down_revision = "<LATEST_EXISTING>"  # 填 alembic heads 当前的版本
branch_labels = None
depends_on = None


def upgrade():
    # ========== Step 1: 加字段 ==========
    op.add_column(
        "users",
        sa.Column("state", sa.String(20), nullable=False, server_default="active"),
    )
    op.add_column(
        "users",
        sa.Column("permissions_rev", sa.Integer(), nullable=False, server_default="0"),
    )

    # ========== Step 2: 老角色迁移（在线，保持服务运行）==========
    conn = op.get_bind()

    # admin → system_admin
    conn.execute(sa.text("UPDATE users SET role = 'system_admin' WHERE role = 'admin'"))

    # biz_owner → dept_admin（承担 L1 审批）
    conn.execute(sa.text("UPDATE users SET role = 'dept_admin' WHERE role = 'biz_owner'"))

    # director → observer + can_view_all=TRUE（保留跨部门只读）
    conn.execute(sa.text("""
        UPDATE users
        SET role = 'observer', can_view_all = TRUE
        WHERE role = 'director'
    """))

    # operator → observer（最小只读）
    conn.execute(sa.text("UPDATE users SET role = 'observer' WHERE role = 'operator'"))

    # ai_engineer → aibp（保守迁移，由 system_admin 后台复核手工提权 dept_admin）
    conn.execute(sa.text("UPDATE users SET role = 'aibp' WHERE role = 'ai_engineer'"))

    # ========== Step 3: 规范化老 department -> org_unit_id（禁止直接把 department 当 id）==========
    conn.execute(sa.text("""
        CREATE TEMP TABLE _dept_name_map AS
        SELECT name, MIN(id) AS id, COUNT(*) AS cnt
        FROM org_units
        GROUP BY name
    """))
    conn.execute(sa.text("""
        CREATE TEMP TABLE _unmatched_departments AS
        SELECT u.id AS user_id, u.department AS raw_department
        FROM users u
        LEFT JOIN org_units ou_id
               ON ou_id.id = u.department
        LEFT JOIN _dept_name_map ou_name
               ON ou_name.name = u.department AND ou_name.cnt = 1
        WHERE u.department IS NOT NULL
          AND u.department <> ''
          AND COALESCE(ou_id.id, ou_name.id) IS NULL
    """))
    unresolved = conn.execute(sa.text("SELECT count(*) FROM _unmatched_departments")).scalar() or 0
    if unresolved:
        raise RuntimeError(
            "存在无法映射到 org_units 的历史 department；"
            "先清洗 users.department / skills.department 后再跑 v2 migration"
        )

    conn.execute(sa.text("""
        WITH mapped_user_dept AS (
            SELECT
                u.id AS user_id,
                COALESCE(ou_id.id, ou_name.id) AS org_unit_id
            FROM users u
            LEFT JOIN org_units ou_id
                   ON ou_id.id = u.department
            LEFT JOIN _dept_name_map ou_name
                   ON ou_name.name = u.department AND ou_name.cnt = 1
            WHERE u.department IS NOT NULL
              AND u.department <> ''
        )
        INSERT INTO user_org_memberships
            (user_id, org_unit_id, membership_type, is_manager, joined_at)
        SELECT m.user_id, m.org_unit_id, 'primary', FALSE, NOW()
        FROM mapped_user_dept m
        WHERE m.org_unit_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM user_org_memberships x
              WHERE x.user_id = m.user_id AND x.org_unit_id = m.org_unit_id
          )
    """))

    # users.department / skills.department 保持展示名，与 org_unit_id 双写一致
    conn.execute(sa.text("""
        WITH mapped_user_dept AS (
            SELECT
                u.id AS user_id,
                COALESCE(ou_id.id, ou_name.id) AS org_unit_id
            FROM users u
            LEFT JOIN org_units ou_id
                   ON ou_id.id = u.department
            LEFT JOIN _dept_name_map ou_name
                   ON ou_name.name = u.department AND ou_name.cnt = 1
            WHERE u.department IS NOT NULL
              AND u.department <> ''
        )
        UPDATE users u
        SET department = ou.name
        FROM mapped_user_dept m
        JOIN org_units ou ON ou.id = m.org_unit_id
        WHERE u.id = m.user_id
    """))

    conn.execute(sa.text("""
        WITH mapped_skill_dept AS (
            SELECT
                s.id AS skill_id,
                COALESCE(ou_id.id, ou_name.id) AS org_unit_id
            FROM skills s
            LEFT JOIN org_units ou_id
                   ON ou_id.id = s.org_unit_id
            LEFT JOIN _dept_name_map ou_name
                   ON ou_name.name = s.department AND ou_name.cnt = 1
        )
        UPDATE skills s
        SET org_unit_id = m.org_unit_id,
            department = ou.name
        FROM mapped_skill_dept m
        JOIN org_units ou ON ou.id = m.org_unit_id
        WHERE s.id = m.skill_id
          AND m.org_unit_id IS NOT NULL
    """))

    # ========== Step 4: is_active=False 用户迁到 state=disabled ==========
    conn.execute(sa.text("UPDATE users SET state = 'disabled' WHERE is_active = FALSE"))

    # ========== Step 5: 全员 permissions_rev += 1 触发重登 ==========
    # （Codex 注意：如果希望无感迁移，跳过此步，老 session 继续有效到 8h 超时）
    conn.execute(sa.text("UPDATE users SET permissions_rev = permissions_rev + 1"))

    # ========== Step 6: 新增索引 ==========
    op.create_index("idx_user_state_role", "users", ["state", "role"])
    op.create_index("idx_user_permissions_rev", "users", ["permissions_rev"])
    op.create_index("idx_membership_manager", "user_org_memberships", ["user_id", "is_manager"])


def downgrade():
    op.drop_index("idx_membership_manager")
    op.drop_index("idx_user_permissions_rev")
    op.drop_index("idx_user_state_role")

    conn = op.get_bind()
    # 角色还原（有损，仅供应急）
    conn.execute(sa.text("UPDATE users SET role = 'admin' WHERE role = 'system_admin'"))
    conn.execute(sa.text("UPDATE users SET role = 'biz_owner' WHERE role = 'dept_admin'"))
    conn.execute(sa.text("""
        UPDATE users SET role = 'director', can_view_all = FALSE
        WHERE role = 'observer' AND can_view_all = TRUE
    """))
    conn.execute(sa.text("UPDATE users SET role = 'operator' WHERE role = 'observer'"))
    conn.execute(sa.text("UPDATE users SET role = 'ai_engineer' WHERE role = 'aibp'"))
    # state / permissions_rev 数据不可完全还原，这里只还原 is_active
    conn.execute(sa.text("UPDATE users SET is_active = (state <> 'disabled')"))

    op.drop_column("users", "permissions_rev")
    op.drop_column("users", "state")
```

### 4.6 后端权限工具函数（新增 `app/auth/access.py`）

新文件：

```python
"""v2 权限工具：基于 UserOrgMembership 的数据访问判定。"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.org.models import OrgUnit, UserOrgMembership


def _role_bucket(user: User) -> str:
    if user.role in ("system_admin", "admin"):
        return "system_admin"
    if user.role in ("dept_admin", "biz_owner"):
        return "dept_admin"
    if user.role in ("aibp", "ai_engineer"):
        return "aibp"
    return "observer"


async def get_managed_departments(db: AsyncSession, user: User) -> set[str]:
    """返回 user 是 manager 的所有部门 id（dept_admin 管辖范围）。"""
    if _role_bucket(user) != "dept_admin":
        return set()
    stmt = select(UserOrgMembership.org_unit_id).where(
        UserOrgMembership.user_id == user.id,
        UserOrgMembership.is_manager == True,  # noqa: E712
    )
    return set((await db.execute(stmt)).scalars().all())


async def get_related_departments(db: AsyncSession, user: User) -> set[str]:
    """返回 user 关联的所有部门 id（aibp 能访问的部门）。"""
    stmt = select(UserOrgMembership.org_unit_id).where(
        UserOrgMembership.user_id == user.id,
    )
    return set((await db.execute(stmt)).scalars().all())


async def _expand_department_subtree(
    db: AsyncSession,
    department_ids: set[str],
) -> set[str]:
    expanded = set(department_ids)
    for org_id in list(department_ids):
        org = await db.get(OrgUnit, org_id)
        if not org or not org.path:
            continue
        stmt = select(OrgUnit.id).where(
            or_(
                OrgUnit.path == org.path,
                OrgUnit.path.like(f"{org.path}/%"),
            )
        )
        expanded.update((await db.execute(stmt)).scalars().all())
    return expanded


async def get_read_scope_departments(db: AsyncSession, user: User) -> set[str] | None:
    """返回 user 读侧可见部门；None 表示不限（全部可见）。"""
    if _role_bucket(user) == "system_admin" or user.can_view_all:
        return None

    related = await get_related_departments(db, user)
    role_bucket = _role_bucket(user)

    if role_bucket == "dept_admin":
        managed = await get_managed_departments(db, user)
        return related | await _expand_department_subtree(db, managed)

    if role_bucket == "observer":
        return await _expand_department_subtree(db, related)

    return related


async def get_accessible_departments(db: AsyncSession, user: User) -> set[str] | None:
    """兼容旧命名；API 返回字段仍叫 accessible_departments。"""
    return await get_read_scope_departments(db, user)


async def can_read_department(db: AsyncSession, user: User, department: str) -> bool:
    """判定 user 是否能读取指定 department_id 的数据。"""
    scoped = await get_read_scope_departments(db, user)
    if scoped is None:
        return True
    return department in scoped


async def can_access_department(db: AsyncSession, user: User, department: str) -> bool:
    """兼容旧命名；等价于 can_read_department，新代码优先用 can_read_department。"""
    return await can_read_department(db, user, department)


async def can_manage_department(db: AsyncSession, user: User, department: str) -> bool:
    """判定 user 是否对指定 department_id 有管理权（仅直接管辖或 system_admin）。"""
    if _role_bucket(user) == "system_admin":
        return True
    if _role_bucket(user) != "dept_admin":
        return False
    managed = await get_managed_departments(db, user)
    return department in managed


async def bump_permissions_rev(db: AsyncSession, user_id: str) -> None:
    """任何权限变更后调用，使其所有旧 session 立刻失效。"""
    from sqlalchemy import update
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(permissions_rev=User.permissions_rev + 1)
    )
    await db.flush()
```

约束补充：
- `get_read_scope_departments` / `can_read_department` 是**读侧专用 helper**，解决 `dept_admin` “关联部门直读 + 管辖部门子树”这一矩阵语义。
- `can_manage_department` 仍只认 `is_manager=True` 的直接部门，**不会**因为子树展开而放大写权限。
- `get_accessible_departments` / `can_access_department` 仅保留为兼容别名；新代码、文档和 code review 一律优先使用 `read_scope` 命名，避免与“管理范围”混淆。

---

## 5. 能力矩阵

### 5.1 Skill

| 操作 | system_admin | dept_admin | aibp | observer |
|---|---|---|---|---|
| 列表 / 详情 / Hall | 全部 | 关联部门直读；其管辖部门展开到下级子树 | 关联部门 + 对象级成员 | 关联部门 + 子树；`can_view_all` 时全公司 |
| 新建 / Fork | ✅ | 管辖部门内 ✅ | 关联部门内 ✅ | ❌ |
| 编辑 | ✅ | 管辖部门内 ✅ | 关联部门内；受 `SkillMember` 约束 | ❌ |
| 发布 / 删除 / 管成员 | ✅ | 管辖部门内 ✅ | 仅 owner | ❌ |
| 执行 / 沙箱测试 | ✅ | 管辖部门内 ✅ | 关联部门内 ✅ | ❌ |

### 5.2 Playbook

| 操作 | system_admin | dept_admin | aibp | observer |
|---|---|---|---|---|
| 列表 / 详情 | 全部 | 关联部门直读；其管辖部门展开到下级子树 | 关联部门 | 关联部门 + 子树；`can_view_all` 时全公司 |
| 新建 | ✅ | 管辖部门内 ✅ | 关联部门内 ✅（创建后 `owner_user_id=自己`） | ❌ |
| 编辑 | ✅ | 管辖部门内任何 Playbook | 仅自己 owner 的 Playbook | ❌ |
| 提审 / 发布 | ✅ | 管辖部门内 ✅ | 仅自己 owner 的 Playbook | ❌ |
| 手动运行 | ✅ | 管辖部门内 ✅ | owner 或显式允许的 aibp | ❌ |

约束：
- Playbook 没有 `PlaybookMember` 的本版里，`owner_user_id` 是唯一对象级写权限锚点。
- `department_id` 是权限真源，`department` 只做展示和兼容。

### 5.3 Review / Approval / Todo

| 操作 | system_admin | dept_admin | aibp | observer |
|---|---|---|---|---|
| 提交审核 | ✅ | 管辖部门内 ✅ | 自己可编辑资源 ✅ | ❌ |
| 审批 / 驳回 | ✅ | 仅作为目标部门候选 approver 时 ✅ | ❌ | ❌ |
| 查看 review / approval 详情 | 全部 | 本人参与或管辖范围 | 本人提交 / 被指派 / 有权限资源 | 本人被指派 / 只读参与 |
| 处理 dispatch todo / 回执 | ✅ | ✅ | ✅ | 仅本人 assignee 的 dispatch / 回执 ✅ |
| 转派 / 改审批链 | ✅ | 管辖部门内 ✅ | ❌ | ❌ |

兼容约束：
- `Review.reviewer`、`ApprovalStep.approver_id` 在 pending 阶段允许为空；它们记录**最终实际处理人**，不是候选人全集。
- 多候选人 fan-out 通过多条 `AITodo` / notification 实现；任一候选人处理后，其余 todo 置 `superseded`。
- `dept_admin` 的子树展开只用于资产读侧 / 看板读侧，不自动扩大审批归属；审批候选仍只按 §6.2 的目标部门 / 上级部门 resolver 解析。

### 5.4 Dashboard / Tasktree / Datasource / Execution

| 操作 | system_admin | dept_admin | aibp | observer |
|---|---|---|---|---|
| Dashboard / Tasktree 查看 | 全部 | 关联部门直读；其管辖部门展开到下级子树 | 关联部门 | 关联部门 + 子树；`can_view_all` 时全公司 |
| Datasource 列表 / 详情 | 全部 | 关联部门直读；其管辖部门展开到下级子树 | 关联部门 | 关联部门 + 子树只读 |
| Datasource 新建 / 编辑 | ✅ | 管辖部门内 ✅ | 关联部门内 ✅ | ❌ |
| Execution 运行记录查看 | 全部 | 关联部门直读；其管辖部门展开到下级子树 | 关联部门 | 关联部门 + 子树只读 |
| WebSocket / 实时流 | active 且有对应资源访问权 | 同左 | 同左 | 仅只读频道；禁止触发执行 |

### 5.5 Admin / Org / Audit

| 操作 | system_admin | dept_admin | aibp | observer |
|---|---|---|---|---|
| 用户管理 | 全部 | 仅本管辖部门成员；不可提拔 dept_admin | ❌ | ❌ |
| `pending` 激活 | 任意 | 仅本管辖部门候选 | ❌ | ❌ |
| org_units 管理 / 钉钉同步 / merge_org | ✅ | ❌ | ❌ | ❌ |
| Audit Log | ✅ | ❌ | ❌ | ❌ |
| `/metrics` 非本地访问 | ✅ | ❌ | ❌ | ❌ |

### 5.6 旧模块收口原则

- 旧模块仍可读 `User.department` / `Skill.department` 展示字段，但**不得**再把它们当权限真源写回数据库。
- 任何新代码禁止新增 `if user.role in (...)` 硬编码；统一调用 `app/auth/access.py`。
- 旧模块若短期内无法彻底迁移，至少要做到：
  - 读范围：用 `get_read_scope_departments` / `can_read_department`（API 字段名仍叫 `accessible_departments`）
  - 管理范围：用 `managed_departments`
  - 资源真源：优先 `org_unit_id` / `department_id`

### 5.7 判定优先级（重要）

当多个权限规则叠加时，**按以下优先级**判定：

1. **state 检查**：`state != 'active'` → 直接拒绝（除挂起页 / 登录 / 改密码等白名单路由）
2. **角色白名单**：`require_role(*roles)` 不过 → 403
3. **部门访问**：读操作用 `can_read_department(user, resource.org_unit_id / department_id)`；写 / 管理操作用 `can_manage_department(...)` 或 owner 约束。不通过 → 403（按 §4.6 函数）
4. **对象级权限**（仅 Skill 写操作）：`SkillMember` 存在且 `user` 非成员或成员 role 不符 → 403
5. **通过** → 放行

Codex 注意：这 5 步是**短路**顺序，任一步未过立即拒绝。

---

## 6. 审批链规则

### 6.1 触发条件

| approval_level | 流程 |
|---|------|
| 0 | 自动通过，无审批 |
| 1 | L1：Skill / Playbook 当前部门的 dept_admin |
| 2 | L1 + L2：L1 同上；L2 = 上级部门的 dept_admin |
| 3+ | 本版明确拒绝（返回 `PARAM_INVALID`），只实现到 L2 |

**关键**：审批不再升到 system_admin。

### 6.2 approver_resolver 改造

**文件**：`app/approval/approver_resolver.py:146-161`

**当前 `_resolve_role` 函数**（第 146-161 行）：
```python
async def _resolve_role(db: AsyncSession, chain_step: dict) -> list[str]:
    """role 类型：查找指定角色的活跃用户。"""
    role = chain_step.get("role")
    if not role:
        raise AppError("PARAM_INVALID", 400, {"detail": "role 类型需要 role 字段"})

    stmt = select(User.id).where(
        User.role == role,
        User.is_active == True,  # noqa: E712
    )
    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"没有找到角色为 {role} 的活跃用户",
        })
    return list(result)
```

**目标代码**：

```python
async def _resolve_role(db: AsyncSession, chain_step: dict) -> list[str]:
    """role 类型：v2 改造为"找指定部门的 dept_admin"或兜底全局角色。"""
    role = chain_step.get("role")
    if not role:
        raise AppError("PARAM_INVALID", 400, {"detail": "role 类型需要 role 字段"})

    # v2 新语义：role=dept_admin 时必须配合 department 过滤
    if role == "dept_admin":
        department = chain_step.get("department")
        if not department:
            raise AppError("PARAM_INVALID", 400, {"detail": "dept_admin 审批需指定 department"})
        stmt = (
            select(User.id)
            .join(UserOrgMembership, UserOrgMembership.user_id == User.id)
            .where(
                User.role == "dept_admin",
                User.state == "active",
                UserOrgMembership.org_unit_id == department,
                UserOrgMembership.is_manager == True,  # noqa: E712
            )
        )
    else:
        # 向后兼容：其他全局 role 直接查
        stmt = select(User.id).where(
            User.role == role,
            User.state == "active",
        )

    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"没有找到可用的审批人（role={role}, department={chain_step.get('department')}）",
        })
    return list(result)
```

**同时补充 `_resolve_dept_admin` 独立策略**（在 `approver_resolver.py` 文件末尾追加）：

```python
async def _resolve_dept_admin(db: AsyncSession, chain_step: dict, context: dict) -> list[str]:
    """
    dept_admin 类型：按 context.skill_department 或 chain_step.department 找管辖该部门的 dept_admin。
    用法：
      - L1: {"type": "dept_admin"}                    # 取 context 里 skill 当前部门
      - L2: {"type": "dept_admin", "level": 2}        # 上级部门
    """
    level = chain_step.get("level", 1)
    department = chain_step.get("department") or context.get("skill_department")
    if not department:
        raise AppError("PARAM_INVALID", 400, {"detail": "dept_admin 类型需要 department 或 context.skill_department"})

    # 如果 level > 1，沿 parent 往上走
    current = department
    for _ in range(level - 1):
        org = await db.get(OrgUnit, current)
        if not org or not org.parent_id:
            raise AppError("APPROVER_NOT_FOUND", 400, {
                "detail": f"部门 {department} 上级 level={level} 超出根部门"
            })
        current = org.parent_id

    stmt = (
        select(User.id)
        .join(UserOrgMembership, UserOrgMembership.user_id == User.id)
        .where(
            User.role == "dept_admin",
            User.state == "active",
            UserOrgMembership.org_unit_id == current,
            UserOrgMembership.is_manager == True,  # noqa: E712
        )
    )
    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"部门 {current} 没有活跃的 dept_admin",
        })
    return list(result)
```

**在 `resolve_approvers` 里注册新策略**（文件 `app/approval/approver_resolver.py:60` 处 elif 链追加）：

```python
elif step_type == "dept_admin":
    return await _resolve_dept_admin(db, chain_step, context)
```

### 6.3 多 dept_admin 分配规则

一个部门有 N 个 dept_admin 时：
- 向所有人发钉钉通知（AITodo 分别创建 N 条，或一条 AITodo 多个 assignee — **本版选 N 条**）
- **任一人审批即完成**，其余 todo 状态变 `superseded`
- 审批通过的 dept_admin 记入 `ApprovalStep.approver_id`

实现位置：`app/approval/service.py` 的 todo 创建逻辑，需要循环 approvers 逐条创建。

### 6.3.1 与现有单 reviewer / 单 approver_id 模型的兼容实现

- `Review.reviewer`、`ApprovalStep.approver_id` 在 `pending` 阶段允许为 `NULL`。
- 创建 review / approval 时：
  - 先调用 `resolve_approvers()` 拿到候选人列表
  - 给每个候选人 fan-out 一条 `AITodo`
  - 不在创建时把 `reviewer` / `approver_id` 固定为单人
- 任一候选人操作成功后：
  - 将赢者写回 `Review.reviewer` 或 `ApprovalStep.approver_id`
  - 其余 todo 统一改 `superseded`
- `list_reviews(reviewer=me)`、审批详情页、权限校验都必须兼容“pending 时 reviewer 为空，靠 todo assignee 判定”的场景。
- `app/approval/service.py:create_instance()` 必须真正接入 `resolve_approvers()`；仅改 resolver 而不改 runtime 不算完成。
- 若外部钉钉审批只支持单 approver，本版以**内部 fan-out 结果为准**：
  - 系统内给全部候选人建 todo
  - 外部审批实例只同步首位可用 approver，作为通知渠道，不作为唯一权限真源

### 6.4 跨部门 Playbook 审批

- Playbook YAML frontmatter 增加字段：
  - `department_id`：权限真源；后端保存时自动填创建人的主部门 org_unit_id
  - `department`：展示名；后端按 `org_units.name` 双写
  - `creator_user_id`：创建时写入，之后不改
  - `owner_user_id`：默认 = creator；禁用 / 转交时可改
- 前端直传的 `department` / `department_id` 一律不可信；`save_playbook()` 必须由后端解析出的当前用户主部门兜底并覆盖。
- 审批只走 `department_id` 对应部门的 dept_admin
- Playbook 引用的跨部门 Skill **已经过它自己的审批**，不重审
- `merge_org` / `rename_org` / 禁用转交时，Playbook frontmatter 必须同步改写 `department_id` / `department` / `owner_user_id`

### 6.5 审批中发生权限变更

- 审批中 approver 被降权 / 禁用 → 审批链重新解析，找其他 dept_admin
- 审批中 requester 被禁用 → 审批作废（`ApprovalInstance.status = cancelled`），通知原 dept_admin
- 实现位置：`app/users/service.py:disable_user` 里扫描 + 重新分派

---

## 7. 对象级权限 SkillMember

### 7.1 opt-in 机制代码位置

**文件**：`app/skills/access.py:49-100`

**需要改造**为：

```python
async def check_skill_write_permission(
    db: AsyncSession,
    user: User,
    skill_id: str,
    action: str,  # 'edit' / 'publish' / 'delete' / 'manage_members'
) -> bool:
    """v2 统一 Skill 写权限判定。"""
    from app.skills.models import Skill
    from app.skills.members import SkillMember
    from app.auth.access import can_read_department, can_manage_department

    # state 不为 active 直接拒绝（上游 get_current_user 也拦了，此处兜底）
    if user.state != "active":
        return False

    # system_admin 全通
    if user.role == "system_admin":
        return True

    skill = await db.get(Skill, skill_id)
    if not skill:
        return False

    # dept_admin 管辖部门内全通
    if user.role == "dept_admin":
        return await can_manage_department(db, user, skill.org_unit_id)

    # aibp 及以下，先看部门关联
    if user.role != "aibp":
        return False  # observer 不能写
    if not await can_read_department(db, user, skill.org_unit_id):
        return False

    # 检查 SkillMember 受控模式
    stmt = select(SkillMember).where(SkillMember.skill_id == skill_id)
    members = (await db.execute(stmt)).scalars().all()

    if not members:
        # 非受控模式：部门内 aibp 都能 edit，但 publish/delete/manage_members 仍要求 dept_admin
        if action in ("publish", "delete", "manage_members"):
            return False
        return True  # edit OK

    # 受控模式：查该用户的 SkillMember.role
    user_member = next((m for m in members if m.user_id == user.id), None)
    if not user_member:
        return False

    if action == "edit":
        return user_member.role in ("owner", "editor")
    if action == "publish":
        return user_member.role == "owner"
    if action == "delete":
        return user_member.role == "owner"
    if action == "manage_members":
        return user_member.role == "owner"
    return False
```

### 7.2 Skill Fork 权限继承

**文件**：`app/skills/template_market.py:fork_template()` 及类似 Fork 入口

**规则**：
- Fork 后新 Skill 的 `skill_members` 只有一条：`(skill_id=new_id, user_id=fork_发起人, role='owner')`
- 原 Skill 的 SkillMember **不带过来**
- 原作者如需协助 → 新 owner 手动加

---

## 8. 钉钉集成

### 8.1 OAuth 首登改造

**文件**：`app/auth/dingtalk_oauth.py:153-167`

**当前代码**（153-167 行）：
```python
if not user:
    # 自动创建新用户（operator角色，当前实现直接放行为活跃账号）
    user = User(
        id=f"dt_{dingtalk_user_id}",
        username=f"dingtalk_{dingtalk_user_id}",
        name=name,
        role="operator",
        dingtalk_user_id=dingtalk_user_id,
        dingtalk_union_id=union_id,
        avatar_url=avatar,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    logger.info(f"钉钉自动创建用户: {name} ({dingtalk_user_id})")
```

**目标代码**：
```python
if not user:
    # v2: 首登创建为 observer + pending，等待 dept_admin 激活
    user = User(
        id=f"dt_{dingtalk_user_id}",
        username=f"dingtalk_{dingtalk_user_id}",
        name=name,
        role="observer",
        state="pending",           # v2 新增
        permissions_rev=0,         # v2 新增
        dingtalk_user_id=dingtalk_user_id,
        dingtalk_union_id=union_id,
        avatar_url=avatar,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    logger.info(f"钉钉自动创建用户（pending）: {name} ({dingtalk_user_id})")
```

### 8.2 钉钉同步改造

**文件**：`app/users/service.py:251-264`

**当前代码**（251-264 行）：
```python
# 创建新用户（默认 operator，当前实现直接写入活跃账号）
new_user = User(
    id=f"dt_{dingtalk_uid}",
    username=dingtalk_uid,
    name=name,
    role="operator",
    dingtalk_user_id=dingtalk_uid,
    dingtalk_union_id=u.get("unionid", ""),
    avatar_url=u.get("avatar", ""),
    is_active=True,
    must_change_password=False,  # 钉钉用户不用密码
)
db.add(new_user)
synced += 1
```

**目标代码**：
```python
# v2: 钉钉同步创建默认 observer + pending
new_user = User(
    id=f"dt_{dingtalk_uid}",
    username=dingtalk_uid,
    name=name,
    role="observer",
    state="pending",
    permissions_rev=0,
    dingtalk_user_id=dingtalk_uid,
    dingtalk_union_id=u.get("unionid", ""),
    avatar_url=u.get("avatar", ""),
    is_active=True,
    must_change_password=False,
)
db.add(new_user)
synced += 1
```

### 8.3 钉钉 is_manager 同步

**文件**：`app/org/service.py:222-289`（钉钉同步主流程）

**关键规则**：
- 钉钉同步时拿到部门每个成员的 `is_manager` 字段 → 写入 `UserOrgMembership.is_manager`
- **不自动**修改 `User.role`（即使 is_manager=True 也不自动变 dept_admin）
- system_admin 后台的"候选 dept_admin"列表从 `UserOrgMembership.is_manager=True AND User.role != 'dept_admin'` 查

### 8.4 挂起页 `/pending`

前端新增路由与页面（详见 §15.3 + §17.1）。

---

## 9. 用户生命周期

### 9.1 创建路径

```
钉钉 OAuth 首登 → state=pending, role=observer → dept_admin 激活
本地账密 scripts/manage_users.py → state=active, role 由 admin 指定
钉钉组织同步 → state=pending, role=observer → dept_admin 激活
```

### 9.2 激活（pending → active）

**API**：`POST /api/users/{user_id}/activate`（详见 §17.2）

**权限**：
- system_admin：可激活任何 pending 用户
- dept_admin：只能激活"钉钉信息里部门属于自己管辖范围"的 pending 用户

**Body schema**：
```json
{
  "role": "aibp" | "observer",           // dept_admin 不能选 dept_admin
  "department_id": "org-unit-id",        // 加入哪个部门（dept_admin 只能选自己管辖的）
  "is_manager": false,                   // 通常 false
  "can_view_all": false                  // 通常 false，仅 system_admin 可设 true
}
```

**后端实现**（新增函数 `app/users/service.py:activate_pending_user`）：
```python
async def activate_pending_user(
    db: AsyncSession,
    user_id: str,
    new_role: str,
    org_unit_id: str,
    *,
    is_manager: bool = False,
    can_view_all: bool = False,
    operator: User,
) -> dict:
    from app.auth.access import (
        bump_permissions_rev,
        can_manage_department,
        get_related_departments,
    )
    from app.org.models import OrgUnit

    target = await db.get(User, user_id)
    if not target:
        raise AppError("AUTH_USER_NOT_FOUND", 404)
    if target.state != "pending":
        raise AppError("USER_NOT_PENDING", 400)

    org = await db.get(OrgUnit, org_unit_id)
    if not org:
        raise AppError("ORG_UNIT_NOT_FOUND", 404)

    pending_orgs = await get_related_departments(db, target)

    # 权限：system_admin 全通，dept_admin 只能激活自己管辖部门的
    if operator.role == "dept_admin":
        if not await can_manage_department(db, operator, org_unit_id):
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        if new_role not in ("aibp", "observer"):
            raise AppError("AUTH_PERMISSION_DENIED", 403, {
                "detail": "dept_admin 只能激活为 aibp 或 observer",
            })
        if can_view_all:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        # 防止 dept_admin 把候选人“拽”进任意部门：
        # 若钉钉同步已给候选人挂了部门 membership，则只能在这些部门内激活
        if pending_orgs and org_unit_id not in pending_orgs:
            raise AppError("AUTH_PERMISSION_DENIED", 403, {
                "detail": "dept_admin 只能在候选人的钉钉所属部门范围内激活",
            })

    if new_role not in ("system_admin", "dept_admin", "aibp", "observer"):
        raise AppError("PARAM_INVALID", 400)

    target.role = new_role
    target.state = "active"
    target.can_view_all = can_view_all
    target.department = org.name  # 主部门展示字段（兼容字段存展示名，不存 org_unit_id）
    await db.flush()

    # 加 UserOrgMembership
    from app.org.models import UserOrgMembership
    existing = await db.execute(
        select(UserOrgMembership).where(
            UserOrgMembership.user_id == user_id,
            UserOrgMembership.org_unit_id == org_unit_id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(UserOrgMembership(
            user_id=user_id, org_unit_id=org_unit_id,
            membership_type="primary", is_manager=is_manager,
        ))

    await bump_permissions_rev(db, user_id)
    await audit.log(
        operator.id, "user.activate", "user", user_id,
        detail={"role": new_role, "department_id": org_unit_id, "department": org.name, "is_manager": is_manager},
    )
    return {"id": user_id, "state": "active", "role": new_role}
```

### 9.3 禁用（active → disabled）带资产转交

**API**：`POST /api/users/{user_id}/disable`（替代当前 DELETE）

**Body schema**：
```json
{
  "successor_user_id": "user-xxx",       // 必填，接手人
  "running_executions": "wait" | "abort" // 正在跑的 execution 怎么处理
}
```

**后端实现**（改造 `app/users/service.py:disable_user`）：

```python
async def disable_user(
    db: AsyncSession,
    user_id: str,
    successor_user_id: str,
    running_executions: str = "wait",   # wait | abort
    *,
    operator: User,
) -> dict:
    from app.auth.access import can_manage_department, bump_permissions_rev
    from app.skills.members import SkillMember
    from app.todos.models import AITodo
    from app.execution.models import ExecutionRun

    target = await db.get(User, user_id)
    if not target:
        raise AppError("AUTH_USER_NOT_FOUND", 404)
    if successor_user_id == user_id:
        raise AppError("SUCCESSOR_INVALID", 400, {"detail": "接手人不能是本人"})

    successor = await db.get(User, successor_user_id)
    if not successor or successor.state != "active":
        raise AppError("SUCCESSOR_INVALID", 400)

    # 硬锁：最后一个 system_admin 不可禁用
    if target.role == "system_admin":
        count_stmt = select(func.count()).select_from(User).where(
            User.role == "system_admin",
            User.state == "active",
            User.id != user_id,
        )
        remaining = (await db.execute(count_stmt)).scalar() or 0
        if remaining == 0:
            raise AppError("LAST_SYSTEM_ADMIN", 403, {"detail": "不能禁用最后一个系统管理员"})

    # 权限：system_admin 全通，dept_admin 只能禁用管辖部门内成员
    if operator.role == "dept_admin":
        from app.auth.access import get_related_departments
        target_depts = await get_related_departments(db, target)
        successor_depts = await get_related_departments(db, successor)
        managed = await get_managed_departments(db, operator)
        if not target_depts & managed:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        if not successor_depts & target_depts & managed:
            raise AppError("AUTH_PERMISSION_DENIED", 403, {
                "detail": "dept_admin 只能把资产转交给同管辖范围内的 active 成员",
            })

    # 1. Skill owner 转交
    from sqlalchemy import update
    await db.execute(
        update(SkillMember)
        .where(SkillMember.user_id == user_id, SkillMember.role == "owner")
        .values(user_id=successor_user_id)
    )

    # 2. 未决 AITodo 转派
    await db.execute(
        update(AITodo)
        .where(AITodo.assignee == user_id, AITodo.decided_at.is_(None))
        .values(assignee=successor_user_id)
    )

    # 3. 正在跑的 ExecutionRun
    if running_executions == "abort":
        # 标记为 cancelled；具体中止逻辑由 execution_service 异步处理
        await db.execute(
            update(ExecutionRun)
            .where(
                ExecutionRun.triggered_by == f"manual:{user_id}",
                ExecutionRun.status == "running",
            )
            .values(status="cancelled")
        )
    # wait 分支什么都不做，让它跑完

    # 4. 禁用
    target.state = "disabled"
    target.is_active = False
    target.updated_at = datetime.utcnow()
    await db.flush()

    await bump_permissions_rev(db, user_id)
    await audit.log(
        operator.id, "user.disable", "user", user_id,
        detail={"successor": successor_user_id, "running_executions": running_executions},
    )
    return {"id": user_id, "state": "disabled"}
```

### 9.4 重启用 `disabled → active`

**API**：`POST /api/users/{user_id}/reactivate`

**权限**：system_admin 全通；dept_admin 只能重启管辖部门内成员。

**实现**：改 `state='active'`，不改 role，`bump_permissions_rev`。

### 9.5 不提供硬删除

所有 "删除用户" 操作都走 `state=disabled`。硬删除只在数据库维护脚本由 system_admin 手工执行。

### 9.6 最后一个 system_admin 硬锁

所有可能"减少 system_admin 数量"的操作前检查：

```python
async def _ensure_not_last_system_admin(db: AsyncSession, user_id: str, new_role: str | None = None):
    """检查是否是最后一个 system_admin。适用：disable、delete、改 role 等。"""
    target = await db.get(User, user_id)
    if not target or target.role != "system_admin":
        return
    # new_role 若仍为 system_admin，允许
    if new_role == "system_admin":
        return
    count = (await db.execute(
        select(func.count()).select_from(User).where(
            User.role == "system_admin",
            User.state == "active",
            User.id != user_id,
        )
    )).scalar() or 0
    if count == 0:
        raise AppError("LAST_SYSTEM_ADMIN", 403)
```

调用点：`disable_user`、`update_user`（改 role 时）；若保留删除接口，也必须同样调用。

---

## 10. Session 立刻失效机制

### 10.1 token 结构变更

**文件**：`app/auth/dependencies.py:22-32`

**当前代码**（22-32 行）：
```python
def create_session_token(user_id: str) -> str:
    """创建签名的session token"""
    return _signer.dumps(user_id)


def verify_session_token(token: str) -> str | None:
    """验证session token，返回user_id或None"""
    try:
        return _signer.loads(token, max_age=settings.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
```

**目标代码**：

```python
def create_session_token(user_id: str, rev: int) -> str:
    """创建签名 session token（v2 含 permissions_rev 快照）。"""
    return _signer.dumps({"uid": user_id, "rev": rev})


def verify_session_token(token: str) -> dict | None:
    """验证 session token，返回 {uid, rev} 或 None。"""
    try:
        payload = _signer.loads(token, max_age=settings.SESSION_MAX_AGE)
        if isinstance(payload, dict) and "uid" in payload and "rev" in payload:
            return payload
        # 兼容老 token（dumps(user_id)）只用于一次切流窗口；切流完成即删掉此分支
        if isinstance(payload, str):
            return {"uid": payload, "rev": -1}  # -1 表示 legacy
        return None
    except (BadSignature, SignatureExpired):
        return None
```

### 10.2 get_current_user 增加 rev 比对

**文件**：`app/auth/dependencies.py:35-56`

**目标代码**：

```python
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 cookie 获取当前登录用户（v2: 含 permissions_rev 与 state 检查）。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError("AUTH_REQUIRED", 401)

    payload = verify_session_token(token)
    if not payload:
        raise AppError("AUTH_SESSION_EXPIRED", 401)

    user_id = payload["uid"]
    token_rev = payload["rev"]

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AppError("AUTH_SESSION_EXPIRED", 401)

    # v2: rev 比对（legacy -1 仅用于一次切流窗口；runbook 必须包含强制重登收口动作）
    if token_rev != -1 and token_rev != user.permissions_rev:
        raise AppError("AUTH_SESSION_EXPIRED", 401, {"reason": "permissions_changed"})

    # v2: state 检查（pending 允许通过，由路由层决定是否跳 /pending；disabled 拒绝）
    if user.state == "disabled":
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)

    # 保留旧 is_active 兜底，只作为本次切流的兼容判断
    if not user.is_active:
        raise AppError("AUTH_ACCOUNT_DISABLED", 403)

    return user
```

### 10.2.1 `pending` 白名单与 active-only 依赖

- `get_current_user` 只做 session + user lookup + `disabled` 拦截，不承担“业务可用”判断。
- 新增 `require_state_active()`；所有业务 HTTP 路由在 v2 中统一改为：
  - 认证：`Depends(get_current_user)`
  - 业务可用：`Depends(require_state_active())`
- `pending` 用户允许访问的白名单仅限：
  - `GET /api/auth/me`
  - `POST /api/auth/logout`
  - `POST /api/auth/change-password`
  - 登录 / 钉钉 OAuth 回调等认证相关公共路由
  - `/pending` 页自身依赖的轻量查询接口
- 所有业务 WebSocket 也需要 active-only helper；不能只改 HTTP 依赖。

### 10.3 登录时签发 token 带 rev

**文件**：`app/auth/router.py:62`

**当前**：
```python
token = create_session_token(user.id)
```

**目标**：
```python
token = create_session_token(user.id, user.permissions_rev)
```

同样改 `app/auth/router.py:176`（钉钉回调路径）。

### 10.4 WebSocket 鉴权

**文件**：`app/auth/dependencies.py:68-102`

目标：在 `get_current_user_ws` 中同样做 rev 比对，不匹配 `close(code=4401)`。

### 10.4.1 所有 token consumer 同步改造清单

以下位置都直接调用了 `verify_session_token()`，必须一起升级到 `{uid, rev}` 语义：

- `app/common/ws_auth.py`
- `app/playbooks/live.py`
- `app/execution/ws.py`
- `app/common/metrics.py`

统一要求：

- rev 不匹配时拒绝连接 / 请求
- `disabled` 一律拒绝
- `pending` 仅允许白名单端点；实时执行、Playbook live、沙箱执行等全都必须要求 `active`
- `metrics` 的远程访问角色从旧 `admin` 改为 `system_admin`

### 10.5 permissions_rev 变更触发点（清单）

Codex 必须在以下位置调用 `bump_permissions_rev(db, user_id)`：

| 位置 | 文件 | 场景 |
|---|---|---|
| `update_user` 改 role | `app/users/service.py` | role 变更 |
| `update_user` 改 can_view_all | `app/users/service.py` | 跨部门能力变更 |
| `disable_user` | `app/users/service.py` | 禁用 |
| `reactivate_user`（新增）| `app/users/service.py` | 重启用 |
| `activate_pending_user`（新增）| `app/users/service.py` | 从 pending 激活 |
| `add_org_membership` | `app/org/service.py` | 加入新部门（尤其 is_manager）|
| `remove_org_membership` | `app/org/service.py` | 离开部门 |
| `update_org_membership` is_manager | `app/org/service.py` | is_manager 标志变更 |
| `merge_org` | `app/org/service.py` | 组织合并（涉及用户批量 bump）|

---

## 11. 组织变更

### 11.1 合并（A → B）

**API**：`POST /api/org/{source_id}/merge?target_id={target_id}`（仅 system_admin）

**后端实现**（新增 `app/org/service.py:merge_org`）：

```python
async def merge_org(
    db: AsyncSession,
    source_id: str,
    target_id: str,
    *,
    operator: User,
) -> dict:
    if operator.role != "system_admin":
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if source_id == target_id:
        raise AppError("PARAM_INVALID", 400)

    source = await db.get(OrgUnit, source_id)
    target = await db.get(OrgUnit, target_id)
    if not source or not target:
        raise AppError("ORG_UNIT_NOT_FOUND", 404)

    # 1. UserOrgMembership: 把 A 的成员迁到 B（合并重复项）
    from sqlalchemy import delete, func, select, update
    from app.skills.models import Skill
    from app.execution.models import OpenClawInstance
    from app.todos.models import AITodo
    from app.auth.access import bump_permissions_rev

    # 1a. 收集涉及到的 user_id（事后 bump_rev）
    affected_users = (await db.execute(
        select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id == source_id)
    )).scalars().all()
    affected_skills = (await db.execute(
        select(func.count()).select_from(Skill).where(Skill.org_unit_id == source_id)
    )).scalar() or 0
    affected_todos = 0  # 实际实现中按被重新解析 / 转派的 todo 数统计

    # 1b. 合并 membership（A → B）；若用户已经在 B 里，则保留 B 的记录，删掉 A 的
    existing_in_target = (await db.execute(
        select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id == target_id)
    )).scalars().all()
    await db.execute(
        delete(UserOrgMembership)
        .where(
            UserOrgMembership.org_unit_id == source_id,
            UserOrgMembership.user_id.in_(existing_in_target),
        )
    )
    await db.execute(
        update(UserOrgMembership)
        .where(UserOrgMembership.org_unit_id == source_id)
        .values(org_unit_id=target_id)
    )

    # 2. Skill 真源与兼容字段迁移
    await db.execute(
        update(Skill)
        .where(Skill.org_unit_id == source_id)
        .values(org_unit_id=target_id, department=target.name)
    )

    # 3. OpenClawInstance.department 兼容字段迁移（仍存展示名）
    await db.execute(
        update(OpenClawInstance)
        .where(OpenClawInstance.department == source.name)
        .values(department=target.name)
    )

    # 4. 未决 AITodo / Approval 立即重新解析审批人（依赖 approver_resolver）
    # 要求：在本次 merge 事务内完成重解析，不允许留给异步补偿任务

    # 5. User.department 字段（主部门展示名）迁移
    await db.execute(
        update(User).where(User.department == source.name).values(department=target.name)
    )

    # 6. Playbook frontmatter 改写（department_id + department 双写）
    from app.playbooks import service as playbook_service
    affected_playbooks = await playbook_service.rewrite_department_bulk(
        source_id=source_id,
        target_id=target_id,
        source_name=source.name,
        target_name=target.name,
    )

    # 7. 源 OrgUnit 软删除（标记 archived；保留历史）
    # 如果 OrgUnit 没有 is_archived 字段，临时做法：把 name 加后缀 [已合并]
    if hasattr(OrgUnit, "is_archived"):
        source.is_archived = True
    else:
        source.name = f"{source.name} [已合并到 {target.name}]"

    # 8. 所有涉及用户 bump_rev（session 踢）
    for uid in affected_users:
        await bump_permissions_rev(db, uid)

    await db.flush()
    await audit.log(
        operator.id, "org.merge", "org_unit", source_id,
        detail={"target_id": target_id, "affected_users": len(affected_users)},
    )
    return {
        "source_id": source_id,
        "target_id": target_id,
        "affected_users": len(affected_users),
        "affected_skills": affected_skills,
        "affected_playbooks": affected_playbooks,
        "affected_todos": affected_todos,
    }
```

**重要**：合并是**破坏性**操作，必须：
1. 前端弹出确认弹窗，列出将迁移的 Skill/用户/todo 数量
2. operator 输入目标部门名做二次确认（仿 GitHub 删 repo）

### 11.2 拆分 / 改名

- **拆分**：不自动，由 system_admin 手动新建 OrgUnit + 逐个迁 Skill/User
- **改名**：`PUT /api/org/{id}`，只改 `name`，id 不变，所有 FK 不受影响

---

## 12. 审计与 Service ID

### 12.1 Service ID 规范

`app/common/audit.py` 中 `"system"` / `"scheduler"` / `"unknown"` 统一替换：

| 旧值 | 新值 | 触发场景 |
|---|---|---|
| `"system"` | `svc_scheduler` | APScheduler 定时任务 |
| | `svc_openclaw` | AIClaw bridge 设备回写 |
| | `svc_mcp` | MCP server 调用 |
| | `svc_sync` | 钉钉组织同步后台任务 |
| `"unknown"` | `svc_anonymous` | 登录失败前的匿名访问 |

### 12.2 查询权限

- 仅 system_admin 可查 `/admin/audit`
- dept_admin 看不到（即使是本部门操作）

### 12.3 审计字段扩展（本次一起做）

```python
# audit_log 表增加 role_snapshot 字段，记录当时用户的 role
role_snapshot = Column(String(20), nullable=True)
```

用于"某角色做了什么"的历史追溯；本版 migration 一并加上。

---

## 13. API Token 策略

**决策**：不开放。

- Skill 脚本所有数据交互统一走 OpenClaw（设备 token 认证）
- 外部系统访问走 system_admin 手工导出 CSV
- 明确不提供任何 Token 签发接口；如需独立服务主体，视为新 RFC，不能占用本版实现口径

---

## 14. 老角色迁移映射表

| 老 role | 新 role | 附加字段 | 备注 |
|---|---|---|---|
| `admin` | `system_admin` | — | 1:1 |
| `ai_engineer` | `aibp` | — | 保守迁；system_admin 后台复核手工提为 `dept_admin` |
| `aibp` | `aibp` | — | 1:1；补齐 UserOrgMembership |
| `biz_owner` | `dept_admin` | — | 提权 |
| `director` | `observer` | `can_view_all=TRUE` | 跨部门只读 |
| `operator` | `observer` | — | 最小只读 |

迁移 SQL 见 §4.5 Step 2。

---

## 15. 前端改动清单

### 15.1 stores/user.ts 重写

**文件**：`web/src/stores/user.ts`

**当前 UserInfo 类型（10-17 行）**：
```typescript
type UserInfo = {
  user_id?: string
  username?: string
  role?: string
  department?: string
  can_view_all?: boolean
  must_change_password?: boolean
}
```

**目标 UserInfo 类型**：
```typescript
type UserInfo = {
  user_id?: string
  username?: string
  name?: string
  role?: 'system_admin' | 'dept_admin' | 'aibp' | 'observer' | ''
  state?: 'pending' | 'active' | 'disabled'
  department_id?: string                 // 主部门 org_unit_id
  department?: string                    // 主部门展示名（兼容字段）
  managed_departments?: string[]         // dept_admin 管辖的部门 id 列表
  accessible_departments?: string[]      // 能访问的部门 id 列表（含下级展开）
                                         // dept_admin=关联部门直读+管辖部门子树；observer=关联部门子树
  can_view_all?: boolean
  must_change_password?: boolean
  avatar_url?: string
}
```

**当前 getter（28-30 行）**：
```typescript
const isAdmin = computed(() => role.value === 'admin')
const isEngineer = computed(() => ['admin', 'ai_engineer'].includes(role.value))
const canViewAll = computed(() => userInfo.value?.can_view_all || isEngineer.value)
```

**目标 getter**：
```typescript
// v2 新 getter
const isSystemAdmin = computed(() => role.value === 'system_admin')
const isDeptAdmin   = computed(() => role.value === 'dept_admin')
const isAIBP        = computed(() => role.value === 'aibp')
const isObserver    = computed(() => role.value === 'observer')

// 能写代码 / 访问工作台
const canWrite = computed(() =>
  ['system_admin', 'dept_admin', 'aibp'].includes(role.value)
)

// 能进管理后台
const canAdmin = computed(() => isSystemAdmin.value)

// 跨部门访问标志（保留含义）
const canViewAll = computed(() =>
  userInfo.value?.can_view_all === true || isSystemAdmin.value
)

// 状态机相关
const state = computed(() => userInfo.value?.state || '')
const isPending  = computed(() => state.value === 'pending')
const isActive   = computed(() => state.value === 'active')
const isDisabled = computed(() => state.value === 'disabled')

// 部门列表
const departmentId = computed(() => userInfo.value?.department_id || '')
const managedDepartments = computed(() => userInfo.value?.managed_departments || [])
const accessibleDepartments = computed(() => userInfo.value?.accessible_departments || [])

function canManageDepartment(dept: string): boolean {
  if (isSystemAdmin.value) return true
  return managedDepartments.value.includes(dept)
}

function canAccessDepartment(dept: string): boolean {
  if (canViewAll.value) return true
  return accessibleDepartments.value.includes(dept)
}
```

**return 语句同步更新（91-94 行）**：
```typescript
return {
  userInfo, darkMode, theme, isLoggedIn, role, department, departmentId, state,
  isSystemAdmin, isDeptAdmin, isAIBP, isObserver,
  canWrite, canAdmin, canViewAll,
  isPending, isActive, isDisabled,
  managedDepartments, accessibleDepartments,
  canManageDepartment, canAccessDepartment,
  fetchUser, login, logout, toggleDark, setTheme, cycleTheme, applyTheme,
  // 兼容层（仅用于本分支联调过渡，合并主干前清理）
  isAdmin: isSystemAdmin,
  isEngineer: canWrite,
}
```

### 15.2 路由守卫改造

**文件**：`web/src/router/index.ts:170-193`

**当前代码**：
```typescript
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const userStore = useUserStore()
  if (!userStore.isLoggedIn) await userStore.fetchUser()
  if (!userStore.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (userStore.userInfo?.must_change_password && to.name !== 'ChangePassword') {
    return { path: '/change-password' }
  }
  const requiredRoles = to.meta.roles as string[] | undefined
  if (requiredRoles && !requiredRoles.includes(userStore.role)) {
    return { path: '/error/403' }
  }
  return true
})
```

**目标代码**：
```typescript
router.beforeEach(async (to) => {
  if (to.meta.public) return true

  const userStore = useUserStore()
  if (!userStore.isLoggedIn) await userStore.fetchUser()
  if (!userStore.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // v2: pending 用户只能去 /pending 或 /change-password
  if (userStore.isPending && to.name !== 'Pending' && to.name !== 'ChangePassword') {
    return { path: '/pending' }
  }

  // active 状态的用户不允许停留在 /pending
  if (userStore.isActive && to.name === 'Pending') {
    return { path: '/' }
  }

  // 强制改密码
  if (userStore.userInfo?.must_change_password && to.name !== 'ChangePassword') {
    return { path: '/change-password' }
  }

  // v2: 角色检查（meta.roles 老语义 + meta.requiredRoles 新语义兼容）
  const requiredRoles = (to.meta.requiredRoles || to.meta.roles) as string[] | undefined
  if (requiredRoles && !requiredRoles.includes(userStore.role)) {
    return { path: '/error/403' }
  }

  return true
})
```

### 15.3 路由表改动

**新增路由**：

```typescript
// 挂起页
{ path: '/pending', name: 'Pending',
  component: () => import('@/pages/Pending.vue'),
  meta: { requiresAuth: true, hideLayout: true } },

// 候选 dept_admin 审批页（dept_admin + system_admin）
{ path: '/admin/pending-users', name: 'AdminPendingUsers',
  component: () => import('@/pages/admin/PendingUsers.vue'),
  meta: { title: '待激活用户', requiredRoles: ['system_admin', 'dept_admin'] } },
```

**修改的路由 meta**（批量替换 `roles` 字段值）：

| 路由 | 旧 roles | 新 requiredRoles |
|---|---|---|
| `skills/new` | `['admin', 'ai_engineer', 'aibp']` | `['system_admin', 'dept_admin', 'aibp']` |
| `skills/:id` | `['admin', 'ai_engineer', 'aibp', 'biz_owner']` | 删除 roles（由组件内细判），或 `['system_admin', 'dept_admin', 'aibp', 'observer']` |
| `dashboard/contract-drift` | `['admin', 'ai_engineer']` | `['system_admin', 'dept_admin']` |
| `datasources/browser` | `['admin', 'ai_engineer']` | `['system_admin', 'dept_admin', 'aibp']` |
| `admin/*`（除 pending-users）| `['admin']` | `['system_admin']` |

### 15.4 挂起页 Pending.vue

**新增文件**：`web/src/pages/Pending.vue`

```vue
<template>
  <div class="pending-page">
    <div class="pending-card">
      <icon-clock-circle class="icon" />
      <h2>账号待审批</h2>
      <p>你的账号已创建，请联系部门管理员（{{ managerHint }}）激活后再使用 SkillForge。</p>
      <div class="info-list">
        <div class="info-row"><span>用户名</span><b>{{ userStore.userInfo?.name || '-' }}</b></div>
        <div class="info-row"><span>钉钉部门</span><b>{{ userStore.userInfo?.department || '未关联' }}</b></div>
        <div class="info-row"><span>申请时间</span><b>{{ createdAt }}</b></div>
      </div>
      <a-space>
        <a-button @click="refresh">刷新状态</a-button>
        <a-button @click="handleLogout">退出</a-button>
      </a-space>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const router = useRouter()
const createdAt = ref('')

const managerHint = computed(() => {
  const dept = userStore.userInfo?.department
  return dept ? `${dept} 的管理员` : '系统管理员'
})

async function refresh() {
  await userStore.fetchUser()
  if (userStore.isActive) router.push('/')
}
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}
onMounted(() => {
  createdAt.value = new Date().toLocaleString()
})
</script>

<style scoped>
.pending-page { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #f6f8fb; }
.pending-card { background: #fff; padding: 48px; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.06); max-width: 440px; text-align: center; }
.icon { font-size: 48px; color: #f7b500; margin-bottom: 16px; }
.info-list { text-align: left; margin: 24px 0; }
.info-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eef1f5; }
.info-row span { color: #8a9099; }
</style>
```

### 15.5 v-if 批量替换清单

**全局搜索替换**（每处 review 原语义再改，不能 sed 一刀切）：

| 文件 | 旧表达 | 新表达 |
|---|---|---|
| `web/src/layouts/AppLayout.vue` | `v-if="userStore.isAdmin"` | `v-if="userStore.isSystemAdmin"` |
| `web/src/layouts/AppLayout.vue` | `v-if="userStore.isEngineer"` | `v-if="userStore.canWrite"` |
| `web/src/composables/skillstudio/useSkillStudioReview.ts` | `['admin', 'ai_engineer']` / `canViewAll` 直通审批、评论 | 审批按钮改为 `system_admin` 直通 + “当前用户是否为 reviewer / 待办 assignee”判定；评论权限按 `submitter/reviewer/已参与 approver` 判定，`canViewAll` 只给只读不授审批 |
| `web/src/pages/Dashboard.vue` | `canViewCosts` 依赖 `admin/ai_engineer` | 改为 `userStore.isSystemAdmin`；页面其他卡片只消费后端 read-scope 结果，不再按 `department` 做前端二次判权 |
| `web/src/composables/skillstudio/useSkillStudioSession.ts:22-24` | `canEditPerspective` 按 `['biz_owner', 'director']` 排除 | 改为 `userStore.canWrite && !userStore.isObserver` |
| `web/src/pages/playbook/PlaybookWorkbench.vue:553-555` | `userStore.isEngineer \|\| userStore.isAdmin` | `userStore.canWrite` |
| `web/src/pages/tasktree/composables/useTaskTree.ts:150` | `canViewAll \|\| isAdmin \|\| isEngineer` | `canViewAll` |
| `web/src/utils/constants.ts` | `roleLabel: { admin, ai_engineer, ... }` | 替换为新 4 种的中文名 |
| 所有 `/admin/**` 页面 | `roles: ['admin']` | `requiredRoles: ['system_admin']` |
| `web/src/pages/admin/AdminUsers.vue` | 角色选项 `['admin', ...]` | `['system_admin', 'dept_admin', 'aibp', 'observer']` |

**roleLabel 新值**（`web/src/utils/constants.ts`）：
```typescript
export const roleLabel: Record<string, string> = {
  system_admin: '系统管理员',
  dept_admin:   '部门管理员',
  aibp:         '业务伙伴',
  observer:     '观察员',
  // 兼容老数据
  admin:        '系统管理员（旧）',
  ai_engineer:  'AI 工程师（旧）',
  biz_owner:    '业务负责人（旧）',
  director:     '总监（旧）',
  operator:     '操作员（旧）',
}

export const roleColor: Record<string, string> = {
  system_admin: 'red',
  dept_admin:   'orange',
  aibp:         'blue',
  observer:     'gray',
}
```

### 15.6 新增页面：/admin/pending-users

**新增文件**：`web/src/pages/admin/PendingUsers.vue`

功能：
- 列表展示 `state='pending'` 的用户（dept_admin 只看自己管辖部门候选，system_admin 看全部）
- 每行"激活"按钮 → 弹窗选择 role / 部门 / is_manager / can_view_all
- 调用 `POST /api/users/{user_id}/activate`

### 15.7 离职接手人弹窗

在 `AdminUsers.vue` 禁用按钮改成打开弹窗：
- 下拉：接手人（同部门 active aibp+）
- 单选：正在跑的 execution 处理方式（wait / abort）
- 展示即将转交的资产清单（Skill owner 数量、未决 todo 数量、运行中 execution 数量）

---

## 16. 后端改动清单

### 16.1 核心函数改造总览

| 文件 | 函数 | 改动 |
|---|---|---|
| `app/auth/models.py:11-30` | `User` | 加 `state` / `permissions_rev` 字段 |
| `app/auth/dependencies.py:22-32` | `create_session_token` / `verify_session_token` | token 结构改 `{uid, rev}` |
| `app/auth/dependencies.py:35-56` | `get_current_user` | 加 rev 比对 + state 检查 |
| `app/auth/dependencies.py:68-102` | `get_current_user_ws` | 同上 |
| `app/auth/dependencies.py:105-109` | `require_department_access` | 改为调用 `app/auth/access.py` |
| `app/auth/router.py:62` | 登录签发 token | `create_session_token(user.id, user.permissions_rev)` |
| `app/auth/router.py:176` | 钉钉回调签发 token | 同上 |
| `app/auth/router.py:34-42` | `UserInfo` pydantic model | 加 `state / department_id / managed_departments / accessible_departments` 字段 |
| `app/auth/router.py:72-79, 93-101` | login / me 返回值 | 带上 `state / department_id` 等新字段 |
| `app/auth/dingtalk_oauth.py:153-167` | 首登创建 User | `role=observer, state=pending` |
| `app/users/service.py:62-103` | `create_user` | 允许 role 值域改到新 4 种 |
| `app/users/service.py:106-148` | `update_user` | 改 role 时检查最后 system_admin；变更后 bump_rev |
| `app/users/service.py:151-167` | `disable_user` | 改签名，加 successor + running_executions 参数，执行资产转交 |
| `app/users/service.py:191-269` | `sync_dingtalk` | 创建用户默认 `pending` |
| `app/users/service.py:+` | `activate_pending_user`（新增）| 见 §9.2 |
| `app/users/service.py:+` | `reactivate_user`（新增）| disabled → active |
| `app/users/router.py:+` | `POST /{id}/activate`、`POST /{id}/disable`、`POST /{id}/reactivate` | 新接口 |
| `app/users/router.py:44, 55, 75, 94, 105, 116` | 所有 `require_role("admin")` | 改为按粒度：system_admin 全通，dept_admin 本部门 |
| `app/approval/approver_resolver.py:146-161` | `_resolve_role` | 见 §6.2 |
| `app/approval/approver_resolver.py:+` | `_resolve_dept_admin`（新增）| 见 §6.2 |
| `app/approval/approver_resolver.py:51-62` | `resolve_approvers` 分派 | 加 `type="dept_admin"` 分支 |
| `app/org/service.py:+` | `merge_org`（新增）| 见 §11.1 |
| `app/org/service.py:+` | `split_org` / `rename_org` | 见 §11.2 |
| `app/org/service.py:222-289`（钉钉同步循环） | 创建 User | `role=observer, state=pending` |
| `app/playbooks/service.py` | `save_playbook` / `get_playbook` / `list_playbooks` | 双写 `department_id + department + creator_user_id + owner_user_id` |
| `app/playbooks/router.py` | create / update / run / publish / list / detail | 不再硬编码 `admin/ai_engineer`，改为 role + owner + department helper 组合判定 |
| `app/skills/router_assets.py` | template publish / release / lineage / fork | 清掉 `require_role("admin", "ai_engineer")`；`publish-as-template` / `create_release` / `fork` 分别走 `publish` / `edit` / `read` helper，不再把 `ensure_skill_department_access` 当万能权限闸门 |
| `app/skills/router_runtime.py` | run / shadow / batch-publish / rollback | 清掉 `admin/ai_engineer/biz_owner` 旧角色白名单；按 `execute` / `edit` / `publish` 动作接入统一 Skill helper |
| `app/execution/router.py` | `run_skill` / `_check_run_department` / `list_runs` / `get_run*` | 去掉 `current_user.department` 真源和 `admin/ai_engineer` 白名单，改为 run 关联 Skill 的 `org_unit_id` + `can_read_department` / `read_scope_hash` |
| `app/workbench/router_chat.py` | REST chat / WS chat / coding WS | 角色白名单改 `system_admin/dept_admin/aibp`；`current_user.department` 仅做展示上下文，不再作为服务层判权真源 |
| `app/aiclaw/router.py` | instances list / detail / CRUD / status | 清掉 `admin/ai_engineer` + `current_user.department` 旧逻辑；system_admin 负责实例写操作，读侧按 read-scope helper 过滤部门 |
| `app/dingtalk/router.py` | callback / card-callback / dispatch-ack | 卡片回调里的 `actor_user.role != "admin"` bypass 改成 v2 system_admin 判定；审批/回执回调不得再按 payload 部门名或 `User.department` 临时判权 |
| `app/reviews/service.py` | `create_review` / `list_reviews` | `reviewer` pending 时允许为空；按 todo fan-out + `department_id/org_unit_id` 过滤 |
| `app/reviews/router.py` | 详情 / approve / reject | 兼容 reviewer 为空，改用 submitter / assignee / acted_by 混合判定 |
| `app/todos/service.py` | list / assign / ack | observer 仅允许本人 assignee 的 dispatch / 回执；部门范围改 helper |
| `app/tasktree/router.py` / `app/tasktree/service.py` | ABAC / cache scope | 改为 `accessible_departments` + `auth_scope_hash` |
| `app/dashboard/router.py` | overview / trends / impact 等 | scope 改 helper；cache key 不能只按 `department` |
| `app/datasources/router.py` | 列表 / 详情 / 编辑 | scope 改 helper；observer 仅只读 |
| `app/common/ws_auth.py` / `app/playbooks/live.py` / `app/execution/ws.py` / `app/common/metrics.py` | token 校验 | 同步到 rev/state 新语义 |
| `app/skills/access.py:49-100` | `check_skill_write_permission` | 见 §7.1 |
| `app/skills/router.py:129` 及其他 ABAC 判定点 | 所有硬编码 `role in (...)` | 改为调 `app/auth/access.py` 的函数 |
| `app/common/audit.py:+` | Service ID 常量 / audit 入参 | 定义 `SVC_SCHEDULER / SVC_OPENCLAW / SVC_MCP / SVC_SYNC / SVC_ANONYMOUS`，并支持写入 `role_snapshot` |
| `app/common/exceptions.py:+` | 新错误码 | 见 §18 |
| `app/auth/access.py`（新增整个文件） | 权限工具函数集（含 read-scope helper） | 见 §4.6 |

### 16.2 require_role 的使用策略

**保留 `require_role` 做"角色白名单"检查**，但**不再**用于"部门 + 角色"组合检查。后者改为：

```python
# 旧写法（分散在各路由）
if current_user.role in ("admin", "ai_engineer", "director"):
    ...

# 新写法
from app.auth.access import can_read_department, can_manage_department

if not await can_read_department(db, current_user, skill.org_unit_id):
    raise AppError("AUTH_PERMISSION_DENIED", 403)
```

### 16.3 模块级扫尾清单（本版不能漏）

静态扫描结果表明，本仓不是“改 3 个 helper 就收工”的结构；以下模块都必须完成权限收口：

- Skills 主链：`app/skills/router.py`、`app/skills/router_files.py`、`app/skills/router_ai.py`、`app/skills/hall_service.py`。清掉旧 `role in (...)`，统一到 `ensure_skill_access` / `can_read_department` / `can_manage_department`。
- Skills 资产与 runtime 侧车：`app/skills/router_assets.py`、`app/skills/router_runtime.py`。这两处仍有 `require_role("admin", "ai_engineer")`、`biz_owner` 和 `ensure_skill_department_access` 旧入口，必须按 `read/edit/publish/execute` 动作拆开收口。
- Playbooks：`app/playbooks/router.py`、`app/playbooks/service.py`、`app/playbooks/live.py`
- Reviews / Approval / Todos：`app/reviews/router.py`、`app/reviews/service.py`、`app/approval/service.py`、`app/todos/service.py`
- Execution：`app/execution/router.py`、`app/execution/ws.py`。`_check_run_department`、列表分页和缓存 key 仍带旧 `department` 真源；必须改成 run → skill → `org_unit_id` + read-scope hash。
- Workbench：`app/workbench/router.py`、`app/workbench/router_chat.py`、`app/workbench/router_patch.py`、`app/workbench/router_session.py`。REST/WS 入口仍硬编码 `admin/ai_engineer/aibp`，并向 service 透传 `current_user.department`；要改成 v2 角色白名单 + 资源 helper。
- AIClaw：`app/aiclaw/router.py`。实例列表/CRUD 仍有旧 `admin/ai_engineer` 与 `current_user.department` 判定，本版必须接入 v2 role + read-scope helper。
- DingTalk 回调：`app/dingtalk/router.py`。卡片回调里的 `admin` 例外、本地用户映射后的审批/回执放行逻辑，都要替换成 v2 system_admin + todo/approval 服务的正式 helper。
- 读侧业务：`app/tasktree/router.py`、`app/tasktree/service.py`、`app/dashboard/router.py`、`app/datasources/router.py`
- 认证周边：`app/common/ws_auth.py`、`app/execution/ws.py`、`app/common/metrics.py`
- 前端遗漏入口：`web/src/composables/skillstudio/useSkillStudioReview.ts`、`web/src/pages/Dashboard.vue`（另加 §15 已列 store / router / layout 批量替换）

验收标准：

- 不再新增旧角色常量 `admin/ai_engineer/biz_owner/operator/director`
- 不再新增 `current_user.department == ...`、`User.department == ...`、`OpenClawInstance.department == ...` 作为权限真源的写法
- 新增缓存 key 必须包含 auth scope，而不是只按 department

---

## 17. API 接口规格

### 17.1 新增接口

#### `POST /api/users/{user_id}/activate`

激活 pending 用户。

**权限**：system_admin（任意用户）/ dept_admin（本部门候选）

**Request Body**：
```json
{
  "role": "aibp",
  "department_id": "org-unit-id",
  "is_manager": false,
  "can_view_all": false
}
```

**Response 200**：
```json
{"id": "dt_xxx", "state": "active", "role": "aibp"}
```

**错误**：
- 400 `USER_NOT_PENDING` — 目标用户不是 pending
- 400 `PARAM_INVALID` — role 不合法或 dept_admin 越权选择
- 403 `AUTH_PERMISSION_DENIED` — dept_admin 操作非管辖部门

---

#### `POST /api/users/{user_id}/disable`

禁用用户，带资产转交。

**权限**：system_admin / dept_admin（本部门）

**Request Body**：
```json
{
  "successor_user_id": "user-xxx",
  "running_executions": "wait"
}
```

**Response 200**：
```json
{"id": "user-yyy", "state": "disabled"}
```

**错误**：
- 400 `SUCCESSOR_INVALID` — 接手人不存在或非 active
- 403 `LAST_SYSTEM_ADMIN` — 禁用会导致 system_admin 归零
- 403 `AUTH_PERMISSION_DENIED` — 越权

---

#### `POST /api/users/{user_id}/reactivate`

重启 disabled 用户。

**权限**：system_admin / dept_admin（本部门）

**Response 200**：`{"id": "user-xxx", "state": "active"}`

---

#### `GET /api/users/pending`

列出 pending 用户。

**权限**：system_admin / dept_admin

**Query**：
- `department_id`（可选）— 过滤指定部门候选人

**Response**：
```json
{
  "total": 12,
  "items": [
    {
      "id": "dt_xxx",
      "name": "张三",
      "dingtalk_department": "EC-投放组",
      "dingtalk_department_id": "org-ec-001",
      "created_at": "2026-04-17T08:00:00"
    }
  ]
}
```

注：dept_admin 看到的列表已在后端按 `get_managed_departments` 过滤。

---

#### `POST /api/org/{source_id}/merge`

合并部门。

**权限**：system_admin

**Query**：
- `target_id=xxx` — 目标部门

**Response 200**：
```json
{
  "source_id": "org-a",
  "target_id": "org-b",
  "affected_users": 23,
  "affected_skills": 8,
  "affected_playbooks": 5,
  "affected_todos": 3
}
```

---

#### `GET /api/auth/me`（扩展返回字段）

**新 Response**：
```json
{
  "user_id": "dt_xxx",
  "username": "dingtalk_xxx",
  "name": "张三",
  "role": "aibp",
  "state": "active",
  "department_id": "org-ec-001",
  "department": "EC-投放组",
  "managed_departments": [],
  "accessible_departments": ["org-ec-001", "org-ec-subA"],
  "can_view_all": false,
  "must_change_password": false,
  "avatar_url": "https://..."
}
```

### 17.2 修改接口

| 接口 | 改动 |
|---|---|
| `POST /api/auth/login` | 返回 UserInfo 含 `state / department_id / managed_departments / accessible_departments` |
| `DELETE /api/users/{id}` | 弃用（返回 410 Gone，提示用 `POST /api/users/{id}/disable`）|
| `POST /api/users/` | role 值域收窄到新 4 种 |
| `PUT /api/users/{id}` | 改 role 时触发最后 system_admin 检查 + bump_rev |
| `POST /api/users/sync-dingtalk` | 同步后的用户 state=pending |

---

## 18. 错误码清单

在 `app/common/exceptions.py` 新增：

```python
ERROR_MESSAGES = {
    # ... 已有
    # v2 新增
    "AUTH_ACCOUNT_NOT_ACTIVE":     "账号尚未激活或已禁用",
    "USER_NOT_PENDING":            "用户状态不是 pending，无法激活",
    "SUCCESSOR_INVALID":           "接手人不存在或已禁用",
    "LAST_SYSTEM_ADMIN":           "不能禁用或删除最后一个系统管理员",
    "ORG_UNIT_NOT_FOUND":          "部门不存在",
    "ORG_MERGE_SELF":              "不能合并部门到自己",
    "PERMISSIONS_REV_MISMATCH":    "会话已过期（权限已变更），请重新登录",
}
```

前端 `web/src/api/request.ts` 对 401 响应额外处理 `PERMISSIONS_REV_MISMATCH`，提示语改"权限已变更，请重新登录"。

---

## 19. Codex 实施指引

分 Phase 推进，每个 Phase 一个 feature 分支，完成后合并到 `feature/role-matrix-v2` 主干，最终整体 merge 到 master。

实施顺序硬约束：

1. 先做身份真源、department 正规化、session rev 与所有 token consumer 收口。
2. 再做 approvals / reviews / todos / skills / playbooks 等写路径。
3. 最后做 tasktree / dashboard / datasources / 前端批量替换。

禁止“先改前端角色枚举，再回头补后端真源”的倒序实施。

### Phase 0 — 准备（0.5 天）

- [ ] 在 dev 备份数据库（`pg_dump` 存一份）
- [ ] 新建分支 `feature/role-matrix-v2`
- [ ] 在 `docs/plans/` 新建 `2026-04-17-role-matrix-v2-implementation.md` 跟踪进度
- [ ] 通读本文档，列出所有疑问

### Phase 1 — 数据模型、正规化与工具函数（2-3 天）

commit 粒度：

1. **`feat(auth): add state and permissions_rev to User model`**
   - 改 `app/auth/models.py:11-30`（§4.1）
   - 新 alembic migration（§4.5），本地跑 `alembic upgrade head` 验证

2. **`feat(auth): add access control utilities`**
   - 新文件 `app/auth/access.py`（§4.6）
   - 单元测试 `tests/test_auth_access.py`：覆盖 4 角色 × can_access / can_manage 各场景

3. **`feat(auth): normalize legacy department to org_unit_id`**
   - 跑 migration precheck（§4.5 Step 3）
   - 确保 `users.department` / `skills.department` 保持展示名，`org_unit_id` 成为真源

4. **`feat(auth): session token with permissions_rev`**
   - 改 `app/auth/dependencies.py:22-32, 35-56, 68-102`（§10）
   - 改 `app/auth/router.py:62, 176`（§10.3）
   - 同步改 `app/common/ws_auth.py`、`app/playbooks/live.py`、`app/execution/ws.py`、`app/common/metrics.py`
   - 测试：老 token 兼容、rev 不匹配即 401、state=disabled 即 403、非 HTTP consumer 一致拒绝

5. **`feat(auth): new error codes for v2`**
   - 改 `app/common/exceptions.py`（§18）

### Phase 2 — 用户生命周期（2 天）

6. **`feat(users): activate pending user`**
   - 新 `activate_pending_user` 函数 + API（§9.2、§17.1）
   - 测试：system_admin / dept_admin 权限边界、非 pending 拒绝

7. **`feat(users): disable with successor transfer`**
   - 改 `disable_user`（§9.3）
   - 新 API `POST /{id}/disable` + `POST /{id}/reactivate`
   - 测试：Skill owner 转交、todo 转派、execution 处理、最后 admin 硬锁

8. **`feat(users): pending list endpoint`**
   - 新 API `GET /api/users/pending`（§17.1）
   - dept_admin 只看自己管辖部门候选

9. **`feat(users): bump permissions_rev on all role/state changes`**
   - 在 §10.5 表格列出的所有位置调 `bump_permissions_rev`
   - e2e 测试：改 role 后旧 session 立即失效

### Phase 3 — 审批链、对象级权限与 Playbook / Review / Todo（3-4 天）

10. **`feat(approval): dept_admin resolver strategy`**
   - 改 `app/approval/approver_resolver.py`（§6.2）
   - 改 `app/approval/service.py:create_instance()` 真实接入 `resolve_approvers()`
   - 测试：L1 / L2 正确解析、没有 dept_admin 时合理报错、多候选人 fan-out 正常

11. **`feat(skills): skill write permission with opt-in member`**
    - 改 `app/skills/access.py:check_skill_write_permission`（§7.1）
    - 测试：空 member = 部门全员 edit；有 member 进入受控

12. **`feat(skills): fork breaks member chain`**
    - 改 `fork_template` 和其他 Fork 入口（§7.2）
    - 测试：Fork 后新 Skill 只有 fork 发起人 = owner

13. **`feat(playbooks): owner-based metadata and v2 acl`**
    - 改 `app/playbooks/service.py` / `app/playbooks/router.py`
    - 双写 `department_id + department + creator_user_id + owner_user_id`
    - 测试：aibp 只能改自己 owner 的 Playbook；dept_admin 可改本部门 Playbook

14. **`feat(reviews): pending reviewer fan-out`**
    - 改 `app/reviews/service.py` / `app/reviews/router.py`
    - reviewer pending 为空；详情 / 列表按 todo assignee 兼容
    - 测试：多 dept_admin 时任一人处理即闭环

15. **`feat(todos): observer dispatch exception`**
    - 改 `app/todos/service.py`
    - 只允许 observer 处理本人 assignee 的 dispatch / 回执
    - 测试：observer 不能审批、不能转派、但能确认本人任务

### Phase 4 — 钉钉集成、组织变更与读侧模块收口（3 天）

16. **`feat(dingtalk): first-login creates pending user`**
    - 改 `app/auth/dingtalk_oauth.py:153-167`（§8.1）
    - 改 `app/users/service.py:251-264`（§8.2）
    - e2e 测试：新钉钉用户登录看到 /pending

17. **`feat(org): merge departments with auto migration`**
    - 新 `merge_org` 函数 + API（§11.1）
    - 测试：Skill/Membership/todo/Playbook 都迁到目标；affected_users 全部 bump_rev

18. **`feat(read-side): tasktree/dashboard/datasource auth scope migration`**
    - 改 `app/tasktree/*`、`app/dashboard/router.py`、`app/datasources/router.py`
    - 缓存 key 改为 auth scope，不再只按 department

19. **`chore(audit): service id constants + role_snapshot`**
    - 替换 `"system"` / `"scheduler"` / `"unknown"`（§12.1）
    - audit migration 增加 `role_snapshot`（§12.3）

### Phase 5 — 前端改造（3 天）

20. **`feat(web): user store v2 getters`**
    - 改 `web/src/stores/user.ts`（§15.1）
    - 保留 `isAdmin / isEngineer` 兼容层

21. **`feat(web): router guard for pending state`**
    - 改 `web/src/router/index.ts:170-193`（§15.2）
    - 新路由 `/pending`、`/admin/pending-users`（§15.3）

22. **`feat(web): pending page and pending users admin`**
    - 新 `Pending.vue`（§15.4）
    - 新 `PendingUsers.vue`（§15.6）

23. **`refactor(web): replace isAdmin/isEngineer globally`**
    - 按 §15.5 清单逐个文件替换
    - `roleLabel / roleColor` 更新

24. **`feat(web): disable user dialog with successor`**
    - 改 `AdminUsers.vue`（§15.7）

### Phase 6 — 迁移与验收（2 天）

25. **`feat(db): v2 role matrix alembic migration`**
    - 本地 dev 跑迁移，验证老数据都迁过去
    - 记录每条 SQL 的执行耗时

26. **`test(e2e): v2 permission matrix`**
    - 新 e2e 测试（§20 测试场景矩阵）

27. **`docs: post-migration runbook`**
    - 在 `docs/operations/` 写 v2 上线运维手册：
      - 迁移前备份
      - 全员重登预告
      - 回滚步骤

### 各 Phase 验收门槛

| Phase | 门槛 |
|---|---|
| 1 | `pytest tests/test_auth_access.py tests/test_session_rev_consumers.py` 全绿 |
| 2 | 新 API 手测 OK；最后 admin 硬锁测试过；bump_rev 生效 |
| 3 | `pytest tests/test_approval_v2.py tests/test_playbook_acl_v2.py tests/test_todo_observer_dispatch.py` 全绿 |
| 4 | 钉钉回调 e2e OK；merge_org 手测数据迁移完整；tasktree/dashboard/datasource cache scope OK |
| 5 | `vitest` 全绿；`vue-tsc` 只有 pre-existing 错误；`playwright` 关键场景 OK |
| 6 | 全量 pytest + vitest + playwright 全绿；staging 跑 1 天 |

---

## 20. 测试场景矩阵

### 20.1 后端 pytest 新增

**文件**：`tests/test_auth_access.py`

| 用例 | 输入 | 预期 |
|------|------|------|
| system_admin 访问任何部门 | `user.role=system_admin`, dept=X | True |
| dept_admin 访问管辖部门 | user 在 X 部门 `is_manager=True` | True |
| dept_admin 访问非管辖部门 | user 不在 Y 部门 | False |
| aibp 访问关联部门 | user 在 X 部门 | True |
| aibp 访问非关联部门 | user 不在 Y 部门 | False |
| observer 访问关联部门 | user 在 X 部门 | True |
| observer 访问关联部门下级 | X 的子部门 X1 | True（path 匹配）|
| observer + can_view_all | user 在 X 但查 Y | True |
| pending 用户调 API | state=pending | 403 AUTH_ACCOUNT_NOT_ACTIVE |
| disabled 用户调 API | state=disabled | 403 AUTH_ACCOUNT_DISABLED |

**文件**：`tests/test_session_rev_consumers.py`

| 用例 | 预期 |
|------|------|
| `app/common/ws_auth.py` 遇到 rev mismatch | 拒绝连接 |
| `app/playbooks/live.py` 遇到 pending 用户 | 拒绝连接 |
| `app/execution/ws.py` 遇到 disabled 用户 | 拒绝连接 |
| `/metrics` 非本地且非 system_admin | 403 |

**文件**：`tests/test_user_lifecycle.py`

| 用例 | 输入 | 预期 |
|------|------|------|
| dept_admin 激活本部门 pending | 目标部门=operator 管辖的 | 200 |
| dept_admin 激活非管辖 pending | 目标部门非管辖 | 403 |
| dept_admin 激活时选 dept_admin role | `role=dept_admin` | 403 |
| system_admin 激活任何 pending | 任意部门 | 200 |
| 禁用带 successor | Skill owner 转交成功 | 200，owner = successor |
| 禁用最后 system_admin | 仅剩一个 | 403 LAST_SYSTEM_ADMIN |
| 禁用后旧 session 访问 | 旧 token | 401 AUTH_ACCOUNT_DISABLED |
| 改 role 后旧 session 访问 | 旧 token | 401 PERMISSIONS_REV_MISMATCH |

**文件**：`tests/test_approval_v2.py`

| 用例 | 预期 |
|------|------|
| L1 审批由 Skill 部门的 dept_admin 处理 | 正确解析 |
| 部门无 dept_admin 时报错 | APPROVER_NOT_FOUND |
| L2 审批沿 parent 向上找 | 正确解析 |
| requester 是 dept_admin 时不自己审 | 过滤掉自己 |

**文件**：`tests/test_playbook_acl_v2.py`

| 用例 | 预期 |
|------|------|
| 新建 Playbook 时忽略前端直传 department | 后端自动改写成当前用户主部门 |
| Playbook 保存双写 `department_id + department` | 返回值和文件内容一致 |
| aibp 编辑非 owner Playbook | 403 |
| dept_admin 编辑本部门 Playbook | 200 |

**文件**：`tests/test_todo_observer_dispatch.py`

| 用例 | 预期 |
|------|------|
| observer 处理本人 dispatch todo | 200 |
| observer 审批 review todo | 403 |
| observer 转派 dispatch | 403 |

**文件**：`tests/test_tasktree_scope_v2.py`

| 用例 | 预期 |
|------|------|
| 两个不同 `accessible_departments` 的用户访问 tasktree | cache key 不串 |
| observer 看子树 | 命中 path 前缀但不误匹配同前缀异部门 |
| can_view_all 用户切换部门过滤 | 只读可看，但不越权写 |

### 20.2 前端 vitest 新增

**文件**：`web/src/__tests__/user-store-v2.test.ts`

| 用例 | 预期 |
|------|------|
| `isSystemAdmin` for role=system_admin | true |
| `canWrite` for observer | false |
| `canManageDepartment('X')` for dept_admin with X | true |
| `isPending` for state=pending | true |

### 20.3 playwright e2e 新增

**文件**：`web/e2e/role-matrix-v2.spec.ts`

| 场景 | 步骤 |
|------|------|
| 钉钉新用户首登 | 登录 → 看到 /pending 页 |
| dept_admin 激活 | /admin/pending-users → 点激活 → 选 role/部门 → 提交 → 列表刷新 |
| observer 访问 Skill 详情 | 看到内容但所有按钮 disabled |
| aibp 非 owner 点发布 | 按钮不可见 |
| 降权 aibp → observer | 被降权用户刷新页面 → 踢回 /login |
| system_admin 合并部门 | /admin/org → 合并 → 确认 → Skill 列表看到在目标部门 |

---

## 21. Edge case 与踩坑提示

### 21.1 切流窗口

- **rev=-1 legacy token 兜底**：§10.1 的 `verify_session_token` 对老 token 返回 `rev=-1`，`get_current_user` 允许 `-1` 通过，但只允许存在于一次切流窗口内。发布 runbook 必须包含“清 cookie / 强制重登 / 删除 legacy 分支”三步闭环，不能长期保留。
- **aibp 保守迁移**：ai_engineer 全部迁 aibp，导致原来能做部门管理的人失去能力。system_admin 需要在 `/admin/users` 里逐个复核提为 dept_admin。**上线前准备好清单**。
- **`User.department` 不能删**：虽然降级为展示字段，但有多处代码（钉钉推送地址、审计日志、前端 UI 展示）在读。保留字段，通过 Membership 维护但同步写入 department = 主部门展示名。

### 21.2 多部门归属的细节

- 一个 aibp 在 A、B 两部门，他访问 C 部门的 Skill → 403
- 一个 dept_admin 管理 A 部门，A 有个子部门 A1，他管不管 A1？
  - 当前实现：**不管**（因为 `UserOrgMembership` 里 A1 没有他 `is_manager=True`）
  - 这是本版正式规则：**不自动继承子部门管理权**

### 21.3 Fork 之后审批链

- Skill S 原在 AI 组，Fork 到 EC 后部门变 EC
- 这时提交审核 → L1 解析找 EC 的 dept_admin，不是 AI 组的
- AI 组原作者即使是 AI 组的 dept_admin，也不管这个 Fork 副本

### 21.4 组织合并的 SkillMember 冲突

- A 合并到 B。假设某 Skill 在 A 有 SkillMember(user=X, role=owner)，user X 在 B 没记录 → 合并后 Skill 部门变 B，SkillMember 仍保留（不变）
- 但如果 user X 不在 B 的 UserOrgMembership 里，他"没权进 B 的 Skill" — **矛盾**！
- 解决：merge_org 里要把 A 的所有成员 Membership 也迁到 B（§11.1 已实现）

### 21.5 钉钉同步 pending 覆盖

- 某 user 已经被 dept_admin 激活（state=active），下次钉钉同步**不要**把他改回 pending
- `app/org/service.py` 的同步逻辑必须：**仅 state=pending 或新创建的才设 pending**

### 21.6 `get_current_user` 里的 `pending` 不要一刀切 403

- pending 用户要能访问 `/api/auth/me`（让前端判断跳 `/pending`）
- pending 不能访问其他业务 API
- 建议：`get_current_user` 放行 pending；业务路由加 `Depends(require_state_active())` 专用依赖

```python
def require_state_active():
    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.state != "active":
            raise AppError("AUTH_ACCOUNT_NOT_ACTIVE", 403)
        return user
    return _check
```

### 21.7 前端 `isAdmin / isEngineer` 兼容层

- §15.1 保留了兼容 `isAdmin = isSystemAdmin`、`isEngineer = canWrite`
- 注意 `isEngineer` 旧语义含 `ai_engineer`（包括跨部门读），`canWrite` 新语义是"能写代码的角色"
- 个别组件（比如权限检查"能否跨部门"）要改用 `canViewAll` 而不是 `isEngineer`
- 兼容层只允许存在于迁移分支；合并主干前必须清理完毕

### 21.8 approver_resolver 的 chain_step 改动

- Skill / Playbook YAML 或数据库中存的历史 `approval_chain` 结构可能是 `{"type": "role", "role": "biz_owner"}`
- 迁移后 biz_owner 已不存在 → 改造 `_resolve_role` 时加兼容：
  ```python
  if role in ("biz_owner", "director"):
      role = "dept_admin"  # 兼容历史数据
  ```
- 或者 migration 里 UPDATE 所有 chain_step JSON 里的 role 字段

### 21.9 SkillMember 降权后的失效

- user X 是 Skill S 的 SkillMember(role=owner)，现在 X 被降为 observer
- 按 §7.1 的 `check_skill_write_permission`：observer 返回 False（不管 SkillMember 有没有记录）
- SkillMember 记录**保留**（不自动删除），作为审计留痕
- UI 上展示时标"已失效"

### 21.10 observer 的“只读”例外

- observer 默认是只读，但保留一个窄口子：**本人 assignee 的 dispatch / 回执 todo**
- 这个例外不能外溢到：
  - 审批 / 驳回
  - 转派 / 改 assignee
  - Skill / Playbook 编辑
  - 任何 LLM 执行

### 21.11 非 HTTP token consumer

- v2 改 session payload 后，最容易漏的是 `app/common/ws_auth.py`、`app/playbooks/live.py`、`app/execution/ws.py`
- 如果只改了 HTTP 依赖，WebSocket 会出现“HTTP 已踢下线，WS 仍能继续订阅 / 执行”的隐蔽越权

### 21.12 cache key 必须带 auth scope

- `tasktree` 已经有 `auth_scope_hash` 思路，其他读侧模块要跟进
- 反例：只按 `department=EC` 缓存 dashboard，`dept_admin(EC)` 与 `observer(can_view_all=false, subtree=EC-投放组)` 会串结果
- 最低要求：cache key 至少包含 role、can_view_all、accessible_departments 哈希、user.id

---

## 22. 边界决策（本版一次定死）

1. **Service Account**：本版不引入独立表或登录主体；统一使用固定 `svc_xxx` 审计 ID。
2. **API Token / Personal Access Token**：本版明确不开放，不提供任何签发接口。
3. **ABAC 策略表**：`abac_policies` 冻结保留，但 v2 runtime 不再新增依赖；所有新权限判断统一走 `app/auth/access.py`。
4. **跨部门 SkillMember 邀请**：本版直接实现为 owner / dept_admin 直加即生效，同时写 audit，并发送站内通知；不做确认流。
5. **钉钉删除部门冲突**：同步发现钉钉已删但 SkillForge 仍有引用时，不自动删除；标记为 orphaned，阻止继续挂新资源，要求 system_admin 手工 merge / rebind。
6. **审计字段**：`role_snapshot` 本版直接纳入 migration，一起落地。
7. **子部门继承**：本版明确不继承；dept_admin 只管理 `is_manager=True` 的直接部门。仅 read-scope helper 会把这些直接管辖部门向下展开到子树，而且该展开**只用于读侧**。

---

## 23. 文档变更记录

| 版本 | 日期 | 作者 | 变更 |
|------|------|------|------|
| v2.0 draft | 2026-04-17 | SkillForge 产品 | 首版，6 角色 → 4 角色 + UserOrgMembership 真源 + SkillMember opt-in + pending 状态机 + permissions_rev session 失效 |
| v2.0 detailed | 2026-04-17 | SkillForge 产品 | Codex 实施版：所有伪代码改为完整代码，加 API 规格 / 错误码 / 测试矩阵 / Phase checklist / Edge case |
| v2.1 detailed supplement | 2026-04-17 | Codex | 补齐部门真源正规化、Playbook 元数据真源、审批 fan-out 兼容、pending 激活与 successor 校验、非 HTTP token consumer、audit `role_snapshot`、边界决策一次定死 |
| v2.1 supplement | 2026-04-17 | Codex | 补齐实施边界：department 正规化、Playbook 真源字段、review/approval fan-out、pending 白名单、非 HTTP token consumer、读侧 cache scope |
| v2.2 supplement | 2026-04-17 | Codex | 明确 `dept_admin` 读范围 helper（关联部门直读 + 管辖部门子树）、保留兼容 alias，并补齐 `router_assets/router_runtime/execution/workbench/aiclaw/dingtalk` 与前端遗漏入口的扫尾清单 |
