# Library Polish — owner feedback backlog from the wave-1 review

**Status:** BACKLOG, logged 2026-09-08 during the owner's first full review of the imported notes. **Not planned yet** — the owner will start a fresh conversation to work these; plan there via `/kis:plan` (small items may bundle into one Standard pass). Three decisions below are already owner-answered — do not re-ask them.
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
