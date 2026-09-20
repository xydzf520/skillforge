---
name: uptime-check
description: "检查指定网站的可用性和响应时间。Use when: 手动触发，需要检查网站或服务的在线状态和性能。"
compatibility: "Requires curl"
metadata:
  author: skillforge
  department: AI
  risk-level: R2---

本 Skill 用于检查指定网站的可用性、HTTP 状态码和响应时间。它通过向目标 URL 发送 HTTP 请求并分析响应来实现。

**操作步骤：**

1.  首先，你需要一个要检查的网站 URL。例如：`https://www.example.com`。
2.  使用 `curl` 命令的 `-o /dev/null`、`-s`、`-w` 和 `-I` 选项来发送 HEAD 请求并获取详细的计时信息，避免下载响应体。
3.  命令格式如下：
    ```bash
    url="https://www.example.com" && \
    curl -o /dev/null -s -w "\
    状态码: %{http_code}\n\
    DNS解析时间: %{time_namelookup} 秒\n\
    建立连接时间: %{time_connect} 秒\n\
    SSL握手时间: %{time_appconnect} 秒\n\
    开始传输前时间: %{time_pretransfer} 秒\n\
    从开始到收到第一个字节的时间: %{time_starttransfer} 秒\n\
    总时间: %{time_total} 秒\n" \
    -I "$url"
    ```
4.  将上述命令中的 `https://www.example.com` 替换为你要检查的实际 URL。
5.  执行命令后，你将看到类似以下的输出：
    ```
    状态码: 200
    DNS解析时间: 0.012345 秒
    建立连接时间: 0.034567 秒
    SSL握手时间: 0.089012 秒
    开始传输前时间: 0.089123 秒
    从开始到收到第一个字节的时间: 0.123456 秒
    总时间: 0.134567 秒
    ```
6.  **结果解读**：
    *   **状态码**：`200` 表示成功，`4xx` 表示客户端错误（如 404 未找到），`5xx` 表示服务器错误。
    *   **DNS解析时间**：将域名解析为 IP 地址所需的时间。
    *   **建立连接时间**：与服务器建立 TCP 连接的时间。
    *   **SSL握手时间**（仅 HTTPS）：建立安全连接的时间。
    *   **开始传输前时间**：从开始到请求即将被发送的时间。
    *   **从开始到收到第一个字节的时间 (TTFB)**：从请求开始到收到服务器第一个响应字节的时间，是衡量服务器响应速度的关键指标。
    *   **总时间**：完成整个请求所花费的总时间。

**注意**：此检查为单次瞬时检查。对于持续的可用性监控，你需要设置定期任务或使用专门的监控服务。