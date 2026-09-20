# 短视频素材复投判断 Demo

这个项目使用 `sf project spec --recipe short-video-analysis` 的标准构建，用于演示 Codex 通过 sf 能力生成可运行 Project Host 项目。

## 运行方式

- 本地预览：直接打开 `web/index.html`，页面会进入离线预览模式。
- 平台运行：执行 `sf project submit --path demo-projects/short-video-reinvest-analysis` 后，在 SkillForge 项目页打开。

## 验证

```bash
.venv/bin/python scripts/sf.py project doctor --path demo-projects/short-video-reinvest-analysis --recipe short-video-analysis --json
```

## 能力覆盖

- 记录投放数据、素材 A、素材 B。
- 上传原视频运行资产。
- 提取 opening / middle / ending 关键帧作为视觉兜底。
- 触发平台 AI 分析。
- 回传 reports / todos / proofs。
