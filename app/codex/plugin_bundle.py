"""Build the distributable SkillForge Codex plugin bundle."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any


PLUGIN_NAME = "skillforge-codex"
PLUGIN_VERSION = "0.3.7"
from app.config import settings

DEFAULT_PLATFORM_URL = settings.PUBLIC_BASE_URL.rstrip("/")

_REPO_ROOT = Path(__file__).resolve().parents[2]

PLUGIN_RELEASE_HISTORY: list[dict[str, Any]] = [
    {
        "version": "0.3.7",
        "date": "2026-06-23",
        "title": "通用 SF 数据写入与存储",
        "status": "current",
        "notes": [
            "新增通用 SF 数据库存储，Codex/sf 可通过 sf data write 写入 JSON、文本、表格或文件内容，并保留 namespace、visibility、metadata、Skill、Run 和 source lineage。",
            "新增 skillforge_sf_data_write/list/get MCP 工具，支持受控写入、分页检索和单条读取；真实写入必须显式加 --real 与 idempotency-key，默认 dry-run。",
            "前端 SF 数据页面可展示通用 SF 数据记录，支持按 namespace、内容类型、Skill、Run、来源和关键词筛选，方便业务确认 Codex 写入的数据是否已落库。",
            "Manifest #version 页面同步展示通用 SF 数据能力，让新电脑安装入口和 Codex 自更新入口都能发现这组数据写入、存储和读取能力。",
        ],
    },
    {
        "version": "0.3.6",
        "date": "2026-06-22",
        "title": "Tmall 项目数据网关与动态日报输出",
        "status": "released",
        "notes": [
            "Project Gateway 新增 Tmall 链接下滑平台缓存数据读取能力，项目可通过 skillforge_tmall_link_decline_data_latest/get 读取已落库 Artifact，避免项目页面直接访问外部业务系统。",
            "示例品牌天猫链接健康日报的 latest-analysis.json 改为服务端动态输出，优先使用最新 tmall-link-decline-collector-v1 缓存生成诊断 findings，项目包静态快照仅作为兜底。",
            "新增 tmall_link_health_latest_analysis 只读项目能力，Project Service invoke 会返回结构化 data，便于页面展示真实数据来源、诊断过程和输出链路。",
            "Tmall Artifact 内容读取默认支持 5MB，覆盖 3MB 级采集 JSON；所有读取仍经过 SkillForge 权限、Trace 和平台数据边界。",
        ],
    },
    {
        "version": "0.3.5",
        "date": "2026-06-09",
        "title": "云视频周度诊断 Skill 与业务分析 Agent",
        "status": "released",
        "notes": [
            "新增 cloud-video-weekly-diagnosis Skill 模板，Codex 可用 sf skill init 直接生成示例品牌周度同主题视频消耗诊断 Skill。",
            "新增 sf agent analysis list/create/update 和 /api/codex/agents/analysis，Codex 可创建部门业务分析 Agent blueprint，绑定 Skill、prompt 版本、分析维度和管理人。",
            "Agent 页面新增部门业务分析 Agent 展示入口，祁莹莹等部门管理人可看到同主题视频诊断 Agent 与提示词版本。",
            "manifest 页面同步展示业务 Agent、云视频周度诊断模板和项目/模块能力，新增能力继续使用彩色渐变强调。",
        ],
    },
    {
        "version": "0.3.4",
        "date": "2026-06-09",
        "title": "云视频卡审拒因与素材分区口径",
        "status": "released",
        "notes": [
            "云视频素材列表 skillforge_cloud_video_videos 新增 system_auto_label_type/video_state/video_type，可按前端“千川卡审”等系统自动标签和视频分区核卡审条数。",
            "新增 skillforge_cloud_video_audit_rejects，对齐云视频卡审原因弹窗 /api/video/get-audit-reject，可按视频读取巨量千川/巨量广告/腾讯ADQ拒因。",
            "Project Gateway 新增 mcp://skillforge_cloud_video_audit_rejects，短视频分析项目可读取卡审原因并生成具体剪辑和合规改进建议。",
            "明确总上传条数应优先用视频统计报表口径核对，卡审条数从素材列表 systemAutoLabelType/raw.isAuditRejectStr/isQcAuditReject 核对，卡审原因从 audit_rejects 核对。",
        ],
    },
    {
        "version": "0.3.3",
        "date": "2026-06-09",
        "title": "云视频视频统计报表口径",
        "status": "released",
        "notes": [
            "新增 skillforge_cloud_video_video_usage_report，对齐云视频视频统计报表 /api/video/query-video-report，可按团队/分组/个人读取上传条数、下载、推送、剪映和爆款统计。",
            "云视频素材列表 skillforge_cloud_video_videos 新增 system_auto_label_type/video_state，可用前端“千川卡审”等系统自动标签快速核卡审条数。",
            "Project Gateway 新增 mcp://skillforge_cloud_video_video_usage_report，短视频分析项目可同时读取上传统计、卡审素材列表、素材统计和投放消耗。",
            "明确总上传条数应优先用视频统计报表口径核对，卡审条数仍从素材列表 raw.isAuditRejectStr/isQcAuditReject 或后续审核理由接口核对。",
        ],
    },
    {
        "version": "0.3.2",
        "date": "2026-06-09",
        "title": "云视频素材统计与卡审口径补齐",
        "status": "released",
        "notes": [
            "云视频素材列表 skillforge_cloud_video_videos 新增 search_type/search_ids，可按上传人、上传分组、上传团队和分类复现前端筛选口径。",
            "新增 skillforge_cloud_video_material_report，对齐云视频广告平台分析页素材统计接口，返回素材数、消耗、标签项和可用于卡审率核对的统计口径。",
            "Project Gateway 新增 mcp://skillforge_cloud_video_material_report，短视频分析项目可同时读取投放消耗、素材统计和人员维度。",
            "修正云视频数据核对说明：消耗明细、素材列表、素材统计属于不同上游接口，Codex 需要按业务指标选择对应 MCP 能力。",
        ],
    },
    {
        "version": "0.3.1",
        "date": "2026-06-09",
        "title": "云视频投放数据 MCP 与个人改进方案输入",
        "status": "released",
        "notes": [
            "新增云视频服务端自动登录能力，账号凭据只在 SkillForge 服务端配置中使用，Codex、浏览器和插件不接触 token 或密码。",
            "新增 skillforge_cloud_video_accounts/categories/tags/videos/ad_report 等只读 MCP 工具，Codex 可读取云视频人员树、素材分类、标签、素材元数据和个人/明细报表。",
            "新增 skillforge_cloud_video_daily_person_video_report，按天聚合 2026 年 5-6 月每个人消耗、Top 花费视频、低 ROI 视频和可交给视频视觉诊断的分析线索。",
            "项目网关支持声明 mcp://skillforge_cloud_video_daily_person_video_report，短视频分析项目可直接调用真实投放数据，再结合运行资产/关键帧/AI 生成个人改进方案。",
            "manifest 页面和 sf 命令说明同步展示云视频 MCP 示例，新增功能使用彩色渐变强调。",
        ],
    },
    {
        "version": "0.3.0",
        "date": "2026-06-09",
        "title": "项目运行资产与短视频生成标准",
        "status": "released",
        "notes": [
            "新增 sf project spec/org resolve/verify，Codex 可按 short-video-analysis 标准生成项目 manifest、Project Gateway 调用和验证流程。",
            "新增 sf project asset upload/list，通过 Codex CLI token 上传原视频、关键帧和数据文件到 ProjectRunAsset，并写入 Project Gateway Trace。",
            "Project Host 能力说明补齐运行资产 metadata、material_a/material_b、visual_frame、原视频优先与关键帧兜底规则。",
            "插件 manifest 页面同步展示项目资产上传命令、短视频生成标准和当前版本能力，方便 Codex 自动更新并发现新命令。",
            "继续要求 AI、MCP、运行资产、钉钉推送和 Skill 提交审核都通过 SkillForge 控制面完成，不暴露平台密钥。",
        ],
    },
    {
        "version": "0.2.9",
        "date": "2026-06-04",
        "title": "Project Gateway、训练闭环与生产数据能力合并",
        "status": "released",
        "notes": [
            "新增 Project Host / Project Gateway 路由，支持 sf project init/doctor/submit/status/invoke/logs。",
            "新增训练资源、数据集、训练候选、训练任务、部署申请、部署审批/驳回/激活/回滚和产物下载相关 sf 命令。",
            "保留生产环境示例品牌云视频、Tmall 链接下滑与语艺客服会话缓存数据能力，可通过 sf data latest/get 和 skillforge_*_data_* MCP 工具读取平台缓存 Artifact；新增通用 SF 数据库存储，可通过 sf data write/records/record 与 skillforge_sf_data_* 工具写入和读取。",
            "保留 sf run/runs、preview/apply、output validate、schedule、review、market、notify、pipeline 和 Skill pull 的完整运维闭环。",
            "继续要求真实 MCP、钉钉推送、Skill 提交审核和发布都通过 SkillForge 控制面完成，不暴露平台密钥。",
        ],
    },
    {
        "version": "0.2.0",
        "date": "2026-05-28",
        "title": "Codex 端完整运维与发布闭环",
        "status": "released",
        "notes": [
            "新增 sf run 与 sf runs：可从 Codex 触发受控运行，并查看执行状态、步骤、结果、Artifact、日志和失败诊断。",
            "新增 sf preview apply 与 sf output validate：预览输出可先校验报告、待办、动作、通知和大小，再按幂等键正式落库。",
            "新增 sf skill init/create/diff/publish-status、sf schedule、sf review、sf market、sf data、sf notify 和 sf pipeline run，覆盖创建、对比、定时、审核、市场共享、缓存数据、通用数据写入、通知和流水线。",
            "平台保存执行日志尾部并脱敏，Market 增加评分记录；Codex API 补齐运行、审核、调度、市场和输出验证端点。",
            "manifest 页面展示全部版本历史，旧版本默认折叠，新功能使用强调色，页面可见英文标签改为中文。",
        ],
    },
    {
        "version": "0.1.9",
        "date": "2026-05-28",
        "title": "输出结果预览 URL",
        "status": "released",
        "notes": [
            "新增 sf preview <file>：把本地 Skill 输出 JSON、Markdown、HTML 或文本上传到 SkillForge，返回可分享的短期预览 URL。",
            "预览页只读展示报告、待办、动作、通知数量、报告正文、待办表和原始 JSON，方便先核对结果再决定是否正式入库。",
            "预览不会生成正式待办、报告、通知、执行记录或钉钉 outbox，适合本地调试和业务确认。",
        ],
    },
    {
        "version": "0.1.8",
        "date": "2026-05-28",
        "title": "缓存数据能力与共享 Skill 安装",
        "status": "released",
        "notes": [
            "新增按权限共享 Skill 安装：可查看部门共享 Skill，并用 sf skill install / pull 下载当前账号可读的 Skill 包。",
            "把天猫链接下滑、语艺客服会话等每日缓存数据拆成受控数据能力，可通过 skillforge_data_capability_* 和专用 *_data_* 工具读取。",
            "新增执行原始数据 Artifact 查询能力：skillforge_execution_artifact_latest / summary 可查看 raw-input/raw-output 的大小、sha256、schema 和 run 引用。",
            "明确每日 Collector 和语艺数据优先复用平台缓存，避免每次分析都重新下载外部平台数据。",
        ],
    },
    {
        "version": "0.1.7",
        "date": "2026-05-28",
        "title": "内部过渡版",
        "status": "internal",
        "notes": [
            "未作为独立生产更新包发布；相关数据能力、Artifact 能力和共享安装能力合并进入 0.1.8 对外发布。",
        ],
    },
    {
        "version": "0.1.6",
        "date": "2026-05-20",
        "title": "/sf 命令按钮与 Codex 缓存刷新",
        "status": "released",
        "notes": [
            "注册 /sf 命令元数据，sf update 后刷新本地 Codex 插件缓存，让 /sf 出现在命令按钮中。",
        ],
    },
    {
        "version": "0.1.5",
        "date": "2026-05-20",
        "title": "更新日志提示",
        "status": "released",
        "notes": [
            "sf update --check 显示更新内容。",
            "执行普通 sf 命令前，如果发现有新版本，会先提示用户升级。",
        ],
    },
    {
        "version": "0.1.4",
        "date": "2026-05-20",
        "title": "登录授权输出优化",
        "status": "released",
        "notes": [
            "sf auth login 等待浏览器授权时立即输出登录链接，方便 Codex 用户复制并完成授权。",
        ],
    },
    {
        "version": "0.1.3",
        "date": "2026-05-20",
        "title": "我的插件与部门 Skill 列表",
        "status": "released",
        "notes": [
            "新增 sf plugin status 和 sf my。",
            "新增中文别名：我的插件、我的部门插件、我的技能。",
            "通过统一 Skill 权限模型支持部门范围的 Skill 列表。",
            "刷新 manifest 页面，集中展示 sf 支持的命令。",
        ],
    },
    {
        "version": "0.1.2",
        "date": "2026-05-20",
        "title": "首个 Codex 插件发布",
        "status": "released",
        "notes": [
            "新增 SkillForge 组织 MCP 工具，可查询当前账号可见用户，并通过平台 outbox 发送受控钉钉工作通知。",
            "新增 sf mcp call：写入类工具默认 dry-run，真实写入必须提供 --real 和幂等键。",
            "新增可复制的 Codex 插件包，内置 sf CLI 和 SkillForge SDK。",
            "新增 sf update：支持读取 manifest、校验 sha256、刷新 MCP 目录和安装本地 sf shim。",
            "分发插件元数据中不再包含本地仓库绝对路径。",
        ],
    },
]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_repo_bytes(path: str) -> bytes:
    return (_REPO_ROOT / path).read_bytes()


def _json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _text_bytes(text: str) -> bytes:
    return text.strip().encode("utf-8") + b"\n"


def _plugin_json() -> bytes:
    return _json_bytes(
        {
            "name": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "description": (
                "SkillForge control-plane integration for Codex auth, MCP discovery, "
                "Skill pull, validation, submission, review tracking, and self-update."
            ),
            "author": {
                "name": "SkillForge",
                "email": "admin@skillforge.local",
                "url": DEFAULT_PLATFORM_URL,
            },
            "homepage": DEFAULT_PLATFORM_URL,
            "repository": f"{DEFAULT_PLATFORM_URL}/api/codex/catalog/manifest",
            "license": "UNLICENSED",
            "keywords": ["skillforge", "mcp", "skill-publishing", "codex"],
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "interface": {
                "displayName": "SkillForge",
                "shortDescription": "Authenticate, test, submit, update, and track SkillForge Skills from Codex.",
                "longDescription": (
                    "Provides Codex with the SkillForge CLI workflow and MCP stdio proxy for governed "
                    "Skill validation, real MCP checks, submission, review status tracking, and plugin "
                    "updates. Secrets remain in SkillForge platform auth and are never stored in the plugin."
                ),
                "developerName": "SkillForge",
                "category": "Productivity",
                "capabilities": ["Authentication", "MCP", "Skill Pull", "Skill Publishing", "Self Update"],
                "websiteURL": DEFAULT_PLATFORM_URL,
                "privacyPolicyURL": DEFAULT_PLATFORM_URL,
                "termsOfServiceURL": DEFAULT_PLATFORM_URL,
                "defaultPrompt": [
                    "Use /sf update to update the SkillForge Codex plugin.",
                    "Use /sf auth to check SkillForge auth.",
                    "Use /sf mcp to inspect SkillForge MCP tools.",
                ],
                "brandColor": "#2563EB",
            },
        }
    )


def _mcp_json() -> bytes:
    return _json_bytes({"mcpServers": {"skillforge": {"command": "sf", "args": ["mcp", "stdio"]}}})


def _command_sf_md() -> bytes:
    return _text_bytes(
        """
