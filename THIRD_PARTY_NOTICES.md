# Third-party components

This preparation repository does not grant a new license over third-party code.
Dependencies retain their own licenses. Preserve their notices when distributing
a built application.

Primary components include FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis,
GitPython, LangGraph, Vue, Arco Design Vue, Vite, React, React Flow, ECharts,
Monaco Editor, Mermaid and noVNC. Exact installed versions are determined by the
lockfiles and dependency constraints. This list is an entry point for review,
not a complete license certification.

The separately licensed coding-assistant runtime formerly bundled with the
private project is **not distributed here**. Its launcher and dedicated proxy
have also been removed. Platform-side historical compatibility helpers and
provenance comments remain; no new coding runtime is integrated or enabled.
See `docs/public/HARNESS_REPLACEMENT.md` for the candidate assessment.
No candidate runtime was vendored or added as a dependency in this change.

Unless otherwise noted, the project's code and documentation use Apache-2.0;
see `LICENSE`. This does not relicense third-party components or replace their
copyright, attribution, source-distribution or other applicable obligations.
Ownership and publication-scope review remain separate from license selection;
see `docs/public/RELEASE_BOUNDARY.md` before public distribution.
