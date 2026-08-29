# State

## Status
Foundation scaffold + Group 0 shell DONE 2026-08-29; HEAD `22bd58a` on `main`, working tree clean. Server running on :9010 (`--no-dispatcher`, log `/opt/data/hermes-hq-serve.log`).

## Now
Idle — awaiting go for Group 1.

## Next
**Group 1 read views**: `/api/projects`, `/api/tasks`, `/api/task/{id}` over `wm_store` readers (port the reader logic from `../hermes-work-manager/wm-tool/wm_dash/reader.py`, not the HTTP layer), Projects list + Tasks list pages using `StatusBadge`. Then the `wm.db` importer.

## Blocker
None.

## Known debt
- `tests/engine/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.
- Mobile top bar stacks into 3 rows (brand / sysbar / tabs); acceptable for now, revisit with Overview.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd web && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched. Owner drops reference images in `screenshots/` (git-ignored).

## Proof (Group 0, final)
- Screenshots at 1440px of `/system?theme=<id>` for violet, nous, nous-light, bronze, slate, hermes, and `?font=jetbrains-mono`: WM-style navbar, glass cards, orbs + grid, 5-state badges all correct; 390px mobile renders.
- Foundation proof (engine tests, `/api/health`, dispatcher `alive: true`) in commit `6620b75`; still valid.
