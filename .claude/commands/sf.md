---
description: Route SkillForge /sf commands to the local sf CLI.
argument-hint: "[update|auth|mcp|我的插件|我的部门插件|doctor|test|submit]"
allowed-tools: [Bash, Read]
---

# SkillForge sf

Use `docs/guides/sf-codex-claude-workflow.md`.

The user invoked this command with: $ARGUMENTS

Map `/sf ...` to the local `sf` CLI. Start with `sf update --check`, `sf auth status`, and `sf mcp catalog` when context is unclear.

Common routes:

- `update`, `更新`, `升级`: run `sf update`.
- `plugin status`, `插件状态`: run `sf plugin status`.
- `auth`, `我是谁`: run `sf auth status`; if it fails, run `sf auth login`.
- `登录`, `授权`: run `sf auth login`.
- `mcp`: run `sf mcp catalog`.
- `我的插件`: run `sf my`.
- `我的技能`: run `sf skill list --scope mine`.
- `我的部门插件`: run `sf skill list --scope department`.
- `查看哪些技能`: run `sf skill list --scope visible`.
- `查看我能编辑哪些技能`: run `sf skill list --scope editable`.
- `哪些可发布`: run `sf skill list --scope publishable`.
- `哪些未上传`: run `sf skill scan --path . --compare-remote`.
- `当前目录有没有上传`: run `sf skill status --path .`.
- `doctor`: run `sf skill doctor --path .`.
- `test`: run `sf skill test --local --path .`.
- `test-real --shop-id <id>`: run `sf skill test --real-mcp --path . --shop-id <id>`.
- `sandbox`: run `sf skill sandbox --path .`.
- `submit`, `上传`, `发布`: run `sf skill submit --path .`.
- `review <submission_id>`: run `sf submission status <submission_id>`.

Do not read or print platform secrets, MCP env, Cookie, API key, DingTalk access_token, or `.env`. Do not write `skills-repo/` directly for submissions; use `sf skill submit`.
