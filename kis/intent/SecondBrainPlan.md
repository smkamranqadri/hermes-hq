# Second Brain Plan

**Status:** PLANNED — approved by owner 2026-09-04 (interview + mockup rounds in session; design locked at mockup v3.1).
**Mockup (UI reference):** https://claude.ai/code/artifact/28097e1a-2343-442f-a21e-ed014e507eb3
**Work mode:** Phase — four phases, each ships something the owner uses that week. Built through the pipeline (dispatched coder runs + independent review), owner clicks exercised live per phase.

## Goal

A second brain inside hermes-hq: capture anything (text/photo/voice, incl. batch brain-dumps), a librarian agent that *proposes* filing/splitting/wiki changes (never writes directly), owner-approved into a Library organized by areas and projects, with notes graduating into linked tasks and reminders. Replaces Apple Notes / Keep / Obsidian / kamran-focus for daily use.

## Decided design (owner-interviewed; do not relitigate in build tasks)

- **DB-native.** `notes`, `areas`, `note_entries`, `proposals` (+ revisions) in `hq.db`. No notes git repo. Portability via **Export** (markdown zip, PARA-style folders, opens in Obsidian) from the Library screen. Private notes excluded from export by default.
- **Types:** `note` (owner-written) · `playbook` (starts as owner note; librarian proposes a structured version, linked to the original) · `wiki` (librarian-compiled from notes, citing them; **on-demand only** in v1). Notes carry a body plus dated **entries** (append log — the 1:1-per-person pattern; owner-facing name **Thoughts**, renamed 2026-09-05). Capture puts the FULL text in the body; the first line only names the note. `authored_by` on everything (owner / librarian / import).
- **Librarian = proposals only, enforced in code.** Agent sessions cannot write notes via the API; the librarian gets `propose-*` endpoints. Proposal kinds: split (one capture → N items), file, wiki-update (diff preview), contradiction (keep-both + `disputed` flag; never silently reconcile), new-task. Each classified `routine` vs `needs_attention`; routine bulk-approves. Approving files the note (leaves inbox → Library/Recent). Human-feedback field on proposals; librarian reads it on revision.
- **Librarian lane:** own agent profile, scheduled ingest + lint runs through the existing dispatcher/schedules with **heartbeat early-exit** (no model call when nothing new). Lint is deterministic code (orphans, broken links, missing metadata, staleness, tag audit, oversized, low-confidence, query-verification against FTS); librarian only fixes via proposals. Brief is injection-conscious: note content is data, never instructions. Agent memory ≠ second brain: nothing auto-ingests from Memory; agent-surfaced candidates become proposals.
- **Organization:** two-level areas seeded from kamran-focus domains (Work, Family, Finance, Health, Home, Career, Study, Content, AI Workflow, Side Project, …). Projects also link to an area — **each mapping proposed individually for owner decision**. Notes link to an area or an HQ project; project-linked notes render on that project's page. Closed tag taxonomy (add to taxonomy first, then use — validated in code). Origin (kamran-focus / keep / apple-notes / obsidian / capture) is **just a tag**; sha256 of source kept internally for dedupe/drift only.
- **Graduation = create-and-link, never convert.** "New task" / "New reminder" create a real HQ task / schedule linked both ways; the note stays a note. **Reminder** is the owner-facing name for schedules created this way (mints an owner task, push on due). Reminders can be **one-time** (2026-09-05): `schedules.one_shot` — fires once, then retires itself (enabled=0, next_fire_at NULL); skipped/error firings keep it armed.
- **Deferred to Phase 2 (owner-accepted 2026-09-05):** combined Library filters — project/tag filter inside an area selection (tag filter needs a list_notes tag param; ships with the review-queue Library polish).
- **Owner tasks:** reserved `owner` assignee; dispatcher claim/candidate predicates skip it; Tasks board gets a "mine" filter + dashboard count.
- **Private vault:** encrypted at rest with a server-held key; agent sessions denied by authorization. Accepted limit: root can decrypt (Knowledge → Known limits). Vault notes: no FTS row, excluded from export by default.
- **Capture:** multi-line editor (batch dumps are the norm — librarian splits), photo attach, voice memo stored untranscribed + flagged for owner (transcription out of scope). Mobile: 16px inputs, 44px+ targets, skeletons.
- **Navigation:** Second Brain = primary tab (Overview / Projects / Tasks / Second Brain / Chat); Agents moves to TOOLS. Phone tab bar: Brain replaces Inbox (unread badge moves to More). In-page sub-nav Home · Library · Review sits in the heading row. Library = folder tree (Areas→sub-areas→notes, Projects, Playbooks, Wiki, Journal, Vault, Archive) + global search (FTS5, unicode61 — Urdu accepted) + Export.
- **Import (5 dumps at `/opt/data/notes-dump/`):** museum (~85%) auto-archived searchable; provably-empty dropped; secrets quarantined to vault-staging (never plain); living ~150 reviewed one-by-one through the Review Queue with librarian proposals. Importer handles: Apple `(Attachments)` dir convention, HTML-entity unescape, Keep 4-blank-line split + BOM, hostile filenames, kamran-focus frontmatter (incl. duplicate-UUID titles), obsidian Archive clone dedupe. Reversible: every import tagged, hash-tracked.

