![SkillForge](docs/brand/readme-en.svg)

# SkillForge

[简体中文](README.md) · **English** · [Apache-2.0](LICENSE) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/xydzf520/skillforge/issues)

**Turn methods discovered by individuals into AI capabilities that teams can reuse, deliver and improve.**

SkillForge is a self-hostable **enterprise AI workspace and skill collaboration platform** connecting business standards, workflows, tool execution, human confirmation and outcome records. It helps organizations turn methods scattered across individual conversations, scripts and experience into team capabilities with owners, versions and acceptance evidence.

Employees run skills and applications through a browser; business owners review outcomes and handle exceptions; product and engineering teams improve methods from feedback. Organizations can connect existing Agent services through a **custom Harness—an Agent runtime and integration framework**—associating execution with platform authorization, versions and outcome records.

The value proposition is that **an investment in completing today's task also retains reusable methods and verifiable experience for the next task**. Whether this reduces rework, improves delivery or lowers costs must be tested against a baseline in the organization's own work.

> This is a **public preview**, published under Apache-2.0 with SK branding and an isolated deployment configuration. The [latest release review](docs/public/RELEASE_CHECK_20260920.en.md) lists passed checks and remaining full-suite failures. Source code and local validation records are available; external integrations, actual training and production deployment require separate configuration and acceptance testing. See the [release boundary](docs/public/RELEASE_BOUNDARY.md) for licensing and publication status.

