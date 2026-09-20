# 部门知识库设计

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


## 目标

SkillForge 的知识库是控制面能力：每个 `OrgUnit(type=department)` 自动拥有一个部门知识库，作为顶部一级导航「知识库」进入。知识库承载业务 SOP、复盘结论、网页/上传文档、Skill 元数据和数据资产说明，供部门成员检索、问答和 Skill 创建/维护时引用。

## 边界

- 知识库归属部门，不改变 Skill Git、审核、发布和节点定时边界。
- 读权限复用现有组织可见范围：system admin / `can_view_all` 可跨部门；普通用户只能看自己可访问的部门。
- 写权限仅允许 system admin、部门管理员管辖部门、AIBP/AI 工程师/业务负责人维护自己可访问的部门。
- URL 抓取只允许管理员触发，服务端会拒绝 localhost、内网、link-local、保留地址等 SSRF 高风险目标。
- 自动同步只写平台已有元数据快照，不读取平台密钥、Cookie、节点文件系统或 `skills-repo/` 私有内容。

## RAG 后端

正式后端为 `lightrag`：

- `department_knowledge_bases.storage_backend = lightrag`，`config_json` 记录 hybrid retrieval、embedding、reranker 能力。
- `knowledge_documents` 保存原文、来源、MIME、索引状态、索引版本和错误信息。
- `knowledge_chunks` 保存切片、语义向量、关键词和实体提示，作为本地可审计索引面。
- `knowledge_index_jobs` 保存 upsert/delete/reindex/upload/import_url/sync 的索引任务轨迹。
- `knowledge_query_logs` 保存检索/问答审计、LightRAG 模式、rerank、延迟和来源 trace。

部署模式：

- `knowledge.lightrag.api_base` 已配置时进入 `server` 模式，索引和检索请求走 LightRAG 兼容服务；服务不可用时返回明确错误，不静默退回旧检索。
- 未配置外部服务时进入 `embedded` 模式，用同一数据结构在本地完成 hybrid RAG，便于开发、测试和单机部署。
- 自建官方 HKUDS LightRAG Server 使用项目标准目录 `deploy/lightrag/`：
  1. `cd deploy/lightrag && cp .env.example .env`，填写 LightRAG LLM、embedding、VLM 与可选 `LIGHTRAG_API_KEY`。
  2. `docker compose up -d`，标准容器名为 `skillforge-lightrag` / `skillforge-docling`，端口仅绑定 `127.0.0.1:19621` 和 `127.0.0.1:15001`。
  3. 后台「AI 配置 → 向量/RAG」启用 `knowledge.rag.enabled`，设置 `knowledge.lightrag.mode=server`、`knowledge.lightrag.flavor=official`、`knowledge.lightrag.api_base=http://127.0.0.1:19621`。
  4. 后台配置 `knowledge.docling.*` 和独立 `knowledge.vlm.*`，用于 PDF/图片/Office 图文解析；VLM 必须是支持图片输入的 OpenAI-compatible 模型。
  5. 点击「测试向量连接」「测试 LightRAG Server」「测试 Docling」「测试 VLM」和「同步文档到 Server」验证索引链路。
- `knowledge.lightrag.flavor` 支持：
  - `official`：官方 HKUDS LightRAG Docker；SkillForge 会调用 `/documents/insert` 或兼容 `/documents/text` 写入，并通过官方 `/query` 做 Server 侧上下文召回。
  - `skillforge`：自研兼容网关，需实现 `/health`、`/documents/upsert`、`/query`。