---
description: Run SkillForge sf commands for update, login, my plugins, department plugins, Agent coverage, business analysis Agent blueprints, MCP catalog/calls, platform AI, raw run data, cached data, Tmall data, cloud-video Skill templates, run tracking, previews, project hosting, training jobs/deployments, notifications, schedules, reviews, market, Skill pull, tests, and Skill submission.
argument-hint: "[update|plugin doctor|auth|agent coverage|agent analysis|mcp|data latest tmall|data write|data records|notify|run|runs|preview|ai analyze|raw query|run analyze|run candidate|project spec|project org resolve|project init|project doctor|project submit|project verify|project status|project invoke|project asset upload|training datasets|training jobs|training deployments|我的插件|我的部门插件|拉取 <skill_id>|doctor|test|submit]"
allowed-tools: [Bash, Read]
---

# /sf

Run a SkillForge command through the local `sf` CLI and the `$skillforge-publisher` skill.

## Arguments

- `command`: SkillForge command text after `/sf`, such as `update`, `plugin status`, `plugin doctor`, `我的插件`, `我的部门插件`, `auth`, `capabilities`, `agent coverage`, `agent analysis list`, `agent analysis create`, `mcp`, `data`, `data latest tmall`, `data write --namespace demo --json '{...}'`, `data records --namespace demo`, `notify`, `run`, `runs`, `preview`, `ai analyze`, `raw query`, `run analyze`, `run candidate`, `project spec --recipe short-video-analysis`, `project org resolve --department ... --owner ...`, `project init`, `project doctor`, `project submit`, `project verify <project_id>`, `project status <project_id>`, `project invoke <project_id> --input-json '{...}'`, `project asset upload <project_run_id> <file>`, `training create`, `training resources`, `training datasets`, `training candidate <skill_id>`, `training jobs`, `training deploy-request <job_id>`, `training deployments`, `拉取 <skill_id>`, `schedule`, `market`, `doctor`, `test`, `submit`, or `review <submission_id>`.
- The user invoked this command with: $ARGUMENTS