## Phases

### Phase 1 — Foundation (no librarian) — SHIPPED 2026-09-05 ✅
Live since 2026-09-05 (build `97ff496`+`eaf635e`, merged + deployed). Owner clicked through on desktop + phone; **three same-day review cycles**, each fixed → verified (scratch Playwright) → deployed:
1. `54327b0` — entries renamed **Thoughts**; capture keeps the FULL text as body (first line only names the note); TaskDetail "✎ from note" back-link.
2. `e2e796e` — **one-time reminders** (`schedules.one_shot`, retires after firing; modal defaults to `once` with date+time); Library Inbox row shows the inbox LIST; "Refile…" + clickable chips edit filing any time.
3. `27446b3` — reminder modal layout: stacked fields, wrapped date/time, hq-select chevron on modal selects; 390px overflow measured zero.
Suite ended at **145 passed 0 failed**. Deferred with owner sign-off: combined Library filters (project/tag inside an area) → Phase 2b.
Schema + migrations (`notes`, `areas`, `note_entries`, `note_revisions`, FTS5 index; task/schedule link columns), `backend/notes.py` API (CRUD, entries append, search, tree counts; **agent-session write refusal** from day one), owner-assignee groundwork (reserved `owner` assignee + dispatcher skip + "mine" filter), frontend: navbar change, Second Brain Home (capture editor: text first; inbox list; recent), Library (tree + search + note preview), Note Detail (body, entries, tags, manual file/edit/archive, New task / New reminder create-and-link), project-page Notes section. Manual filing only.
**DoD:** suite green incl. new `test_notes.py` (agent write refusal, owner-task skip predicate, entries, FTS); Playwright 390px + desktop proofs; owner captures, files, searches, and creates a linked task + reminder live.

### Phase 2 — Librarian + Review Queue (re-sliced 2026-09-05 after P1 learnings: ship in small owner-reviewable slices, split proposals are the highest-value piece — batch dumps are the owner's real capture pattern)
**2a — Librarian core + split/file proposals. BUILT + DEPLOYED 2026-09-05 (`a48f246`) — one owner step left: provision model auth for the `librarian` Hermes profile, then Resume schedule #2 and Retry task #190.** Shipped: `proposals` table + store round trip, `wm note` CLI group (the librarian's only write surface; notes stay owner-session-only), owner HTTP review endpoints, `librarian` profile (installed live, port 8656) + `librarian-specialist` skill, ingest schedule #2 with `heartbeat=librarian_ingest` (paused until auth exists), `/brain/review` queue + sub-nav badge + phone triage at 390px. Tag DISCIPLINE via skill + `wm note tags`; the enforced closed taxonomy moved to 2b.
**DoD 2a:** batch dump → split proposal → approve round trip live on the owner's phone ✅ (proven at 390×844 `isMobile` on live :9010; the librarian's own model call is the one unproven link — fresh profiles get no inference provider and credentials are owner-only); librarian direct note-write refused (test) ✅; ingest run with nothing new spends no model call ✅ (live scheduled tick recorded `skipped — heartbeat: nothing new`, no task minted).
**2b — Review polish + Library filters.** wiki-update (diff preview) + contradiction (`disputed`, keep-both) + new-task proposal kinds, per-item edit/defer + human-feedback field the librarian reads on revision, lint lane (deterministic checks → notification), and the deferred Library filters: project/tag within an area (list_notes tag param).
**DoD 2b:** a planted contradiction pair gets `disputed` and renders; lint report lands as a notification; area+tag filter returns the right subset.
**2c — Photo + voice capture.** Photo attach (upload infra exists) + voice memo stored untranscribed + flagged for the owner (Urdu voice is an intended input; transcription stays out of scope).
**DoD 2c:** photo and voice captured from the phone PWA land on notes and render; voice flagged for owner processing.

### Phase 3 — Import
Importer job (per-source parsers, dedupe by hash + title heuristics, museum auto-archive, empties dropped, secrets quarantine), triage sessions through the Review Queue for the living set, coverage stat on Home.
**DoD:** all 5 sources imported reversibly; counts reconcile against the dump analysis (~967 units, 69 dup groups); zero plaintext secrets outside quarantine; owner completes first triage session.

### Phase 4 — Vault, wiki, export, polish
Private vault (encryption at rest, authz denial, vault UI + move-to-vault, quarantined secrets land here), on-demand wiki compile (librarian task per request, citations to source notes), Export zip, reminders polish (push on due), backlinks pane, revision history UI.
**DoD:** vault note unreadable via agent session (test) and invisible in export; wiki article compiled from ≥3 notes with citations; export zip opens as a sane Obsidian vault.

## Out of scope (v1)
Voice transcription; scheduled wiki auto-refresh; combined Agents+Chat view (future candidate); E2E passphrase encryption; embeddings/semantic search; importing the nine extra repos listed in the old-hermes export; area assignment for existing projects in bulk (each is owner-decided, trickled).

## Risks
Scope (mitigated by phases + usage between them); root-can-decrypt limit (documented, accepted); librarian run cost (early-exit, batch cadence); Urdu FTS recall (trigram fallback later if needed); iOS MediaRecorder quirks (test early at 390px); importer edge cases (raw dumps kept immutable, import reversible).
