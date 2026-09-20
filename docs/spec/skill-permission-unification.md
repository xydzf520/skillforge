# Skill 权限统一目标文档

> 日期：2026-05-02
>
> 目的：把 Skill 列表、Skill 详情、能力大厅、Portal、置顶、直接运行能力的权限判断收敛到同一套规则，避免“列表可见，点击才报错”，并给后续 AI/人工改权限代码一个明确边界。

## 1. 背景

当前项目里权限判断分散在多层：

- 登录态：`get_current_user` / `require_state_active`
- 路由角色：`require_role(...)`
- 组织范围：`UserOrgMembership`、`OrgUnit.path`、`get_accessible_departments`
- Skill 对象权限：`Skill.visibility`、`Skill.org_unit_id`、`SkillMember`
- 前端路由与按钮：`meta.roles`、`userStore.isEngineer`、`canViewAll`
- 大厅与 Portal 的独立过滤：`hall_service._apply_visibility`、`portal_service.list_portal_skills`

这些规则没有统一入口，导致同一个用户在不同页面看到的 Skill 范围不一致。典型症状是：

- `/skills` 列表能看到某些 Skill，但进入 `/skills/{id}` 后 403。
- 能力大厅能看到能力卡片，点进具体 Skill 或运行时才报错。
- 置顶、最近访问、搜索结果可能展示已被撤权的 Skill。
- `can_view_all` 被误当成所有操作的通行证，而它业务上应是读权限扩展。
- `require_role` 只做角色白名单，部分业务接口没有同时要求 active 状态。

## 2. 预期目标

### 2.1 用户体验目标

1. 用户在列表页看到的 Skill，默认就是可进入详情的 Skill。
2. 用户看不到无权读取的 Skill 名称、描述、部门、Git 版本、运行统计等具体信息。
3. 用户无权执行/编辑/发布/删除时，前端按钮不展示或禁用，并显示明确原因。
4. “点击才 403”只允许出现在权限刚被变更、前端缓存尚未刷新等竞态场景；正常页面渲染不得依赖失败请求来发现无权限。
5. 能力大厅可以做“能力发现”，但不能泄露用户无权读取的具体 Skill 或可直接运行入口。

### 2.2 后端一致性目标

1. Skill 列表、详情、Hall、Portal、置顶、命令面板、节点映射等所有读取 Skill 的入口，必须共用同一套 read access 规则。
2. 写操作、运行操作、审核操作、发布操作必须按 action 明确判定，不能只靠路由角色白名单。
3. `can_view_all` 只扩大读范围，不自动授予 `edit/publish/delete/manage_members/execute`。
4. `system_admin` / legacy `admin` 语义通过统一角色兼容 helper 处理，不在业务代码里零散写 `user.role == "admin"`。
5. `require_skill_access` 与列表 SQL filter 语义一致：列表不会多给，详情不会少给。
6. 权限缓存必须包含 `user.id` 和 `permissions_rev`，或在权限变更时可可靠失效。

### 2.3 前端一致性目标

1. 前端不再用 `isEngineer` 推断某个 Skill 是否可编辑；必须消费后端返回的 `permissions`。
2. 路由 `meta.roles` 只控制“能不能进入这个模块”，不代表“能不能操作某个 Skill”。
3. Skill 卡片、表格行、Hall 卡片、Portal 卡片的按钮状态统一从 `permissions` 得出。
4. 置顶、最近访问、命令面板搜索结果必须经过后端可读过滤；本地缓存只作为候选，不作为权限依据。

## 3. 权限模型定义

### 3.1 用户状态

业务接口只允许 `state == active` 的用户访问。

- `pending` 用户只能访问认证相关接口和 pending 页必要接口。
- `disabled` 用户任何业务接口都不能访问。
- `require_role(...)` 内部也应基于 active 用户，避免 pending 用户通过只配角色白名单的接口。

### 3.2 角色与附加字段

角色使用 v2 角色体系：

- `system_admin`：系统管理员，完整管理权限。
- `dept_admin`：部门管理员，写权限限制在直接管辖部门，读权限可展开到管辖部门子树。
- `aibp`：AI BP，负责关联部门内 Skill 创建、维护、提审。
- `observer`：观察者，只读，不执行、不编辑、不发布。

兼容 legacy：

- `admin` 兼容 `system_admin`
- `ai_engineer` 兼容 `aibp`
- `biz_owner` / `director` 按已有兼容表映射到 `dept_admin` 或 `observer` 场景
- `operator` 兼容 `observer`

`can_view_all` 是读侧扩展字段，只表示“能读取全公司范围的可读资源”，不是写权限。

### 3.3 Skill 资源字段

Skill 访问以以下字段为准：

- `Skill.visibility`
  - `company`：全公司 active 用户可读。
  - `department`：组织范围内用户可读。
  - `private`：仅 SkillMember 或系统管理员可读。