## Workflow

1. Use `$skillforge-publisher`.
2. Route the text after `/sf` to the local `sf` CLI.
3. If authentication is needed, run `sf auth login` and use the browser authorization flow.
4. Never read, print, write, or bypass platform secrets, cookies, API keys, or MCP credentials.
5. For Skill submit/publish work, use SkillForge review and submission APIs rather than editing platform state directly.

## Common Routes

- `/sf` or `/sf help`: print local Chinese help; do not call the platform or trigger login.
- `/sf update`, `/sf 更新`, or `/sf 升级`: run `sf update`; for local manifest preview use `sf update --check --base-url <url>` and keep it transient unless the user explicitly asks to persist.
- `/sf plugin status` or `/sf 插件状态`: run `sf plugin status`; `--base-url <url>` is a transient check by default.
- `/sf plugin doctor --fix` or `/sf 修复插件`: run `sf plugin doctor --fix` to diagnose local install, auth, MCP, Project API, and repair marketplace/config/shim.
- `/sf 我的插件`: run `sf my` to inspect the local Codex plugin and list your owned/editable Skills.
- `/sf 我的部门插件`: run `sf skill list --scope department`.
- `/sf 安装 <skill_id>`: run `sf skill install <skill_id>` to download a readable shared Skill into a local workspace.
- `/sf auth` or `/sf 我是谁`: run `sf auth status`.
- `/sf 登录` or `/sf 授权`: run `sf auth login`.
- `/sf capabilities` or `/sf 能力`: run `sf catalog refresh`, `sf capabilities`, `sf platform capabilities`, and `sf editor capabilities`.
- `/sf agent coverage` or `/sf Agent覆盖`: run `sf agent coverage` to inspect department execution/analysis/training Agent coverage and platform fallback.
- `/sf agent analysis list --department "示例品牌内容电商运营部"`: list department business analysis Agent blueprints that bind Skills, prompt versions, dimensions, and editors.
- `/sf agent analysis create --name "示例品牌同主题视频消耗诊断 Agent" --department "示例品牌内容电商运营部" --skill-id samplebrand-weekly-video-diagnosis --owner "祁莹莹" --prompt-version analysis_v1`: create or update a governed business analysis Agent for Codex-generated Skills.
- `/sf mcp`: run `sf mcp catalog` for a readable summary; use `sf mcp catalog --json` for the raw catalog.
- `/sf mcp call skillforge_org_search_users --args '{"query":"张三"}'`: query visible organization users.
- `/sf mcp call skillforge_cloud_video_session --args '{}'`: verify SkillForge server-side cloud-video login without exposing token or password.
- `/sf mcp call skillforge_cloud_video_accounts --args '{"max_people":20}'`: read the cloud-video team/group/person tree for report account IDs.
- `/sf mcp call skillforge_cloud_video_daily_person_video_report --args '{"start_date":"2026-05-01","end_date":"2026-06-09","top_videos_per_person":5,"low_videos_per_person":5}'`: read daily person spend and video spend facts for short-video personal improvement plans.
- `/sf mcp call skillforge_cloud_video_material_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","search_type":1}'`: read cloud-video material statistics aligned with the ad analysis page for upload-person/group/team checks.
- `/sf mcp call skillforge_cloud_video_video_usage_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","data_type":3,"ids":["4016"]}'`: read cloud-video video usage statistics for upload-count reconciliation.
- `/sf mcp call skillforge_cloud_video_audit_rejects --args '{"video_id":"100715117","platform_type":2}'`: read cloud-video audit reject reasons for a card-audited video.
- `/sf ai analyze --prompt "分析这次运行" --context-json '{...}'`: call platform AI through SkillForge Gateway with server-side model credentials.
- `/sf raw query --source decision_logs --skill-id <skill_id>`: query redacted raw Skill run data through SkillForge Gateway and Skill permissions.
- `/sf run analyze --run-id <execution_run_id>`: collect redacted Skill run data and call platform AI for one-step run diagnosis.
- `/sf run candidate --run-id <execution_run_id>`: create an awaiting-review training candidate from redacted run diagnosis lineage.
- `/sf project spec --recipe short-video-analysis --json`: return the Project Host contract, Gateway SDK rules, run asset metadata rules, visual-analysis fallback contract, and short-video generation recipe for Codex to generate code.
- `/sf project org resolve --department "示例品牌内容电商运营部" --owner "祁莹莹" --visibility company`: resolve department/owner IDs and project creation permissions before Codex writes `projectforge.yaml`.
- `/sf project init`: run `sf project init --path .` for static projects, or add `--kind external_web --entry-url <url>` for external URL projects; generates `projectforge.yaml` and Project Gateway SDK examples.
- `/sf project doctor`: run `sf project doctor --path .`; for generated short-video projects run `sf project doctor --path . --recipe short-video-analysis --json` and let Codex fix failed `checks[]`.
- `/sf project submit`: run `sf project submit --path .` to auto-register entry_url projects or upload static packages through SkillForge Project Host; do not write platform static dirs directly.
- `/sf project verify <project_id> --recipe short-video-analysis --json`: invoke the uploaded project service and read redacted logs/trace so Codex can verify the generated project loop.
- `/sf project asset upload <project_run_id> <file> --asset-type visual_frame --role material_a --material-role material_a --frame-time 2 --frame-label opening`: upload a run asset through the Codex token so videos/keyframes enter ProjectRunAsset and Gateway Trace.
- `/sf project status <project_id>`, `/sf project invoke <project_id> --input-json '{...}'`, `/sf project asset list <project_run_id>`, and `/sf project logs <project_run_id> [--limit N] [--offset N|--ingress-cursor C --capability-cursor C]`: inspect or invoke uploaded projects through the Codex CLI token, with redacted input/output/assets/capability gateway records.
- `/sf skill init samplebrand-weekly-video-diagnosis --template cloud-video-weekly-diagnosis --department "示例品牌内容电商运营部" --trigger-type cron --cron '0 8 * * *'`: scaffold a directly runnable cloud-video weekly diagnosis Skill with MCP calls, prompt `analysis_v1`, reports, todos, and DingTalk notification plumbing.
- `/sf training resources`: list visible training Agent/GPU resources, including platform fallback summaries.
- `/sf training datasets` and `/sf training readiness <skill_id>`: inspect data asset readiness without returning raw DecisionLog payloads.
- `/sf training candidate <skill_id>`: create an awaiting-review training candidate from a Skill's governed data asset readiness.
- `/sf training jobs --status running`: list training jobs and their model, route, progress, ETA, and gateway.
- `/sf training create --title "训练新模型" --target-skill-id <skill_id>`: create an awaiting-review training job with optional model, dataset, parameter, and gateway fields.
- `/sf training approve|dispatch|retry|collect|evaluate|cancel <training_job_id>` and `/sf training collect-due`: trigger governed training state-machine actions and batch result compensation through SkillForge permissions.
- `/sf training deploy-request <training_job_id> --target-skill-id <skill_id>` and `/sf training deployments`: request and inspect model deployments.
- `/sf training deployment-approve|deployment-reject|deployment-activate|deployment-rollback <deployment_id>`: move a model deployment through approval, rejection, activation, or rollback.
- `/sf training download-artifact <training_job_id> <artifact_id>`: download a model artifact through the training Agent proxy and server-side permission checks.
- `/sf mcp call skillforge_dingtalk_send_work_notice --args '{"user_ids":["u1"],"title":"标题","markdown":"正文"}'`: dry-run an organization DingTalk push; add `--real --idempotency-key <key>` to enqueue a real work notice.
- `/sf 拉取 <skill_id>` or `/sf 同步到本地 <skill_id>`: run `sf skill pull <skill_id> --path .`.
- `/sf mcp call skillforge_data_capability_list --args '{}'`: list platform-cached data capabilities such as SampleBrand cloud-video data, Tmall link-decline data and Yuyi customer-service data.
- `/sf mcp call skillforge_sf_data_write --args '{"namespace":"demo","data":{"hello":"world"}}' --real --idempotency-key <key>`: write generic SF data; defaults to dry-run unless `--real` is supplied.
- `/sf mcp call skillforge_sf_data_list --args '{"namespace":"demo"}'`: list generic SF data records.
- `/sf mcp call skillforge_sf_data_get --args '{"id":"sfdata_xxx"}'`: read a generic SF data record.
- `/sf mcp call skillforge_samplebrand_cloud_video_data_latest --args '{}'`: inspect the latest cached SampleBrand cloud-video weekly raw data artifact summary.
- `/sf mcp call skillforge_tmall_link_decline_data_latest --args '{}'`: inspect the latest cached Tmall link-decline raw data artifact summary.
- `/sf mcp call skillforge_execution_artifact_latest --args '{"skill_id":"tmall-link-decline-collector-v1"}'`: inspect the latest platform-persisted raw execution artifact summary and size.
- `/sf data list`: list platform-cached data capabilities.
- `/sf data write --namespace demo --json '{"hello":"world"}' --real --idempotency-key <key>`: write generic SF data.
- `/sf data records --namespace demo`: list generic SF data records.
- `/sf data record <sfdata_id>`: inspect one generic SF data record.
- `/sf data latest samplebrand`: inspect the latest cached SampleBrand cloud-video artifact summary.
- `/sf data latest tmall`: inspect the latest cached Tmall link-decline artifact summary.
- `/sf notify users --query 示例成员甲`: search visible organization recipients.
- `/sf notify send --title 标题 --markdown 正文 --query 示例成员4`: dry-run a DingTalk notice; add `--real --idempotency-key <key>` for real enqueue.
- `/sf run <skill_id> --params '{"dry_run":false}'`: trigger a governed Skill execution.
- `/sf runs status <run_id>`: inspect run status.
- `/sf runs result <run_id>`: inspect generated output, reports, and todos.
- `/sf runs logs <run_id>`: inspect redacted runtime log tails.
- `/sf preview output.json`: upload a local Skill output JSON/Markdown/HTML/text file to SkillForge and return a read-only preview URL.
- `/sf preview apply <preview_id>`: dry-run formal result creation from a preview; add `--real --idempotency-key <key>` to persist.
- `/sf output validate output.json`: validate output contract and size.
- `/sf doctor`: run `sf skill doctor --path .`.
- `/sf doctor --remote`: check local package plus remote publish status, schedule, auth, and MCP catalog.
- `/sf test`: run `sf skill test --local --path .`.
- `/sf test-real --shop-id <id>`: run `sf skill test --real-mcp --path . --shop-id <id>`.
- `/sf sandbox`: run `sf skill sandbox --path .`.
- `/sf skill init <skill_id>`: create a local Skill scaffold.
- `/sf skill diff --path .`: compare local package files with the readable platform package.
- `/sf skill publish-status <skill_id>`: inspect latest review, sync attempts, schedules, and last run.
- `/sf schedule get <skill_id>` / `/sf schedule set <skill_id> --cron '50 7 * * *'` / `/sf schedule stop <skill_id>`: manage node schedule configuration through SkillForge.
- `/sf submit`, `/sf 上传`, or `/sf 发布`: run `sf skill submit --path .`.
- `/sf review <submission_id>`: run `sf submission status <submission_id>`.
- `/sf review list`, `/sf review get <review_id>`, `/sf review comment <review_id> --content ...`, `/sf review approve <review_id>`: operate Review records through SkillForge permissions.
- `/sf market list`, `/sf market install <skill_id>`, `/sf market rate <skill_id> --rating 5`: inspect, install, and rate shared Skills.
- `/sf pipeline run --path . --remote --submit`: run local checks, remote checks, and optional submit as one guarded pipeline.
"""
    )


def _skill_sf_md() -> bytes:
    return _text_bytes(
        """