- embedding 通过后台「AI 配置 → 向量/RAG」维护 `ai.embedding.*`。后台内置防呆预设：Ollama 本地、SiliconFlow、OpenAI、自定义 OpenAI-compatible。Ollama 默认使用 `http://127.0.0.1:11434/v1` + `bge-m3`，不需要 API Key；SiliconFlow 只需填 API Key，系统自动写入 `https://api.siliconflow.cn/v1`、模型 ID 和向量维度。
- 本地验证默认推荐 Ollama `bge-m3`；线上 SiliconFlow 向量默认推荐 `Qwen/Qwen3-Embedding-8B`，用于部门知识库、LightRAG 和长文档业务问答；低成本大批量索引可选 `Qwen/Qwen3-Embedding-0.6B`，中文/英文短文本也可选 `BAAI/bge-large-zh-v1.5` / `BAAI/bge-large-en-v1.5`。
- reranker 通过后台 `ai.reranker.*` 可选接入 `/rerank`；同平台时可留空 reranker key，运行时沿用 embedding key。SiliconFlow 默认 `BAAI/bge-reranker-v2-m3`。
- 后台提供 `POST /api/knowledge/rag/test-embedding` 测试真实 embedding 调用和返回维度，避免 key、模型、维度填错。
- 后台提供 `POST /api/knowledge/rag/test-server`、`POST /api/knowledge/rag/test-docling`、`POST /api/knowledge/rag/test-vlm` 分别验证 LightRAG、Docling 和独立 VLM 配置。

## 检索与问答

检索采用 LightRAG hybrid 口径：

- 文档切片：约 1100 字符，保留 overlap。
- 语义：正式 embedding 向量召回；embedded dev 无配置时用本地 hash embedding 保证可运行。
- 图谱提示：抽取 Skill ID、版本、指标和中文短语，写入 `entities_json`。
- 关键词：切片内容、标题和关键词命中共同参与排序。
- Rerank：配置后对召回结果二次排序。

`POST /api/knowledge/ask` 默认 `use_llm=true`：命中依据不足时拒答；有依据时通过统一 `app.common.ai.call_llm` 生成引用式回答，失败时降级为 extractive answer。返回包含 `citations`、`confidence`、`insufficient_evidence` 和 `retrieval`。

## 新增、修改、删除

- 打开知识库页面或调用 `GET /api/knowledge/bases` 时自动为所有部门补齐知识库。
- 新增：页面「新增文档」或 `POST /api/knowledge/documents` 写入文档，保存后创建索引任务并重建索引。
- 上传：`POST /api/knowledge/documents/upload` 支持 txt/md/html/json/csv/yaml/xml/docx；PDF 在安装 `pypdf` 后可解析。
- 导入链接：`POST /api/knowledge/documents/import-url`，仅管理员可用，抓取后转为 URL 文档。
- 修改：`PUT /api/knowledge/documents/{document_id}` 可改标题、正文、来源、标签和 metadata；正文或状态变化时自动重建/删除索引。
- 删除：`DELETE /api/knowledge/documents/{document_id}` 软删除为 `status=deleted`，不再参与检索；保留审计。
- 重建索引：`POST /api/knowledge/documents/{document_id}/reindex`。
- 自动同步：`POST /api/knowledge/sync` 把当前部门的 Skill 元数据和数据源元数据写入/更新为知识文档。

## API

前端页面调用：

- `GET /api/knowledge/summary`：顶部 KPI。
- `GET /api/knowledge/bases`：部门知识库列表。
- `GET /api/knowledge/rag/health`：LightRAG/embedding/reranker 健康摘要。
- `GET /api/knowledge/index-jobs?kb_id=...`：索引任务。
- `GET /api/knowledge/documents?kb_id=...`：文档列表。
- `POST /api/knowledge/search`：返回检索命中、分数、摘要、来源和 retrieval trace。
- `POST /api/knowledge/ask`：返回基于知识库来源的问答结果。
- `POST /api/knowledge/context`：返回可注入 Skill / Agent Prompt 的 `prompt_context`。

示例：

```json
{
  "query": "五星价格力下降怎么处理",
  "org_unit_id": "dept-ec",
  "top_k": 6,
  "max_chars": 6000
}
```

返回中的 `prompt_context` 按来源编号组织，`sources` 保留文档 ID、部门、来源类型和分数，便于 run trace 记录可还原依据。