- `Skill.org_unit_id`：组织真源。
- `Skill.department`：展示/兼容字段，只在历史数据缺 `org_unit_id` 时兜底。
- `SkillMember`：对象级权限，进入受控模式后控制 edit/publish/delete/manage_members。

### 3.4 action 定义

统一 action 集合：

- `read`：列表、详情、Hall、Portal、置顶、搜索、依赖展示、节点映射等读取元数据。
- `execute`：运行、沙箱运行、Portal submit、直接触发节点/中心执行。
- `edit`：编辑文件、保存结构化内容、生成并落盘测试、回滚、shadow 配置等会修改 Skill 的操作。
- `review`：审核、评论、语义 diff 等审核链操作。
- `publish`：发布、批量发布、创建 release、推到模板市场。
- `delete`：删除、停用、下线。
- `manage_members`：成员、可见性、owner 转移等 IAM 操作。

## 4. 统一 Helper 设计目标

### 4.1 `build_skill_access_filter`

新增统一 SQL filter helper：

```python
async def build_skill_access_filter(
    db: AsyncSession,
    user: User,
    action: str = "read",
):
    """返回可直接拼到 select(Skill).where(...) 的 SQLAlchemy 条件。"""
```

目标：

- 用于列表、状态计数、Hall 聚合、Portal 聚合、置顶列表、搜索结果等批量查询。
- 与 `require_skill_access(db, skill_id, user, action)` 共享同一套策略。
- 尽量在 SQL 层过滤，避免先查全量再逐条 Python 过滤。
- 对 SQLite / PostgreSQL 测试环境都可运行。

### 4.2 `get_skill_permissions`

新增单对象权限计算：

```python
async def get_skill_permissions(
    db: AsyncSession,
    skill: Skill,
    user: User,
) -> dict[str, bool]:
    return {
        "read": ...,
        "execute": ...,
        "edit": ...,
        "review": ...,
        "publish": ...,
        "delete": ...,
        "manage_members": ...,
    }
```

目标：

- 列表 item、详情 bootstrap、Hall 卡片、Portal 卡片都返回同样结构。
- 前端按钮不再自行推断对象级权限。
- 权限变更后通过 `permissions_rev` 或缓存失效刷新。

### 4.3 `require_skill_access`

`require_skill_access` 继续作为单对象强制闸门，但实现必须复用统一策略：

```python
async def require_skill_access(db, skill_id, user, action):
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)
    permissions = await get_skill_permissions(db, skill, user)
    if not permissions.get(action):
        raise AppError("SKILL_ACCESS_DENIED", 403)
    return skill
```

原则：所有写操作仍以后端闸门为准；前端按钮只是体验优化，不能作为安全边界。

## 5. 预期判定矩阵

### 5.1 read

| 条件 | read |
|---|---|
| `system_admin` / legacy `admin` | 允许 |
| `can_view_all=True` | 允许读取所有 Skill |
| `visibility=company` | active 用户允许 |
| `visibility=department` | 用户可访问 `skill.org_unit_id` 或历史 `department` 兼容匹配时允许 |
| `visibility=private` | SkillMember 允许 |
| 其他 | 拒绝 |

部门判断必须基于 `UserOrgMembership` + `OrgUnit.path`，必要时才回退 `Skill.department == User.department`。

### 5.2 execute

| 条件 | execute |
|---|---|
| `system_admin` / legacy `admin` | 允许 |
| `dept_admin` | 仅管辖部门内允许 |
| `aibp` | 关联部门内允许 |
| `observer` | 拒绝 |
| `can_view_all=True` | 不因该字段自动允许 |
| Skill 不可 read | 拒绝 |

建议额外约束：

- 生产执行只允许 `status=active`。
- 草稿/影子/停用 Skill 的运行必须走明确的沙箱或预览入口。

### 5.3 edit

| 条件 | edit |
|---|---|
| `system_admin` / legacy `admin` | 允许 |
| `dept_admin` | 直接管辖部门内允许 |
| `aibp` + 非受控模式 | 关联部门内允许 |
| `aibp` + 受控模式 | SkillMember role 为 `owner/editor` 允许 |
| `observer` | 拒绝 |
| `can_view_all=True` | 不因该字段自动允许 |

受控模式定义：某 Skill 只要存在任意一条 `SkillMember`，即进入受控模式。

### 5.4 publish / delete / manage_members

| 条件 | publish/delete/manage_members |
|---|---|
| `system_admin` / legacy `admin` | 允许 |
| `dept_admin` | 直接管辖部门内允许 |
| `aibp` | 仅 SkillMember role 为 `owner` 允许 |
| `observer` | 拒绝 |
| `can_view_all=True` | 不因该字段自动允许 |

### 5.5 review

