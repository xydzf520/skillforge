# Prompts Registry

本目录存放所有由 `PromptRegistry` 管理的 system prompt。

## 文件命名规则

```
<name>@<version>.md       例如 agent_chat@v1.md, reviewer@v2.md
<name>.md                 没有 @ 时默认 version=v1
```

文件加载在 `app.main` lifespan 中调用 `init_registry()` 完成。

## 使用方式

```python
from app.common.prompt_registry import prompt_registry

rendered, prompt_hash = prompt_registry.build(
    "agent_chat",
    context={
        "skill_name": "EC-投放-01",
        "skill_md_content": "...",
        "policy_pack_yaml": "...",
        # ...
    },
)
# rendered 传给 LLM
# prompt_hash 传给审计日志，用于"本次审核用的哪版 prompt"追溯
```

## 当前已注册 prompt

| name | 用途 | 主要 placeholder |
|---|---|---|
| `agent_chat` | Agent 对话测试的系统提示词 | skill_name / skill_md_content / policy_pack_yaml / antipatterns / test_cases / recent_executions / datasource_status |
| `compact_summarizer` | F1 上下文压缩的摘要 LLM | (无 placeholder，固定文本) |

## 添加新版本

1. 复制 `<name>@v1.md` 为 `<name>@v2.md`
2. 编辑内容
3. 重启服务（自动注册）
4. 在 `/admin/prompts` 页面切换默认版本

## 文件格式约定

- UTF-8 编码
- Markdown 语法（首行可以是 H1 标题，但作为 prompt 一部分发给 LLM）
- 用 `{placeholder}` 注入动态变量
- 注释（不发给 LLM）：可以放在文件顶部用 HTML 注释 `<!-- ... -->`
