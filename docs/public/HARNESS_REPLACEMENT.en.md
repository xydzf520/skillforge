# Coding Harness replacement and retirement record

[中文（默认）](HARNESS_REPLACEMENT.md) · Reviewed: 2026-09-20

## Decision

**Prioritize an OpenCode Server adapter, evaluate DeepSeek Harness experimentally, and retain OpenHands SDK as a remote-sandbox option.** This is a project-specific engineering recommendation, not a performance ranking. No replacement adapter is connected or accepted in this edition.

The original project identified its former coding runtime as `aiclawcode`. It is distinct from AIClaw / OpenClaw / Bridge execution nodes, which remain unchanged.

SkillForge already owns its Vue workbench, FastAPI services, permissions, MCP definitions, Skill Git and review. It needs a replaceable execution engine, rather than another enterprise control plane.

| Candidate | Official capabilities and license | Project assessment | Current status |
|---|---|---|---|
| OpenCode | MIT; HTTP/OpenAPI, SSE, cancellation, permission responses; DeepSeek and custom model providers | An independent service fits the existing backend/UI boundary; requires event, permission and workspace adapters | First candidate; not installed or integrated |
| DeepSeek Harness | MIT; plugins, Python SDK, several model API protocols; developer preview | Promising for customized execution; profile behavior and compatibility need explicit acceptance | Experimental candidate; source/docs reviewed, not run |
| OpenHands Software Agent SDK | MIT; Python Agent/Conversation, tools and remote Agent Server | Consider for isolated background workloads; adapt its event and conversation model | Alternative; not installed or integrated |

Sources: [OpenCode server](https://opencode.ai/docs/server/), [providers](https://opencode.ai/docs/providers/), [license](https://github.com/anomalyco/opencode/blob/dev/LICENSE); [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), [SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.md), [providers](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md); [OpenHands architecture](https://docs.openhands.dev/sdk/arch/overview), [license](https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE).

Licenses above describe the inspected components, not the root project or every transitive dependency.

## DeepSeek-specific findings

Inspected source: `ddefc45fbc7f8e46dd73185e68295696d1297887`.

The official [safety notice](https://github.com/deepseek-ai/deepseek-harness/blob/ddefc45fbc7f8e46dd73185e68295696d1297887/SAFETY.md) describes an unaudited developer preview. Its [minimal SDK profile](https://github.com/deepseek-ai/deepseek-harness/blob/ddefc45fbc7f8e46dd73185e68295696d1297887/docs/user/guide/python-sdk.md) uses `danger-full-access` and enables a session-log contributor that uploads unaccepted log suffixes alongside DeepSeek requests. This finding applies to that profile and revision, not every profile.

For an enterprise pilot, pin the revision, use an isolated container/home, select plugins explicitly and disable `session-log-deepseek.enabled`; then verify actual egress. Disabling that plugin does not remove business context from ordinary model requests. Workspace paths alone are not an isolation boundary.

## Responsibility boundaries

- **SkillForge control layer:** identity, organizational access, context selection, tools, versions, reviews, evaluation, budgets and audit.
- **Third-party Harness:** model/tool loop, cancellable sessions, execution state and artifacts through an adapter.
- **Model provider:** official DeepSeek, an authorized compatible gateway or a local model. Test each route for tools, streaming, reasoning fields and usage rather than inferring compatibility from its name.

The proposed flow is `Workbench → adapter → isolated runtime → draft/tests → Skill Git → review/publish`. Adapter edges are future work.

Enterprise assets remain in the platform: validated workflows, prompts, skills, evaluation cases and reviewed outcomes. A runtime swap is not model training. Authorized events must be sanitized, checked and reviewed before becoming examples or training data. Retain links to Skill commits, prompt/model versions, tool summaries, outputs and corrections; raw logs are not ground truth.

## Changes delivered

- Restricted vendor source and artifacts were excluded during initial export and have not been restored.
- Removed CLI startup commands, `CODING_AGENT_DIST_PATH` and `CODING_AGENT_NODE_BIN` settings. The compatibility session refuses new launches even with old enable flags.
- Removed the embedded upstream proxy, standalone GLM proxy and runtime-only probe/benchmark scripts.
- Connection and runtime-based MCP tests return an explicit unavailable result without loading model credentials or launching tools.
- Missing runtime ends Skill generation without deleting a draft or retrying that failure.
- The settings page shows replacement status instead of an obsolete enable form. Old saved configuration cannot reactivate the removed CLI or block the whole control plane's startup.
- Replaced guidance recommending the old default runtime and removed its dedicated integration analysis.

Platform orchestration, permission policies, MCP definitions, historical event translation, drafts, validation contracts and database history remain. Some compatibility code retains old protocol terminology; it is not an implemented replacement. Provenance comments remain for ownership review. Removing attribution would not establish redistribution rights.

The private original, independent node execution, ordinary model services and Skill review state machine remain unchanged.

## Proposed adapter contract

| Responsibility | Platform acceptance requirement |
|---|---|
| Capabilities and health | Version and explicit support for approvals, cancellation, recovery and usage |
| Create session and send turn | User, Skill, commit, prompt/config versions and idempotency key |
| Events | Normalize text, tools, approvals, file changes, usage, completion and errors |
| Approve tool | Bind session, call ID and argument hash; edited arguments invalidate approval |
| Cancel and close | Bounded wait and execution-side confirmation |
| Resume | Recover existing state without replaying tools; cross-engine continuation is not assumed |
| Artifacts and usage | Changes/test evidence return to SkillForge; redact credentials and distinguish measured from estimated cost |

Expose OpenCode only to the backend, authenticate it, and isolate processes/containers, homes and workspaces by authorization scope. A shared service password is not application multi-user authorization. Mount only the selected draft workspace initially. MCP credentials and publish/notification actions remain governed by SkillForge. Do not reproduce the old creation path's `bypassPermissions` assumption.

Before enabling an adapter, verify dependency/license pinning, clean installation, real tools and denials, approval invalidation, two-user isolation, cancellation, timeouts, crash recovery and idempotency. Validate generation through testing, diff, Git and human review, plus log egress and data-to-training gates.

This turn validates retirement, not live third-party execution or model integration. See [validation evidence](VALIDATION.md).
