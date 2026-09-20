---
name: sys-monitor
description: "查看系统资源使用情况：CPU、内存、磁盘、进程。Use when: 用户问系统状态、内存占用、磁盘空间、CPU使用率、或想看哪个进程占资源最多。NOT for: 网络配置、防火墙管理。"
compatibility: "Requires Linux with free, df, top commands"
metadata: { "aiclaw": { "emoji": "📊", "requires": { "bins": ["free", "df"] } } }
---

# System Monitor Skill

快速查看系统资源使用情况。

## 命令

### 内存使用
```bash
free -h
```

### 磁盘空间
```bash
df -h --total | grep -E "^/|total"
```

### CPU 使用率（5秒采样）
```bash
top -bn1 | head -5
```

### 占内存最多的进程 Top 10
```bash
ps aux --sort=-%mem | head -11
```

### 占CPU最多的进程 Top 10
```bash
ps aux --sort=-%cpu | head -11
```

## 输出建议

- 内存使用超过 80% 时提醒用户
- 磁盘使用超过 90% 时告警
- 列出资源占用异常的进程
