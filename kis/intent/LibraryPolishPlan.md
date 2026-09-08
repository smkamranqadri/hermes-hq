# Library Polish — owner feedback backlog from the wave-1 review

**Status:** SHIPPED 2026-09-08 — all seven slices live the same day (A `e46f992`, B `89fda2c`, C+D `b20d523`, E `bd11d90`, F `9345949`, G `b646fd9`; librarian task #212 → proposals #78–#82). Planned that morning (Phase, slices A–G below), owner-interviewed the same day. Wave-1 review is FINISHED (owner-confirmed 2026-09-08). Execution: a Sonnet subagent builds each slice in its own worktree, an Opus subagent reviews (code, then live at 390×844), the orchestrator merges + deploys per slice; subagents never touch `kis/`. Decisions in this file are owner-answered — do not re-ask them.
**Context:** wave 1 (focus, 107 notes) is live; the owner is triaging librarian proposals. Waves 2–5 (~670 more notes) are HELD until this review + the area descriptions land.

## Bugs (diagnosed where marked ✓)

1. **Archive returns you to "All notes" instead of where you came from.** Archiving from a note page (or list) navigates to the Library root, losing the area/filter you were working in. Same class as the search-clear fix (`aff0c24`): actions must preserve the owner's place.
2. **✓ Refile's area select repeats every word.** Reproduced: all 12 areas are top-level, and `AreaSelect` (`components/brain.tsx`) renders each as `<optgroup label="Work">` containing a single option also named "Work" — every area prints twice. Fix: flat options for parentless areas; optgroup only when children exist.
3. **✓ "A lot of dups" — nothing went wrong; the source held them.** 8 duplicate-title groups in the live 139. Six are **focus Task/Note mirror pairs**: the source keeps the same item under `Tasks/X.md` AND `Notes/X.md` (its own create-and-link; task files carry `source: note/text` frontmatter naming their origin). Bodies differ (checkbox line), so the importer correctly kept both under the only-drop-provably-lossless rule. The other two groups: the deliberately-kept Multi-branch trio + `zzz` test fixtures.
   **Owner decision (2026-09-08): a targeted librarian pass proposes `append_entry` per pair** — task text becomes a Thought on the note, source archived, owner approves each (~6). Uses existing machinery. The importer itself should learn the pairing for future focus-shaped sources (`source:` frontmatter + same title = pair).
4. **Note cards don't show all tags.** List rows truncate the tag row; show them all (wrap) — or see item 8.

## UX / features

5. **Tag inputs become pickers.** Everywhere a tag is typed free-text (capture, edit, refile, proposal edit), offer the previously-used tags (the closed taxonomy, with counts) instead of a bare text field; same idea for the area on filing surfaces. Free-text stays possible for owner coinage.
6. **Filter dropdowns show only available options.** The Library's tag and project filters list the whole taxonomy/project roster; they should list only values that occur in the CURRENT selection (respecting area + archive state), with counts — an empty-result filter option is a dead end.
7. **Pagination.** The Library requests `limit=100` with no paging; at 139 notes "all" already clips. Needs "load more" (or infinite scroll) **before waves 4–5 land ~450 more notes**. Known-limit note already in `e1d383f`.
8. **Tags in the Library tree.** Consider a Tags section in the sidebar tree (like Areas), which would also relieve item 4.
9. **Related-notes section on the note page.** **Owner decision (2026-09-08): broadest option** — explicit links (note↔note, note↔task/schedule) first, then shared-tag notes, then same-area siblings, labelled by relation so the three read differently. Watch noise on big areas (Work = 49); cap + "more" link.
10. **Archive refine labels:** owner wants **"Active" / "Archive"** instead of "Archive hidden" / "Archive included". Note when renaming: the view select already uses "Filed" for `status=active` — keep the two controls from reading as synonyms.
11. **Combobox (type-to-filter select) as a system-wide component** — owner: *"maybe later, not in this run."* Log only; do not scope into the polish pass uninvited.

## Policy (owner-decided 2026-09-08 — enforce, don't re-ask)

12. **Area is required for a note to become `active`.** Capture stays frictionless (inbox needs nothing); the transition to `active` — owner edit, refile, or proposal approval — must carry an area **or** a project. Enforce in code like the tag taxonomy (store-level, so every path inherits it). Existing area-less active notes: surface via lint (`orphan` already exists) rather than mass-migrate.

## Taxonomy questions (feed these into the area-descriptions interview — owner is still forming views; do NOT pre-decide)

- **Note #112**: a side project that is *related to* BrightLane and Work — the multi-home case again (area DIY-style single-home vs project links).
- **Content**: is it what he needs to *consume* (reading/watch lists) or what he plans to *post*? Possibly two things wearing one name.
- **Career** now accumulates social media + personal brand: split the area, or tags, or projects?
- **Journal**: unclear what belongs in it at all.
These four are exactly what `areas.description` (the planned descriptions pass) exists to answer — draft descriptions from his settled filings + rejection feedback, interview him on these four explicitly, and write the exclusions ("Content — what I plan to publish; consumption lists live in …").

## Order suggestion for the next session

Descriptions interview (needs his finished review) → quick bug fixes (1, 2, 10, 4) → policy 12 → pickers/filters (5, 6) → related notes (9) → pagination (7) → mirror-pair librarian pass (3) → then wave 2.

## Plan (2026-09-08) — slices, in order

Common contract for every slice: builder works on branch `polish/<slice>` in a worktree, `npm ci` there, suite via the main checkout's `.venv/bin/python -m pytest tests/backend -q` (must stay 0 failed; baseline 294), frontend `npm run build` must pass (`tsc -b`), commits on the branch, leaves the tree clean, never edits `kis/`. Reviewer reads the diff against this plan, tags Critical/Important/Minor, then after deploy verifies live at 390×844 `isMobile` (scrollWidth 390, 0 page errors, screenshots). Deploy = pre-deploy `wm backup`, ff-merge, `npm run build` (live at once), `hermes-hq service restart` only when backend changed, health 200, `wm check`. Owner re-logs in after a restart and checks on the phone.

**Additional item (found 2026-09-08 by `wm note lint`):** near-duplicate tags `BrightLane` / `brightlane`. Data, not code — the owner merges them via the taxonomy when convenient; not in any slice.

### Slice A — quick bugs (1, 2, 4, 10). Frontend only, Standard.
- **1** `pages/BrainNote.tsx:45-50`: after archiving, return to where the owner came from. `NoteRow` (`components/brain.tsx:175`) passes `state={{from: pathname+search}}` on its Link; BrainNote navigates to `location.state.from` when present, else `/brain/library`. Restore stays on the note page.
- **2** `AreaSelect` (`brain.tsx:207-220`): a parentless area with no children renders as a flat `<option>`; `<optgroup>` only when it has children. Today all 18 areas are top-level, so the select must print each name once.
- **4** `NoteRow` (`brain.tsx:189`): drop `slice(0, 4)`, render every tag in a wrapping row (`flex-wrap`, `min-w-0`).
- **10** `pages/BrainLibrary.tsx:151-154`: the refine control becomes **Active / Archive** and **Archive shows only archived notes in the current scope** (owner decision A, 2026-09-08 — no mixed list). Wire: `arch` param → `status=archived` with the same area/project/tag/type/q filters, instead of `exclude_archived=false`. Check the view select still reads distinctly ("Filed" vs "Active" — if the two read as synonyms, the refine label wins and the builder flags it in its report).
- Accept: build passes; live at 390×844 — archive from an area-filtered list returns to that list; refile select lists each area once; a note with 6 tags shows 6 chips; Archive shows archived-only rows for area Work.

### Slice B — policy 12: area or project required to become `active`. Backend + frontend, Standard.
- Store-level, beside the taxonomy check (`core/wm_store.py` `_validate_filing` 5372, `update_note` 5074, `approve_proposal` file 5793 / split 5846): a note may only END UP `active` with `area_id` or `project_id` set — covers `update_note` (explicit `status='active'`, and clearing area/project on an already-active note), proposal `file` approval (`new_area` counts as an area), split parts (already inbox when unfiled — pin with a test). Refusal is a `ValueError` → **409** with a sentence the UI can show; approval re-validates BEFORE touching anything (existing rule).
- Import (`backend/notes_import.py:513`) already requires an area for `active` — pin with a test, no change.
- Restore (`BrainNote.tsx:101`): an archived note with no area/project restores to **`inbox`** (button reads "Restore to inbox"), one with a home restores to `active`.
- `FileNoteModal` (`brain.tsx:250-269`): Save disabled until an area or project is chosen; helper text says why; a 409 still renders honestly.
- Existing area-less active notes: none today (0); `lint` `orphan` stays the surface.
- Accept: suite +tests for every path above (update_note both directions, file approval with/without area, new_area, split parts, restore-to-inbox); live 409 on a raw POST; mobile modal shows the disabled state.

### Slice C — tag/area pickers (5) + scoped filters (6). Backend + frontend, Standard.
- Backend: `GET /api/notes/facets` taking the list filters (`status/area_id/project_id/type/tag/q/exclude_archived`) and returning `{tags:[{tag,count}], projects:[{id,name,count}], areas:[{id,name,count}]}` counted over the CURRENT selection via `_scope_sql` (`wm_store.py` ~5238); store function + tests (counts respect area + archive state; a tag absent from the scope is absent from the list).
- Library filters (`BrainLibrary.tsx:149-165`): tag and project selects come from facets and list only options with count > 0, with counts; the currently selected value stays listed even at 0 so the owner can clear it.
- `TagPicker` in `brain.tsx` replacing the three free-text tag inputs (`FileNoteModal` 269, `EditProposalModal` file 729 and split 764): a text field (free text still allowed, comma-separated, 16 px) plus the registered tags as tappable chips with counts (from `/api/notes/tags`), filtered by what is typed, tap toggles; selected chips show above. **Not the system-wide combobox** (item 11 — deferred).
- `AreaSelect` options show note counts from `useNotesTree()`.
- Accept: suite +tests; live — filter lists shrink when an area is chosen; picker chips toggle on the phone; free-text coinage still works.

### Slice D — related notes (9). Backend + frontend, Standard.
- Store `related_notes(note_id, cap=6)` → `GET /api/note/{id}/related`: three labelled groups in this order — **Linked** (existing `_note_links` targets, note↔note and note↔task/schedule), **Shared tags** (active notes sharing ≥1 tag, ordered by shared-tag count desc then updated desc, excluding self and linked), **Same area** (same `area_id`, active, newest first, excluding the above). Each group capped at 6 with `more: <total>`.
- BrainNote: a "Related" card below the body with the three groups labelled so they read differently; "more" links to the Library pre-filtered (tag / area). Empty groups are omitted; the card is omitted when all three are empty (honest empty state rule).
- Accept: tests for ordering/exclusion/caps; live on a Work note (49 notes) the card stays short and the "more" link lands on the filtered Library.

### Slice E — pagination (7). Backend + frontend, Standard.
- List endpoint (`backend/notes.py:98`) gains a total: keep the array body for existing callers and add header `X-Total-Count` (or an opt-in `with_total=1` envelope — builder picks, reviewer checks no caller breaks). Search path included.
- Library: page size 50, `useInfiniteQuery` on `offset`, a "Load more (shown / total)" button with `Btn busy`, skeleton on first load only; filters reset paging. Inbox worklist ordering (`owner_first`) must survive paging.
- Accept: test paging + total; live — with 139 notes "Filed" shows 50 then loads the rest; scrollWidth 390.

### Slice F — tags in the Library tree (8). Frontend only, Fast/Standard.
- Sidebar tree (`BrainLibrary.tsx:118-142`): a **Tags** section under Areas using the facets of the current view, count per tag, click sets `tag`; mobile keeps the select. Collapsible if > 12 tags.
- Accept: live at 1440 and 390.

### Slice G — mirror-pair pass (3). Importer change (Standard) + a librarian task through the pipeline.
- `backend/notes_import.py`: for focus, a `type: task` unit whose `source:` frontmatter names a note unit with the same title is planned as an ENTRY (Thought) on that note — task text verbatim, task unit not a separate note; `tools/analyze_notes_dump.py` counts the pairs; baseline JSON regenerated and `--check` green; tests on a fixture pair. Re-running wave 1 stays a no-op (already-imported units are matched by ident).
- Live pairs (6): a `wm` task for the `librarian` profile listing the six pairs by note id, instructing one `append_entry` proposal per pair (task text as the Thought, `archive_source: true`), owner approves each in the queue. Created by the orchestrator via `wm`, not a subagent; runs only after A–F are live.
- Accept: analyzer table shows the pairs; 6 proposals in the queue; after approval 6 fewer duplicate-title groups in lint.

### Then
Area-descriptions interview (four taxonomy questions above, now over 18 areas — four with zero active notes: Health, Home, Journal, Study) → wave 2 per `intent/ImportPlan.md`.

## After shipping (2026-09-08)
- **Item 3 outcome differs from the plan:** the owner rejected all five librarian `append_entry` proposals with "dup, archive one" — for the LIVE pairs he wants the duplicate archived, not merged as a Thought. Done (#113, #66 archived; the other pairs already had one side archived). The importer's Thought-merge stays for fresh imports; if a future wave's `merge_needed` rows come up, ask him again rather than assume merge.
- Owner decisions during the run: Archive = archived-only in scope (A); **no origin tags on import** and `focus` stripped from the 97 live notes (the tag drowned the Related card); `authored_by` shown as provenance text, never a chip; no auto-reload after deploys (close/reopen the Home Screen app instead).
- Card fix he reported as "not all tags": the card lacked the PROJECT chip — added in C.
- Leftovers (minor, logged in State → Known debt): TagPicker shows zero-count taxonomy tags; sidebar not sticky; `BrightLane`/`brightlane` taxonomy near-duplicate; project select in the file modal counts only projects that hold notes.
- Item 11 (system-wide combobox) remains deferred by the owner.
