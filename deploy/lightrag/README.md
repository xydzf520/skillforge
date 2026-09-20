# SkillForge LightRAG Stack

This directory is the project-standard LightRAG deployment for SkillForge.
It runs an isolated LightRAG server plus local Docling parsing service and
binds both ports to loopback only.

## Start

```bash
cd deploy/lightrag
cp .env.example .env
# Fill LightRAG LLM, embedding and VLM credentials in .env.
# 国内机器可在 .env 中把 LIGHTRAG_IMAGE/DOCLING_IMAGE 切到 m.daocloud.io 代理。
docker compose up -d
```

## Verify

```bash
curl http://127.0.0.1:19621/health
curl http://127.0.0.1:15001/docs
```

## SkillForge Admin Settings

Configure these keys through the admin UI, not through the repository:

- `knowledge.rag.enabled=true`
- `knowledge.lightrag.mode=server`
- `knowledge.lightrag.flavor=official`
- `knowledge.lightrag.api_base=http://127.0.0.1:19621`
- `knowledge.lightrag.api_key=<same as LIGHTRAG_API_KEY if enabled>`
- `knowledge.docling.enabled=true`
- `knowledge.docling.api_base=http://127.0.0.1:15001`
- `knowledge.vlm.provider=openai`
- `knowledge.vlm.api_base/api_key/model`
- `ai.embedding.provider/api_base/api_key/model/dim`

LightRAG uses its own container `.env` for runtime credentials. SkillForge
stores admin-visible connection and model settings in `system_config` for
health checks, UI tests and orchestration. The Docker stack uses the official
LightRAG variable names such as `LIGHTRAG_PARSER`, `DOCLING_ENDPOINT`,
`VLM_PROCESS_ENABLE`, `VLM_LLM_*` and `LIGHTRAG_*_STORAGE`.