---
name: sf
description: Use SkillForge commands from Codex CLI, including update, plugin status, my plugins, department plugins, auth, capabilities, mcp, business analysis Agent blueprints, cloud-video weekly diagnosis Skill templates, data, Tmall cached data, notify, run tracking, preview apply, project hosting, training, schedules, reviews, market, pull, doctor, test, submit, and pipeline.
---

# SkillForge CLI Command

Use this skill when the user invokes SkillForge from Codex CLI with `/sf ...`, `$sf`, or short phrases such as `update`, `plugin status`, `plugin doctor`, `我的插件`, `我的部门插件`, `auth`, `capabilities`, `mcp`, `agent analysis`, `data`, `data latest tmall`, `notify`, `run`, `runs`, `preview`, `schedule`, `review`, `market`, `project spec`, `project org resolve`, `project init`, `project doctor`, `project submit`, `project verify`, `training create`, `training jobs`, `training resources`, `training deployments`, `拉取 <skill_id>`, `doctor`, `test`, `submit`, or `pipeline`.

SkillForge is the control plane. Codex may inspect local Skill packages and call the authorized `sf` CLI, but authorization, real MCP/API calls, Git ingestion, review, publish, and node deployment must go through SkillForge.

## Command Routing

Use the local `sf` CLI from PATH. If `sf` is not available and this plugin directory is visible, run `python3 scripts/sf.py update --install-sf-shim` from the plugin root to install the shim.

