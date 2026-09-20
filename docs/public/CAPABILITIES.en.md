# Product map and code review

[中文](CAPABILITIES.md) · **English** · [Back to README](../../README.en.md)

Reviewed on 2026-09-20. This document covers major product modules, frontend/backend entry points and key implementations in the public preparation edition. “Present” means corresponding code was found; it does not mean every branch, integration or production scenario has passed acceptance. See the [validation record](VALIDATION.md) for checks actually executed.

## 1. Reading guide

| Question to explain | Where it is covered |
|---|---|
| What an enterprise deploys | README product form and the map below: browser workspace, control services, developer interfaces and execution nodes |
| Enterprise adoption and human–AI collaboration | README rollout, custom Harness integration and the delivery process below |
| Workflows, prompts, Skills and logs as assets | README assets and training; version, prompt, evidence and quality management below |
| Data origins, training targets and destinations | README lifecycle diagram and implementation boundaries for knowledge, learning, training and deployment |
| Custom Harness integration | Internal orchestration versus external Harness responsibilities; SDK/API/MCP/Bridge paths |
| Strong models and private small models | README product direction, business benchmarks, total cost and deployment evidence |
| Portfolio and résumé connection | [Product case](PORTFOLIO.en.md); establish code capabilities, personal responsibilities and actual outcomes separately |
| Chinese default, English access and public preparation | Both READMEs, this bilingual document and the [release boundary](RELEASE_BOUNDARY.md) |

## 2. Capability map: implementation, purpose and limits

| Module | Present implementation and purpose | Code evidence and boundary |
|---|---|---|
| Capability hall and application portal | Discover capabilities, run forms, view results and share capabilities in an internal market | [Hall](../../app/hall/), [Portal](../../app/portal/); this is not an established public trading marketplace |
| Skill creation and Studio | Turn requests into task contracts; manage definitions, inputs, outputs, code and tests | [AgentCore](../../app/agent_core/), [Skills](../../app/skills/); manual editing and ordinary model-based skeleton paths remain; full-file generation through the retired coding runtime is unavailable |
| Templates, instances and releases | Distinguish reusable templates, concrete instances, releases and derivation relationships | [Asset models](../../app/skills/asset_models.py); business Skills use a separate Git repository |
| Workflow orchestration | Playbook editing, dependency graphs, dependency-ordered skill dispatch and step events | [Executor](../../app/playbooks/executor.py), [editor](../../playbook-editor/); the current executor is primarily topologically sequential, not arbitrary parallel autonomous collaboration |
| Business Agents | Department, owner, associated Skill, prompt version, analysis dimensions and default parameters | [Agent models](../../app/agents/models.py), [API](../../app/agents/router.py); current analysis blueprints contain scenario presets, not universal autonomous business Agents |
| Project applications and hosting | Lightweight pages, standalone runs, project services, capability gateway, artifacts and traces | [Projects](../../app/projects/), [pages](../../web/src/pages/project/); integration requires contracts and authorization, not unrestricted application hosting |
| Personal interface customization | AI previews of declarative interfaces; save and restore versions scoped to user, Skill and surface | [UI service](../../app/portal/ui_service.py), [models](../../app/portal/models.py); actual implementation exists, not just a specification; customization cannot change skill logic or permissions |
| Prompt management | File versions, content inspection, hashes, default selection and audit records | [Registry](../../app/common/prompt_registry.py), [admin API](../../app/common/prompts_router.py); not every Agent prompt uses the registry; default persistence is limited as described below |
| Data connections and collection | Sources, files, browser collection, API snapshots and collection evidence | [Sources](../../app/datasources/), [collection](../../app/collection/), [browser](../../app/browser/); deployers configure and verify business login, scope and availability |
| Data governance | Asset and data-product registries, policies, field handling and data contracts | [Governance](../../app/datasources/governance_service.py); governance interfaces do not prove every business path enforces the same policy |
| Organization and access | Users, departments, membership, skill action permissions and ABAC policies | [Organization](../../app/org/), [skill access](../../app/skills/core/access.py), [ABAC](../../app/auth/abac_service.py); organizational governance within an enterprise instance does not establish validated public multi-tenant isolation |
| Review and approval | Skill reviews, comments, semantic differences, approval steps and resource quotas | [Reviews](../../app/reviews/), [Approval](../../app/approval/); publishing a Skill, approving a business action and approving a model deployment are distinct |
| Human tasks and reports | Decisions, drafts, dispatch, assignment, SLA, acknowledgements, timelines and unread reports | [Todos](../../app/todos/), [Inbox](../../app/inbox/), [notifications](../../app/dingtalk/); successful notification does not establish business completion |
| Nodes, scheduling and queues | Bridge registration/status, node schedules, platform fallback, task claims, timeouts and retries | [Bridge](../../bridge/), [scheduler](../../app/execution/scheduler.py), [queue](../../app/execution/task_queue.py); retries do not by themselves guarantee exactly-once external writes |
| Execution visibility and task tree | Steps, decisions, artifacts, node state, execution chains and diagnostics | [Execution](../../app/execution/), [TaskTree](../../app/tasktree/); only integrated, reported activity is observable |
| Knowledge retrieval | Department knowledge bases, documents, media, chunks, indexing, search, Q&A and context | [Knowledge](../../app/knowledge/); embedding, parsing, vision and retrieval backends require configuration; retrieval does not change model weights |
| Learning provenance | Events, extracted artifacts and source relationships for knowledge, experience, samples and improvement candidates | [Learning](../../app/learning/); generating a candidate does not mean it was adopted, released or trained |
| Datasets, training and deployment | Sources, samples, dataset versions, jobs, artifacts, evaluation, canaries and rollback | [Training](../../app/training/); models, compute and business evaluation are required; automatic training/deployment are off by default |
| Media production and review | Creative planning, generation jobs, batches, annotations, revisions, candidates, deliveries and performance data structures | [Media](../../app/media/); generation models, nodes and media sources are dependencies; enterprise media and proven marketing outcomes are not bundled |
| Skill optimizer | Failure analysis, editable-zone restrictions, candidates, benchmark packs, gates and shadow management | [Optimizer](../../app/optimizer/); simulation fallback and execution-version isolation limitations prevent claims of a validated autonomous optimization loop |
| Models and cost | Purpose-specific model configuration, usage records, attribution, summaries and quota alerts | [AI calls](../../app/common/ai.py), [usage](../../app/common/cost_tracker.py); estimates depend on upstream usage and configured prices, are not invoices and do not form a complete cost-routing engine |
| Developer and Harness integration | CLI, MCP, Python/TypeScript SDKs, project runs and training interfaces | [CLI](../../scripts/sf.py), [SDK](../../sdk/), [gateway](../../app/codex/); platform authorization remains in force; restricted third-party runtimes are excluded |

