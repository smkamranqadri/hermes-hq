# State

## Status
Foundation scaffold + Group 0 shell DONE 2026-08-29 (on `main`). Server running on :9010 (`--no-dispatcher`, log `/opt/data/hermes-hq-serve.log`).

## Now
Idle — awaiting go for Group 1.

## Next
**Group 1 read views**: `/api/projects`, `/api/tasks`, `/api/task/{id}` over `wm_store` readers (port the reader logic from `../hermes-work-manager/wm-tool/wm_dash/reader.py`, not the HTTP layer), Projects list + Tasks list pages using `StatusBadge`. Then the `wm.db` importer.

## Blocker
None.

## Known debt
- `tests/engine/test_t2/t5/t7.py` fail identically in the source repo (goal lifecycle draft→planned changed after they were written). Not caused by the move; fix when touching goal release.
- Dispatcher loop runs every tick against the real engine; `--no-dispatcher` exists for dev.

## How to run
See `README.md`. Dev: `.venv/bin/hermes-hq serve --no-dispatcher` + `cd web && npm run dev` (proxies /api to :9010). Legacy WM dashboard still live on :9009 and untouched.

## Proof (latest: Group 0 + navbar/font feedback)
- Screenshots 1440px violet `/tasks` (old-WM navbar anatomy), nous `/system?font=fraunces` (serif applied everywhere), 390px mobile (3-row stack). Appearance menu reachable via ◐ pill.
- Screenshots at 1280px of `/system?theme=<id>` for violet, violet-light, nous, bronze, slate, hermes: glass top bar with border, orbs + dot grid, glass cards, Inter/JetBrains Mono, 5-state badges; mobile 390px shows brand row + scrollable tab row.
- Bug found+fixed during proof: 250ms theme transition made light theme screenshot dark → transition removed.
- Foundation proof (engine tests, /api/health, dispatcher alive) recorded in commit 6620b75 message history; still valid.
