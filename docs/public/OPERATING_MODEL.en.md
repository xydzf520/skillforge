# How an enterprise operates with SkillForge

[中文](OPERATING_MODEL.md) · **English** · [Back to README](../../README.en.md)

These diagrams explain ownership, business handoffs, one runtime integration and how experience informs improvements. Organization and delivery diagrams describe a suggested operating model; the runtime diagram uses existing interfaces; the improvement diagram is a decision method. None represents full production acceptance. See the [capability map](CAPABILITIES.en.md) and [validation record](VALIDATION.md).

Blue identifies platform components or assets, purple identifies human responsibilities, and orange identifies external capabilities requiring configuration or adaptation. Colors classify responsibilities, not implementation status. Each diagram includes a text alternative and an SVG version.

<a id="organization"></a>

## 1. Organization: business owns outcomes, the platform retains methods

This is a responsibility diagram, not an actual company's reporting hierarchy or an automatic mapping to application roles and permissions. One person may hold several responsibilities; review, authorization and outcome ownership must still be explicit.

```mermaid
flowchart TD
    Owner["Business owner: goals and acceptance"] -->|Rules and example cases| Build["Product, development and AI team"]
    Build -->|Testable version and change description| Review["Reviewer: quality and release scope"]
    Review -->|Reviewed version| Asset["Shared skills, workflows, prompts and knowledge"]
    Admin["Administrator: identity, access and integrations"] -->|Configure scope and runtime environment| Asset
    Asset -->|Authorized capabilities and entry points| User["Employees: run tasks and handle actions"]
    User -->|Outcomes, corrections and failures| Evidence["Execution records and business evidence"]
    Evidence -->|Verify quality, cost and outcomes| Owner
    Evidence -->|Locate problems and propose changes| Build
    classDef human fill:#f5f0ff,stroke:#7654ad,color:#302541
    classDef platform fill:#edf4ff,stroke:#4774b8,color:#183153
    class Owner,Build,Review,Admin,User human
    class Asset,Evidence platform
```

[Open standalone SVG](diagrams/organization.en.svg)

**Text path:** the business owner defines standards → the delivery team implements → a reviewer approves the version → employees use authorized capabilities → feedback and verified outcomes return to the owner and builders. Administrators configure access and infrastructure; business owners assess value.

| Handoff | Retained information | Acceptance |
|---|---|---|
| Business → builders | Goals, definitions, sources, exceptions and confirmation boundaries | Both sides can judge the same examples |
| Builders → reviewers | Fixed version, tests, permissions and change description | Review passes and release scope is explicit |
| Platform → employees | Visible capabilities, versions, input instructions and exception handling | Verified execution in an authorized environment |
| Employees → business owner | Artifacts, corrections, actions, outcomes or reasons for uncertainty | Owner verifies business completion |

<a id="delivery"></a>

## 2. Delivery: generated output is distinct from completed business work

This synthetic example uses operational anomaly analysis. Connections represent handoffs. Data, the specific Skill, notifications and business actions require configuration; this is not a bundled, ready-to-run business integration.

```mermaid
flowchart TD
    Goal["Owner defines metrics and acceptance cases"] --> Build["Team implements a Skill or project"]
    Build --> Test["Save version; test normal, missing and failed cases"]
    Test --> Review{"Version review passes?"}
    Review -->|No: record reasons and revise| Build
    Review -->|Yes: define authorized scope| Release["Release reviewed version; verify node configuration"]
    Release --> Run["Employee runs task or configured node schedules it"]
    Run --> Result{"Execution outcome"}
    Result -->|Failed or uncertain| Exception["Retain evidence; owner handles exception"]
    Result -->|Reviewable artifact produced| Report["Report and actions: conclusions, evidence, advice"]
    Report --> Decision{"Owner confirms business action?"}
    Decision -->|Reject or revise| Feedback["Record corrections, reasons and improvement candidates"]
    Decision -->|Confirmed and authorized| Action["Perform business action; verify receipt"]
    Action --> Outcome["Record outcome; keep unknowns explicit"]
    Exception --> Feedback
    Outcome --> Feedback
    Feedback -->|Next version needs fresh validation| Build
    classDef human fill:#f5f0ff,stroke:#7654ad,color:#302541
    classDef platform fill:#edf4ff,stroke:#4774b8,color:#183153
    class Goal,Review,Decision,Exception,Action,Outcome human
    class Build,Test,Release,Run,Result,Report,Feedback platform
```

[Open standalone SVG](diagrams/delivery.en.svg)

**Text path:** goal → implementation and tests → review and release → execution → human review → actual action → verified outcome. Failure, rejection and uncertainty have separate exits feeding subsequent improvement.

Track three distinct states: **asset release, task execution and verified business outcome**. The labels are not database status enums, and they do not imply that every notification channel supplies read receipts. Without evidence, business completion must not be inferred.

Implementation references: [Skill lifecycle](../../app/skills/lifecycle/), [execution records](../../app/execution/models.py), [project service](../../app/projects/service.py), [business handoffs](CAPABILITIES.en.md).

<a id="runtime"></a>

## 3. Runtime: integrating a custom Harness