## 3. Delivering one business workflow

The following synthetic business-exception analysis example connects people and records. Users supply their own data source and specific Skill.

1. **Define objectives and acceptance**: the business owner supplies metric definitions, exceptions, outputs, ownership and approval boundaries, with a current human baseline.
2. **Build the capability**: product and engineering teams implement a Skill, Playbook or project application; configure tools and data contracts, and pin prompts and test cases.
3. **Validate a version**: use synthetic normal, missing, erroneous and repeated inputs, save the Git version and submit for review. Preview or simulation success does not establish live business validity.
4. **Release and deploy**: distribute the reviewed version to authorized users and nodes. Inspect platform release, node version, schedule configuration and actual run status separately.
5. **Execute**: employees run the capability manually, or configured nodes run on schedule. A custom Harness participates through authorized interfaces and reports runs and artifacts.
6. **Create reports and tasks**: owners review evidence, revise recommendations or reject them with reasons. Business writes and notifications follow the relevant interface rules.
7. **Verify outcomes**: distinguish generation, sending, reading, confirmation and actual business completion; record acknowledgements where available and retain unknowns.
8. **Choose the improvement layer**: fix missing data, refresh knowledge, revise workflow steps or prompts as appropriate; consider training when reliable samples and evaluation objectives exist.
9. **Validate the next version**: pin the revision and benchmark, compare quality, review effort, exceptions and cost, then review, release, observe and retain a rollback path.

Three lifecycles coexist: **asset versions** (draft, review, release), **execution tasks** (waiting, running, failed, completed), and **business outcomes** (recommendation, confirmation, action, verification). One “completed” label cannot substitute for the other two.

## 4. Two meanings of a custom Harness

**Orchestration foundations in this repository**: AgentCore uses LangGraph for intent, risk, generation, tool binding, preview and release checks, with checkpoints and human-interruption recovery. Playbooks and execution services compose tasks and dispatch to nodes. These are components of a custom business runtime, not a universal adapter for every Agent.

**An enterprise's external Harness**: it may own the Agent loop, context and tool selection, use authorized capabilities through SDK/API/MCP, and receive tasks and report results through compatible node protocols. An adapter must handle identity, timeouts, cancellation, idempotency, versions and error semantics. Completing one model call is insufficient integration evidence.

Use a shared run identity to connect objective → inputs and sources → Skill/prompt/model versions → tool calls → human confirmation → artifacts → actual outcomes. Verify completeness for each integration. This is an acceptance requirement, not a claim that every existing path already records every field.

## 5. Managing enterprise assets

| Asset | What to retain | How to verify it |
|---|---|---|
| Workflows | Steps, dependencies, owners, inputs, outputs, approval boundaries and failure handling | Normal, exceptional and interrupted-run recovery cases |
| Prompts | Objectives, standards, variables, examples, counterexamples, model parameters, versions and hashes | Compare revisions on the same samples; associate effective versions with calls |
| Skills | Definitions, code, tool scope, tests, versions, releases and node state | Confirm the reviewed version was actually executed and preserve traceability |
| Logs and feedback | Input sources, tool actions, outputs, corrections, business outcomes and unknowns | Distinguish simulated/live, successful/failed and inferred/confirmed; check sensitivity and retention |
| Knowledge, samples and evaluation sets | Source permissions, quality, time, content hashes, versions and intended use | Review stale knowledge, duplication, train/eval leakage and incorrect labels |
| Private models | Base-model provenance, dataset version, parameters, artifact hashes, evaluation, deployment and rollback | Compare with the original model and human standards; measure quality, latency and total cost |

