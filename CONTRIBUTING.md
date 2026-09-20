# 参与贡献 Contributing

欢迎围绕企业 AI 工作流、Skill 生命周期、Harness 集成与可靠性提交改进。项目处于公开预览阶段，当前能力和限制见 [README](README.md) 与[验证记录](docs/public/RELEASE_CHECK_20260920.md)。

## 开始之前

1. 先搜索已有 [Issues](https://github.com/xydzf520/skillforge/issues)。较大的产品、权限或架构调整先描述问题、使用场景和验收方式。
2. 按 [本地启动](README.md#quickstart)配置独立开发环境。数据库测试会重建表，只使用独立测试库和合成数据。
3. 平台源码和业务 Skill Git 仓库分开管理。不要提交企业技能、真实业务数据、账号、密钥、Cookie、内部地址、运行日志或原私有历史。

## 提交改动

- 从 `main` 建立功能分支，保持改动聚焦，说明解决的问题和用户可见变化。
- 修改行为时补充相关回归验证；文档改动检查链接和中英文表述。
- 不通过跳过失败测试、放宽权限或隐去限制宣称功能完成。无条件运行外部服务、付费模型或真实训练的测试应说明所需环境。
- 运行 `python scripts/check_public_distribution.py --staged` 和 `git diff --check`。其他验证按改动范围选择，参考 [AGENTS.md](AGENTS.md) 和[验证指南](docs/public/VALIDATION.md)。
- PR 说明测试结果与未验证范围。仅贡献你有权提交的内容，保留第三方许可与声明；项目适用 [Apache-2.0](LICENSE)。

安全问题按 [SECURITY.md](SECURITY.md) 私下报告。普通问题使用 Issue；请勿将生产日志直接附到公开讨论中。

## English

SkillForge is a public preview. Discuss substantial product, permission or architecture changes in an issue before implementation. Use an isolated development environment and synthetic data; database tests recreate tables.

Keep platform code separate from business Skill repositories. Never commit credentials, cookies, company data, private deployment details or private Git history. Submit focused pull requests with relevant tests, bilingual documentation updates where needed, and an honest account of unverified behavior. Do not weaken permissions or skip failing tests to claim success.

Before committing, run `python scripts/check_public_distribution.py --staged` and `git diff --check`, then the checks required for your changed area in [AGENTS.md](AGENTS.md). Contribute only material you are authorized to submit and retain third-party notices. Report vulnerabilities privately using [SECURITY.md](SECURITY.md).
