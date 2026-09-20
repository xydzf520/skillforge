"""大厅 v3 模块（内部能力资产中心）。

- /api/hall/overview     统一 landing summary（Skill + 数据 + 团队 3 视图）
- /api/hall/data         数据能力列表（Discoverability 过滤）
- /api/hall/data/{id}    数据能力详情（元数据 + my_access + consumers）
- /api/hall/team         团队能力（按部门聚合）
- /api/hall/team/{dept}  团队详情

Skill 能力仍走现有 /api/skills/hall（阶段 4 加 ?include=profile）。
"""
