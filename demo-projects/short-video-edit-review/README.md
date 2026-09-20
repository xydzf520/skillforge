# 内容电商剪辑复盘 Demo

这个项目使用 `sf project spec --recipe short-video-analysis` 的标准构建，用于演示 Codex 通过 sf 能力生成内容电商剪辑复盘工具。

## 运行方式

- 本地预览：直接打开 `web/index.html`，页面会进入离线预览模式。
- 平台运行：执行 `sf project submit --path demo-projects/short-video-edit-review` 后，在 SkillForge 项目页打开。

## 验证

```bash
.venv/bin/python scripts/sf.py project doctor --path demo-projects/short-video-edit-review --recipe short-video-analysis --json
```

## 能力覆盖

- 记录商品卖点、目标人群、投放数据和素材 A/B。
- 上传原视频运行资产。
- 提取首屏、商品露出、卖点证据和结尾关键帧。
- 按首屏钩子、商品露出、节奏密度、卖点证据、行动引导生成剪辑复盘。
- 回传 reports / todos / proofs。
