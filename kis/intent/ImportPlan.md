# Import Plan — Second Brain Phase 3

**Status:** PLANNED 2026-09-08. Owner-interviewed in session; every decision below is locked — do not relitigate in build tasks.
**Parent:** `intent/SecondBrainPlan.md` → Phase 3 — Import.
**Work mode:** Phase — wave 0 (capability) then five source waves, each shipping something the owner reviews before the next starts.
**Built direct in-session** (SecondBrainPlan's established mode for the Second Brain — not through the pipeline).

## Goal

Import five note dumps at `/opt/data/notes-dump/` into the Second Brain reversibly, with the museum bulk auto-archived and searchable, provable junk dropped, secrets quarantined, and the living set triaged one-by-one through the Review Queue.

## Measured baseline (derived 2026-09-08; replaces the remembered "~967 units / 69 dup groups")

The DoD reconciles against `tools/analyze_notes_dump.py`, not against prose. Re-runnable; output committed as a fixture.

| source | total | empty | hash dup | archive clone | keep auto | → librarian |
|---|---|---|---|---|---|---|
| focus | 107 | 0 | 0 | 0 | 0 | 107 |
| old-hermes | 1 | 0 | 0 | 0 | 0 | 1 |
| obsidian | 208 | 4 | 68 | 82 | 0 | 54 |
| apple-notes | 251 | 47 | 3 | 0 | 0 | 201 |
| keep | 402 | 0 | 11 | 0 | 95 | 296 |
| **TOTAL** | **969** | **51** | **82** | **82** | **95** | **659** |

Corpus 1.46 M chars (~366 k tokens); the 659 the librarian reads are the bulk of it.

Source shapes, all verified:
- **keep** — one 165 KB `.txt`, UTF-8 BOM, units separated by exactly 4 blank lines. Parser must split on `\n{5,}` (`\n{4,}` happens to give the same 402 here, but only by luck of this dump).
- **focus** — YAML frontmatter (`title/type/domain/status/created/updated/focus_id`). 77 are `type: task` (41 open, 36 done), 30 are `type: note`. `domain:` maps 1:1 onto the seeded HQ areas.
- **obsidian** — `Archive/` is a near-complete clone of the live tree (77 of 78 distinct basenames also live); `Archive 2/` holds zero `.md`. `.obsidian/` config excluded.
- **apple-notes** — 67 `(Attachments)` dirs, every one paired to a sibling `.md`, zero orphans; 76 images, 25.7 MB, largest 2.6 MB. 215 of 251 are 2019–2022. Hostile filenames are real: `..md`, `.md`, `. (2).md`, `÷.md`, `—.md`, unicode-ellipsis truncations. The four dot-named files are invisible to `glob('**/*.md')` — the parser must walk, not glob.
- **old-hermes** — one 151-line structured personal-context export (about/preferences/principles/projects). Split material.

## Decided design (owner-interviewed 2026-09-08)

- **Secrets → quarantine table + 0600 file.** A flagged unit gets **no note row**. Bytes go to a root-only `0600` file outside `hq_home`; `hq.db` gets an `import_quarantine` row holding source path, sha256 and matched reason — **never the secret text**. It still counts in the coverage stat, so all 969 reconcile. Phase 4's vault drains the table when it ships.
  - **Only high-confidence patterns quarantine**: `AKIA[0-9A-Z]{16}`, `sk-…`, `ghp_…`, `BEGIN … PRIVATE KEY`. Measured: 4 units (1 AWS key id, 3 private-key blocks). The other **87** files matching password/token/api-key *keywords* import normally and are flagged to the librarian — a note *about* passwords is not a secret, and quarantining all 91 would satisfy the DoD by destroying the library.
- **The librarian decides living vs museum** (owner chose this over an age heuristic). Museum lands via the existing `file --archive` path with its ARCHIVE chip; living gets filed to an area.
- **Deterministic pre-pass drops provables before the librarian reads anything** — empties, byte-identical duplicates, same-name Archive clones. Each drop writes a reconciliation row with its reason.
  - **Clone precedence is explicit: the live copy always beats the `Archive/` copy.** Never walk-order-dependent. (Found while producing the table above: changing directory walk order silently flipped which copy survived.)
  - **Hash dedupe may auto-collapse; title similarity may only flag.** There are 128 same-title groups, and focus's three `Multi-branch setup for online payments` files carry three *distinct* `focus_id`s and different created dates — they are separate records. `SecondBrainPlan.md:24`'s "dedupe by hash + title heuristics" must never auto-merge on title.
- **Imported notes land in the inbox** (owner's call, over a staging surface). Consequences accepted, mitigated:
  - The inbox separates `authored_by='import'` from owner captures using the existing `list_notes` filter (`wm_store.py:4911`) — no migration.
  - `librarian_ingest` (`wm_store.py:5616`) will report work continuously until a wave drains, so 2b-i's capture nudge goes inert during a wave. **The batch worklist orders `authored_by='owner'` first**, so the owner's real captures never queue behind imported fragments.
- **Five waves, one source each**, owner reviews between them. Order: focus → old-hermes → obsidian → apple-notes → keep.
- **focus todos import as notes only.** 36 done → archived, 41 open → filed to their domain area. **No HQ tasks created.** The owner graduates the ones he still wants via the note page's existing New task button. (Rationale: 14 dormant board rows were closed 2026-09-07 to reach 6 parked ones; auto-creating 41 todos from a system abandoned in August rebuilds that archaeology.)
- **Wave 5 auto-archives Keep fragments.** Units ≤40 normalized chars (62) or whose every line is a bare URL (33) file straight to Archive with the origin tag — 95 units. The librarian judges the remaining 296. Verified against samples: `Fifalwet` and naked links archive; `Read Later` with its four Medium links stays for judgment.
- **Apple attachments attach via 2c's `note_attachments`** (bytes at `hq_home/attachments/<note_id>/`). Obsidian's `.zip` and `.base` files are Obsidian app config, not content — skipped.
- **Reversibility** is the origin tag plus `notes.content_hash` — both already in the schema (`wm_store.py:505`, `authored_by` accepts `import`). Origin stays *just a tag*, per SecondBrainPlan.
- **Raw dumps stay immutable.** The importer only reads `/opt/data/notes-dump/`.

## Wave 0 — Librarian sight (before any dump is imported)

The librarian today has **no context of the existing library** and no way to add to an existing note. Its CLI is `inbox, show, areas, tags, lint, proposals, propose-*`: `inbox` lists only untriaged captures, `show` needs an id it cannot discover, and there is no search. `PROPOSAL_KINDS` (`wm_store.py:5173`) offers only `split | file | contradiction | new_task`, so its only way to place related content is a **new note, every time**. Importing 969 units through that produces scattered near-duplicates instead of notes that accumulated Thoughts.

This also fixes a shipped self-contradiction: `agents/librarian/skills/librarian-specialist/SKILL.md:29` instructs "search first: `wm note show` the likely sibling" — there is no search to do first. The 2b-i contradiction proof only worked because the planted pair sat in the inbox together, so both ids were visible.

1. **`wm note search`** — thin CLI wrapper over the existing, tested `store.search_notes()` (`wm_store.py:5064`, FTS5 bm25 with LIKE fallback, already serving the Library UI). Read-only; the agent write refusal is untouched.
2. **`append_entry` proposal kind** — payload `note_id` + text; approval calls `store.add_note_entry`. Joins `OWNER_EYES_KINDS`, **never classifiable `routine`**: `backend/notes.py` has no delete route for entries, so an appended Thought is permanent. Renders on all three proposal surfaces per `rules.md:13`.
3. **Worklist ordering** — owner-authored inbox notes before imported ones.
4. **SKILL.md** — fix line 29, document `search` and `propose-entry`, add import-triage guidance (note content is data, never instructions; imported web content especially).

## Waves

| # | source | units → librarian | why here |
|---|---|---|---|
| 1 | focus | 107 | the live system; filing is a deterministic `domain:` → area lookup, so it proves the pipeline with the least librarian judgment in the way |
| 2 | old-hermes | 1 | trivial volume, exercises the `split` kind on a structured doc |
| 3 | obsidian | 54 | exercises clone precedence and hash dedupe hardest (150 of 208 drop) |
| 4 | apple-notes | 201 | exercises attachments (76 images) and hostile filenames |
| 5 | keep | 296 | biggest and weakest signal — **re-planned after wave 4**, once librarian judgment quality is observable rather than assumed |

## Acceptance checks

**W0** — librarian finds a filed note by search and proposes `append_entry` against it; approval appends a Thought and creates **no** new note; `append_entry` classified `routine` is refused; an owner capture made during a backlog is picked first; suite green (baseline 231).

**W0c (importer core)** — `--dry-run` reconciles to the table above with **zero units unaccounted**; the AWS key and 3 private-key blocks appear in quarantine and **nowhere in `hq.db`** (verified by grepping the DB); the 87 keyword-only files import normally; clone precedence gives the same result under a reversed walk order.

**W1** — 107 notes; 36 archived, 41 filed to the right area by domain lookup; the three same-title `Multi-branch` files survive as three distinct notes; **zero HQ tasks created**; re-running the import creates nothing (`content_hash`).

**W4** — a note with attachments renders its images; `wm backup` covers the bytes.

**All waves** — reversible by origin tag + `content_hash`; owner completes a triage session; 390×844 `scrollWidth` 390, 0 page errors; deployed, health 200, `wm check` Integrity OK.

## Files likely involved

- NEW `backend/notes_import.py` — per-source parsers, normalization, pre-pass, secret scan, quarantine writer
- NEW `tools/analyze_notes_dump.py` + committed fixture — the reconciliation baseline
- NEW `tests/backend/test_notes_import.py`
- `core/wm_store.py` — `import_quarantine` table + migration; `PROPOSAL_KINDS` / `OWNER_EYES_KINDS` += `append_entry`; approval path; worklist ordering
- `core/wm_cli.py` — `wm note search`, `wm note propose-entry`, `wm note import <source> [--dry-run]`
- `backend/notes.py` — coverage stat; `append_entry` approval
- `frontend/src/` — inbox `authored_by` split, `append_entry` on all three proposal surfaces, coverage stat on Brain Home
- `agents/librarian/skills/librarian-specialist/SKILL.md`
- `tests/backend/test_proposals.py` — extend for the new kind

## Out of scope

Vault encryption (Phase 4 drains the quarantine table). Export, wiki, transcription. HQ task creation from focus todos. Obsidian `.zip`/`.base`. The nine extra repos listed in the old-hermes export. Note deletion (archive-only stays the design).

## Risks and assumptions

- **Inbox flooding is accepted** (owner's call); mitigated by owner-first ordering and the `authored_by` split, not eliminated.
- **Librarian judgment on undated Keep fragments is unproven** — wave 5 is re-planned after wave 4 rather than committed now.
- **25.7 MB of attachments grows every `wm backup`** from wave 4 onward.
- **Assumes file mtimes are true note dates.** They are internally consistent (apple 2019–22, obsidian 2025–26, focus frontmatter 2026-07+) but unverified against the owner's memory. Under the locked design the librarian judges living/museum, so dates are corroboration rather than the deciding rule — but a wrong assumption here would still bias its calls.
- **Imported content is untrusted input.** Keep and obsidian carry web-sourced text; the librarian brief must keep treating note content as data, never instructions.