审核权限应和审核流绑定：

- `system_admin` 可审核。
- `dept_admin` 可审核管辖部门内 Skill。
- 受控 Skill 中 `SkillMember.role=reviewer/owner` 可审核。
- submitter 不能自审。
- reviewer / assignee / 已参与审批人可按审核流查看必要详情。

## 6. 页面与 API 收口范围

### 6.1 P0 必须收口

| 模块 | 目标 |
|---|---|
| `GET /api/skills/` | 使用 `build_skill_access_filter(..., "read")`，列表和详情 read 一致 |
| `GET /api/skills/departments` | 只返回当前用户可读 Skill 的部门聚合 |
| `GET /api/skills/pinned` | 只返回当前用户仍可 read 的置顶 Skill |
| `GET /api/skills/hall` | 使用同一 read filter，不再维护第二套可见性规则 |
| `GET /api/hall/capabilities` | 聚合前先用同一 read filter 过滤 Skill |
| `GET /api/hall/capability/{category}` | 详情里的 Skill 列表只含当前用户可 read 的 Skill |
| `GET /api/portal/skills` | 用同一 read filter，缓存按 user/rev 隔离 |
| `GET /api/portal/skills/{id}` | 继续强制 `require_skill_access(..., "read")` |
| 前端 SkillList/Hall/Portal 卡片 | 消费 `permissions` 字段控制按钮 |

### 6.2 直接运行能力

直接运行能力当前来自 `skills-repo` 文件系统，不一定有数据库 Skill 行。因此必须二选一：

#### 方案 A：明确全公司公开能力

适用场景：`gpt-imagegen` 这类确实希望所有 active 用户可见可用的工具能力。

要求：

- 文档和 UI 明确标识“公开能力”。
- 不读取或暴露私有 Skill 的 `SKILL.md`。
- 不接入 Skill 详情页权限语义。
- 运行接口仍要记录 user_id、输入输出脱敏、审计日志、频控。

#### 方案 B：接入 Skill 权限

适用场景：直接运行能力本质上仍是某个 Skill 的一种运行方式。

要求：

- `contract.json` / `skillforge.yaml` 必须声明可映射到 DB Skill 的 `skill_id`。
- 列表、详情、上传、运行均调用统一 helper。
- `/hall/direct-capabilities/{id}/run` 至少要求 `execute=True`。
- 详情文档只对 `read=True` 用户返回。

建议目标：平台只保留方案 A 的少量“公开工具能力”；业务 Skill 统一走方案 B。

## 7. 后端返回字段目标

Skill 列表 item、Hall Skill item、Portal Skill item 应至少返回：

```json
{
  "id": "skill-id",
  "name": "Skill 名称",
  "department": "部门",
  "status": "active",
  "visibility": "department",
  "git_commit": "abcdef12",
  "permissions": {
    "read": true,
    "execute": true,
    "edit": false,
    "review": false,
    "publish": false,
    "delete": false,
    "manage_members": false
  },
  "permission_reason": {
    "read": "department_scope",
    "execute": "aibp_department_scope",
    "edit": "not_skill_member"
  }
}
```

`permission_reason` 可先做可选字段；P0 至少要有 `permissions`。

## 8. 缓存与失效

权限相关缓存必须满足以下任一条件：

1. cache key 包含 `user.id` + `permissions_rev` + 查询参数；
2. 或权限变更时能准确删除所有相关 cache。

需要覆盖的变更源：

- 用户 role / state / can_view_all 修改
- UserOrgMembership 增删改
- OrgUnit path / parent 调整
- Skill visibility / org_unit_id / department 修改
- SkillMember 增删改
- Skill 删除、停用、发布状态变化

Portal 当前按 primary org 缓存列表，目标应改为：

```text
portal:skills:{user.id}:{permissions_rev}:{search}:{category}:{page}:{page_size}
```

或者短期直接取消 Portal Skill 列表缓存，避免对象级 SkillMember 泄漏。

## 9. 前端目标

### 9.1 路由

- `/skills` 是否可进入，是模块级权限，不是对象级权限。
- `/skills/:id` 不应仅靠 `meta.roles` 决定是否能打开；真正判断在后端 `bootstrap/get`。
- 普通用户如果只需要运行 Skill，应优先跳 Portal；如果进入 Studio，则由后端 `permissions.edit` 决定是否只读。

### 9.2 列表与卡片

- 行点击前不做本地角色推断；读权限由列表数据保证。
- 编辑、删除、发布、批量发布等按钮使用 `record.permissions.*`。
- 批量操作只能对当前页中对应 action 为 true 的记录生效。
- 置顶列表加载后如果后端过滤掉已撤权 Skill，前端应同步清理本地显示。

### 9.3 Hall

