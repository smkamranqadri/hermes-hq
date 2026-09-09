# Library Reconcile — make the system before importing the rest

**Status:** slice 1 SHIPPED 2026-09-09 (`d9fbc71`); **slice 2 (items 5–8) SHIPPED 2026-09-09**; **item 9 SHIPPED 2026-09-09**. The plan is COMPLETE. Owner-interviewed in session after a full audit of all 295 notes. Items 1–4 are the first slice.
**Parent:** `intent/ImportPlan.md` (waves 4–5 are HELD until this lands — owner: *"before adding more notes, I want to reconcile these first and make proper system, then new migration can go better instead of dumping"*).
**Work mode:** Standard for items 1–4 (no schema change), then re-plan 5–8.

## The audit (all 295 notes, measured 2026-09-09)

295 notes — 176 active, 119 archived; 244 imported, 51 owner-authored.

1. **Markdown renders on the note page but NOT in list rows.** `react-markdown` + `remark-gfm` already handle the note page well. `components/brain.tsx:209` prints `n.body` raw inside a 2-line clamp, and **247 of 295 notes contain markdown** (163 headings, 158 bullet lists, 77 checkboxes, 43 code fences). So every Library row reads as ```` ```bash sudo apt update ``` ```` instead of a sentence. **This is what "the view does not support markdown" actually is** — a list-row problem, not a renderer problem.
2. **The obsidian folder tree WAS the organization and the import did not carry it into tags.** Lost groupings: `Hermes Agent OS/` 36, `BrightLane/Internal/` 20, `BrightLane/Feedback/` 11 (the 1:1 notes, one per person), `Tutorials/NestJs/` 10 (a numbered 9-part course), `Tutorials/Other/Server Setup/` 6, `Testlink/` 5, `Caprover/` 4, `Docker/` 3. The NestJs course survives only because the numbers are in the filenames.
3. **Provenance is NOT lost, though** — `notes.content_hash` IS `Unit.sha256` and `backend/notes_import.py:668 source_of_content_hash()` already maps a note back to its dump file. **Verified: 244 of 244 imported notes match.** No schema column is needed to recover the folder path. (An earlier draft of this plan proposed a `source_path` column; the owner rejected it — "we already have playbook and wiki collection, don't think we need to add one more" — and he was right, the capability already exists.)
4. **58% of notes carry no tags** (173 of 295; 78 of them active), so tags cannot carry navigation yet.
5. **The 25 tags in use are already drifting:** `BrightLane` 43 vs `brightlane` 3, a `ser` typo, and the `zzz-test` 5 / `test` 2 / `zzz-deep` 2 fixtures still registered in the closed taxonomy.
6. **`type` is 100% unused** — all 295 notes are `type: note`; `playbook` and `wiki` exist and nothing uses them.
7. **Relationships barely exist:** `note_links` holds **7 rows** across 295 notes; `areas.parent_id` exists with **0 sub-areas** in use; there is no area↔area relation at all.
8. **13 people notes are indistinguishable** — twelve teammates (one note each), `1 on 1 Meeting` — every one is *Work + BrightLane*.
9. **8 todo notes** (`Todo`, `Work Todo`, `Old Todo`, `Personal Todo` ×2, …) scattered across Work / Content / Development, and their items are **numbered lists, not checkboxes**, so nothing tracks state and lint cannot see them.
10. **16 same-title shadow pairs** (an archived `Archive/` copy behind a live twin). **Investigated per pair: none needs content rescued.** 8 are strict supersets (0 archived-only lines); 6 differ by a single line; `Projects` (#242 live vs #171 archived) and `Manager A` (#246 vs #172) are two *snapshots in time* of the same board, deliberately moved to `Archive/` by the owner. **The duplicate feeling is presentational** — the same title appears twice in search with nothing saying which is current.
11. **The "Librarian ingest" schedule (`*/30 * * * *`) auto-triaged the whole 121-note obsidian backlog overnight** (tasks #214, #216–#218, #220–#221). The owner then reversed it by hand: the librarian proposed archiving the entire `Prompt *` block as museum and **he moved all 36 to active in AI Workflow**. The automation ran ahead of the judgment. **PAUSED 2026-09-09** (schedule #2) until this plan lands; `Library lint` (#3, read-only) stays on.

## Owner decisions (2026-09-09 — locked)

- **No new organizing concept.** No `source_path` column and no new collection entity; `type` (note | playbook | wiki) is the collection primitive that already exists and is unused. Folder structure becomes **tags**, derived deterministically from the dump via `source_of_content_hash`.
- **Areas get BOTH** hierarchy (the unused `parent_id`) **and** sideways area↔area relations.
- **Pause the Librarian ingest** while the system is rebuilt.
- **Shadow pairs: show a diff per pair, owner decides** — done; the audit found nothing to rescue, so the remaining work is presentational (item 8), not a merge.
- Waves 4–5 stay HELD until items 1–4 are live.

## Slice 1 — items 1–4 (this slice, no schema change)

### 1. List rows show a readable excerpt, not raw markdown
`components/brain.tsx:209` (`NoteRow`). A `plainExcerpt(body)` helper strips fenced code, heading markers, list/checkbox markers, emphasis, link syntax and blockquote markers, collapses whitespace, and returns the first readable prose. Same treatment for the split-part preview (`:119`) and the proposal description (`:145`) so every clamped preview in the app reads the same way. The note PAGE is untouched — it already renders markdown correctly.

### 2. Backfill topic tags from the folder tree (186 notes)
One pass using `source_of_content_hash`. Rule, in order: take the ident's path segments **excluding the source root and the filename**; drop container folders (`archive`, `archive 2`, `other`, `tasks`, `notes`, `unfiled`, `tutorials`); drop any segment that equals an existing **area** name (the area already says it); kebab-case the rest; add a single `tutorial` tag when the path passes through `Tutorials/`. **Never re-add an origin tag** — the source root is dropped first, so `obsidian`/`keep`/`focus` can never appear (owner decision 2026-09-08 stands).
Measured output: **186 notes tagged**, vocabulary `brightlane` 67, `tutorial` 45, `hermes-agent-os` 36, `internal` 27, `feedback` 16, `nestjs` 10, `server-setup` 7, `testlink` 5, `caprover` 4, `docker` 4, `technical` 4, + ~23 smaller. **Imported notes left untagged afterwards: 43, not the 3 an earlier draft of this plan claimed** — that figure came from the *unrefined* rule that still tagged area names and container folders. The 43 are all focus, whose folders WERE the `domain:` that already becomes the note's area, so a topic tag would only repeat it; **39 of the 43 carry an area** and are navigable by it, and the 4 without one are archived. This is the rule working, not a gap.

### 3. The importer applies the same rule
`_import_tags(unit)` returns the folder-derived topic tags instead of `[]`, so waves 4–5 land organized. Its docstring keeps the no-origin-tag rule and explains the distinction: **provenance is not a tag; topic is.** Analyzer baseline regenerated, `--check` green.

### 4. Tag hygiene + a convention
- Convention: **lowercase-kebab**, enforced where tags are registered.
- New store functions (audited, one transaction each, no raw SQL for callers): `rename_tag(old, new)` — rewrites every note's `tags` JSON and the taxonomy row, merging when the target already exists — and `retire_tag(tag)` — removes it from the taxonomy and from every note.
- Applied: `BrightLane`→`brightlane` (43 notes; closes the standing lint finding), `NestJs`→`nestjs`, `Nodejs`→`nodejs`, `Prompts`→`prompts`, `Family`→`family`, `Server Setup`→`server-setup`, `Things to do`→`things-to-do`; retire the `ser` typo and the `zzz-test` / `test` / `zzz-deep` fixtures.
- `hermes-agent` (7) and `hermes-agent-os` (36) are two folder spellings — **ask the owner before merging**, do not guess.

## Slice 2 — items 5–8 (SHIPPED 2026-09-09)

5. **`type` put to work.** All 295 notes were `type: note`; `playbook`/`wiki` existed unused while the Library tree already had a Collections section filtering by type. Two evidence-backed rules, deliberately narrow — everything else stays `note` rather than being guessed at: a note tagged `tutorial` **that actually has steps** becomes a **playbook** (`tutorial`-tagged notes measure 68–100% shell-command lines), and a `BrightLane/Internal` reference doc becomes a **wiki**. Two exclusions the first dry run earned: `Tech` is a 38-char bookmark (`https://nats.io/`) tagged `tutorial` only by folder — a bookmark is a note, so a playbook must have a code fence, ≥2 command lines, or ≥300 chars; and per-person logs (`Manager A`, 10k chars of dated notes and salary figures) sit in `Internal` too — they are logs, not reference. Result: **34 playbooks, 13 wiki**.
6. **Series prev/next** — `_series_for` in `wm_store`, a **computed view with no new schema**: the dumps kept courses as numbered FILES in one folder, the folder is now a tag, and the number survived into the title. A note is in a series when its title carries a leading number AND it shares a tag with other numbered notes; **the SMALLEST shared tag wins**, because every NestJs part is also tagged `tutorial` (35 notes) and picking the commonest tag would splice unrelated runbooks into the course. Surfaced above the Related card as "Part 3 of 9" with prev/next.
7. **Areas relate two ways** (owner chose both). `areas.parent_id` had existed unused since the schema was written — `update_area` now sets it, refusing self-parenting, nesting under a sub-area, and any move that would make three levels. New `area_links` table for **affinity**, stored once per pair with the lower id first (a CHECK enforces it) so "Work relates to Career" and "Career relates to Work" cannot drift apart. `GET/POST/DELETE /api/area/{id}/relations`, and an `AreaRelationsBar` on the area view that both shows and EDITS them — without the editor the feature would have shipped dead, since there were 0 relations.
8. **Superseded twins marked, not merged.** One `note_links` row per pair from the archived copy to the live one; `related_notes` resolves links in both directions, so each note now names the other. **No content was merged and nothing was destroyed** — the audit had already shown none of the 16 pairs needs rescuing.

## Item 9 — todo notes normalized (SHIPPED 2026-09-09)

The 4 ACTIVE todo notes now carry real checkboxes: **#238 Old Todo 51, #240 Personal Todo 58, #247 Todo 80, #249 Work Todo 29 — 218 in total.** The archived twins were left alone: they are historical snapshots already linked to their live copy by item 8, so rewriting them buys nothing.

Rules, because this edits the owner's own words:
- **Item text is preserved character for character** — only the marker changes. Verified per note by token diff: the only tokens lost are the list ORDINALS themselves, **zero non-digit tokens**, and every code fence count is identical before and after.
- **Nothing is ever marked done.** Completion is not inferrable from prose, so every item lands unchecked even where the text reads "done …". The words still say it; the owner ticks what is finished.
- A top-level number whose children are indented is a **grouping**, not a todo — `Todo` is organized by person (`the owner`, `Support Task`, `Person A`, `personal`, `person-b`, `person-c`), and those became bold headings.
- Blank-line-separated prose notes treat a **paragraph** as one item, so a wrapped line does not become two half-checkboxes.

**Two defects the verification caught, both from actually looking rather than trusting the head of the output:**
1. The lookahead that detects a grouping required the child to have TEXT, but the owner's list ends with headings whose sub-item is a bare `1. ` placeholder — so `Support Task`, `Person A`, `personal`, `person-b` and `person-c` were turned into checkboxes instead of headings.
2. **Preserving tab indentation broke rendering entirely.** A tab-indented line is an indented CODE BLOCK in markdown, so #247 first rendered **0 checkboxes and showed raw `- [ ]`** while the flat #249 rendered 29. Indentation is now dropped and the grouping is carried by the heading, with a blank line after it so a fresh list starts. Restored from the `note_revisions` snapshot and re-applied.

Live at 390×844: #238 50, #240 58, #247 79, #249 29 rendered checkboxes, no raw `- [ ]` visible on any of them, person headings intact, `scrollWidth` 390, 0 page errors. (Rendered counts trail the written ones by the handful of EMPTY placeholder items that came from the source's own bare `1. ` slots.) `note_revisions` holds the pre-edit body of every note touched — that is the undo path.

## Acceptance (slice 1)

- Suite stays 0 failed, with tests for `plainExcerpt`, the folder-tag rule (including that a source root can never become a tag), `rename_tag` merge-into-existing, and `retire_tag`.
- Live at 390×844: Library rows read as prose, `scrollWidth` 390, 0 page errors.
- `wm note tags` shows the kebab vocabulary with no `BrightLane`/`brightlane` split and no `zzz-*`; `wm note lint` no longer reports `tag_duplicates`.
- Imported notes with no tags: 244 → **43** (all focus, filed by area — see slice 1 item 2); `wm check` Integrity OK; health 200.
- A dry run of the importer on an already-imported source stays a **no-op** (tags change nothing about `content_hash`).
- `wm backup` before the backfill — notes are archive-only, so the backup IS the rollback.

## Out of scope

Any schema column for provenance or collections. Merging shadow-pair content. Waves 4–5. Deleting notes (archive-only stays the design).
