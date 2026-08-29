# Group 2 — Status & unblock (started 2026-08-29, owner said "go ahead")

Scope
- Overview `/`: stat strip (needs you / working / queued / done today), Needs-you list with inline primary action, Working list (agent, elapsed, session), Queued, compact activity feed. Poll 10s.
- Activity `/activity` (Tools menu): unified timeline over `activity` + `state_transitions`, filters project/agent, paged; `GET /api/activity`.
- Live run log: `GET /api/run/{id}/log?offset=` tails `runs/<id>.log`; Task detail shows the latest run's log with follow-tail polling.
- Reviews: Task detail already shows verdicts; Overview shows open-review count; Tasks list gets a "in review" quick filter (state=working + reason).

Out of scope: Agents page, Chat, cutover (weekend), notifications.

Acceptance
- Overview renders from real data with zero fabricated numbers; empty states honest.
- Activity API paged, filter by project/agent verified with tests.
- Run log endpoint returns incremental bytes; Task detail tail updates while a run is active (verified against the old WM's live run via the snapshot copy or a real run after cutover).
- Screenshots 1440 + 390 for Overview, Activity, Task detail log.
