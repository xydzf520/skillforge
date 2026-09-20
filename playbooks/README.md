# ⚠️ 本目录已迁移

Playbook YAML 文件已统一迁到 **`skills-repo/playbooks/`**，由 `git_service` 管理
版本/tag/push，代码侧（`app/playbooks/service.py`）不再读写本目录。

**请勿在此目录下新建 Playbook 文件** —— 会被忽略，且不会进入审核/发布流程。

历史迁移实现见 [迁移脚本](../scripts/migrate_playbooks_to_skills_repo.py)。公开版 CHANGELOG 只记录公开准备变更，不包含原项目 2.0.8 发行记录。

本目录保留仅为兼容旧部署脚本，未来版本会删除。