- `update`, `更新`, `升级`: run `sf update`.
- `help`, `帮助`, or no arguments: run `sf help`; do not call the platform or trigger login.
- `plugin status`, `插件状态`, `我的codex插件`: run `sf plugin status`.
- `plugin doctor`, `插件诊断`, `修复插件`: run `sf plugin doctor`; add `--fix` to repair marketplace, Codex config, and sf shim.
- `我的插件`: run `sf my`.
- `我的技能`: run `sf skill list --scope mine`.
- `我的部门插件`, `部门插件`, `我的部门技能`: run `sf skill list --scope department`.
- `安装 <skill_id>`, `下载 <skill_id>`: run `sf skill install <skill_id>`.
- `auth`, `我是谁`: run `sf auth status`; if it fails, run `sf auth login`.
- `登录`, `授权`: run `sf auth login`.
- `capabilities`, `能力`: run `sf catalog refresh`, `sf capabilities`, `sf platform capabilities`, and `sf editor capabilities`.
- `agent coverage`, `Agent覆盖`: run `sf agent coverage`.
- `agent analysis list --department "示例品牌内容电商运营部"`: list governed department business analysis Agent blueprints.
- `agent analysis create --name "示例品牌同主题视频消耗诊断 Agent" --department "示例品牌内容电商运营部" --skill-id <skill_id> --owner "祁莹莹" --prompt-version analysis_v1`: create or update a department business analysis Agent blueprint.
- `mcp`: run `sf mcp catalog` for a readable summary; use `sf mcp catalog --json` for raw JSON.
- `mcp call <tool> --args '{...}'`: call an authorized SkillForge MCP tool; write tools default to dry-run unless `--real --idempotency-key <key>` is supplied.
- `mcp call skillforge_cloud_video_daily_person_video_report --args '{"start_date":"2026-05-01","end_date":"2026-06-09"}'`: read daily person and video spend from cloud-video for short-video project analysis.
- `mcp call skillforge_cloud_video_material_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","search_type":1}'`: read material count/cost/tag statistics from cloud-video for card-audit and material-quality checks.
- `mcp call skillforge_cloud_video_video_usage_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","data_type":3,"ids":["4016"]}'`: read upload count and usage statistics from cloud-video for reconciliation.
- `mcp call skillforge_cloud_video_audit_rejects --args '{"video_id":"100715117","platform_type":2}'`: read card-audit reject reasons for a specific cloud-video material.
- `ai analyze --prompt "..." --context-json '{...}'`: call platform AI through SkillForge Gateway with server-side model credentials.
- `raw query --source decision_logs --skill-id <skill_id>`: query redacted raw Skill run data through SkillForge Gateway and Skill permissions.
- `run analyze --run-id <execution_run_id>`: collect redacted Skill run data and call platform AI for one-step run diagnosis.
- `run candidate --run-id <execution_run_id>`: create an awaiting-review training candidate from redacted run diagnosis lineage.
- `project spec --recipe short-video-analysis --json`: return the Project Host contract, Gateway SDK methods, run asset metadata rules, visual-analysis fallback contract, and short-video generation recipe.
- `project org resolve --department "示例品牌内容电商运营部" --owner "祁莹莹" --visibility company`: resolve organization and owner IDs plus current project permissions before Codex writes manifest metadata.
- `project init`: run `sf project init --path .` for static projects, or `sf project init --kind external_web --entry-url <url>` for external URL projects.
- `project doctor`: run `sf project doctor --path .`; for generated short-video projects run `sf project doctor --path . --recipe short-video-analysis --json` and fix failed `checks[]`.
- `project submit`, `project 上传`: run `sf project submit --path .` through SkillForge Project Host; entry_url projects auto-register and static projects upload, without bypassing platform review/gateway boundaries.
- `project verify <project_id> --recipe short-video-analysis --json`: invoke the uploaded project service and read redacted logs/trace to verify the generated project loop.
- `project asset upload <project_run_id> <file>` / `project asset list <project_run_id>`: upload and list run assets such as original videos and visual_frame keyframes through the Codex token.
- `project status <project_id>` / `project invoke <project_id> --input-json '{...}'` / `project logs <project_run_id> [--limit N] [--offset N|--ingress-cursor C --capability-cursor C]`: inspect or invoke project services and view paginated redacted input/output/asset/capability records.
- `skill init samplebrand-weekly-video-diagnosis --template cloud-video-weekly-diagnosis --department "示例品牌内容电商运营部" --trigger-type cron --cron '0 8 * * *'`: scaffold a runnable cloud-video weekly diagnosis Skill with prompt `analysis_v1`.
- `training resources`: run `sf training resources` to inspect visible training Agent/GPU capacity and platform fallback.
- `training datasets` / `training readiness <skill_id>`: inspect training data asset readiness without raw DecisionLog payloads.
- `training candidate <skill_id>`: create an awaiting-review candidate from a Skill's governed data assets.
- `training jobs --status running`: run `sf training jobs --status running` to inspect training progress, route, ETA, model name, and gateway.
- `training create --title "训练新模型" --target-skill-id <skill_id>`: create an awaiting-review training job with model, dataset, parameters, and gateway hints.
- `training approve|dispatch|retry|collect|evaluate|cancel <training_job_id>` and `training collect-due`: run governed training state-machine actions and batch result compensation through SkillForge permissions.
- `training deploy-request <training_job_id> --target-skill-id <skill_id>` and `training deployments`: request and inspect model deployments.
- `training deployment-approve|deployment-reject|deployment-activate|deployment-rollback <deployment_id>`: approve, reject, activate, or roll back a model deployment.
- `training download-artifact <training_job_id> <artifact_id>`: download a model artifact through the governed training Agent proxy.
- `拉取 <skill_id>`, `同步到本地 <skill_id>`: run `sf skill pull <skill_id> --path .`.
- `mcp call skillforge_data_capability_list --args '{}'`: list platform-cached data capabilities such as SampleBrand cloud-video data, Tmall link-decline data and Yuyi customer-service data.
- `mcp call skillforge_samplebrand_cloud_video_data_latest --args '{}'`: inspect latest cached SampleBrand cloud-video collector data summary without re-downloading external data.
- `mcp call skillforge_tmall_link_decline_data_latest --args '{}'`: inspect latest cached Tmall collector data summary without re-downloading external data.
- `mcp call skillforge_execution_artifact_latest --args '{"skill_id":"tmall-link-decline-collector-v1"}'`: inspect latest platform-persisted raw data artifact summary.
- `data list`: run `sf data list` to list platform-cached data capabilities.
- `mcp call skillforge_sf_data_write --args '{"namespace":"demo","data":{"hello":"world"}}' --real --idempotency-key <key>`: run `sf mcp call skillforge_sf_data_write --args '{"namespace":"demo","data":{"hello":"world"}}' --real --idempotency-key <key>` to write generic SF data through MCP.
- `mcp call skillforge_sf_data_list --args '{"namespace":"demo"}'`: run `sf mcp call skillforge_sf_data_list --args '{"namespace":"demo"}'` to list generic SF data records.
- `mcp call skillforge_sf_data_get --args '{"id":"sfdata_xxx"}'`: run `sf mcp call skillforge_sf_data_get --args '{"id":"sfdata_xxx"}'` to read one generic SF data record.
- `data write --namespace demo --json '{"hello":"world"}' --real --idempotency-key <key>`: run `sf data write --namespace demo --json '{"hello":"world"}' --real --idempotency-key <key>` to write generic SF data.
- `data records --namespace demo`: run `sf data records --namespace demo` to list generic SF data records.
- `data record <sfdata_id>`: run `sf data record <sfdata_id>` to inspect one generic SF data record.
- `data latest samplebrand`: run `sf data latest samplebrand` to inspect latest cached SampleBrand cloud-video collector data.
- `data latest tmall`: run `sf data latest tmall` to inspect latest cached Tmall collector data.
- `notify users --query 示例成员甲`: run `sf notify users --query 示例成员甲` to find visible notification recipients.
- `notify send --title 标题 --markdown 正文 --query 示例成员4`: dry-run a DingTalk notice; add `--real --idempotency-key <key>` for real enqueue.
- `run <skill_id>`: run `sf run <skill_id>` to trigger governed execution.
- `runs status <run_id>`, `runs result <run_id>`, `runs logs <run_id>`: run `sf runs status <run_id>`, `sf runs result <run_id>`, and `sf runs logs <run_id>` to inspect execution state, outputs, and redacted logs.
- `preview output.json`: run `sf preview output.json` to upload local Skill output and get a read-only preview URL.
- `preview apply <preview_id>`: run `sf preview apply <preview_id>` to dry-run formal persistence; add `--real --idempotency-key <key>` for actual write.
- `output validate output.json`: validate reports/todos/actions/notifications and output size.
- `doctor`: run `sf skill doctor --path .`.
- `doctor --remote`: run `sf doctor --remote --path .`.
- `test`: run `sf skill test --local --path .`.
- `test-real --shop-id <id>`: run `sf skill test --real-mcp --path . --shop-id <id>`.
- `sandbox`: run `sf skill sandbox --path .`.
- `skill init <skill_id>`: create a local Skill scaffold.
- `skill diff --path .`: compare local files with platform package.
- `skill publish-status <skill_id>`: inspect publish, sync, schedule, and last run state.
- `schedule get/set/stop/sync <skill_id>`: manage node schedule through SkillForge.
- `submit`, `上传`, `发布`: run `sf skill submit --path .`.
- `review <submission_id>`: run `sf submission status <submission_id>`.
- `review list/get/comment/request-changes/approve/reject`: operate Review records through SkillForge permissions.
- `market list/detail/install/metrics/certify/visibility/ratings/rate`: run `sf market list`, `sf market install <skill_id>`, and `sf market rate <skill_id> --rating 5` to inspect, install, certify, and rate shared Skills.
- `pipeline run --path . --remote --submit`: run local check, remote check, and optional submit.
- `查看哪些技能`: run `sf skill list --scope visible`.
- `我的技能`: run `sf skill list --scope mine`.
- `我的部门插件`: run `sf skill list --scope department`.
- `安装 <skill_id>`, `下载 <skill_id>`: run `sf skill install <skill_id>` to download a readable shared Skill locally.
- `查看我能编辑哪些技能`: run `sf skill list --scope editable`.
- `查看我能发布哪些技能`, `哪些可发布`: run `sf skill list --scope publishable`.
- `哪些未上传`: run `sf skill scan --path . --compare-remote`.
- `local pending`: run `sf skill scan --path . --compare-remote`.
- `当前目录有没有上传`: run `sf skill status --path .`.
- `status <submission_id>`: run `sf submission status <submission_id>`.
- `review <submission_id>`: run `sf submission status <submission_id>`.

