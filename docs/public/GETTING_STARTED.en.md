# Deployment and validation guide

[中文](GETTING_STARTED.md) · **English** · [Back to README](../../README.en.md)

<a id="quickstart"></a>

## Run locally

Clone the public repository below for local deployment. If Docker is available, the [isolated preview deployment guide](../../deploy/preview/README.md) describes using separate storage and locally generated credentials.

Prepare Python 3.12, npm 11.13.0, Node.js 22.22 or a later compatible version, Git, PostgreSQL and Redis. Run the following from the repository root; Docker Compose starts the local dependencies:

```bash
git clone https://github.com/xydzf520/skillforge.git
cd skillforge
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install --global npm@11.13.0
python scripts/init_public_env.py

# Enable Gitea and browser containers separately when needed
docker compose up -d postgres redis
# Check dependency health; wait for the database before migrations below
docker compose ps

# Business skills use a separate Git repository
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

Open `http://127.0.0.1:8000`. The administrator password is entered interactively during initialization; there is no shared default password. The generated `.env` uses unique random credentials and mode `0600`. Repeated initialization does not overwrite it. Local HTTP uses a non-Secure cookie; enable `COOKIE_SECURE=true` for HTTPS deployment.

Before publishing to nodes, create your own remote Skill asset repository, configure `SKILL_REPO_CANONICAL_REMOTE` and verify write access. Model gateways, credentials, data sources, DingTalk, browsers and compute nodes are configured by the deployer. The platform source repository and business Skill repository remain separate.

The old coding-runtime launcher, proxy and activation UI have been removed. Replacement adapters are not integrated or accepted; coding sessions remain explicitly unavailable. Manual editing and ordinary model calls retain their own paths. See the [retirement and replacement assessment](../../docs/public/HARNESS_REPLACEMENT.en.md): OpenCode is the first candidate, with DeepSeek Harness for experimental integration.

### What to verify after startup

1. Log in, verify user and department scope, and establish your own Skill repository. An accessible web page establishes service startup only.
2. Configure one model and one read-only data capability. Use a [synthetic project example](../../docs/examples/projects/README.md) or a synthetic Skill to verify inputs, outputs and traces.
3. For node execution, verify registration identity, received version, run reports and stopping behavior. Complete one real read-only task and human handoff first.
4. Configure the models, nodes and storage needed for training or media production, then accept those capabilities separately. Verify backup/recovery, access and failure handling before production use.

The database backup script is not a complete disaster-recovery solution. The database, Skill Git, deployment configuration and file assets need consistent backups and isolated restoration checks. Protect credential backups separately with encryption; keep them out of public source and training data.

<a id="validation"></a>

## Validation, documentation and release status

```bash
python scripts/check_public_distribution.py
git diff --check
npm run typecheck --prefix web
npm run test --prefix web -- --maxWorkers=4
npm run build --prefix web

# Use an isolated test database only: tests recreate tables
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/skillforge_test \
DATABASE_URL_SYNC=postgresql+psycopg2://USER:PASSWORD@localhost:5432/skillforge_test \
DEBUG=true COOKIE_SECURE=false python -m pytest tests/ -q
```

Targeted regression, type-checking, production builds, clean-database startup and isolated Docker server deployment have been verified within the documented scope. Full backend regression, external integrations and production acceptance remain incomplete. Dependency audit results are time-sensitive; see the [latest release review](../../docs/public/RELEASE_CHECK_20260920.en.md) for current results and the [validation record](../../docs/public/VALIDATION.md) for earlier checks.

Code review also identified limitations in prompt-default persistence, optimizer candidate isolation and simulated-result separation, skipped cross-model checks, and complete backup/recovery. These have not been fixed or accepted as part of this documentation work; see the [limitations and completion criteria](../../docs/public/CAPABILITIES.en.md). Existing screens or APIs do not establish that future automatic model selection, cost optimization or broader autonomous operation are already available.

- [Complete capability map, business process and code review](../../docs/public/CAPABILITIES.en.md)
- [Architecture and code evidence](../../docs/public/ARCHITECTURE.md)
- [Product case, responsibility and delivery evidence](../../docs/public/PORTFOLIO.en.md)
- [Documentation index](../../docs/README.md)
- [Release boundary](../../docs/public/RELEASE_BOUNDARY.md) and [third-party notices](../../THIRD_PARTY_NOTICES.md)

This repository uses an independent history and excludes enterprise runtime data, credentials and private deployment materials. It is published as a preview under Apache-2.0; ownership and the actual scope of authorized distribution remain subject to the [release-boundary review](../../docs/public/RELEASE_BOUNDARY.md).