This sequence describes a recommended first **read-only Project SDK task**, not every Skill's execution path. SDK/API foundations exist; the integrator implements Harness context management, adaptation and recovery. Models and tools require configuration and authorization.

```mermaid
sequenceDiagram
    actor User as Employee or app
    participant Harness as Custom Harness
    participant Gateway as Project Gateway
    participant Service as Model or tool
    participant Record as Run records
    User->>Harness: Submit task and inputs
    Harness->>Gateway: Create run with scoped identity
    Gateway-->>Harness: Return run identifier
    Harness->>Gateway: Record input and request capability
    Gateway->>Gateway: Check identity, scope and quota
    alt Validation fails
        Gateway-->>Harness: Reject without invoking capability
        Harness-->>User: Explain failure and next action
    else Validation passes
        Gateway->>Service: Invoke configured capability
        alt Result received
            Service-->>Gateway: Result and available usage
            Gateway->>Record: Record call, provenance and outcome
            Gateway-->>Harness: Return result and call identifier
            Harness->>Gateway: Ingest artifact and verified outcome
            Gateway->>Record: Store output and configured learning data
            Harness-->>User: Result, evidence and unknowns
        else Failure or timeout
            Gateway-->>Harness: Return failure or timeout status
            Harness-->>User: Show exception without claiming success
        end
    end
```

[Open standalone SVG](diagrams/runtime.en.svg)

**Text path:** business input → Harness → project authorization and capability checks → model or tool → execution records → artifact ingestion. Local Harness steps that are not reported are not platform-observed data. Credentials do not belong in shared logs or prompts.

- Writes, notifications and other external actions follow their specific tool authorization and confirmation rules. This read-only example grants no write authority; verify uncertain outcomes before deciding whether to retry.
- Node scheduling is a separate path: platform reviews versions and distributes configuration → compatible Bridge node executes → status and results return. Nodes are the primary scheduler; the platform retains fallback compensation.
- The retired Workbench coding runtime is not part of this sequence. OpenCode / DeepSeek Harness remain candidates, not integrated replacements.
- Project sample sinks and automatic training use separate settings, and a legacy sink fallback remains. Review [SDK data handling](../guides/project-gateway-sdk.md) before supplying business data.

Code references: [SDK](../../sdk/), [Project API](../../app/projects/router.py), [Bridge](../../bridge/README.md), [Harness replacement status](HARNESS_REPLACEMENT.en.md).

<a id="improvement"></a>

## 4. Improvement: identify which layer needs to change

This is a human or team review method, **not an implemented automatic diagnosis or model router**. A problem can require several branches. Correct sources, permissions and evaluation standards before deciding to train.

```mermaid
flowchart TD
    Evidence["Execution evidence, corrections and business outcomes"] --> Problem{"Which layer needs improvement?"}
    Problem -->|Missing data or stale facts| Knowledge["Fix sources, retrieval and knowledge"]
    Problem -->|Unsuitable rules, steps or tools| Skill["Revise prompts, skills or workflows"]
    Problem -->|Persistent capability gap and usable samples| Ready{"Data rights, independent evaluation and compute ready?"}
    Ready -->|No| Prepare["Prepare prerequisites; keep a working approach"]
    Ready -->|Yes| Train["Approve training of a private small model or adapter"]
    Knowledge --> Evaluate["Fix versions; compare quality, human review and total cost"]
    Skill --> Evaluate
    Train --> Evaluate
    Evaluate --> Accept{"Meets business criteria?"}
    Accept -->|No| Hold["Reject promotion; retain issues and evidence"]
    Accept -->|Yes| Deploy["Review and release according to change type"]
    Deploy --> Observe["Observe real outcomes; withdraw or roll back regressions"]
    Observe --> Evidence
    classDef platform fill:#edf4ff,stroke:#4774b8,color:#183153
    classDef human fill:#f5f0ff,stroke:#7654ad,color:#302541
    classDef external fill:#fff4e5,stroke:#a46413,color:#54370f
    class Evidence,Knowledge,Skill,Evaluate,Observe platform
    class Problem,Ready,Prepare,Accept,Hold,Deploy human
    class Train external
```

[Open standalone SVG](diagrams/improvement.en.svg)

**Text path:** verify the problem → choose knowledge, workflow or model improvement → evaluate against a baseline → review → observe outcomes. Knowledge and workflow changes do not update model parameters; training changes weights or adapters.

Strong models may help with planning, candidates and failure analysis. Private small models can serve evaluated, stable tasks. Switching requires evidence about quality, latency, maintenance and total cost, not model size or price alone. Keep related-source data out of independent evaluation splits; unreviewed model output is not a ground-truth label.

The complete data-object, storage, training-approval, evaluation, deployment and rollback diagram remains in the [README training flow](../../README.en.md#training), avoiding a second independently maintained data flow. See the [capability map](CAPABILITIES.en.md) for current training and optimizer limitations.

## Maintaining these diagrams

The Mermaid blocks on this page are the editable sources; `diagrams/*.svg` are static copies for readers without Mermaid support. Regenerate both languages when editing and validate syntax, readable text and local links. Do not add real company personnel, addresses, business data, credentials or unverified outcome claims.
