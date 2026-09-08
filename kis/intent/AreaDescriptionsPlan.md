# Area Descriptions Pass — settle the area taxonomy, then describe it

**Status:** **SHIPPED 2026-09-08** — both steps live on :9010 (suite 371 → **388**, +17 `tests/backend/test_areas.py`). Owner-interviewed in session; every decision in "Owner decisions" is locked — **do not re-ask them in build tasks.** Outcomes that differ from the plan are recorded under "What shipped" at the bottom.
**Parent:** `intent/LibraryPolishPlan.md` → "Taxonomy questions" + "Then". Gates `intent/ImportPlan.md` waves 2–5 (~670 notes).
**Work mode:** Standard — two ordered steps in one slice (mechanism, then content), one owner review at the end.
**Build cycle:** direct in-session per the 2026-09-08 rule (small slice), then a fresh **Opus reviewer** subagent on the diff before deploy. The descriptions themselves are owner-reviewed, not agent-reviewed.

## Why this exists

The librarian files notes into areas, and across waves 2–5 it will file ~670 of them. Today an area is a bare name: `wm note areas` prints `#3 Content` and nothing else, so the agent guesses the boundary — and its guesses are exactly what the owner spent the wave-1 review rejecting (`docker manager` → AI Workflow, corrected to DevOps; `desk pet` → Home, corrected to DIY; `action figure` → Home, corrected to Shopping). A description that names what does **not** belong is the cheapest way to stop that repeating 670 times.

## What the evidence actually shows (measured 2026-09-08, live DB)

**The 18 areas have two origins, and only one of them is the owner's judgment.**

- **11 are inherited.** They are the old focus system's `domain:` values, copied in by the importer: Work 49, Home 6, Career 6, AI Workflow 6, Side Project 5, Content 5, Finance 3, Family 3, Study 1, Health 1, Projects 1. Never chosen for this library.
- **7 were coined by the owner during the wave-1 review**, each by rejecting a librarian filing: Development, Space, Games, Open Source, Shopping, DIY, DevOps. Each holds 1–2 notes.

So of 53 active notes, **44 were filed by the importer from the source's own taxonomy** and only ~9 filings plus 16 rejections are owner judgment. **Drafting rule that follows: the 16 rejections and 7 coinages are strong evidence; importer-`domain:` filings are weak evidence. Where they disagree, the rejection wins.**

**The four zero-active areas are not equally empty:**

| area | active | what its archived notes actually are |
|---|---|---|
| Journal | 0 | `test`, `اردو نوٹس`, `asdfasd` — test debris only. Never held a real note. |
| Home | 0 | 3× `Multi-branch setup for online payments` (a Work/payments item the source filed `domain: Home`) + `Test Urdu voice note`. No real Home content ever. |
| Health | 0 | `Schedule dentist appointment` (real), a demo dentist note, 2 `zzz` fixtures. |
| Study | 0 | `Prepare for VU BBIT 2nd Semester final exam` (real). |

## Owner decisions (2026-09-08 — locked)

1. **Describe *and* prune.** The pass settles the area set first, then describes what survives. Describing an area about to be retired is wasted work, and the description is the natural place to record why an area exists.
2. **Retire Journal and Home. Keep Health and Study.** Journal has no real content and no clear purpose; Home's only real notes are misfiled Work. Health and Study each hold a genuine note and are obvious life areas that waves 2–5 will refill from Apple Notes and Keep.
3. **Split Content.** `Content` = what I publish; a new area **`Reading`** = what I consume. Chosen over folding publishing into Career, with the empty-at-launch risk raised and accepted — Keep's `Read Later` material (ImportPlan, wave 5) is its intake.
4. **Personal-brand strategy stays in Career.** Career = positioning yourself (job search, portfolio, DevRel communities, how-to-be-visible, the personal-brand plan). Content = the artifacts that plan produces.
5. **Multi-home: no rule — decide per note.** An area is not multi-valued and no join table is built. **Operational form:** a note that plausibly fits two areas is **never classified `routine`** — the librarian proposes it with the ambiguity named in the summary and the owner decides. This reuses the shipped rule in `SKILL.md:36` rather than adding machinery.
6. **Keep all 7 coined areas.** They are the owner's explicit calls from two days ago and waves 2–5 will fill them. **Trip-wire:** if a one-sentence description cannot distinguish an area from its neighbours, bring it back to the owner rather than inventing a boundary. `Development` (1 note, `ui lib`) against `DevOps` and `AI Workflow` is the boundary expected to fail this test.

