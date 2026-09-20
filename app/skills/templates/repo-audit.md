---
name: repo-audit
description: "审计GitHub仓库的基本信息、贡献者、最近提交和开放PR。Use when: 需要手动触发对指定GitHub仓库进行快速审计时。"
compatibility: "Requires curl and jq"
metadata:
  author: skillforge
  department: AI
  risk-level: R2---

## GitHub仓库审计

此Skill用于审计指定的GitHub仓库，获取其关键信息，包括仓库描述、星标数、分支、最近提交、贡献者列表以及开放的Pull Requests。

### 使用方法

1.  运行此Skill时，你需要提供目标GitHub仓库的完整名称，格式为 `owner/repo`（例如：`torvalds/linux`）。
2.  Skill将调用GitHub REST API获取数据。

### 操作步骤

1.  **设置仓库名称**：将目标仓库名称赋值给变量 `REPO`。
    ```bash
    REPO="owner/repo" # 请替换为实际的仓库名，例如 torvalds/linux
    ```

2.  **获取仓库基本信息**：使用 `curl` 调用GitHub API获取仓库的详细数据。
    ```bash
    echo "=== 仓库基本信息 ==="
    curl -s "https://api.github.com/repos/$REPO" | jq -r '
      "名称: \(.full_name)",
      "描述: \(.description // "无")",
      "语言: \(.language // "未检测到")",
      "星标数: \(.stargazers_count)",
      "分支数: \(.forks_count)",
      "开放Issue数: \(.open_issues_count)",
      "默认分支: \(.default_branch)",
      "创建时间: \(.created_at)",
      "最近更新: \(.updated_at)"
    '
    ```

3.  **获取贡献者列表（前10位）**：获取为该仓库提交过代码的主要贡献者。
    ```bash
    echo -e "\n=== 主要贡献者（前10位） ==="
    curl -s "https://api.github.com/repos/$REPO/contributors?per_page=10" | jq -r '.[] | "\(.login) (\(.contributions) 次提交)"'
    ```

4.  **获取最近提交（前5条）**：获取默认分支上的最新提交记录。
    ```bash
    echo -e "\n=== 最近提交（默认分支，前5条） ==="
    curl -s "https://api.github.com/repos/$REPO/commits?per_page=5" | jq -r '.[] | "\(.commit.author.date[0:10]) - \(.commit.author.name): \(.commit.message | split("\n")[0])"'
    ```

5.  **获取开放的Pull Requests**：列出所有状态为 `open` 的PR。
    ```bash
    echo -e "\n=== 开放的 Pull Requests ==="
    curl -s "https://api.github.com/repos/$REPO/pulls?state=open" | jq -r '.[] | "#\(.number) \(.title) (由 \(.user.login) 创建于 \(.created_at[0:10]))"'
    ```

### 注意事项

*   此Skill依赖于公开的GitHub REST API v3，无需认证即可获取公开仓库的信息。对于私有仓库，此Skill无法访问。
*   命令中使用了 `jq` 工具来解析JSON输出，请确保系统已安装。
*   GitHub API有速率限制（未认证状态下每小时60次请求）。频繁审计多个仓库可能会触发限制。