## Safety

- Never read, request, persist, or print platform MCP env, Cookie, API key, DingTalk access_token, business-system token, or `.env` contents.
- Do not write SkillForge platform `skills-repo/` or project static directories directly for submissions; use `sf skill submit` / `sf project submit`.
- Do not bypass SkillForge review or publish state.
- Runtime Skill code should call `skillforge_sdk.SkillForge.fetch_api("mcp://tool_name", ...)`; browser project code should use Project Gateway/postMessage instead of platform secrets.
- Skill scripts should return `todos` and `reports`; they must not send DingTalk messages directly.
"""
    )


def _publisher_skill_md() -> bytes:
    return _text_bytes(
        """
---
name: skillforge-publisher
description: Use when a user wants Codex to update the SkillForge plugin, inspect plugin status, list my or department Skills, pull a governed Skill package, validate, preview, apply outputs, trigger or inspect runs, manage schedules, inspect cached data such as Tmall link-decline artifacts, locally debug, call authorized real SkillForge MCP/API tools, package, submit, list visible SkillForge Skills, find local Skills not uploaded, check review status, operate market sharing, or handle slash-style commands starting with /sf. This skill must use SkillForge auth, the sf CLI, MCP Gateway, and submission APIs; it must not access platform secrets directly or bypass SkillForge review.
---

# SkillForge Publisher

## Core Rule

SkillForge is the control plane. Local Codex may inspect and edit a Skill package, but authorization, real MCP/API calls, Git ingestion, review, publish, and node deployment must go through SkillForge.

Never read, request, persist, or print platform MCP env, Cookie, API key, DingTalk access_token, or business-system token.

## Command Routing

Use the local `sf` CLI first. If `sf` is not on PATH and this plugin directory is visible, run `python3 scripts/sf.py update --install-sf-shim` from the plugin root.

For `/sf ...` messages, map the text after `/sf` to these commands:

