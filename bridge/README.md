# SkillForge Bridge 部署说明

本页按当前 `bridge/skillforgebridge.py` 核对，日期：2026-09-20。Bridge 是执行节点连接程序，与已移除的编程 Harness 不是同一个组件。系统安装、容器及实际节点连接仍需在部署环境验收。

## 作用与前提

Bridge 主动连接 SkillForge 的 `/api/aiclaw/bridge/ws`，首次使用 enrollment token 注册，后续使用设备私钥签名认证。它还要连接部署者自己的、协议兼容的本地网关；平台源码不包含已配置的企业节点。

代码提供 AIClaw / OpenClaw 的配置发现与协议适配。不同网关版本仍需核对握手、工具和能力上报，不能据此保证所有版本免配置兼容。

## 首次运行：明确选择前台还是安装服务

1. 在自己的 SkillForge 后台创建实例，生成 enrollment token 并下载节点脚本；以页面给出的有效期为准。
2. 将下载文件放在节点本地受控目录。下载文件可能包含注册配置，不提交到源码仓库或公共镜像。
3. 先用前台模式检查连接。下面变量改为实际下载文件路径：

```bash
BRIDGE_SCRIPT='./downloaded-bridge.py'
python3 "$BRIDGE_SCRIPT" --daemon
```

连接与能力上报通过后，在需要自启动的主机上显式安装：

```bash
python3 "$BRIDGE_SCRIPT" --install
```

`--install` 会尝试安装依赖、更新脚本、停止同实例旧进程并注册服务。Linux 使用用户级 systemd 等可用路径，macOS 使用 LaunchAgent；能否开机启动取决于系统权限和安装结果。

**当前无参数运行也会进入 `install_service()`，并非只显示托盘。** 前台检查请保留 `--daemon` 或 `--tray`。平台后端“默认不自动注册服务”的配置不改变这个独立安装程序的行为。

| 参数 | 代码行为 |
|---|---|
| `--daemon` | 前台运行连接循环；容器和进程管理器使用此模式 |
| `--tray` | 托盘模式，需要桌面环境与相关依赖 |
| `--install` / 无参数 | 安装或更新服务并启动；可能重启已有同实例进程 |
| `--status` | 显示实例、版本和本地状态；启动过程仍可能自举依赖 |
| `--update` | 检查并应用脚本更新，此命令不主动重启 |
| `--uninstall` | 停止并移除自启动配置，保留本地状态 |

## 本地状态与更新

当前状态目录为 `~/.skillforge_bridge/<state_slug>/`，其中 `state_slug` 是实例 ID 的 MD5 前 8 位，用于路径命名，不是认证凭据。旧实例名称目录有迁移兼容逻辑；以 `--status` 的实际路径为准。

| 文件 | 用途 |
|---|---|
| `device.key` | 设备签名私钥；备份和迁移时按凭据处理 |
| `env.json` | 实例与连接配置，可能包含凭据 |
| `bridge.py`、`venv/` | 安装副本与依赖 |
| `bridge.log`、`bridge.pid` | 运行日志与进程状态 |

目录权限使用 `0700`，私钥与含凭据配置使用 `0600`。服务器存储设备公钥，设备私钥保留本地；整个数据库备份仍含其他业务资料，不能因此视为可公开文件。

自动更新会向配置的 SkillForge 服务检查脚本版本，下载后保留当前配置并替换脚本；正常自动更新路径可能重启进程。更新失败应结合日志判断，不能以“检查过版本”代替“已应用并重新连接”。

## 使用源码与环境配置

公开源码模板可从环境变量读取配置。以下为占位示例，必须替换为自己的实例、服务和网关；变量值要保留引号：

```bash
export INSTANCE_ID='replace-with-instance-id'
export ENROLLMENT_TOKEN='replace-with-one-time-token'
export SKILLFORGE_WS_URL='wss://skillforge.example.com/api/aiclaw/bridge/ws'
export SKILLFORGE_HTTP_BASE='https://skillforge.example.com'
export LOCAL_AICLAW_URL='ws://gateway.example.com:18789'
python3 bridge/skillforgebridge.py --daemon
```

需要网关凭据时，按部署配置提供对应凭据。不要将真实 token 写入仓库、镜像构建参数或命令执行日志。

## 容器示例：用户、HOME 与持久卷一致

下面是待部署验证的示例，本次没有 Docker 实机验收。使用仓库内无注册凭据的源码模板构建；不要把后台下载的含配置脚本复制进可分发镜像。

将下面内容保存为项目根目录的 `Dockerfile.bridge`：

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir websockets cryptography
RUN groupadd --gid 1001 bridge && useradd --uid 1001 --gid 1001 --create-home bridge
ENV HOME=/home/bridge
WORKDIR /app
COPY bridge/skillforgebridge.py /app/bridge.py
USER 1001:1001
CMD ["python", "/app/bridge.py", "--daemon"]
```

在本机准备未提交的 `.env.bridge`，填写上一节变量（不写 `export`），设置文件权限为 `0600`。`LOCAL_AICLAW_URL` 必须能从容器访问；容器内 `127.0.0.1` 指容器自身。

```bash
chmod 600 .env.bridge
docker build -f Dockerfile.bridge -t skillforge-bridge .
sudo mkdir -p /opt/skillforge_bridge
sudo chown 1001:1001 /opt/skillforge_bridge
sudo chmod 700 /opt/skillforge_bridge
docker run -d \
  --name skillforge-bridge \
  --restart unless-stopped \
  --env-file ./.env.bridge \
  -v /opt/skillforge_bridge:/home/bridge/.skillforge_bridge \
  skillforge-bridge
```

没有持久卷会在容器重建时丢失设备身份。示例显式创建 UID/GID 与 HOME，避免状态写入 `/root` 而挂载到另一路径。实际发布还需固定依赖与镜像版本，并验证重启、更新和设备身份恢复。

## 验收与排查

| 现象 | 核对事项 |
|---|---|
| enrollment 无效或过期 | 在自己的后台重新生成并核对实例 ID |
| Bridge 已连接但任务不可用 | 本地网关、协议版本、节点用途与能力上报 |
| 重启后要求重新注册 | 实际 HOME、状态目录、持久卷与文件权限 |
| 托盘不显示 | 桌面支持与依赖；可先用 `--daemon` 验证连接 |
| 服务未自动启动 | 安装输出、用户级服务权限、系统会话与实际服务状态 |
| 更新反复发生 | 实际运行副本、脚本哈希、进程重启与重连日志 |

验收要分别核对平台连接、网关连接、任务执行和结果回传，不能只看一个“在线”标记。设备丢失或凭据泄露时，在平台撤销身份并重新注册；历史运行记录保留。
