# --- Build the editor from source; no generated bundles in Git ---
FROM node:22-slim AS editor
WORKDIR /app/playbook-editor
COPY playbook-editor/package.json playbook-editor/package-lock.json ./
RUN npm install --global npm@11.13.0 && npm ci
COPY playbook-editor/ .
RUN npm run build

# --- 阶段1: 构建Vue前端 ---
FROM node:22-slim AS frontend
WORKDIR /web
# Puppeteer is used by optional development tooling, not the Vite production build.
ENV PUPPETEER_SKIP_DOWNLOAD=true
COPY web/package.json web/package-lock.json ./
RUN npm install --global npm@11.13.0 && npm ci
COPY web/ .
COPY --from=editor /app/web/public/playbook-editor /web/public/playbook-editor
COPY CHANGELOG.md /CHANGELOG.md
RUN npm run build

# --- 阶段2: Python后端 ---
FROM python:3.12-slim

WORKDIR /app
ARG DEBIAN_MIRROR=https://deb.debian.org

RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 skillforge \
    && useradd --system --uid 1001 --gid skillforge --home-dir /app --shell /usr/sbin/nologin skillforge

COPY requirements.txt .
COPY LICENSE THIRD_PARTY_NOTICES.md ./
ARG PYPI_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "$PYPI_INDEX_URL" --timeout 30 --retries 2 -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .
COPY scripts/ scripts/
COPY playbooks/ playbooks/
COPY bridge/ bridge/
COPY sdk/ sdk/
COPY docs/ docs/
COPY CHANGELOG.md README.md README.en.md ./

# 从前端构建阶段复制产物
COPY --from=frontend /web/dist web/dist

# 非 root 运行（1001:skillforge）。skills-repo / 数据目录等需要 volume 挂载时
# 请确保宿主机目录 chown 到 1001，或显式给 uid=1001 写权限。
RUN mkdir -p /app/data /app/skills-repo /app/skill-remote.git \
    && chown -R skillforge:skillforge /app
USER skillforge

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