- `/sf`, `/sf help`, `/sf 帮助`: `sf help` and no platform request.
- `/sf update`, `/sf 更新`, `/sf 升级`: `sf update`; local/gray manifest checks can use `sf update --check --base-url <url>` without rewriting persisted base_url.
- `/sf plugin status`, `/sf 插件状态`, `/sf 我的codex插件`: `sf plugin status`; `--base-url <url>` is transient unless paired with `--persist-base-url`.
- `/sf plugin doctor`, `/sf 插件诊断`, `/sf 修复插件`: `sf plugin doctor`; add `--fix` to repair marketplace, Codex config, and sf shim.
- `/sf 我的插件`: `sf my`.
- `/sf 我的技能`: `sf skill list --scope mine`.
- `/sf 我的部门插件`, `/sf 部门插件`, `/sf 我的部门技能`: `sf skill list --scope department`.
- `/sf 安装 <skill_id>`, `/sf 下载 <skill_id>`: `sf skill install <skill_id>`.
- `/sf auth`, `/sf 我是谁`: `sf auth status`; if it fails, run `sf auth login`.
- `/sf 登录`, `/sf 授权`: `sf auth login`.
- `/sf 查看哪些技能`: `sf skill list --scope visible`.
- `/sf 查看我能编辑哪些技能`: `sf skill list --scope editable`.
- `/sf 查看我能发布哪些技能`, `/sf 哪些可发布`: `sf skill list --scope publishable`.
- `/sf 哪些未上传`: `sf skill scan --path . --compare-remote`.
- `/sf local pending`: `sf skill scan --path . --compare-remote`.
- `/sf 当前目录有没有上传`: `sf skill status --path .`.
- `/sf status <submission_id>`: `sf submission status <submission_id>`.
- `/sf 拉取 <skill_id>`, `/sf 同步到本地 <skill_id>`: `sf skill pull <skill_id> --path .`.
- `/sf mcp`: `sf mcp catalog`（默认摘要；原始 JSON 用 `sf mcp catalog --json`）。
- `/sf agent coverage`: `sf agent coverage`，查看部门执行/分析/训练 Agent 覆盖度与平台兜底。
- `/sf agent analysis list --department "示例品牌内容电商运营部"`: `sf agent analysis list --department "示例品牌内容电商运营部"`，查看部门业务分析 Agent blueprint。
- `/sf agent analysis create --name "示例品牌同主题视频消耗诊断 Agent" --department "示例品牌内容电商运营部" --skill-id <skill_id> --owner "祁莹莹" --prompt-version analysis_v1`: 创建或更新绑定 Skill/prompt/维度/管理人的业务分析 Agent。
- `/sf mcp call skillforge_org_search_users --args '{"query":"张三"}'`: query visible organization users.
- `/sf mcp call skillforge_cloud_video_session --args '{}'`: 验证云视频服务端登录态，不暴露 token 或密码。
- `/sf mcp call skillforge_cloud_video_accounts --args '{"max_people":20}'`: 读取云视频团队、分组和人员树，拿到报表 account_id。
- `/sf mcp call skillforge_cloud_video_daily_person_video_report --args '{"start_date":"2026-05-01","end_date":"2026-06-09","top_videos_per_person":5,"low_videos_per_person":5}'`: 读取每日人员消耗和视频消耗，用于短视频项目生成个人改进方案。
- `/sf mcp call skillforge_cloud_video_material_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","search_type":1}'`: 读取云视频广告平台分析页素材统计，用于按上传人/分组/团队核对素材数、消耗和卡审相关口径。
- `/sf mcp call skillforge_cloud_video_video_usage_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","data_type":3,"ids":["4016"]}'`: 读取云视频视频统计报表，用于核对团队/分组/个人上传条数。
- `/sf mcp call skillforge_cloud_video_audit_rejects --args '{"video_id":"100715117","platform_type":2}'`: 读取云视频具体视频的卡审拒因，用于汇总卡审原因和改进建议。
- `/sf ai analyze --prompt "分析这次运行" --context-json '{...}'`: call platform AI through SkillForge Gateway with server-side model credentials.
- `/sf raw query --source decision_logs --skill-id <skill_id>`: query redacted raw Skill run data through SkillForge Gateway and Skill permissions.
- `/sf run analyze --run-id <execution_run_id>`: collect redacted Skill run data and call platform AI for one-step run diagnosis.
- `/sf run candidate --run-id <execution_run_id>`: create an awaiting-review training candidate from redacted run diagnosis lineage.
- `/sf project init`: run `sf project init --path .` for static projects, or add `--kind external_web --entry-url <url>` for external URL projects; generates `projectforge.yaml` and Project Gateway SDK examples.
- `/sf project doctor`: run `sf project doctor --path .` to validate a lightweight web project package.
- `/sf project submit`: run `sf project submit --path .` to auto-register entry_url projects or upload static packages through SkillForge Project Host; do not write platform static dirs directly.
- `/sf project asset upload <project_run_id> <file>` / `/sf project asset list <project_run_id>`: upload/list ProjectRunAsset entries such as original videos and keyframes through SkillForge Project Host.
- `/sf project status <project_id>`, `/sf project invoke <project_id> --input-json '{...}'`, and `/sf project logs <project_run_id> [--limit N] [--offset N|--ingress-cursor C --capability-cursor C]`: inspect/invoke project service runs and paginated redacted input/output/asset/capability gateway records.
- `/sf skill init samplebrand-weekly-video-diagnosis --template cloud-video-weekly-diagnosis --department "示例品牌内容电商运营部" --trigger-type cron --cron '0 8 * * *'`: 生成可运行的云视频周度同主题消耗诊断 Skill，包含 MCP 数据读取、analysis_v1 prompt、报告/待办输出和钉钉推送配置。
- `/sf training resources`: `sf training resources`，查看训练 Agent/GPU 资源和平台兜底。
- `/sf training datasets` / `/sf training readiness <skill_id>`: 查看训练数据资产门禁，不返回 DecisionLog 原始正文。
- `/sf training candidate <skill_id>`: 基于 Skill 历史数据资产生成待审批训练候选。
- `/sf training jobs --status running`: `sf training jobs --status running`，查看训练任务进度、路由、预计耗时、模型名和网关。
- `/sf training create --title "训练新模型" --target-skill-id <skill_id>`: 创建待审批训练任务，可带模型、数据、参数和网关。
- `/sf training approve|dispatch|retry|collect|evaluate|cancel <training_job_id>` 和 `/sf training collect-due`: 通过 SkillForge 权限触发训练审批、下发、结果同步、批量补偿、评估或取消。
- `/sf training deploy-request <training_job_id> --target-skill-id <skill_id>` 和 `/sf training deployments`: 申请并查看模型部署。
- `/sf training deployment-approve|deployment-reject|deployment-activate|deployment-rollback <deployment_id>`: 审批、驳回、激活或回滚模型部署。
- `/sf training download-artifact <training_job_id> <artifact_id>`: 通过训练 Agent 代理和平台权限下载模型产物。
- `/sf mcp call skillforge_dingtalk_send_work_notice --args '{"user_ids":["u1"],"title":"标题","markdown":"正文"}'`: dry-run an organization DingTalk push; add `--real --idempotency-key <key>` to enqueue.
- `/sf mcp call skillforge_data_capability_list --args '{}'`: list platform-cached data capabilities such as SampleBrand cloud-video data, Tmall link-decline data and Yuyi customer-service data.
- `/sf mcp call skillforge_sf_data_write --args '{"namespace":"demo","data":{"hello":"world"}}' --real --idempotency-key <key>`: write generic SF data.
- `/sf mcp call skillforge_sf_data_list --args '{"namespace":"demo"}'`: list generic SF data records.
- `/sf mcp call skillforge_sf_data_get --args '{"id":"sfdata_xxx"}'`: read a generic SF data record.
- `/sf mcp call skillforge_samplebrand_cloud_video_data_latest --args '{}'`: inspect latest cached SampleBrand cloud-video raw data summary.
- `/sf mcp call skillforge_samplebrand_cloud_video_daily_analysis_input --args '{"analysis_date":"2026-06-09"}'`: read one SampleBrand analysis day from cached collector data without downloading the full weekly artifact.
- `/sf mcp call skillforge_cloud_video_daily_person_video_report --args '{"start_date":"2026-05-01","end_date":"2026-06-09"}'`: read governed cloud-video daily person/video spend data for project analysis.
- `/sf mcp call skillforge_cloud_video_material_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","search_type":1}'`: read governed cloud-video material statistics for upload, quality-label and card-audit analysis.
- `/sf mcp call skillforge_cloud_video_video_usage_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","data_type":3,"ids":["4016"]}'`: read governed cloud-video upload usage statistics for reconciliation.
- `/sf mcp call skillforge_cloud_video_audit_rejects --args '{"video_id":"100715117","platform_type":2}'`: read governed cloud-video audit reject reasons for material improvement analysis.
- `/sf mcp call skillforge_yuyidata_customer_service_data_latest --args '{}'`: inspect latest cached Yuyi customer-service raw data summary.
- `/sf mcp call skillforge_execution_artifact_latest --args '{"skill_id":"tmall-link-decline-collector-v1"}'`: inspect latest platform-persisted raw data artifact summary.
- `/sf data list`: `sf data list`.
- `/sf data write --namespace demo --json '{"hello":"world"}' --real --idempotency-key <key>`: `sf data write --namespace demo --json '{"hello":"world"}' --real --idempotency-key <key>`.
- `/sf data records --namespace demo`: `sf data records --namespace demo`.
- `/sf data record <sfdata_id>`: `sf data record <sfdata_id>`.
- `/sf data latest samplebrand`: `sf data latest samplebrand`.
- `/sf data latest tmall`: `sf data latest tmall`.
- `/sf notify users --query 示例成员甲`: `sf notify users --query 示例成员甲`.
- `/sf notify send --title 标题 --markdown 正文 --query 示例成员4`: dry-run a DingTalk notice; add `--real --idempotency-key <key>` to enqueue.
- `/sf run <skill_id>`: `sf run <skill_id>`.
- `/sf runs status <run_id>` / `/sf runs result <run_id>` / `/sf runs logs <run_id>`: inspect run state, result, and redacted log tails.
- `/sf preview output.json`: `sf preview output.json`，上传本地 Skill 输出并返回只读预览 URL。
- `/sf preview apply <preview_id>`: `sf preview apply <preview_id>`，先 dry-run 正式落库结果；真实写入必须加 `--real --idempotency-key <key>`。
- `/sf output validate output.json`: `sf output validate output.json`。
- `/sf capabilities`, `/sf 能力`: `sf catalog refresh`, then `sf capabilities`, `sf platform capabilities`, and `sf editor capabilities`.
- `/sf doctor`: `sf skill doctor --path .`.
- `/sf doctor --remote`: `sf doctor --remote --path .`.
- `/sf test`: `sf skill test --local --path .`.
- `/sf test-real --shop-id <id>`: `sf skill test --real-mcp --path . --shop-id <id>`.
- `/sf sandbox`: `sf skill sandbox --path .`.
- `/sf skill init <skill_id>`: `sf skill init <skill_id>`.
- `/sf skill diff --path .`: `sf skill diff --path .`.
- `/sf skill publish-status <skill_id>`: `sf skill publish-status <skill_id>`.
- `/sf schedule get/set/stop/sync <skill_id>`: manage node schedule through SkillForge.
- `/sf submit`, `/sf 上传`, `/sf 发布`: `sf skill submit --path .`.
- `/sf review <submission_id>`: `sf submission status <submission_id>`.
- `/sf review list/get/comment/request-changes/approve/reject`: operate Review records through SkillForge permissions.
- `/sf market list/detail/install/metrics/certify/visibility/ratings/rate`: inspect, install, certify, and rate shared Skills.
- `/sf pipeline run --path . --remote --submit`: run local checks, remote checks, and optional submit.

