# SkillForge product case: turning business experience into enterprise AI capabilities

[中文](PORTFOLIO.md) · **English** · [Back to README](../../README.en.md)

## Project and responsibility at a glance

| Item | Description |
|---|---|
| Business problem | How can methods developed by individual AI users become reusable, transferable and verifiable team capabilities? |
| Product | Enterprise AI workspace, Skill and workflow collaboration, authorized execution, feedback and capability assets |
| My responsibility | Responsible for building and advancing the SkillForge enterprise AI platform |
| Case focus | Business problem definition, platform product planning, human–AI collaboration, AI application delivery and improvement strategy |
| Available evidence | Product and process descriptions, current code, synthetic examples and targeted local validation |
| Edition shown | Independently prepared from the original project for public release; enterprise data is excluded, and external integration and production acceptance are recorded separately |

The central question is: **after one person completes work with AI, how can the team reuse the effective method and improve it from actual outcomes?**

Personal responsibility is self-reported. Implementation, individual contribution and business outcomes require separate evidence; code alone cannot establish contribution shares or returns.

## 1. Define product scope from business needs

Everyday enterprise use brings four connected needs:

- **Employees can use it:** clear entry points, input requirements, output standards and exception handling.
- **Business owners can accept the work:** inspect evidence, confirm actions and verify actual outcomes.
- **Product and engineering can maintain it:** change, test and hand over rules, prompts, tools and versions.
- **Leaders can assess investment:** inspect quality, human review, maintenance cost and team reuse.

The product connects business standards → Skills or applications → review and authorized execution → human verification → improvement. Deliver a capability with explicit acceptance criteria, then use real feedback to determine expansion. [See roles and handoffs](OPERATING_MODEL.en.md).

## 2. Product choices and tradeoffs

The following summarizes the current product structure for discussion alongside specific versions. It is not a complete historical decision log.

| Problem | Design choice | Value and cost |
|---|---|---|
| Sharing methods across people | Package methods as Skills, input/output contracts, cases and versions | Supports reuse and handoffs; rules and cases require ongoing maintenance |
| Coordinating runtime environments | Platform owns access, assets, review and observation; Harnesses and compatible nodes execute | Business methods and runtimes can evolve separately; adapters and regression checks remain necessary |
| Coordinating applications and nodes | A shared capability gateway organizes model, tool and data calls; queues, schedulers and Bridge coordinate distributed execution | Reuse integration and operational controls with quotas, permissions and evidence; high availability and general cross-instance routing still need validation |
| Connecting generated output to delivery | Record reports, tasks, owner decisions and actual outcomes separately | Makes accountability and completion evidence explicit; human review belongs in the cost model |
| Choosing how feedback should improve capability | Separate knowledge updates, prompt/workflow changes and model training | Match investment to the problem before committing to training without sufficient samples or evaluation |
| Choosing strong or private small models | Compare exploratory and stable tasks on quality, latency and total cost | Preserves choices without assuming that smaller models are always cheaper or more accurate |

These choices emphasize business priorities, handoffs, reuse, execution boundaries and evaluation. See the [capability map](CAPABILITIES.en.md) for implementation limits and outstanding work.

## 3. Connect capabilities to inspectable evidence

| Capability | Product expression | Evidence |
|---|---|---|
| Enterprise AI product planning | Connect business scenarios, employee use, owner acceptance and platform governance | [Product form](../../README.en.md#product), [operating diagrams](OPERATING_MODEL.en.md) |
| Agent application productization | Organize inputs, context, authorized capabilities, execution records and human intervention | [AgentCore](../../app/agent_core/), [Project service](../../app/projects/), [SDK](../../sdk/) |
| Gateway and distributed-execution product design | Coordinate application integration, capability calls, node execution, quotas and traceability | [Gateway responsibilities and boundaries](../guides/project-gateway-sdk.md#网关职责与分布式协作), [task queue](../../app/execution/task_queue.py), [scheduler](../../app/execution/scheduler.py) |
| Platform and asset design | Manage Skills, workflows, prompts, feedback provenance and versions | [Asset architecture](ARCHITECTURE.md), [capability map](CAPABILITIES.en.md) |
| Delivery and acceptance | Separate implementation, configuration, tests and actual business completion | [Validation record](VALIDATION.md), [value evaluation](../../README.en.md#value-measurement) |
| Enterprise model evolution | Connect qualified samples, datasets, training, evaluation, deployment review and rollback | [Data and training flow](../../README.en.md#training) |

HR can start with project scope and responsibility; business interviewers can discuss scenarios, tradeoffs and delivery; technical interviewers can follow interfaces, code and validation records. Job titles, dates, adoption and returns need personal and business evidence rather than inference from code or test counts.

## 4. Demonstrate one business story

A synthetic operational-anomaly analysis can connect the product:

| Step | Judgment to explain | Material |
|---|---|---|
| Define the goal | Which exceptions matter, and who accepts the result? | Synthetic inputs, metric definitions, positive/negative cases and output standards |
| Organize capability | How can another person reuse the method? | Configured manual/reuse path, Skill version and input/output contract |
| Authorize execution | Who can run which version in which environment? | Review, access, node configuration and execution records |
| Verify delivery | How does the owner receive the report and confirm subsequent action? | Reports, tasks, confirmation or rejection, actual or unknown outcomes |
| Improve | Does failure call for a data, workflow or model change? | Traceable feedback, candidates, cases and next-version validation |

This is a demonstration route, not a bundled enterprise scenario. Check [creation paths](../spec/skill-creation-paths.md) and runtime configuration first. Explain unconfigured steps with diagrams and label synthetic data. The retired coding runtime is removed; automatic file generation, custom Harness adaptation and real training are not accepted demonstrations.

## 5. Showing capability through AI-assisted development

A useful account covers the full method: **define the problem and acceptance criteria → prepare context and constraints → collaborate with AI coding tools → inspect diffs and tests → verify deployment → correct problems**.

Each actual example should explain which judgments the person owned, what AI did and how errors were identified and corrected, supported by designs, diffs, tests or deployment evidence. Tool names supplement the development method. CLI/MCP/SDK interfaces do not prove that an author used every tool or independently wrote the whole system.

Using AI coding tools during development differs from integrating a Harness into the product runtime. The former demonstrates development collaboration; the latter requires specific runtime capability and acceptance evidence.

## 6. Connecting the case to a résumé

> Responsible for product planning and delivery of SkillForge, an enterprise AI platform with centralized control and distributed execution. Advanced a unified capability gateway for model, tool and business-data calls alongside execution across compatible nodes. Connected permissions, version review, quotas and run traces with business delivery, feedback and training/evaluation workflows to support reuse across applications and improvement of enterprise-owned models.

The résumé summary should emphasize the transferable ability to turn business needs into platform capabilities and coordinate delivery. Gateway, queue and node mechanisms belong in the project experience as evidence. Do not claim a validated highly available cluster, superior throughput or quantified savings without separate measurements.

In a personal résumé, follow the responsibility and approach with separately verified, authorized outcomes, specifying scope, period and measurement definitions. Actual training, adoption, efficiency improvements and public-release preparation are separate claims.

A portfolio can link to the [public SkillForge source](https://github.com/xydzf520/skillforge), where recruiters can review the product, architecture and implementation. Describe public-preview capabilities, personal contributions and verified business outcomes separately; see the [release boundary](RELEASE_BOUNDARY.md).
