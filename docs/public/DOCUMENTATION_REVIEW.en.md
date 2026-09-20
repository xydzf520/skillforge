# Documentation review for the public preparation edition

[中文（默认）](DOCUMENTATION_REVIEW.md) · Reviewed: 2026-09-20

The documentation mixed product goals, historical implementation records and current acceptance. Some operational examples assumed the original deployment. This revision aligns entry points and instructions with code; it does not implement missing features or certify production readiness.

The inventory started with 97 tracked Markdown files, including product descriptions, designs, guides, examples and executable prompt files. Focused code checks covered the README, creation wizard, authoring runtime, Project SDK, training sink and Bridge.

| Finding | Correction |
|---|---|
| Availability limits appeared too late | Both READMEs now distinguish ordinary model paths, retired coding-runtime paths and externally configured nodes |
| Historical “implemented/released” claims looked current | Added scope notices to 27 design documents; preserved normative permission and review requirements |
| Creation guide promised star ratings and fixed durations | Replaced unsupported claims with prerequisites and path-specific limits; fixed three broken links and documented the template redirect |
| SDK assumed specific machines, hardware and models | Uses catalog model IDs; explains historical route aliases and separately describes trace, samples, sink jobs and actual training |
| Bridge instructions contradicted defaults and state paths | Explicit foreground mode, no-argument installation behavior, hashed state directory, consistent container user/HOME/volume and credential-free source template |
| Operational examples implied real business evidence | Replaced unprovided measurement claims with reproducible synthetic examples; thresholds still require business validation |
| Knowledge/skills were conflated with model training | Separated three improvement paths and distinguished queueing, delivery, parameter updates and deployment |
| Navigation and changelog lagged behind | Organized by reader intent and documented Harness retirement and this review |

Code references: [creation wizard](../../web/src/pages/skill/SkillCreationWizard.vue), [routes](../../web/src/router/index.ts), [Workbench API](../../app/workbench/router.py), [runtime boundary](../../app/coding_agent/runtime_availability.py), [Project service](../../app/projects/service.py), [Bridge](../../bridge/skillforgebridge.py).

Remaining implementation work is explicit:

- No replacement coding Harness is integrated or accepted.
- Project routing and training-sink defaults still contain historical coupling. An enabled sink is distinct from automatic model training; disabling training does not disable all data records. This revision documents the behavior without changing it.
- Some executable prompts contain preferences inherited from a previous user, including an unlimited-token assumption. They need versioned behavior changes and regression testing, not editorial rewriting. They were not changed in this documentation pass.
- Ownership, external integration and business outcomes require independent evidence. A dependency's MIT license does not license the whole project.

Validation covers local links, current operational example syntax, synthetic arithmetic, code references and publication scans. Docker examples have not been built or deployed in this review. Historical design pseudocode is not presented as runnable setup guidance. See the matching entry in [VALIDATION.md](VALIDATION.md).

No API, business default, node service, model configuration or enterprise data was changed. The private source repository remains intact.
