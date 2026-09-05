# DocsPlan — User docs served at /help + docs-maintenance gate

Status: APPROVED 2026-09-05 (owner interview via /kis:plan). **PARKED 2026-09-05** — owner is mid-build on the second-brain project and will pick this up later; do not start without an owner go.
Mode: Phase (3 phases, each verified before the next).

## Decisions (owner interview 2026-09-05)
- Audience: **future users** — product-style docs for anyone installing hermes-hq, not an owner-only operator guide.
- Tooling: **MkDocs Material** (open source, Python — fits the uv/venv stack), pinned in an optional `docs` dependency group.
- Serving: the app serves the built site at **`/help`**. Swagger stays at `/docs` (FastAPI default already occupies it — checked `backend/app.py:45`).
- Content: **full guide**, ~9 pages (list below), reusing README + `docs/workflow.md`.
- Process: docs maintenance wired into **both** `kis/knowledge/rules.md` and the Definition of Done in `docs/workflow.md` (lesson from #138: only description/DoD binds reviewers).
- Execution: this session (not the pipeline), Phase Mode.

## Out of scope
Screenshots (text-first v1; add later only with a regeneration script), docs versioning (mike), multi-language, moving Swagger off `/docs`, any dispatcher/pipeline changes.

## Phase 1 — Tooling + serving
- `mkdocs.yml` at repo root; `docs_dir: docs` so `docs/workflow.md` becomes a page, not a duplicate. `site/` output gitignored.
- `pyproject.toml`: optional `docs` group with pinned `mkdocs-material`.
- Skeleton nav with stub pages for the Phase 2 list.
- `backend/app.py`: mount built site at `/help` (only if the dir exists — dev without a docs build must not break).
- `install.sh` + `service update`: build docs **tolerantly** — a docs build failure warns and continues, never aborts install/update. Add docs/ to update's rebuild-what-changed detection.
- Verify: `mkdocs build --strict` clean; `GET /help` → 200 on the live service (restart needed — wipes owner login sessions); `GET /docs` Swagger unchanged; re-run `install.sh` succeeds.

## Phase 2 — Content (~9 pages)
Getting started / install · Core concepts (projects, goals, tasks, agents) · Status model & "Needs you" · Phase-gate workflow (adapted from `docs/workflow.md`) · Screen tour (board, task detail, chat, files, terminal, memory, skills, MCP, schedules — flows and actions, not pixel layouts) · Approvals & gated tasks · Operating the service (update, auto-update, logs) · Troubleshooting / FAQ.
- Every claim verified against the running app (per memory: verify from outside the agent shell, stale reads happen).
- Verify: `mkdocs build --strict` clean; browse `/help` incl. a 390×844 mobile spot-check (Playwright).

## Phase 3 — Process wiring
- `kis/knowledge/rules.md`: rule — any user-visible change updates the affected `/help` page(s).
- `docs/workflow.md` Definition of Done: same gate, so reviewer briefs enforce it.
- `README.md`: link to `/help`.
- Optional: top-bar Help link in the frontend (if done, the 390×844 mobile-check rule applies).
- Verify: rules + DoD lines present; README link renders; frontend change (if any) mobile-checked.

## Acceptance checks (whole plan)
- `mkdocs build --strict` clean.
- `/help` serves the full guide on the live service; mobile spot-check passes.
- `/docs` Swagger unchanged.
- `install.sh` re-run and `service update` succeed; a forced docs-build failure only warns.
- rules.md + workflow DoD contain the docs-update gate.

## Risks / assumptions
- New pinned Python dep (`mkdocs-material`) in an optional group.
- Docs start accurate against the current UI; staying accurate relies on the Phase 3 gate.
- Backend restart for the mount wipes in-memory login sessions (owner re-login).

## KIS writes on completion
- Knowledge: docs rule in `rules.md`; docs toolchain note (`/help` mount, mkdocs, tolerant build) in `technical.md`.
- Intent: this plan marked COMPLETE.
- State: per-phase proof lines; Next updated.
