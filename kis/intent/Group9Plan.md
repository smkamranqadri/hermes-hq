# Group 9 — PWA polish (approved 2026-08-31, Standard mode) — COMPLETE 2026-08-31 (all four blocks shipped + proven live; suite 97 passed)

Owner picked all four scope blocks from the 2026-08-31 planning round (phone-feel bundle, app badge, offline fallback, phone debt items), then approved as-is (**A**). Skeletons were dropped from scope after inspection: 15 of 16 pages already use `Skeleton`/`Loading` from `ui.tsx`; only static `More.tsx` lacks one (confirm during 9-1 that it fetches nothing — if so, it needs none).

Facts that shape it: `sw.js` is push-only by design ("no offline caching — the app must never show stale state"); any offline handling must be navigation-only and static. Push payloads today carry `{title, body, tag, href, id}` (`backend/push.py`); the Badging API can only run while the app is open unless the push payload carries the unread count for `sw.js` to apply — and it is iOS ≥ 16.4 installed-PWA only (degrade silently elsewhere). Toast container is `fixed bottom-4` in `Toast.tsx`; the phone tab bar is `fixed bottom-[calc(env(safe-area-inset-bottom,0px)+0.5rem)]` (`TabBar.tsx`, `sm:hidden`). Unread source: `useNotifications` (`/api/notifications?limit=50` → `{notifications, unread}`, 15 s poll). Every page check: 390×844 `isMobile` + 1440, 16 px fields, no horizontal overflow (memory `hermes-hq-ops` has the Playwright settings).

## 9-1 Phone-feel bundle — DONE 2026-08-31
1. [x] `Toast.tsx`: on `<sm` raise the stack above the tab bar (`bottom-[calc(env(safe-area-inset-bottom,0px)+4.5rem)] sm:bottom-4` or a measured `--tabbar-h` var); desktop unchanged. Closes the "toasts over the tab bar" debt row.
2. [x] `index.css`: `overscroll-behavior-y: none` on `html`/`body` (kill rubber-band flash + accidental pull-to-refresh in standalone); verify inner scrollers (Chat, terminal, sheets) still scroll and chain correctly.
3. [x] Confirmed `More.tsx` is static (theme picker, links, sign-out — no data fetch), so no skeleton is needed.
Proof: Playwright on live :9010 — trigger a toast on a 390 px page with the tab bar visible → toast bottom edge above tab bar top edge (bounding boxes); desktop toast position unchanged; overscroll checked by hand on the phone (not automatable) + CSS asserted.

## 9-2 App badge (Inbox unread on the home-screen icon) — DONE 2026-08-31
1. [x] Frontend effect (App shell or `Bell.tsx`, guarded `('setAppBadge' in navigator)`): `setAppBadge(unread)` on every `useNotifications` result, `clearAppBadge()` at 0.
2. [x] `backend/push.py`: include current `unread` in the push payload; `sw.js` push handler applies `self.navigator.setAppBadge(unread)` when present — badge stays right while the app is closed.
3. [x] Backend test: payload carries `unread` (extend the existing push tests); suite stays green.
Proof: pytest for the payload; live — mark-all-read → badge clears (effect path asserted via CDP or a `navigator.setAppBadge` stub in Playwright); iOS behavior noted as owner-checked on the phone.

## 9-3 Offline fallback page — DONE 2026-08-31
1. [x] `frontend/public/offline.html`: static branded "you're offline — hermes-hq needs the server" page (inline CSS, JetBrains Mono stack, retry button = reload). No app data, no API calls.
2. [x] `sw.js`: version-keyed cache precaching only `/offline.html` at install (old caches cleaned on activate); `fetch` handler **only** for `request.mode === 'navigate'` — network-first, catch → cached offline page. Never touches `/api/*`, assets, or non-navigation requests; the no-stale-state rule holds.
Proof: Playwright/CDP offline emulation → navigation shows offline.html; back online → reload shows live data (nothing stale); `/api/*` requests bypass the sw (network entries confirm).

## 9-4 Phone debt items — DONE 2026-08-31
1. [x] `Activity.tsx` already had "Load older" (100 + cursor `next_before`) — shipped at cutover (`9fb2cac`); the State debt row was stale. Proven live instead of rebuilt.
2. [x] `TaskDetail.tsx`: collapsible sections on phones so the page isn't one long stack — headers with counts, primary block (status/actions) open by default, rest collapsed; desktop layout unchanged.
Proof: Playwright 390 px — Activity paginates (row count grows on tap); Task detail initial height meaningfully shorter, sections expand/collapse, fields 16 px, `scrollWidth` 390; 1440 unchanged (screenshots).

## 9-5 Sync — DONE 2026-08-31
1. [x] State: drop shipped debt rows (toasts, Activity load-more, task-detail-length), add proof one-liner; Knowledge: Badging API + offline-page notes under Web Push; this plan marked COMPLETE.
2. [x] Commit per step, push at the end (owner rule).

Out of scope: skeletons (already everywhere they're needed), any data/API caching, Files artifact grouping, "Needs you" prompts for asking runs, per-profile API keys, iOS splash screens.
Risks: `sw.js` is the only file with update-lifecycle risk (`skipWaiting` already on — clients pick the new sw next load; self-review the diff); Badging API absent outside installed PWAs / iOS < 16.4 (silent no-op by design); the offline fetch handler must never intercept non-navigation requests — scoped by `mode === 'navigate'` and covered by the proof.
