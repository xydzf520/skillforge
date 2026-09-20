# 独立预览部署

使用 `compose.yml` 启动独立应用、PostgreSQL 和 Redis。数据库与缓存没有宿主机端口；应用默认只监听本机 `18082`。这份配置用于全新预览，不覆盖既有企业部署，不导入本机业务库、模型密钥或 Cookie。

在此目录创建权限为 `0600` 的 `.env`，提供 `SKILLFORGE_RELEASE_SHA`、独立随机的 `POSTGRES_PASSWORD`、`SECRET_KEY`、`ENROLLMENT_PEPPER`、Fernet 格式的 `COOKIE_ENCRYPT_KEY`，以及 `PUBLIC_BASE_URL`、`CORS_ALLOWED_HOSTS` 和 `COOKIE_SECURE`。需要外部预览时显式设置 `PREVIEW_BIND`；HTTPS 应设置 `COOKIE_SECURE=true`，不要使用服务器 SSH 密码作为应用密码。

先运行 `docker compose build app`、`docker compose up -d db redis`，再用 `docker compose run --rm app alembic upgrade head` 完成迁移。在应用容器中初始化 `/app/skills-repo` 与 `/app/skill-remote.git` 这两个独立的技能资产 Git 仓库，再通过 `scripts/init_db.py` 交互输入独立管理员初始密码。最后运行 `docker compose up -d app`，检查 `/health`、登录页、静态资源及需要登录的 API。

模型、节点与数据接入仍需单独配置。自动训练、自动部署、浏览器心跳与学习自动流转在此预览中关闭；网页可访问不表示这些能力已验收。应用使用非 root 用户，保留正常账号认证和首次改密要求。

构建时默认使用 Debian 官方 CDN。如目标网络下载缓慢，可通过 `.env` 的 `DEBIAN_MIRROR` 指定 [Debian 官方镜像列表](https://www.debian.org/mirror/list)中的 HTTPS 站点；保留正常证书和软件包签名校验。前端生产构建跳过开发工具的 Chromium 下载，独立浏览器运行环境仍需按需配置。

Python 依赖默认使用 PyPI；`PYPI_INDEX_URL` 允许按目标网络选择 HTTPS 镜像，例如遵循 [TUNA 使用说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)配置。该参数只控制构建下载来源，不更改依赖版本范围，也不关闭 TLS 校验。

升级前备份数据库、五个数据卷及部署配置，保留原镜像与源码归档。普通 `docker compose down` 保留数据；不要使用 `down -v`。数据库发生迁移时，回滚需要配套备份，不能只切换旧镜像。
