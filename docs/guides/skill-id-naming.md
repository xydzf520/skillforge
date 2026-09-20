# Skill ID 命名规范（v2.8.0+）

## 规则

Skill ID 必须匹配正则：`^[A-Za-z0-9\u4e00-\u9fff\-_]{1,50}$`；禁止 `..` / `/` / `\`。

校验入口统一在 `app/skills/id_gen.validate_skill_id`；所有写路径（create / fork / import / scan-repo）必调。

## 生成规则（按来源）

| 场景 | 格式 | 生成函数 | 示例 |
|------|------|---------|------|
| 用户命名（对话创建 / 手写） | 自定义，≤ 50 字符 | — | `EC-投放-01` / `my-skill-01` |
| 对话 / Architect 生成 | `<slug>-<uuid6>` | `gen_from_name(name)` | `tou-su-fen-ji-a1b2c3` |
| 从 zip import | `imp-<dept_abbr>-<uuid8>` | `gen_from_import(dept)` | `imp-ec-3a8b1c2d` |
| Fork 模板 / 已有 Skill | `fork-<parent_slug>-<uuid6>` | `gen_from_fork(parent_id)` | `fork-ec-tpl-01-9f0e1d` |
| e2e / 自动化测试 | `test-<uuid10>` | `gen_for_test()` | `test-8f7a1b0c2d` |

**强约束**：生产库**不应出现** `test-*` 前缀的 Skill。

## 老格式兼容

历史数据里可能有：

- `imported-<8 字符 hex>` — 早期 zip import 产物；视作 `import` 分类
- `imported-<8 字符 base36>` — 早期 e2e 测试残留（`Date.now().toString(36)`）；视作 `e2e_test`，生产库应清理

`classify_skill_id` 自动识别上述老格式。

## 清理脏数据

前端：`/admin/data-hygiene` → "Skill ID 卫生" tab
- 选中 e2e 测试残留 → 点"批量清理" → 输入 `DELETE` → 确认
- 操作写 audit，git 历史保留，DB 记录消失

后端：
- `GET /api/skills/admin/hygiene?filter_class=e2e_test` — 列出
- `POST /api/skills/admin/hygiene/bulk-cleanup` — 批量删（仅 admin + confirm_text）

## 开发者守则

1. **新增写路径**必须调 `validate_skill_id`
2. **生成 id 不要再 inline `uuid4().hex[:8]`**；走 `id_gen` 里的 3 个函数
3. **e2e 测试**用 `gen_for_test()` 或明确 `test-xxx` 前缀，不再用 `imported-<Date.now().toString(36)>`
4. **数据库审计**每季度跑一次 `classify_skill_id` 扫描，确认 `e2e_test` / `manual` 比例正常