Resulting taxonomy: **17 areas** (18 − Journal − Home + Reading).

## The edge case this pass must not ship broken

`list_areas(db_path, archived=False)` already filters `archived=0` (`core/wm_store.py:4642`), so archiving an area removes it from **every** consumer in one flip — the CLI, `GET /api/areas`, the tree, and all selects. That is what makes retirement cheap, and it is also the bug: `areaLabel()` (`frontend/src/components/brain.tsx:168`) resolves an area *name* by finding the id in that same list, so **Home's 4 and Journal's 3 archived notes would render with no area chip at all** — a note that silently loses its home, which breaks the honest-state rule. Label resolution must see archived areas; filing surfaces must not.

## Slices

Common contract: suite via `.venv/bin/python -m pytest tests/backend -q` stays **0 failed**; frontend `npm run build` passes; no raw SQL touches the DB — every write goes through the audited store path; `kis/` is not edited by any subagent.

### Step 1 — mechanism (backend + frontend + CLI)

- **Schema:** `areas.description TEXT` + migration. `areas.archived` already exists; no change.
- **Store:** `update_area(area_id, name=, description=, archived=)` writing an audit row per edit; `create_area(..., description=)`. **Retiring an area that still holds ACTIVE notes is refused** with a `ValueError` → 409 naming the count — those notes would become homeless, which policy 12 forbids.
- **API:** `PATCH /api/areas/{id}`; `GET /api/areas?include_archived=1` for label resolution only.
- **CLI:** `cmd_note_areas` (`core/wm_cli.py:793`) prints each area's description under its name — this is the librarian's orientation surface and the main consumer of the whole pass.
- **Frontend:** `areaLabel()` resolves from the archived-inclusive list and renders a retired area **muted**, the same provenance treatment `authored_by` got on 2026-09-08 — never a chip, never blank. Filing surfaces (`AreaSelect`, `FileNoteModal`) keep using the active-only list so a retired area can never be filed into. Area description shows on the Library area header and as helper text in the refile modal.
- **Librarian `SKILL.md`:** read the descriptions before filing; a note fitting two areas is never `--routine` (decision 5).

### Step 2 — content and data (owner-gated)

- Draft **17 descriptions**, each with an explicit **exclusion clause naming where the excluded thing goes instead** — e.g. *"Content — what I plan to publish. Reading lists live in Reading; the career strategy behind a post lives in Career."* Reading's description names its wave-5 intake explicitly, since it ships with zero notes.
- Create **Reading**; retire **Journal** and **Home**.
- Refile Career → Content: **#70** `Share Awais Sheikh's research on social media`, **#71** `Write AWS article for AWS Community Builder profile`, **#106** `post on social media`.
- **`wm backup` before any data write** — notes are archive-only, so the DB backup is the only rollback (ImportPlan, wave-1 lesson).
- **The full move list is shown to the owner and applied only on his OK.** Nothing is refiled silently.

**Two items go to the owner rather than being decided in the build:**
- **#34 `Domain and Product Name Ideas`** sits in Content but is product naming, not publishing — likely Side Project. On the move list, not decided by the agent.
- **`Development` vs `DevOps` vs `AI Workflow`** — the boundary expected to fail the one-sentence test (decision 6).

## Acceptance checks

