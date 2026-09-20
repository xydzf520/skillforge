# MiniMax H3 媒体节点运维指南

> **适用范围**：本文记录本仓库固定安装清单与编译器约束，不是已完成 GPU 或外部模型验收的证明，也不保证与上游最新版本一致。安装前核对配置、模型许可、来源清单和目标硬件；实际验证范围见[验证记录](../public/VALIDATION.md)。


## 边界

SkillForge 只负责受控安装、能力观测、调度和审计。H3 的 ComfyUI 推理实际发生在媒体节点 Bridge 上；`agent_purpose=media` 的节点不接收 Skill 定时或文件同步。

平台只接受固定安装配置 `h3_all_modes_v1`，带宽固定为 `3 MB/s`。Bridge 不接受任意下载 URL、Shell 命令或 ComfyUI 工作流。模型、节点、模板和本地 ComfyUI 健康检查全部通过之前，节点必须保持 `media.configured=false`，调度器不得派单。

## 提示词策略

策略版本为 `minimax-h3-context-ir-v1`。DeepSeek `deepseek-v4-flash` 只负责生成结构化创意和分镜，服务端确定性编译器负责校验时间轴、官方运镜白名单、引用角色和限制，并编译最终的 H3 时间轴提示词。

资料优先级：

1. [MiniMax H3 生成指南](https://platform.minimax.io/docs/guides/video-generation)
2. [MiniMax 视频生成 API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)
3. [MiniMax H3 Context-IR](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)
4. [MiniMax 官方 Skills 仓库](https://github.com/MiniMax-AI/skills/tree/main/skills/frontend-dev)

当前代码约束：提示词最多 7000 字符，时长为 4 到 15 秒整数；参考图片最多 9 张，视频和音频各最多 3 个，混合引用最多 12 个；图生视频与参考复刻素材不能混用。本地首版图生视频只开放首帧图。

包装、Logo 或中文文字必须真实时，编译器默认推荐图生视频并要求正确产品图；没有产品图会保留明确警告和人工审核门禁。

## 固定安装清单

来源仓库固定为 `Comfy-Org/MiniMax-H3`，revision 固定为 `014cd40f7e177756c6b2473c0d93b1c89a790dd2`。

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 20970379616 | `e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a` |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 20970379616 | `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779` |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15687142551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| `minimax_h3_video_vae_fp16.safetensors` | 5207808496 | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| `minimax_h3_audio_vae_fp32.safetensors` | 605254808 | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |

ComfyUI 固定为 `v0.31.0`，仅监听 `127.0.0.1:8188`，使用用户级 systemd 服务。安装目录位于 Bridge 数据目录下的隔离 `media/` 目录。`.part` 文件支持续传，哈希不匹配的文件会被隔离并使安装失败。

## 管理命令

先按平台工作流检查登录和能力：

```bash
sf update --check
sf auth status
sf mcp catalog
```

查看进度：

```bash
sf media status <instance_id>
```

先做不下载的环境检查，再在明确接受模型许可后开始正式安装：

```bash
sf media bootstrap <instance_id> --dry-run
sf media bootstrap <instance_id> --accept-license
```

取消不会删除已下载的 `.part` 文件，下一次正式安装会续传：

```bash
sf media cancel <instance_id>
```

管理员节点详情页同步展示真实下载字节、速率、滚动 ETA、当前文件、安装阶段和错误。成功后还需核对能力上报至少包含 `media.configured=true`、`media.online=true`、`workload_roles=["video_generation"]` 和三种支持模式，之后才能做参考复刻派单验收。