Read: [Enterprise value](#value) · [Our view of the future](#future) · [Author and career interests](#case-study) · [Screenshots](#screenshots) · [Product](#product) · [Project management](#projects) · [Shared Skills and plugins](#sharing) · [SDK integration](#project-sdk) · [Use cases and value validation](#enterprise) · [Collaboration and custom Harness](#harness) · [Authoring Skills](#authoring) · [Enterprise assets](#assets) · [Data and training lifecycle](#training) · [Technology](#technology) · [Quickstart](#quickstart) · [Validation](#validation)

<a id="value"></a>

## Why an enterprise would use SkillForge

Taking individual AI practices into everyday business operations requires reusable methods, accountable delivery, operational management and continuous improvement. SkillForge organizes the product around five intended outcomes:

| Business problem | Product mechanism | Intended value |
|---|---|---|
| One person can deliver, but others must rediscover the method | Version business rules, prompts, tools and example cases as Skills and workflows | **Reuse expertise**: share validated methods and reduce repeated implementation and handoff effort |
| Content is generated, but its next owner or action is unclear | Connect outputs to reports, tasks, owner confirmation and outcome records | **Deliver business work**: make responsibilities and next actions explicit, and track whether recommendations are acted upon |
| Models, tools and scripts are difficult to manage together | Organizational access, review, releases, versions, execution records and usage information | **Control operations**: define scope and retain evidence for diagnosis, review and withdrawal |
| Previously corrected problems recur | Link feedback and failures to sources, knowledge, improvement candidates and qualified samples | **Accumulate experience**: use actual work to inform the next method and its evaluation |
| Changing models or runtimes risks repeated implementation | Connect runtime environments through Skill assets, input/output contracts and SDK / API interfaces | **Preserve choices**: reuse business rules and evaluation materials where possible, then validate the appropriate model or Harness |

For **employees**, the benefit sought is an accessible method with clear standards. **Business owners** need verifiable outcomes, exception handling and accountability. **Product and engineering teams** need reusable tools, versions and cases. **Enterprise leaders** need evidence about quality, cost and reuse to decide whether further investment is justified.

The core long-term assets are **workflows, prompts, Skills and execution records linked to business outcomes and human corrections**. They retain why a method is used, how it works and what happened in practice. After selection and review, they can support knowledge reuse, skill improvement or private-model training. Raw logs require access, quality and retention policies; volume alone does not establish asset value.

Models and Harnesses can evolve while the organization maintains its own standards, methods and evaluation evidence. A private small model is an option once data, quality and cost conditions justify it. Runtime adaptation, automatic model selection and realized benefits each require validation. The edition overview below distinguishes implemented foundations, configuration prerequisites and missing integrations.

For module-by-module implementation evidence, read the [product map, code review and known limitations](docs/public/CAPABILITIES.en.md).

### Position in the open-source ecosystem

Open-source projects already address enterprise AI workflows, retained skills and continual learning. SkillForge focuses on organizing these elements into collaborative processes that an organization can use, hand over and improve.

| Related project | Primary direction described in its official documentation | Shared concerns with SkillForge |
|---|---|---|
| [Coze Studio](https://github.com/coze-dev/coze-studio) | Visually build, debug and publish Agents, applications and workflows | Building and delivering AI capabilities |
| [Acontext](https://github.com/memodb-io/Acontext) | Distill conversations, execution traces and task outcomes into reusable Skills | Retaining experience and reusing skills |
| [Agent Lightning](https://github.com/microsoft/agent-lightning) | Collect interactions and train models while Agents run with real Harnesses | Connecting execution data to training |
| [Reef](https://github.com/Human-Agent-Society/reef) | Connect inference, feedback, learning and versioned delivery, including model-weight and Harness optimization | Continuous improvement from feedback |

This is a comparison of documented directions, checked against official repositories on 2026-09-20. It is not a deployment benchmark or a claim that these projects are integrated into SkillForge.

**SkillForge's intended distinction is turning business experience into AI capability assets that an organization can maintain over time:** employees use capabilities, business owners review outcomes and handle exceptions, and product and engineering teams maintain versions. Workflows, prompts, Skills and human corrections retain their provenance and links to business outcomes. Teams can then decide whether to revise a process, improve a skill or prepare qualified data for private-model training.

The project is positioned as an **enterprise AI capability collaboration and asset management platform**. Its open-source value should be demonstrated through reproducible business cases, accountable handoffs and verifiable improvement records. The table below and capability documentation distinguish code foundations, availability and pending acceptance. Replacement coding Harness integration and real training remain unaccepted; the project makes no claim to be the first, the only, or a complete production learning loop.

<a id="future"></a>

## Our view of the future

The following describes product direction and judgment, not completed functionality or an inevitable industry outcome.

**Enterprise AI will increasingly be evaluated through tasks that can be accepted or rejected.** Beyond an answer, teams need to know which data was used, what actions were completed, where confirmation was required and how a failure can be recovered. SkillForge organizes this information around tasks, versions, tools and execution evidence.

**Organizations will need capability assets that can accumulate over time.** People, models and tools may change, while validated business rules, skill versions, cases and evaluation standards remain reusable. The product aims to make these assets discoverable, composable and maintainable, reducing repeated implementation across teams.

**As models become stronger, organizations need ownership of their business assets.** We expect foundation-model capabilities to keep advancing. Workflows, prompts, Skills and execution logs linked to business outcomes and human corrections will be among an organization's most important AI assets. Workflows describe how work gets done; prompts express objectives and standards; Skills package methods into capabilities; logs and feedback show whether those methods work in practice. Preserve traceable, reviewable experience and apply access and retention policies to raw data. Accumulating unlimited logs does not establish their value.

**An organization-controlled, privately deployed small model is a business option worth evaluating.** When the total cost of frontier-model calls, long contexts and human review is high for frequent tasks, or generic outputs do not sufficiently match business conventions, validated business samples can support small-model fine-tuning. Here, a “proprietary private small model” means an organization controls operation and access while keeping its proprietary data and fine-tuned artifacts private. It can build on a base model whose license permits that use; this does not imply training from scratch or owning all rights to the base model. Open platform source and private enterprise data and models can be managed separately.

### Combining strong models with private small models

| Task conditions | Suggested approach | Decision criteria |
|---|---|---|
| New scenarios, complex problems or insufficient examples | Use a strong model for exploration, with business review | Quality of judgment, verifiability and exploration value |
| Frequent tasks, stable rules and reliable samples | Use a private small model after evaluation | Business metrics, latency, total cost per task and maintenance cost |
| Exceptions the small model cannot handle reliably | Escalate to a person, or to a strong model where data authorization permits | Explicit failure signals and escalation conditions; retain the outcome |
| New feedback and corrections | Update knowledge, prompts, Skills or training samples | Identify the cause before deciding whether to retrain |

This is a direction for **capability accumulation and using different models for different tasks**. Model prices change, and private deployment has compute and operational costs. Choose using the same business test set and measured workloads. The project has foundations for model connections, training and deployment to target skills; general automatic model selection, cost optimization and fallback across models still require scenario-specific adaptation and validation.

**Learning should be driven by actual outcomes.** A failed task may require a correction to data, knowledge, tools or rules. Model training becomes appropriate when the problem warrants it and the available samples and evaluation conditions are sufficient. Improvement depends on identifying the layer that needs to change.

**People will focus more on objectives, standards and exceptional cases.** Agents can perform bounded steps, while accountability, permission grants and consequential decisions still need identifiable owners. Reliable collaboration should let people inspect, intervene and correct execution.

**Execution frameworks can evolve while enterprise capability assets endure.** A custom Harness lets an organization choose orchestration and execution methods for its tasks. The platform connects these environments through stable authorization, version, input/output and evidence interfaces. We aim to preserve validated business rules, skills and evaluation materials when a model or Harness changes. Moving between runtimes still requires adaptation and regression testing.

| Direction | Existing foundation | What we aim to improve |
|---|---|---|
| A reusable team capability library | Skill versions, portals, project applications and access structures | Easier discovery, composition and reuse across teams |
| Explainable human–AI collaboration | Reviews, execution records, tasks and feedback | Clearer handoffs, recovery and business acceptance workflows |
| Evidence-based improvement | Knowledge, learning provenance, samples and training controls | Stronger data selection, evaluation baselines and controlled rollout |

This direction depends on accountable business teams, usable data, clear access boundaries and standards for checking outcomes. **SkillForge aims to become an enterprise AI capability system that accumulates through real work.**

<a id="case-study"></a>

## About the author and career interests

I have **over 10 years of product experience** across consumer applications, enterprise SaaS and smart hardware, with experience in product growth, cross-functional team management and enterprise AI implementation. I am seeking **product leadership, senior specialist or AI product roles in Shanghai**.

## Current edition at a glance

| Scope | Current status |
|---|---|
| Workbench, manual editing, versions and review | Code and targeted local checks exist; configure the backend and storage below |
| Ordinary AI calls, requirement interviews and skeleton synthesis | Separate model-call paths; require your own model configuration and acceptance |
| Workbench coding chat and full-file generation through the retired runtime | **Unavailable**; OpenCode / DeepSeek Harness are replacement candidates only |
| Nodes, business integrations, model training and deployment | Require configured services and hardware; no enterprise data, model weights or production acceptance are included |

Use this README for setup. Historical “implemented” or “released” labels in design documents are not acceptance results for this edition. See the [documentation review](docs/public/DOCUMENTATION_REVIEW.en.md).

<a id="screenshots"></a>

## An enterprise view of the product

Follow **employee use → team building → governance → operations and improvement** through these nine screenshots to see how AI capabilities fit into everyday work.

These are the **actual public-edition frontend with synthetic fixtures**, not enterprise records, real training results or production acceptance evidence. The UI is shown in Chinese with English captions below. Click any image for the original. [Sources and reproduction](docs/screenshots/README.md).

### 1. Employee use: discover capabilities and open business applications

| Capability hall: where to start | Project applications: delivery to business users |
|---|---|
| [![Capability hall with departmental filters and business capabilities; synthetic data](docs/screenshots/capability-hall.jpg)](docs/screenshots/capability-hall.jpg) | [![Project directory with customer-service QA, business analysis and knowledge tools; synthetic data](docs/screenshots/project-applications.jpg)](docs/screenshots/project-applications.jpg) |
| Discover capabilities by department and scenario to find the right entry point for a task. | Organize lightweight web apps, dashboards and internal tools by department, visibility and runtime state. These are directory examples that have not been run. |

### 2. Team building: turn methods into Skills and workflows

| Skill authoring: define business standards | Workflow composition: define steps and exceptions |
|---|---|
| [![Skill authoring with business requirements, interview and output controls; synthetic data](docs/screenshots/skill-authoring.jpg)](docs/screenshots/skill-authoring.jpg) | [![Playbook canvas with dependencies, timeouts and failure policies; synthetic data](docs/screenshots/workflow-canvas.jpg)](docs/screenshots/workflow-canvas.jpg) |
| Define triggers, criteria, outputs and human confirmation. This image shows requirement entry; no model generation was performed. | Inspect dependencies, sequence, timeouts and failure policies so teams can review and maintain execution methods. This is a read-only preview. |

### 3. Governance: establish ownership, access and an audit trail

| Organization: who belongs to each team | Role permissions: capabilities and scope |
|---|---|
| [![Organization tree with departments, a project group, primary and secondary memberships; synthetic data](docs/screenshots/organization-management.jpg)](docs/screenshots/organization-management.jpg) | [![Department administrator capability summary by resource, operation, scope and source; synthetic data](docs/screenshots/role-permissions.jpg)](docs/screenshots/role-permissions.jpg) |
| Inspect departments, project groups, memberships and managers to understand cross-team collaboration. Membership counts include secondary assignments, not unique headcount. | Inspect role summaries, account state and department scope. Actual access also depends on account state, organizational membership and object permissions; this is not a per-permission grant editor. |

**Audit trail: inspect who acted on which object, when, and why an action was handled or denied.**

[![Audit trail with approval, permission denial and skill editing events; synthetic data](docs/screenshots/audit-trail.jpg)](docs/screenshots/audit-trail.jpg)

Find recorded events by user, action, result and time for investigation and review. Approval and denial events here are synthetic examples, not actions or security acceptance tests performed during this capture.

### 4. Operations and improvement: observe delivery and retain useful evidence

| Node operations: where tasks execute | Data and training: which experience to retain |
|---|---|
| [![Node operations with availability, schedules, Git versions and execution records; synthetic data](docs/screenshots/node-operations.jpg)](docs/screenshots/node-operations.jpg) | [![Learning flow from raw records through cleaning, training, evaluation and deployment; synthetic data](docs/screenshots/learning-flow.jpg)](docs/screenshots/learning-flow.jpg) |
| Compare platform and node versions, and inspect schedules, failures and execution results. | View records, retained assets, training, evaluation and deployment together, with human review entry points. Actual training and deployment still require configured environments and acceptance checks. |

<a id="product"></a>

## 1. How the product supports these outcomes

The product combines a browser workspace, connected execution nodes and interfaces for development tools.

| Audience | Product interface | Main activities |
|---|---|---|
| Employees | Capability hall, application portal, inbox and tasks | Discover authorized capabilities, provide inputs, read reports, handle tasks and give feedback |
| Business owners, product and AI teams | Skill Studio, workflow editor, reviews and execution monitoring | Define business rules, assemble tools and workflows, validate results, manage versions and releases |
| Administrators and developers | Administration pages, CLI / SDK / MCP and Bridge nodes | Configure organizational access and data connections, connect models and tools, deploy nodes and investigate failures |

The main objects serve different purposes:

- **Skill**: a reusable way to perform a task, with an intended scope, inputs and outputs, business rules, tool use, tests and versions.
- **Agent**: advances a task within authorized tools, context and execution boundaries, handing steps that require confirmation back to a person.
- **Harness**: hosts the Agent execution loop, organizing context, model calls, tool execution, state recovery and human intervention. Organizations can build their own and integrate through platform interfaces.
- **Playbook**: organizes capabilities into a repeatable business workflow.
- **Project application**: brings a report, analysis tool or other lightweight web application into the workspace. It uses authorized capabilities through the platform gateway and reports results back.
- **Bridge and execution nodes**: connect actual runtime environments, receive skills and scheduling configuration, execute tasks and report status.

For example, an employee can run a business-analysis capability from a web form, while a configured node can run it on a schedule. Both entry points should preserve the version, inputs and evidence needed for review.

### Capabilities in the workspace

| Part of the work | Product capabilities | What users gain |
|---|---|---|
| Discover capabilities | Capability hall, application portal and internal market | Find, run and reuse capabilities within authorized scope |
| Build and combine capabilities | Skill Studio, templates and instances, Playbooks and business Agent blueprints | Turn business methods into owned, versioned skills and workflows |
| Connect business systems | Data sources, browser collection, MCP, data contracts and project gateway | Complete tasks using the organization's own data and services |
| Build business applications | Project hosting, standalone web entry, application services and SDKs | Reports, forms and small tools become products employees can use directly |
| Personalize the experience | AI previews of forms/results, saved personal versions and restoration | Adjust personal pages within allowed components while preserving underlying logic and permissions |
| Deliver and collaborate | Reviews, approvals, report inbox, task assignments, SLA and acknowledgements | Hand recommendations to accountable owners and record acceptance or rejection |
| Run and diagnose | Execution nodes, schedules, queues, traces, task tree and diagnostics | See which version is running, where it failed and who needs to act |
| Produce and review media | Creative planning, batches, annotations, revisions, delivery and performance data | Bring content generation into a reviewable business process |
| Accumulate and improve | Knowledge retrieval, learning provenance, skill candidates, datasets, training and deployment | Improve context, business methods and model capabilities separately |
| Govern and measure | Organizational access, prompt versions, model settings, usage cost and audit | Establish who can do what, which configuration was used and what usage was recorded |

These have code foundations; availability depends on configuration and acceptance. A business Agent blueprint is distinct from a runtime node, and an internal market is not an established public trading marketplace. The [capability map](docs/public/CAPABILITIES.en.md) lists implementation entry points and limits.

<a id="projects"></a>

### Project: manage applications created by individuals and departments

**Turn a person's useful tool into an application the team can find, use and maintain.** Dashboards, forms, analysis pages and internal tools built with Codex, Claude Code or other development tools can join one Project directory. Organizations track ownership, department, visibility, versions and run records, reducing reliance on personal computers, chat links and temporary services.

[![Enterprise Project directory organized by department, application type and runtime state; synthetic examples](docs/screenshots/project-applications.jpg)](docs/screenshots/project-applications.jpg)

| What the organization manages | How Project represents it |
|---|---|
| Ownership and maintenance | Owner, department, description and entry point; directory views include “created by me,” department and company |
| Access | Private, department and company visibility; application access, project editing and access to other users' runs have separate checks |
| Delivered versions | Static packages or external entry points link to project versions; uploads retain package checksums and runs reference versions |
| Shared capabilities | `projectforge.yaml` declares entry points, capabilities and output contracts; Project Gateway / SDK provides authorized models, tools and data |
| Evidence after use | User, input, output, capability calls, reports, tasks and run traces; configured and governed records can enter the learning-asset workflow |

**From individual creation to organizational use:** define a task → build an application → confirm owner and department → check the package and capability declarations → an authorized member uploads or registers it → colleagues open it from the directory → results and feedback return → maintain the next version.

`sf project init --path .` prepares the project manifest, `sf project doctor --path .` checks the entry and contracts, and `sf project submit --path .` uploads a static package or registers an external URL through platform APIs. Check ownership and visibility in `projectforge.yaml` before submission. See the [project and developer-tool workflow](docs/guides/sf-codex-claude-workflow.md) and [synthetic project examples](docs/examples/projects/README.md).

Project currently focuses on lightweight web applications, dashboards, internal tools and external entry points. Applications with their own backend still need an execution environment. Registration does not automatically collect all code from personal devices, and application visibility does not grant everyone access to other users' inputs or outputs. Project registration and Skill review/publication are distinct workflows; application acceptance and release rules need to reflect their risks.

<a id="sharing"></a>

### Codex and other development tools: share Skills, tools and applications

**A method validated by one member can become a Skill that colleagues discover and reuse from Codex.** SkillForge provides the `sf` CLI, a Codex plugin entry point and MCP integration. Claude Code can use the same CLI and the repository's command instructions. Individuals and departments retain business methods, rules, prompts, tool contracts and version evidence alongside their applications.

| Shared layer | What is shared | How colleagues use it |
|---|---|---|
| **Skill: reusable methods** | Business instructions, prompts, scripts, input/output contracts and package versions | Discover own, department or visible skills; download or install authorized packages into a local workspace, then use, test or improve them |
| **MCP: reusable tools and data access** | Registered tools for organization lookup, data capabilities, run queries and analysis | Inspect the catalog from Codex or another compatible client and call through the platform under the current identity |
| **Project: reusable applications** | Web applications, dashboards and internal tools | Business users open applications in the shared directory; developers integrate and maintain them through CLI, SDK and API |

| Discover shared Skills in the development workflow | Inspect shared tool capabilities |
|---|---|
| [![SF command center with own and department Skills plus project integration commands; synthetic catalog](docs/screenshots/shared-skill-commands.jpg)](docs/screenshots/shared-skill-commands.jpg) | [![MCP catalog with organization lookup, data capabilities and run analysis; synthetic catalog](docs/screenshots/shared-mcp-capabilities.jpg)](docs/screenshots/shared-mcp-capabilities.jpg) |
| The `sf` plugin connects skill discovery and project integration to development. Commands are displayed only; no upload or installation was performed. | Discover tools, read/write attributes and invocation entry points. This is a selected synthetic catalog with zero calls. |

The sharing workflow is **capture an individual method → test and submit for review → share within authorized scope → colleagues install and reuse → test and submit subsequent changes for review**. Local installation retains the Skill ID and base version. Installation, editing, review and publication are distinct; downloading a package does not publish it company-wide.

After installing `sf` and signing in to your own SkillForge deployment:

```bash
sf skill list --scope mine
sf skill list --scope department
sf skill list --scope visible
sf skill install <skill_id> --path ./skills
sf skill status --path ./skills/<skill_id>
sf mcp catalog
```

Submit changes with `sf skill submit --path ./skills/<skill_id>`. Catalog access and downloads are permission-bound. Sharing covers authorized Skill assets and capability entry points, not personal Codex accounts, subscriptions, conversation history or upstream credentials. Integration with external development tools does not restore the removed embedded Workbench coding runtime.

<a id="project-sdk"></a>

### SDKs: connect web apps, backend services and custom Harnesses

Plugins support a person's development workflow; **SDKs support programmatic execution and integration**. The same platform capabilities can be called from Codex, project pages, backend services or a custom Harness, linked to the appropriate project and run.

| Integration | Where it runs | Existing code capabilities |
|---|---|---|
| [Browser Project Gateway SDK](web/public/project-gateway-sdk.js) | Hosted web apps or external pages adapted to the host | Obtain run context from the parent window, record inputs, call declared capabilities and return reports/tasks without holding upstream model credentials |
| [Node / TypeScript Project SDK](sdk/node/index.ts) | Backend services, automation and custom Harnesses | Create runs with project credentials, call capabilities, submit outputs, upload assets and read traces; includes timeouts, error classification and bounded retries |
| [Python Project SDK](sdk/python/skillforge_project_sdk.py) | Python services, data processing and Agent programs | Corresponding project-run methods plus training-sample and dataset-version integration methods |
| [Skill runtime SDK](app/skill_runtime_sdk/skillforge_sdk.py) | Skill scripts in authorized execution environments | Use execution context to access platform data, tools and analysis while retaining data evidence |

The basic path is **project identity → create a run → record inputs → call authorized capabilities → return outputs/reports/tasks → inspect the trace → retain reusable evidence under governance rules**. Project SDK credentials have project scope, operation scopes, expiry and revocation; backend credentials stay server-side. Deployers still configure models, tools and training environments, and SDK integration alone does not make logs a qualified training dataset.

SDKs are provided as repository source; this does not imply published npm/PyPI packages or installation in arbitrary clients without adaptation. The [Project models](app/projects/models.py), [API](app/projects/router.py) and [service](app/projects/service.py) show ownership, versioning, access checks and execution records.

<a id="enterprise"></a>

## 2. Validate value in one business workflow

### Start with one task that can be evaluated

Choose work that is **frequent, has an accountable owner, has accessible data and produces verifiable results**. Establish a baseline before expanding automation.

| Example scenario | How it can be organized | What people remain responsible for |
|---|---|---|
| Business performance analysis | Read authorized data, identify exceptions, explain findings and create follow-up tasks | Metric definitions, exception criteria and actual business actions |
| Customer support and knowledge | Organize authorized materials, retrieve relevant knowledge and draft answers or recommendations | Knowledge approval, sensitive information and external commitments |
| Content and media production | Organize briefs, generation, quality review and targeted revisions | Brand standards, content rights and final publication |
| Internal department tools | Connect small analysis pages or forms as project applications and reuse platform capabilities | Data access, application acceptance and ongoing maintenance |

These are adoption examples. They require the organization's own data, rules and service connections; installing the platform does not connect those business systems automatically. Start with repeatable departmental work whose results can be checked. For a one-off personal need, or when data access and acceptance criteria are missing, establish the task and prerequisites before investing in a shared platform.

<a id="value-measurement"></a>

### How to establish whether the investment is worthwhile

Before a pilot, fix its task scope, measurement period, accountable owner and human baseline. Compare the same representative cases, including normal inputs, missing data, failures and exceptions. Record sample sizes and unknown outcomes.

| Dimension | What to compare | Evidence to retain |
|---|---|---|
| Delivery quality | Acceptance rate, critical errors, rework and human corrections | Fixed criteria, task denominator, failure and rejection reasons |
| Work efficiency | End-to-end time and human handling and review effort | Records from input preparation through outcome verification, including exceptions |
| Unit task cost | Total cost per accepted task | Model, infrastructure, human review, rework and allocated maintenance costs, including failed attempts |
| Team reuse | Whether the same effective version is reused and another person can take over | Scope of use, reused versions, handoff outcomes and maintenance effort |
| Business outcome | Whether agreed actions occurred and outcomes were confirmed | Actual business receipts and owner verification, with unknowns separate |

**Total cost per accepted task = all relevant costs for that task scope during the period ÷ accepted task count.** Use the organization's own cost-accounting conventions; do not calculate the ratio when no tasks were accepted. Treat saved time as released capacity first. Count a financial benefit only after verifying a reduction in spending or attributable additional value.

The platform provides execution, usage and feedback records. Business outcomes and some human costs still require external receipts or manual records. This is a pilot evaluation method, not a built-in financial accounting system or a claim of achieved returns. Validate delivery, cost and maintenance ownership against agreed quality criteria before expanding to more teams.

### Suggested rollout

1. **Choose a task**: identify its users and owner, and record current time spent, rework and errors.
2. **Define acceptance criteria**: specify inputs, outputs, decision evidence and actions that require confirmation.
3. **Connect the minimum capabilities**: grant only the necessary data and tool access; validate with synthetic data and read-only operations first.
4. **Run and review**: test normal inputs, missing data and failures, then compare with human results and revise the rules.
5. **Review and release**: pin a skill version, define visibility and configure execution nodes before introducing it to the target team.
6. **Improve from feedback**: examine task completion, rework and adopted recommendations before changing the workflow or model.

Acceptance should focus on task quality, human review effort, recovery from failures and reuse. Model calls and generated output measure activity; they do not establish business value by themselves.

### Completing one business workflow

For a synthetic business-exception analysis: the owner defines conventions and acceptance → product and engineering build the skill, tools and prompts → test inputs validate a reviewed version → release to an authorized node → generate reports and tasks → the owner confirms, revises or rejects → verify the actual business outcome → feed corrections and results into knowledge, skill improvements or training preparation.

Distinguish **asset release, task execution and business completion**. A generated report has not necessarily been read, a notification does not prove an action was completed, and finished training does not mean the new model is live. Compare quality, review time, end-to-end duration and total cost on the same task cohort, retaining failures and unknowns. See the [delivery process](docs/public/CAPABILITIES.en.md) for handoffs and measurement criteria.

<a id="harness"></a>

## 3. How people and AI collaborate

SkillForge connects business standards, implementation, use and review in one collaboration process.

![Enterprise collaboration: business standards, implementation, review, authorized use and outcome feedback](docs/public/diagrams/organization.en.svg)

This is a generic responsibility model, not a real reporting hierarchy or an application-permission map. Business ownership → delivery → review → authorized use → outcome feedback forms the collaboration loop. See the [organization, delivery, Harness runtime and improvement diagrams](docs/public/OPERATING_MODEL.en.md); the [data and training flow](#training) explains asset movement separately.

| Role | Responsibility | Handoff |
|---|---|---|
| Business owner | Define the problem, rules, acceptable results and approval boundaries | Task brief, decision criteria, positive examples and counterexamples |
| Product / AI team | Translate the task into skills, workflows and user interfaces | Input/output contracts, interactions, tool scope and acceptance plan |
| Developers and AI coding tools | Implement, debug, add tests and submit through authorized interfaces | Testable implementation, version and change description |
| Reviewers and administrators | Check access, risk, quality and release configuration | Reviewed version and explicit scope of use |
| Users | Run tasks and handle steps requiring human judgment | Actual outcomes, corrections and failure feedback |

CLI, SDK and MCP interfaces let AI coding tools participate in implementation and validation. Platform authorization still applies. Saving, submitting for review and releasing are distinct states: generated code or a saved draft does not automatically acquire production execution rights.

The platform manages skill assets, permissions, review, configuration distribution and observability. Scheduled execution primarily belongs to runtime nodes, with a platform fallback path. Business-rule management can therefore remain separate from the specific execution environment.

The repository also contains its own orchestration foundations. AgentCore uses LangGraph to organize generation and checks, with checkpoints and human-interruption recovery. Playbooks and execution services compose skills and dispatch to nodes. External Harness integration extends this control plane with a team's own execution approach; business workflows need not all use the same Agent loop.

### Integrating a custom Harness

Teams with an existing Agent service, or those that want to own its execution logic, can keep their Harness and connect it through an adapter. The Harness determines how a task runs. SkillForge manages which capabilities are available, who may use them, which versions have been reviewed and how outcomes are retained. Explicit identity, input/output contracts and execution records connect the two.

```mermaid
flowchart LR
    U[Employees or business systems] --> H[Custom enterprise Harness]
    H --> R[Task loop, context and execution state]
    H <-->|SDK / API / MCP| S[SkillForge control plane]
    S --> G[Authorized model and tool gateways]
    S <-->|Configuration and results| B[Bridge and compatible execution nodes]
    H -->|Return results and evidence| F[Run records, feedback and capability assets]
    S --> F
```

| Integration need | Existing foundation | What the custom Harness must implement |
|---|---|---|
| Orchestrate tasks and use platform capabilities | Python / TypeScript Project SDKs and HTTP APIs | Create runs with project authorization, submit inputs, invoke capabilities, return outputs and read traces |
| Give an Agent access to enterprise tools | MCP gateway and local stdio proxy | Discover authorized tool definitions, translate calls and results, and respect write confirmation and idempotency requirements |
| Execute skills in an environment you control | Bridge and node protocols | Adapt task and version distribution, identity verification, status and result reporting; validate cancellation and failure recovery |

Start with one read-only task: **authorize a project → create a run → obtain inputs → invoke an authorized capability → return results → verify execution records**. Model and tool credentials remain with the respective server-side services. Integrations use scoped authorization rather than extracting upstream secrets from the platform. Notifications, business-data changes and other external actions must follow the relevant tool's authorization and confirmation rules.

Source entry points: [Python SDK](sdk/python/skillforge_project_sdk.py), [TypeScript SDK](sdk/node/index.ts), [Project API](app/projects/router.py) and [Bridge deployment](bridge/README.md). These provide integration foundations; a custom Harness still requires an adapter and acceptance testing. The existing Bridge targets compatible gateway protocols, so arbitrary Agent runtimes should not be assumed to work without configuration or adaptation.

### Unified capability gateway and distributed execution

SkillForge separates centralized control from distributed execution. Business applications and custom Harnesses use a shared capability gateway to invoke models, tools and business data. The platform manages authorization, versions, quotas and execution records; compatible nodes perform the tasks. Teams can reuse integration and operational controls while retaining execution feedback as knowledge, sample candidates and evaluation material.

| What needs coordination | Current implementation | Business value |
|---|---|---|
| Model and tool calls across applications | Project SDKs, model profiles, MCP capabilities and access scopes | Reduce repeated integration and separate applications from provider credentials |
| Tasks and state across nodes | Bridge dispatch and result reporting, node schedules, platform fallback, worker registry and task queue | Manage tasks, versions and exceptions across execution environments |
| Concurrency, duplicate requests and quotas | PostgreSQL task claiming and scheduler locks, request idempotency and Redis rate limiting across processes | Reduce duplicate execution and consumption while controlling resource use |
| Operational evidence and improvement inputs | Run traces, model usage attribution, feedback and training-asset interfaces | Connect calls to business tasks and support quality, cost and model-improvement decisions |

These are implemented coordination mechanisms, not proof of a highly available cluster or measured throughput. Bridge connections remain process-local; rate limiting currently fails open when Redis is unavailable; resident-model and training-sink paths retain fixed-node compatibility constraints. General node routing, cross-instance failover and large-scale concurrency require further validation. See the [gateway responsibilities and boundaries](docs/guides/project-gateway-sdk.md#网关职责与分布式协作) (Chinese).

<a id="authoring"></a>

## 4. How to author a useful Skill

Write down the business judgment before implementing it in an editor or development workflow. A reusable Skill explains **what to do, what supports the decision, which actions are permitted and when to stop**.

| Part of the specification | Questions to answer |
|---|---|
| Objective and scope | Who is it for? What problem does it solve? When is it not applicable? |
| Inputs and sources | Which fields, time ranges and permissions are required? How should missing data be handled? |
| Decision rules | How are metrics defined? What evidence supports a conclusion? What exceptions apply? |
| Tools and action boundaries | Which operations are read-only? Which writes, notifications or external actions require confirmation? |
| Outputs and handoff | What must the result contain? Who reviews it? What counts as completion? |
| Tests and failure handling | What should happen with normal inputs, exceptions, timeouts and repeated execution? |

The following is an **example business brief** for drafting a skill, not an executable configuration file:

```text
Name: Daily business exception report
Objective: Help the business owner identify exceptions worth investigating and explain the evidence.
Inputs: Authorized daily aggregates, comparison periods, metric definitions and owner configuration.
Rules: Compare using approved definitions; report an inability to conclude when data is missing or inconsistent.
Outputs: Exceptions, data sources and time ranges, possible causes, open questions and recommended actions.
Boundaries: Read only authorized data; do not automatically change prices or budgets or make external commitments.
Handoff: Ask the owner to confirm business actions before execution.
Acceptance: Test normal data, missing data, changed definitions and repeated execution.
```

Implementation turns the brief into a skill definition, input/output contracts, tool bindings and tests. Saving creates a version in the separate Skill Git repository. After review, a specific approved version is used for release and node distribution. Execution records and feedback help diagnose problems and propose the next revision.

### Maintaining prompts, interfaces and code

- **Business prompts**: maintain objectives, conventions, variables, examples, counterexamples and output criteria alongside the skill or project version; compare revisions on the same samples.
- **Registered platform prompts**: the registry supports versions, content inspection, hashes and default selection. Defaults currently live in process memory; unified editing of all prompts and persistence across workers are incomplete. A UI selection does not establish durable configuration.
- **Default skill interfaces and code**: shared capability changes follow validation, versioning, review and release.
- **Personal interfaces**: declarative overlay preview, saving and restoration are implemented, scoped to user, Skill and surface. They cannot inject arbitrary scripts, expand data access or change execution logic.

Source: [prompt registry](app/common/prompt_registry.py), [personal UI service](app/portal/ui_service.py).

<a id="assets"></a>

## 5. What the organization retains after each use

```mermaid
flowchart TD
    A[Business goals and decision criteria] --> B[Skill or project application]
    B --> C[Versioning, review and release]
    C --> D[Authorized node execution]
    D --> E[Results, evidence and human feedback]
    E --> F[Selection, quality checks and provenance]
    F --> G[Knowledge and reusable experience]
    F --> H[Skill or workflow improvement candidates]
    H --> C
    F -. Eligible and authorized .-> I[Training and evaluation datasets]
    I --> J[Node training and evaluation]
    J --> K[Reviewed deployment or rollback]
```

The purpose is to connect results, evidence and feedback to their sources. Suitable material can support knowledge, skill improvements or training preparation. Failures and unknown outcomes should also remain available for review.

| Retained asset | How it can be reused |
|---|---|
| Skills and workflows | Preserve business methods, input/output contracts, tool bindings and traceable versions |
| Prompts and decision criteria | Associate objectives, metric definitions, examples, counterexamples and acceptance criteria with the relevant skill or application version |
| Execution evidence and feedback | Explain how a task was completed, where it failed and which decisions were corrected |
| Knowledge and experience | Supply processed business context for retrieval and future tasks |
| Samples and evaluation sets | Support model comparison or training when provenance, permissions and quality are sufficient |

Three different improvements are involved: **updating knowledge** supplies better context; **improving a skill or workflow** changes rules, tools or steps; **training a model** changes model parameters under a defined objective, dataset and compute setup.

Assets also need owners, allowed scope, effective versions, provenance and acceptance evidence. Logs should distinguish real execution from simulation, inference from confirmation and outputs from actual outcomes, with sensitivity and retention handling. Organizational controls, skill access, data governance, audit and usage records provide foundations; verify permission enforcement and evidence completeness for every integration path.

The code includes learning events, artifacts and provenance links, along with dataset versioning, training jobs, evaluation and deployment management. Actual training requires separately configured models, nodes and data. Automatic training and deployment approval are disabled by default in this edition. Knowledge ingestion, sample generation or job creation alone does not prove successful training or improved performance.

<a id="training"></a>

## 6. How data flows and models are trained

**The complete lifecycle is: business execution produces data → governance and review → traceable assets → knowledge, workflow or model improvements chosen for the problem → validation and reuse in business.** Model training is one branch. SkillForge manages provenance, dataset versions, training jobs and deployment states; configured training runtimes perform parameter updates.

### From business inputs to the next execution

The diagram shows the main objects and execution stages represented in the code. Arrows describe transitions subject to configuration, permissions and review. They do not mean every run automatically completes every stage.

```mermaid
flowchart TD
    A[Authorized business data, documents and user inputs] --> B[Skill / Agent / project / custom Harness execution]
    B --> C[Inputs, tool calls, outputs, versions and business outcomes]
    C --> D[LearningEvent source events]
    U[Human corrections, acceptance or rejection and reasons] --> D
    D --> E[Deduplication, sensitivity handling, scope and quality review]
    E --> Q[Records requiring review or unsuitable for use]
    E --> F[LearningArtifact and provenance links]
    F --> K[Knowledge and Agent experience]
    F --> P[Prompt, Skill or workflow improvement candidates]
    P --> PR[Validation, review and release]
    PR --> B
    K --> B
    F --> S[TrainingSample and separate evaluation cases]
    S --> V[Pinned dataset version and checksum manifest]
    V --> T[Training approval and node synchronization]
    T --> N[Configured runtime executes LoRA / QLoRA or other tasks]
    N --> O[Model artifacts, checksums, logs and evaluation metrics]
    O --> G{Evaluation and business acceptance passed?}
    G -->|No| Q
    G -->|Yes| R[Deployment review and canary validation]
    R --> M[Activate the target model version]
    M --> B
    M -->|Regression and a usable previous version| RB[Roll back and record the reason]
    RB --> B
```

### Inputs, outputs and records at each stage

| Stage | How data changes | Records and checkpoints |
|---|---|---|
| 1. Ingestion and execution | Authorized inputs enter a skill, project or Harness, which invokes models and tools for the task | Run ID, sources, inputs, tool calls and outputs, plus applicable skill and model versions |
| 2. Experience capture | Execution, project inputs and outputs, tool calls and human handling outcomes become learning events | `LearningEvent` preserves source type, source ID, hash, department and run relationships; unconnected or unreported data is not inferred |
| 3. Governance and extraction | Handle sensitive fields, deduplicate by source and content, and extract knowledge, experience, samples or improvement candidates | `LearningArtifact` stores type, quality information, sensitivity and processing state; `LearningFlowEdge` connects sources to downstream objects |
| 4. Sample creation | Assemble suitable inputs, context and target outputs into training samples; failures, corrections and exceptions can become evaluation cases | `TrainingAssetSource` and `TrainingSample` retain sources, content hashes, labels and organizational scope; model-generated content still needs quality review |
| 5. Dataset versioning | Select samples and create a specific version with media references, sample lists and checksums | `TrainingDatasetVersion`, manifest and SHA-256; text training packages support train / eval distinctions, while acceptance must also check for leakage between related sources |
| 6. Approval and training | Specify the objective, base model, dataset version, parameters, evaluation gates and target node; dispatch after approval | `TrainingJob`, `TrainingJobTask`, node status and training logs; successful data synchronization is tracked separately from training completion |
| 7. Collection and evaluation | Collect artifacts, metrics and checksums, then check the configured evaluation conditions | Model or adapter artifacts, evaluation results and failure stage; compare against the original model and human business criteria as well |
| 8. Deployment and rollback | Request deployment, review, validate a canary and activate; withdraw regressions and restore a previous version when available | `TrainingModelDeployment`, target skills, runtime, rollout scope, review and rollback records |
| 9. Further learning | Later business runs use the selected version, and new outcomes and corrections re-enter the learning flow | Link deployed versions to actual task outcomes to support the next evidence-based improvement |

### What to train and how to judge it

Private small models can initially target **well-defined, verifiable and frequently repeated** business capabilities. The following are sample-design examples, not industry models already delivered by this repository:

| Target capability | What the samples should contain | Acceptance focus |
|---|---|---|
| Business classification and extraction | Redacted inputs, field definitions and confirmed classifications or structured outputs | Classification accuracy, field accuracy and handling of missing information |
| Business reports and decision explanations | Metric definitions, necessary context, evidence and expert-corrected conclusions | Factual and numerical consistency, evidence references, exceptions and uncertainty |
| Tool selection and step planning | Tasks, permitted tools, valid invocation steps, failures and corrections | Parameter correctness, access boundaries, task completion and recovery |

Strong models can help draft examples, organize cases or suggest labels. Business experts and evaluation processes verify them before dataset selection. Frequently changing facts should first be considered for retrieval or data-tool access; stable business conventions may also be addressed through prompt or workflow changes.

The repository includes training-gateway calls and some local LoRA training runners. **Current local evaluation includes generation checks on a small set of held-out samples. This is basic validation, not a comprehensive business benchmark or proof of generalization.** Before deployment, add independent test sets, comparisons with the original model, business review and cost measurement. Retain failed artifacts for diagnosis; the end of a training process does not establish an improvement.

### Where data lives and how a custom Harness connects

| Data | Storage or execution location | How it moves |
|---|---|---|
| Skill code, workflows and versioned prompts | Separate Skill Git repository or project source | Pin a reviewed version and release it to authorized execution environments |
| Run records, learning events, samples and dataset metadata | Platform database | Connect through source IDs, run IDs, hashes and provenance edges |
| Documents, media, data files and model artifacts | Configured storage or runtime nodes | Retain controlled references, manifests and checksums; synchronize to target nodes as required, with file handling determined by the integration |
| Parameter training and model inference | Configured training / inference runtimes | The platform dispatches tasks and collects status, metrics and artifact references |
| Credentials | Controlled configuration on the relevant server or node | Exclude them from training samples, prompts and shared logs |

A custom Harness can create runs, submit inputs, invoke authorized capabilities and return outputs through Project SDK/API interfaces. The interfaces also provide training-source synchronization, sample creation, dataset version creation and synchronization. Integrations should associate project, run, skill version, applicable model version, business outcomes and human corrections. Returning only a final answer leaves out evidence needed to diagnose failures and build reliable samples. Unreported local steps are not platform-observed data.

Automatic training, automatic training dispatch and automatic deployment approval are disabled by default in this edition. Learning-event capture and model-parameter training have separate controls. This lifecycle requires authorized data, sample rules, a trainable model, compute, evaluation criteria and an inference environment. Existing sensitive-field handling and organizational scope checks still need validation against the organization's data; they do not guarantee automatic redaction of every data type.

Code evidence: [learning events and relationships](app/learning/models.py), [extraction and routing](app/learning/service.py), [training assets and deployment models](app/training/models.py), [training and deployment services](app/training/service.py), [Bridge training runners](bridge/skillforgebridge.py) and [runtime model selection](app/execution/model_context.py). See the [validation record](docs/public/VALIDATION.md) for the limits of actual external training and production verification.

<a id="technology"></a>

## 7. Code and technology

```mermaid
flowchart TD
    U[Employees, business owners, developers and administrators] --> W[Web workspace and business applications]
    H[Custom Harness and developer tools] <-->|SDK / API / MCP| C[SkillForge control services]
    W --> C
    C --> G[Organization access, data contracts, review and audit]
    C <--> A[Skill Git, platform database and file assets]
    C <-->|Versions and configuration, status and results| N[Bridge and authorized runtime nodes]
    C <-->|Authorized calls| X[Enterprise data, models and business tools]
    N --> R[Skill execution, media tasks, training and inference runtimes]
```

Control services, business assets, execution environments and external systems have separate deployment or configuration responsibilities. The diagram describes those responsibilities, not plug-and-play support for arbitrary external services. Web interfaces, gateways and nodes must all respect authorization boundaries.

| Layer | Technology and source |
|---|---|
| Web workspace | Vue 3, Arco Design, Vite; `web/` |
| Workflow editor | React, React Flow; `playbook-editor/` |
| Platform services | FastAPI, SQLAlchemy, PostgreSQL, Redis; `app/` |
| Skill lifecycle and governance | GitPython, review and release; `app/skills/`, `app/reviews/` |
| Agent workflows and nodes | LangGraph, persistent checkpoints, Bridge; `app/agent_core/`, `app/execution/`, `bridge/` |
| Knowledge and learning | `app/knowledge/`, `app/learning/`, `app/training/` |
| Tool collaboration and application integration | `scripts/sf.py`, `sdk/`, `tools/`, `app/projects/` |

See [architecture and code evidence](docs/public/ARCHITECTURE.md) for the underlying models and boundaries. Supporting documents are currently primarily in Chinese.

<a id="quickstart"></a>

## 8. Run locally

Clone the public repository below for local deployment. If Docker is available, the [isolated preview deployment guide](deploy/preview/README.md) describes using separate storage and locally generated credentials.

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

The old coding-runtime launcher, proxy and activation UI have been removed. Replacement adapters are not integrated or accepted; coding sessions remain explicitly unavailable. Manual editing and ordinary model calls retain their own paths. See the [retirement and replacement assessment](docs/public/HARNESS_REPLACEMENT.en.md): OpenCode is the first candidate, with DeepSeek Harness for experimental integration.

### What to verify after startup

1. Log in, verify user and department scope, and establish your own Skill repository. An accessible web page establishes service startup only.
2. Configure one model and one read-only data capability. Use a [synthetic project example](docs/examples/projects/README.md) or a synthetic Skill to verify inputs, outputs and traces.
3. For node execution, verify registration identity, received version, run reports and stopping behavior. Complete one real read-only task and human handoff first.
4. Configure the models, nodes and storage needed for training or media production, then accept those capabilities separately. Verify backup/recovery, access and failure handling before production use.

The database backup script is not a complete disaster-recovery solution. The database, Skill Git, deployment configuration and file assets need consistent backups and isolated restoration checks. Protect credential backups separately with encryption; keep them out of public source and training data.

<a id="validation"></a>

## 9. Validation, documentation and release status

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

Targeted regression, type-checking, production builds, clean-database startup and isolated Docker server deployment have been verified within the documented scope. Full backend regression, external integrations and production acceptance remain incomplete. Dependency audit results are time-sensitive; see the [latest release review](docs/public/RELEASE_CHECK_20260920.en.md) for current results and the [validation record](docs/public/VALIDATION.md) for earlier checks.

Code review also identified limitations in prompt-default persistence, optimizer candidate isolation and simulated-result separation, skipped cross-model checks, and complete backup/recovery. These have not been fixed or accepted as part of this documentation work; see the [limitations and completion criteria](docs/public/CAPABILITIES.en.md). Existing screens or APIs do not establish that future automatic model selection, cost optimization or broader autonomous operation are already available.

- [Complete capability map, business process and code review](docs/public/CAPABILITIES.en.md)
- [Architecture and code evidence](docs/public/ARCHITECTURE.md)
- [Product case, responsibility and delivery evidence](docs/public/PORTFOLIO.en.md)
- [Documentation index](docs/README.md)
- [Release boundary](docs/public/RELEASE_BOUNDARY.md) and [third-party notices](THIRD_PARTY_NOTICES.md)

This repository uses an independent history and excludes enterprise runtime data, credentials and private deployment materials. It is published as a preview under Apache-2.0; ownership and the actual scope of authorized distribution remain subject to the [release-boundary review](docs/public/RELEASE_BOUNDARY.md).

## License

Unless otherwise noted, this project's code and documentation are licensed under **Apache License 2.0**. See [LICENSE](LICENSE) for the full terms.

- Commercial use, modification and redistribution are permitted, including use in proprietary products subject to the terms.
- Redistribution requires a license copy, retention of applicable notices and prominent notices of modified files. If the distribution includes a NOTICE file, its applicable attribution notices must also be retained as specified.
- The license includes a scoped contributor patent grant and patent-litigation termination provisions. It does not grant trademark rights.

Third-party components retain their own licenses, which are not replaced by this project's license. See [third-party notices](THIRD_PARTY_NOTICES.md). This is a reading summary; the [official Apache text](https://www.apache.org/licenses/LICENSE-2.0) and repository LICENSE provide the terms.