- Suite 0 failed, with new `tests/backend/test_areas.py` covering: description round-trip; retire **refused** while active notes remain; retire **succeeds** at zero active; an archived area still resolves a label; `create_area` with a description; an audit row per edit.
- `wm note areas` — read back **from the deployed service, not the checkout** — prints **17** areas each with a description; Journal and Home absent.
- Note **#83** (archived, Home) still shows `Home` muted as retired — not a blank chip. A retired area is absent from every filing select.
- `Reading` exists with a description naming its wave-5 intake.
- **0 homeless active notes** after the refile; `wm note lint` clean; `wm check` Integrity OK; health 200.
- Live at 390×844 `isMobile`: `scrollWidth` 390, 0 page errors, description legible on the area view and in the refile modal.

## Risks and assumptions

- **Refiling has no undo** — notes are archive-only. Mitigated by the pre-write `wm backup` and owner sign-off on the move list, not eliminated.
- **Reading ships empty.** Its value is unproven until wave 5; the risk was raised and accepted (decision 3).
- **Assumes descriptions change librarian behaviour — unproven until a wave runs.** Wave 2 (old-hermes, 1 note) is too small to prove it; **obsidian (121 notes) is the honest test.**
- Descriptions are drafted from partly-inherited filings; the weighting rule above is the mitigation.
- "No rule" for multi-home means **more proposals reach the owner's desk, not fewer**, across ~670 notes. Accepted. If it gets noisy during wave 3 the fallback is tags doing more work — not a new area rule.

## Out of scope

Multi-area membership / any join table. Renaming, merging or re-parenting the 7 coined areas. An areas admin screen. The `BrightLane`/`brightlane` tag near-duplicate (owner's call, logged in State). Waves 2–5 themselves. The system-wide combobox (LibraryPolishPlan item 11, deferred by the owner).

## Then

Wave 2 per `intent/ImportPlan.md` (old-hermes, 1 unit — exercises `split` on a structured doc), then obsidian 121 as the real test of whether the descriptions work.

## What shipped (2026-09-08)

**Step 1 — mechanism.** `areas.description` + migration (idempotent, applied to the live DB on service restart); `update_area(name/description/archived)` and `get_area` on the audited store path with an audit row per edit; the retire guard refusing while live notes or live sub-areas remain, with the count in the message; `PATCH /api/areas/{id}` and `GET /api/areas?include_archived=1`; `wm note areas` printing each description under its name; `areaLabel`/`AreaChip`/`areaRetired` so a retired area renders muted as `Name · retired` instead of vanishing; the Library area header showing the description and the refile modal showing it at the moment of filing; librarian `SKILL.md` updated for both the descriptions and the ambiguity rule.

**Two defects the tests caught before deploy, both now pinned by regression tests:**
1. A whitespace-only description built `UPDATE areas SET  WHERE id=?` — "not provided" and "clear it" had both collapsed to `None`. Fixed with an `_UNSET` sentinel.
2. The PATCH route forwarded `description` unconditionally, so **renaming an area would have wiped its description**. Fixed with `model_fields_set`; an explicit `{"description": null}` still clears on purpose. Both generalised into `knowledge/rules.md`.

**Step 2 — content, owner-approved before anything was written.** Two decisions changed the plan:
- **Space was retired too** and `space news` (#52) moved to Reading. The plan predicted `Development` would be the area that failed the one-sentence test; it got a clean line, and **Space** turned out to be the weak one — once Reading existed, "space news" was a reading habit rather than an area. So the taxonomy is **16 live areas**, not the 17 the plan projected.
- **#34 `Domain and Product Name Ideas` moved to Side Project** (product naming, not publishing) — the item the plan deliberately routed to the owner rather than deciding.

Final moves: #70, #71, #106 Career → Content; #34 Content → Side Project; #52 Space → Reading. Retired: Journal, Home, Space. Created: Reading (id 19). All 16 live areas carry a description; 0 say "no description yet".

**Reading shipped with one note instead of empty** — the risk the plan flagged and the owner accepted was resolved by his own Space decision, not by wave 5.
