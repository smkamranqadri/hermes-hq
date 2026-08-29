# State

## Status
Foundation scaffold DONE 2026-08-29 (commit on `main`). Engine, service, and UI shell exist; no data views yet.

## Now
Idle — next task not started.

## Next
**Group 1 read views**: `/api/projects`, `/api/tasks`, `/api/task/{id}` over `wm_store` readers (port the reader logic from `../hermes-work-manager/wm-tool/wm_dash/reader.py`, not the HTTP layer), Projects list + Tasks list pages using `StatusBadge`. Then the `wm.db` importer.

## Blocker
None.

## Known debt
- `tests/engine/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.
- Dispatcher loop runs every tick against the real engine; `--no-dispatcher` exists for dev.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd web && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched.

## Proof (foundation scaffold)
- Engine tests from new location: t1, t3, t4, t6, run_wrapper_orchestrator, run_wrapper_provider_error PASS; t2/t5/t7 pre-existing failures (verified same in source).
- `hermes-hq serve --port 9010 --no-dispatcher`: `/api/health` → `{"ok":true,"version":"0.1.0"}`, `/api/system` → real paths, `/` and `/tasks` → 200 SPA.
- `hermes-hq serve --port 9011 --interval 2`: `/api/system.dispatcher` → `alive: true, last_tick set, last_error: null`.
- Screenshots (1280 and 390 wide): 7-tab top bar, active pill, LIVE/PAUSED dot, SYSTEM link, 5-state badges rendered.
- `hermes-hq wm status` passthrough works.
