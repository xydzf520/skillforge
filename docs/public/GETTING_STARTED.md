# 部署与验证指南

**中文** · [English](GETTING_STARTED.en.md) · [返回 README](../../README.md)

<a id="quickstart"></a>

## 本地启动

可直接克隆公开仓库进行本地部署。已有 Docker 环境也可参考[独立预览部署说明](../../deploy/preview/README.md)，使用独立数据库和自行生成的凭据。

准备 Python 3.12、Node.js 22.22 或更新的兼容版本、npm 11.13.0、Git、PostgreSQL 和 Redis。以下从项目根目录执行，Docker Compose 用于启动本地依赖：

```bash
git clone https://github.com/xydzf520/skillforge.git
cd skillforge
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install --global npm@11.13.0
python scripts/init_public_env.py

# Gitea 和浏览器容器按需启用
docker compose up -d postgres redis
# 查看依赖健康状态；数据库可连接后再执行下方迁移
docker compose ps

# 业务技能使用独立 Git 仓库
git init -b main skills-repo
git -C skills-repo config user.name SkillForge
git -C skills-repo config user.email skillforge@example.com
git -C skills-repo commit --allow-empty -m "Initialize local skill assets"

alembic upgrade head
python scripts/init_db.py
npm ci --prefix playbook-editor
npm run build --prefix playbook-editor
npm ci --prefix web
npm run build --prefix web
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。管理员密码在初始化时交互输入，没有通用初始密码。`.env` 使用独立随机凭据、权限为 `0600`，重复初始化不会覆盖已有配置。本地 HTTP 使用非 Secure Cookie，部署 HTTPS 时启用 `COOKIE_SECURE=true`。

向节点发布前，需要建立自己的 Skill 资产远端仓库，配置 `SKILL_REPO_CANONICAL_REMOTE` 并验证可写。模型网关、密钥、数据源、钉钉、浏览器和计算节点均由部署者配置；平台源码仓库与业务 Skill 仓库分别管理。

公开版已移除旧第三方编程运行时的启动器、代理和启用入口。替代 Harness 尚未接入验收，编程会话明确保持不可用；手动编辑和普通模型调用沿用各自流程。选型建议为 OpenCode 优先、DeepSeek Harness 实验接入，见 [清理记录与替换评估](../../docs/public/HARNESS_REPLACEMENT.md)。

### 启动之后先验证什么

1. 登录并确认用户与部门范围，建立自己的 Skill 资产仓库；网页可打开仅代表服务启动。
2. 配置一个模型和一个只读数据能力，用[合成项目示例](../../docs/examples/projects/README.md)或自己的合成 Skill 验证输入、输出和 trace。
3. 如需节点执行，核对注册身份、接收版本、运行回报与停止行为；先完成一条真实只读任务及人工处理流程。
4. 训练和素材生产分别配置相应模型、节点与存储，再做独立验收。需要上线时先验证备份恢复、权限和异常处理。

现有数据库备份脚本不构成完整灾难恢复方案。数据库、Skill Git、部署配置及文件资产需要一致的备份和隔离恢复验证；凭据备份另行加密保管，不进入公开源码或训练数据。

<a id="validation"></a>

## 验证、文档与发布状态

```bash
python scripts/check_public_distribution.py
git diff --check
npm run typecheck --prefix web
npm run test --prefix web -- --maxWorkers=4
npm run build --prefix web

# 仅使用独立测试数据库：测试会重建表
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/skillforge_test \
DATABASE_URL_SYNC=postgresql+psycopg2://USER:PASSWORD@localhost:5432/skillforge_test \
DEBUG=true COOKIE_SECURE=false python -m pytest tests/ -q
```

已有定向回归、类型检查、生产构建、空库启动和独立服务器 Docker 部署记录。完整后端回归、外部集成及生产部署仍未完成验收。依赖审计结果具有时效性，当前已执行结果见[最新复核](../../docs/public/RELEASE_CHECK_20260920.md)，历史过程见[验证记录](../../docs/public/VALIDATION.md)。

本次代码核对还明确了提示词默认版本持久化、优化器候选执行隔离及模拟结果区分、跨模型检查跳过行为、完整备份恢复等限制。这些尚未作为工程功能修复或验收；详见[实现限制与完成标准](../../docs/public/CAPABILITIES.md)。未来的自动择模、成本优化和更完整的自治运行也不能由现有页面或接口推导为已经可用。

- [完整能力地图、业务流程与代码核对](../../docs/public/CAPABILITIES.md)
- [架构与代码证据](../../docs/public/ARCHITECTURE.md)
- [项目展示与能力说明](../../docs/public/PORTFOLIO.md)
- [文档索引](../../docs/README.md)
- [发布边界](../../docs/public/RELEASE_BOUNDARY.md)与[第三方组件说明](../../THIRD_PARTY_NOTICES.md)

本仓库采用独立历史，不包含企业运行数据、凭据或内部部署资料。公开版已按预览状态发布，采用 Apache-2.0；相关成果权属与实际可公开范围按[发布边界](../../docs/public/RELEASE_BOUNDARY.md)核对。