Strong models can help generate candidates; enterprises retain standards and outcomes validated in business. Log text must not automatically become a correct training answer. Label failures and human corrections explicitly. Evaluate knowledge updates, skill changes and parameter training separately instead of using data volume as proof of learning.

## 6. Measuring enterprise value

Use a consistent task scope and time period, retain sample counts, failures and unknowns, and compare with the pre-adoption baseline.

- **Quality**: acceptance rate, critical errors, rework and human corrections, including denominators and sampling methods.
- **Efficiency**: end-to-end time, human review and exception-handling effort, beyond model response latency.
- **Cost**: model calls, compute, storage, operations and people. Platform usage estimates cover only part of this total.
- **Adoption and reuse**: actual users, teams, repeated use and reused versions. Page visits or call counts do not establish business gains.
- **Control**: traceable failures, actionable confirmations, duplicate handling, version withdrawal and consistent recovery.

This is a business measurement approach. Dashboards, run records and usage tracking provide foundations; installation does not deliver comprehensive ROI measurement or financial accounting.

## 7. Implementation limitations identified in this review

These remain engineering acceptance items. This documentation update does not mark them as fixed.

| Item | Current implementation and effect | Completion criterion |
|---|---|---|
| Prompt defaults | `PromptRegistry.set_default` updates an in-process dictionary. The API audits the change but does not persist the selection; restart or multiple workers can produce different defaults | Durable selection, conflict handling, worker refresh and effective-version traceability; cover every call requiring management |
| Optimizer version isolation | The controller temporarily replaces shared `SKILL.md` during evaluation and restores it; the runner does not explicitly pass an immutable candidate version to the executor | Isolated snapshots and verification of the actual node version, without affecting simultaneous edits or production runs |
| Simulated optimizer results | Node-call failures fall back to local simulation, which participates in assertions and aggregation | Separate simulated/live statistics and require real execution plus independent evaluation before live promotion |
| Cross-model checks | An absent or failing secondary model records skipped / secondary_failed without blocking the main flow | Define business-required reviews and expose skipped checks; do not treat skipping as successful agreement verification |
| Training evaluation | Local runners include small held-out generation checks; the platform checks returned metrics against configured gates | Independent business benchmarks, source deduplication, original-model comparisons and deployment observation |
| Backup and recovery | `scripts/backup_db.sh` exports only the database, uses a fixed container/path and lacks pipeline failure checking | Configurable backups with visible failures; include Skill Git, configuration and file assets, and verify isolated restoration |
| Public and production release | Apache-2.0 selected; ownership and third-party distribution scope, dependency audit work and Docker/external-service acceptance remain outstanding | Resolve release scope, upgrade and retest dependencies, and verify deployment and recovery before release |

Implementations: [prompt registry](../../app/common/prompt_registry.py), [optimizer controller](../../app/optimizer/controller.py), [benchmark runner](../../app/optimizer/benchmark_runner.py), [secondary-model check](../../app/agent_core/nodes/cross_model_check.py), [backup script](../../scripts/backup_db.sh). Findings are based on code-path inspection, not reproduced production incidents.

## 8. From local demonstration to enterprise pilot

| Stage | Required outcome |
|---|---|
| Local startup | Unique credentials, database migrations, web entry, a separate Skill repository and synthetic examples |
| Authorized pilot | Named owner, minimal data scope, configured model and node; one real read-only task and human handoff |
| Limited operation | Verified versions, retries, external-action acknowledgements, cost, permissions and failure recovery; a completed backup/restore exercise |
| Model improvement | Valid samples and evaluation sets, training, comparison, reviewed deployment and observed business outcomes |
| Wider adoption | Expand teams, capabilities and nodes based on quality/cost evidence; check permissions, capacity and maintenance ownership |

Use synthetic data for public demonstrations, following requirements/design → implementation → review/deployment → execution evidence → improvement. Personal contributions and outcomes require their own evidence. Keep public-release preparation, training capability and actual business results distinct.

## Coding Harness status

The old runtime and launcher have been removed. Coding sessions and runtime-based creation are unavailable; history and platform governance remain. No replacement adapter is accepted. See the [assessment](HARNESS_REPLACEMENT.en.md).

## Deployment and data-default clarification

Project SDK training-sink queueing is independent of automatic model training and retains a legacy target fallback; resident-model routes still use fixed aliases. Running Bridge without arguments attempts service installation. See the [SDK guide](../guides/project-gateway-sdk.md), [Bridge guide](../../bridge/README.md) and [documentation review](DOCUMENTATION_REVIEW.en.md).