- Hall 可以展示抽象能力分类，但进入具体 Skill 实现列表时必须只展示可 read 的 Skill。
- “运行”按钮必须要求 `execute=True`。
- 对无 execute 的可读 Skill，显示“可查看，不可运行”。

## 10. 验收标准

### 10.1 功能验收

1. 构造同部门 `private` Skill，非成员用户在 `/api/skills/` 看不到，在 `/api/skills/{id}` 仍 403。
2. 构造跨部门 `department` Skill，非范围用户在 Skills、Hall、Portal、置顶、搜索都看不到。
3. 给用户添加 SkillMember 后，private Skill 在列表、Hall、Portal 同步出现。
4. 撤销 SkillMember 后，列表、Hall、Portal、置顶刷新后同步消失。
5. `can_view_all=True` 用户能看全量列表，但不能编辑、发布、删除没有写权限的 Skill。
6. pending 用户访问 `require_role` 保护的业务接口也被拒绝。
7. 直接运行能力若选择方案 B，无 execute 权限用户无法调用 run。

### 10.2 回归测试目标

至少补充/调整以下测试：

- `tests/test_skill_access.py`
  - read filter 与 require read 一致
  - can_view_all 只读不写
  - system_admin 全通
  - SkillMember 受控模式
- `tests/test_skill_hall.py`
  - Hall 与 Skills 列表可见性一致
  - Hall filters 不泄漏无权部门/分类
- `tests/test_portal.py`
  - Portal cache 按 user/rev 隔离
  - private Skill 只对 member 出现
- `tests/test_permission_boundaries.py`
  - pending + require_role-only 接口被拒绝
  - pinned 过滤撤权 Skill
- 前端测试
  - SkillList 按 `permissions` 显示/隐藏操作按钮
  - Hall 卡片按 `permissions.execute` 显示运行入口

### 10.3 手工验收账号

建议准备以下账号矩阵：

- `system_admin`
- `dept_admin`，管辖 A 部门
- `aibp`，关联 A 部门
- `observer`，关联 A 部门
- `aibp`，关联 B 部门
- `can_view_all=True` 的 observer

测试资源：

- company Skill
- A department Skill
- B department Skill
- private Skill，无成员
- private Skill，A aibp 为 editor
- 受控 Skill，A aibp 非成员
- 受控 Skill，A aibp 为 owner

## 11. 分阶段实施建议

### P0：读权限一致

1. 实现 `build_skill_access_filter(..., "read")`。
2. `/api/skills/`、`/api/skills/departments`、`/api/skills/pinned` 接入。
3. Hall、Portal 聚合接入同一 read filter。
4. 列表返回 `permissions.read`，并预留完整 `permissions` 字段。
5. 补 read 一致性测试。

完成标准：无权 Skill 不再出现在任何 Skill 列表入口。

### P1：action 权限完整化

1. 实现 `get_skill_permissions`。
2. `require_skill_access` 改为调用统一权限计算。
3. `can_view_all` 限制为 read。
4. edit/publish/delete/manage_members 接入角色 + 部门 + SkillMember 规则。
5. 前端按钮按 `permissions` 渲染。

完成标准：同一个 Skill 在所有页面展示的可操作按钮一致，后端仍能拦截越权调用。

### P2：直接运行能力收口

1. 明确每个 direct capability 属于方案 A 还是 B。
2. 方案 B 能力接入 DB Skill 权限。
3. run/upload/history/detail 端点补审计、频控、权限检查。

完成标准：直接运行能力没有“登录即可跑私有业务能力”的入口。

### P3：权限缓存与审计

1. 所有权限相关 cache key 纳入 `user.id + permissions_rev`。
2. SkillMember / org membership / visibility 修改触发缓存失效。
3. 越权访问保留审计事件，便于排查权限配置问题。

完成标准：撤权后旧 session 或旧缓存不会继续展示旧权限。

## 12. AI 修改硬约束

后续 AI 或人工改权限相关代码时必须遵守：

1. 不允许新增第四套/第五套权限判断。
2. 不允许在业务代码里直接写 `user.role in (...)` 后决定 Skill 对象权限。
3. 不允许让 `can_view_all` 直接绕过写操作。
4. 不允许列表接口返回未通过 `read` 权限的 Skill。
5. 不允许前端用 `isEngineer` / `isAdmin` 推断某个 Skill 的 edit/publish/delete 权限。
6. 不允许为了修 UI 体验而移除后端 `require_skill_access`。
7. 修改权限后必须补测试，至少覆盖“列表不可见 + 详情 403”一致性。

## 13. 不在本阶段解决

- 不重构整个 RBAC/ABAC 数据模型。
- 不改钉钉组织同步协议。
- 不把所有业务模块一次性迁到新权限 helper；本阶段先收口 Skill 主链。
- 不改变 Skill Git / Gitea / skills-repo 的版本管理模型。
