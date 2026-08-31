# Polish + Ops Bundle (Standard) — planned 2026-08-31

Owner picked "all" of the remaining small rows (interview 2026-08-31): badge =
distinct "Handed over" chip; brand board = draft edits → owner review → apply →
KEEP PARKED.

## Slices

A. **Manual badge**: `classify()` returns optional `label` ("Handed over") for
   `manual`; the `human` envelope carries it, `StatusBadge` prefers
   `human.label` over `LABEL[state]`, `status.ts` mirrors, parity test pinned.
   Grouping/order/filters unchanged — manual stays in the done group.
B. **Per-profile gateway keys**: rotate `API_SERVER_KEY` for analyst / writer /
   coder / reviewer to fresh `secrets.token_urlsafe(32)` values (marked
   `# hermes-hq`), stop/restart their gateways, prove a probe/chat with a new
   key. Check the install template: strip any literal `API_SERVER_KEY` so
   `gateways._ensure_env` generates per-profile keys on fresh installs. Root/
   orchestrator key untouched.
C. **Agent history paging**: `GET /api/agent/{name}?history=N` (default 120,
   max 1000) → `agents.detail` slice + `readers.agent_sessions` limit follow N;
   `AgentDetail` "Show more" refetches with a larger N when it exhausts what is
   loaded.
D. **Brand board (ops, owner-gated)**: draft description/DoD edits for
   #82 (GitHub) / #85 (Medium) / #86 (LinkedIn) from
   `/opt/data/projects/personal-brand/positioning-messaging-strategy-option-a.md`
   Revision notes (same source #109 used); show the owner the drafts; apply
   ONLY approved text via `wm task edit`; tasks stay `manual` (owner releases
   later). #89 needs NO repoint — same task ids keep its deps valid.

## Acceptance

1. Suite green (116 + new/updated status + agents tests).
2. Playwright 390 `isMobile` + 1440: "Handed over" chip on a manual task,
   "Done" on a done task, Tasks tab groups unchanged, scrollWidth 390.
3. Keys: four distinct specialist keys, none equal to root; specialist gateway
   answers a probe/chat after rotation; fresh-install path generates keys.
4. `/api/agent/coder?history=200` returns >120 items; UI pages past 120.
5. #82/#85/#86 edited with `task_edited` audit rows, still `manual`; `wm check`
   green; #89 untouched.

## Status

PLANNED — awaiting `/kis:act`.
