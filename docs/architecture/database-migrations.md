# 数据库迁移命名与版本约定

## 命名规范

所有 Alembic 迁移文件统一使用 **三位数字前缀 + 下划线 + 简短英文描述** 的格式：

```
NNN_short_snake_case_description.py
```

示例：
- `018_openclaw_bridge.py`
- `019_create_decision_requests.py`
- `020_create_ai_todos.py`
- `026_skill_draft_lock_and_fatigue.py`

### 约束

1. **三位数字前缀必须连续递增**：当前最大编号 + 1，绝不跳号
2. **描述用 snake_case，不超过 6 个单词**：体现"做了什么"而非"为什么"
3. **不允许重复编号**：同一编号只能有一个 migration 文件
4. **不允许重命名已合入主干的 migration**：会断 alembic 的 down_revision 链

## 编写规则

### revision 与 down_revision

- `revision`：本文件的标识符，**必须**与文件名前缀一致（如 `revision = "026"`）
- `down_revision`：上一个 migration 的 revision，按时间顺序依次链接
- `branch_labels` / `depends_on`：保持 `None`

### 升降级要求

- `upgrade()` 必须可重入安全，建议先 `if not column_exists(...)` 之类的防御
- `downgrade()` 必须真实可用，能完全回滚 upgrade 的所有改动
- 不允许 `pass` 占位的 downgrade，遇到不可逆操作必须明确 `raise NotImplementedError`

### 数据迁移

- 大表 backfill 必须分批 + 限速，避免长事务锁表
- 大表加 NOT NULL 列：分两步——先加可空列 + 默认值 → backfill → 改 NOT NULL
- 外键约束分两步：先加列 → backfill → 加约束

## 部署流程

1. 本地：`alembic revision -m "create_xxx" --autogenerate`，**手动核对生成的 SQL**
2. 重命名生成的文件为 `NNN_xxx.py`，与代码内部 `revision` 一致
3. CI 跑 `alembic upgrade head`，回滚链验证
4. 部署：`bash scripts/deploy.sh` 自带 `alembic upgrade head`，无需手动运行

## 校验

启动时 `app/database.py:init_db` 会自动校验当前数据库的 alembic_version 与代码中最大
revision 是否一致，不一致会 logger.warning，不阻塞启动。

## 历史决策

- **为什么不用时间戳前缀（YYYYMMDDHHMM）？**
  顺序编号便于 PR review 时看出顺序，三位数字 26 个版本到目前为止够用，达到 999 之前
  不会有歧义。后续若超过 999，再扩位为四位 `0001_*`（一次性 rename 全部历史，要谨慎）。
- **为什么编号连续而非按 PR 合入顺序？**
  避免分支冲突时编号乱序。新建 migration 前必须 git pull main 拿最新编号。
