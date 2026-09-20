# Pre-release review · 2026-09-20

This is a public source preview, not a production acceptance report. The source is published at [xydzf520/skillforge](https://github.com/xydzf520/skillforge). This report records publication checks and isolated server deployment verification. Publication does not establish production readiness.

## Changes

Both READMEs now position SkillForge in the existing open-source ecosystem using official project references. Remaining business-brand templates have been generalized, and the unpublished public Git history has been rebuilt from the cleaned tree; the earlier preparation history remains in the private audit directory.

Known Python, Web and editor dependency advisories were patched. npm 11.13.0 is pinned; compatible Monaco, Arco and vue-tsc versions are retained. Monaco uses a patched DOMPurify 3.4 release through a dependency override. Missing runtime imports, public changelog parsing and retired briefing routing were fixed. An excluded deployment-specific review adapter now fails explicitly without preventing unrelated MCP catalogs from loading.

The private AI portal integration has now been removed from the implementation: SSO tickets, the OIDC provider, signing-key generation, identity forwarding, deployment configuration and desktop/mobile navigation entries. Platform password and DingTalk login remain available. Unknown API requests cannot fall back to the frontend.

## Verified locally

| Check | Result |
|---|---|
| Targeted backend regression | 144 passed, 1 skipped; includes 58 node-scheduling tests, retirement, briefing isolation, changelog, configuration, execution and MCP checks |
| Identity removal regression | 77 passed, 1 skipped: retired routes return 404 with static assets and cookies; legacy environment settings cannot restore them; password/DingTalk login, session permissions, distribution and SPA regressions passed. This set overlaps the preceding suite and must not be added to it as a total |
| Web tests | 474 passed, 1 skipped across 64 files |
| Web and workflow editor | Type checks and production builds passed |
| Blocking Python diagnostics | Ruff E9, F63, F7 and F82 passed; not the complete style check |
| Dependency audit | No known advisories in the installed Web/editor npm trees or Python environment at the scan date |
| Clean installation | Isolated PostgreSQL 18.6 / Redis 8.0.5: migration, admin initialization, backend health and production page HTTP 200 passed |
| Isolated container deployment | Code revision `d1a5e96`: image built; PostgreSQL 15.19 / Redis 7.4.11 and application healthy; migrations reached `138_media_review_wall_v41`; 16/16 smoke checks passed for login, authorization, skill listing, workflow editor, static assets and changelog. SK login UI verified in a browser. Models and execution nodes remain unconfigured; backup restoration has not been rehearsed |
| Distribution | Source and fresh-history secret scans, distribution checks and known-private-marker checks; detailed logs remain private |

After identity removal, all eight retired endpoints returned 404 in the production application; the homepage and login-provider endpoint returned 200. Web type checking, build and 474 tests passed again. The initial default-concurrency run had five failures (four timeouts and one timer assertion); the documented `--maxWorkers=4` run passed without relaxing assertions or increasing timeouts.

## Remaining acceptance work

A broad backend run was stopped after identifying failures: **1,852 passed, 34 failed and 1 skipped at interruption**. Targeted fixes were tested afterward; these numbers are not a post-fix full-suite result. Remaining areas include older Agent coverage assertions, deployment-specific capability fixtures, Skill Git setup, inbox visibility/lineage expectations, training automation assumptions and outdated media manifest assertions. Some expected inbox records were missing; permissions were not relaxed to make tests pass. The existing full CI is retained and must not be represented as passing.

The subsequent isolated server deployment verified Docker image builds, PostgreSQL 15 / Redis 7, fresh database migrations and basic access, as recorded above. External identity services, real training and model deployment remain unverified. The replacement coding Harness is not integrated. Ownership and distribution scope are separate checks; see the [release boundary](RELEASE_BOUNDARY.md).

Archives are exported only from the cleaned Git commit and exclude credentials, business data, installed dependencies, local logs and prior Git history. This candidate targets a new deployment, not an in-place upgrade of a private enterprise deployment.