## Auth

Interactive commands must use popup/browser authorization. Do not create CLI sessions by writing the database or token cache directly.

`sf auth login` starts a browser authorization flow. If the desktop browser cannot open, show the printed URL and let the user authorize in a browser.

`sf mcp stdio` is the only non-popup path. It should return an auth-required error until the user explicitly logs in.

## Workflow

1. Run `sf update --check`.
2. Run `sf auth status`; if missing or expired, run `sf auth login`.
3. Run `sf plugin doctor --fix`.
4. Run `sf catalog refresh`.
5. Run `sf capabilities`, `sf platform capabilities`, `sf editor capabilities`, and `sf mcp catalog`.
5. If the user wants an existing governed Skill locally, run `sf skill pull <skill_id> --path .` before editing.
6. Inspect the current Skill package. Expected files are `SKILL.md`, `skillforge.yaml`, `contract.json`, `scripts/main.py`, tests, fixtures when needed, and `prompts/` when using platform intelligence.
7. Run `sf skill doctor --path .`.
8. Run `sf skill test --local --path .`.
9. For output checks, run `sf preview output.json` and `sf output validate output.json`; only use `sf preview apply --real` with an explicit idempotency key after validation.
10. For real data checks, run `sf skill test --real-mcp --path . --shop-id <shop_id>` or use cached data through `sf data latest/get`, for example `sf data latest tmall`.
11. Submit with `sf skill submit --path . --message "<message>"`.
12. Report submission id, returned Git commit/hash, review status, blockers, and next action.

## MCP

Use `sf mcp stdio` for local Codex MCP proxy configuration. Real execution happens in SkillForge.

Runtime Skill code should call `skillforge_sdk.SkillForge.fetch_api("mcp://tool_name", ...)`. Local real-MCP mode and node runtime both route through SkillForge Gateway when a run token is present.

Codex CLI may call organization MCP tools directly with `sf mcp call`. For example, `skillforge_org_search_users` queries visible users, `skillforge_agent_coverage` checks department execution/analysis/training Agent coverage, `skillforge_dingtalk_send_work_notice` queues DingTalk work notices through the platform outbox, `skillforge_raw_data_query` reads redacted Skill run data by permission, and `skillforge_run_analyze` combines run data with platform AI diagnostics. The notice tool defaults to dry-run and requires `--real --idempotency-key <key>` for real pushes.

Codex CLI may also inspect platform-persisted raw execution artifacts with `skillforge_execution_artifact_latest` and `skillforge_execution_artifact_summary`. These return summaries, sizes, sha256 hashes, and run references by default, not full raw JSON.

Codex CLI may discover and reuse platform-cached data capabilities with `skillforge_data_capability_list`, `skillforge_data_capability_latest`, and `skillforge_data_artifact_get`. SampleBrand cloud-video, Tmall link-decline, and Yuyi customer-service data are exposed through dedicated aliases: `skillforge_samplebrand_cloud_video_data_latest`, `skillforge_samplebrand_cloud_video_data_get`, `skillforge_samplebrand_cloud_video_daily_analysis_input`, `skillforge_tmall_link_decline_data_latest`, `skillforge_tmall_link_decline_data_get`, `skillforge_yuyidata_customer_service_data_latest`, and `skillforge_yuyidata_customer_service_data_get`. These read node-scheduled Skill artifacts; they do not re-download external platform data. For generic data written by sf/Codex, use `skillforge_sf_data_write`, `skillforge_sf_data_list`, and `skillforge_sf_data_get`; real writes require `--real --idempotency-key`.

Codex CLI may list and install shared Skills by permission. `sf skill list --scope department` shows department-shared readable Skills, and `sf skill install <skill_id>` downloads the readable Skill package into a local workspace with `skillforge.yaml.base_commit` for later status checks and submissions.

Never run production MCP scripts directly from the user's laptop to obtain business data. The platform is the only real MCP execution endpoint.

## Boundaries

- Do not write platform `skills-repo/` directly for user submissions; use `sf skill submit`.
- Do not bypass review or publish state.
- Do not push directly to node deployment directories.
- Do not send DingTalk messages from `scripts/main.py`; return `todos` and `reports`.
- Do not include secrets, tokens, Cookies, or `.env` files in packages.
"""
    )


def _agent_yaml() -> bytes:
    return _text_bytes(
        """
interface:
  display_name: "SkillForge"
  short_description: "Run SkillForge auth, capability, update, test, submit, and review workflows"
  brand_color: "#2563EB"
  default_prompt: "Use $sf to update the SkillForge Codex plugin or run SkillForge skill workflows."

policy:
  allow_implicit_invocation: true
"""
    )


def _script_wrapper() -> bytes:
    return _text_bytes(
        """
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${SKILLFORGE_SF_PYTHON:-python3}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/sf.py" "$@"
"""
    )


def plugin_files() -> dict[str, bytes]:
    return {
        ".codex-plugin/plugin.json": _plugin_json(),
        ".mcp.json": _mcp_json(),
        "commands/sf.md": _command_sf_md(),
        "skills/sf/SKILL.md": _skill_sf_md(),
        "skills/sf/agents/openai.yaml": _agent_yaml(),
        "skills/skillforge-publisher/SKILL.md": _publisher_skill_md(),
        "scripts/sf.py": _read_repo_bytes("scripts/sf.py"),
        "scripts/sf": _script_wrapper(),
        "sdk/skillforge_sdk.py": _read_repo_bytes("app/skill_runtime_sdk/skillforge_sdk.py"),
    }


def build_plugin_bundle() -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for rel, data in sorted(plugin_files().items()):
                arcname = f"{PLUGIN_NAME}/{rel}"
                info = tarfile.TarInfo(arcname)
                info.size = len(data)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if rel == "scripts/sf":
                    info.mode = 0o755
                else:
                    info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def plugin_update_manifest() -> dict[str, Any]:
    bundle = build_plugin_bundle()
    current_release = PLUGIN_RELEASE_HISTORY[0]
    return {
        "name": PLUGIN_NAME,
        "latest_version": PLUGIN_VERSION,
        "bundle_url": f"/api/codex/plugin/{PLUGIN_NAME}/bundle",
        "format": "tar.gz",
        "sha256": f"sha256:{sha256_hex(bundle)}",
        "min_cli_version": PLUGIN_VERSION,
        "recommended_cli_version": PLUGIN_VERSION,
        "default_platform_url": DEFAULT_PLATFORM_URL,
        "install_hint": "sf update",
        "release_notes": current_release["notes"],
        "release_title": current_release["title"],
        "version_history": PLUGIN_RELEASE_HISTORY,
    }
