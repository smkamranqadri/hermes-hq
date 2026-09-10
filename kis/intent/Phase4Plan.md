# Phase 4 Plan — Vault, Export, Reminders, Revisions

**Status:** **COMPLETE 2026-09-10** — all four slices shipped and live: **A Export** (`d4b742a`), **B Vault** (`125ad58`, two review rounds + a security pass), **C reminders push-on-due** and **D revision history**. Suite 423 → 520. Proof for each in `state/current.md`. Planned 2026-09-10 (owner-interviewed in session; the eight decisions below are locked — do not relitigate in build tasks).
**Parent:** `intent/SecondBrainPlan.md` → Phase 4.
**Work mode:** Phase — four slices in a fixed order, each shipping something the owner reviews before the next starts.
**Build route:** slices A, C, D direct in-session; **slice B (Vault) is built by an implementer subagent and reviewed by an independent reviewer subagent**, plus a `security-review` pass over the crypto and the destruction transaction before deploy (owner's call — it is the first irreversible delete path this system has ever had).

## Goal

Close the Second Brain with the two pieces that carry Phase 4's DoD — a **private vault** the librarian structurally cannot reach, and an **export** that is real portability rather than a readable souvenir — plus two measured gaps that are cheap to close (reminders that fire without telling anyone; a revision table with no read path).

## Measured starting facts (verified 2026-09-10 at plan time, not remembered)

**Stale after the 2026-09-10 queue drain — this table is the plan-time baseline, not current reality.** The library is now 1037 notes (461 active / 576 archived / 0 inbox) and `note_revisions` holds 942 rows. Live counts live in State → Now.

| fact | value | how it was checked |
|---|---|---|
| library | 945 notes — 193 active / 237 archived / 515 inbox | `hq.db` |
| areas / tags | 17 live / 59 | `hq.db` |
| note types in use | 898 `note`, 34 `playbook`, **13 `wiki`** | `hq.db` |
| attachments | 86 rows, ~31 MB | `note_attachments`, disk |
| revisions | **400 rows, zero read path** | `note_revisions`; no store fn, no route, no UI |
| quarantined secrets | 4 rows, sha256 + 0600 bytes outside `hq_home` | `import_quarantine` |
| vault code today | **none** | no `vault` symbol in `backend/` or `frontend/src` |
| reminder firing | mints the owner task, **notifies nothing** | `core/wm_store.py:4452` `_spawn_from_schedule` — the only `add_notification` in the schedule path is the failure one at :4550 |
| same-title collisions | **128 groups** across the dumps | ImportPlan baseline |
| body `[[wikilinks]]` | 5 notes, **3 of them bash `[[ $COLORTERM = … ]]`** | scan of all bodies |
| `cryptography` | importable, **undeclared** (transitive via `pywebpush` → `py-vapid`) | venv + `pyproject.toml` |
| `hashlib.scrypt` | available, no new dependency | stdlib |
| suite baseline | **423** collected | `pytest --collect-only` |

## Decisions locked in the 2026-09-10 interview

1. **Phase 4 is four slices, not six.** Vault + Export are the pillars; reminders push-on-due and the revision-history UI ride along. **Wiki compile and the `wiki-update` proposal kind move OUT** to their own future `/kis:plan`. **The backlinks pane is CUT.**
2. **Vault unlock = a separate passphrase**, not the login password and not a server-held key. **This REVERSES the 2026-09-04 decision** (server-held key; E2E passphrase declined for searchability/recoverability) — the reversal is deliberate and gets written to Knowledge as such when slice B ships.
3. **A server-held escrow wrap exists** so a forgotten passphrase is recoverable. **Constraint that makes this coherent: the escrow wrap is NEVER on the read path.** The API/web read path unwraps with the passphrase alone; the escrow key is used only by a deliberate **root-only CLI** recovery command. Without that separation the passphrase is decoration.
4. **Everything is sealed** — title, body, Thoughts, attachment bytes. A locked row renders as a lock icon and a date. **Unlocking decrypts titles for that session**, so an unlocked vault reads like an ordinary list and only the locked state is a wall of dates (mitigation for the "vault becomes write-only" risk).
5. **Vault notes live in their own `vault_notes` table**, not behind a flag on `notes`. Agent denial becomes **structural**: `list_notes`, `search_notes`, `lint_library`, `related_notes`, the proposal path, the `wm note` CLI group and the export cannot reach rows that are not in `notes`. Vault is its own Library folder with **no area/tag/project filing** — a row you cannot read is a row you cannot file.
6. **Move-to-vault is the one carve-out from archive-only** (the 2026-09-07 rule stands everywhere else). It destroys the plaintext in **three** places — the `notes` row, its **FTS5 index entry**, and **every `note_revisions` snapshot** — in one transaction, only after the ciphertext has been written and read back. An audit row records id, title sha256 and revision count, which proves the deletion without preserving the secret.
7. **Export is readable AND provably re-importable.** PARA folders + full YAML frontmatter + attachments beside their notes, and the round trip runs through the **production** import path — a real `export` source parser in `backend/notes_import.py`, not a test-only reader.
8. **Build route** as stated above.

### Assumptions taken (owner may correct at any slice gate)
- Escrow key at `/opt/data/hermes-hq/vault-escrow.key`, **0600, root-owned, outside `hq_home`, excluded from `wm backup`** — if a backup ever carried both halves, a single leaked backup would be plaintext and the passphrase would have bought nothing.
- `vault_notes` carries only structural columns it needs for itself (created/updated/kind) — no area, no tags, no project.
- The vault re-seals on every restart, **including the 05:00 PKT auto-update**; the passphrase is never persisted client-side.
- Vault content is **never** in the ordinary export, locked or unlocked.

## Slices

### Slice A — Export (direct in-session) — **SHIPPED 2026-09-10** ✅ (`d4b742a`)
Ships before the vault deliberately: no schema change, no crypto, no deletion — and it puts a real backup of 945 notes in the owner's hands **before** slice B introduces the first hard-delete path. If the destruction step ever misfires, the zip is what saves the corpus.

Scope: `GET /api/brain/export.zip`, owner-only (agent session refused). Folders `Areas/<Area>/`, `Projects/<Project>/`, `Inbox/`, `Archive/`. Per-note YAML frontmatter: `id`, `type`, `area`, `project`, `tags`, `created`, `updated`, `authored_by`, `links`, Thought dates. Attachments written beside their note. **Filenames disambiguated by note id** — 128 same-title groups exist, and `Areas/Work/Server Setup.md` written twice is one file. New `export` source parser in `backend/notes_import.py` (the module the analyzer and importer already share, so the baseline cannot drift).

**DoD — every line met** (measured, see State → Proof; the mechanics moved to Knowledge → technical.md → Second Brain): zip opens in Obsidian as a sane PARA vault; **945 notes present**; **86 attachment files byte-identical** to disk; **0 filename collisions** across the 128 same-title groups; agent session refused (test); **round trip** through `wm brain-import --source export` into a scratch DB reconciles 945 notes / 59 tags / 17 areas / 86 attachments and a **re-run reports `created 0`**; zip size and wall time **measured and reported** (it becomes a background job if it stalls the single-process service); suite green above 423 with the new count in the SAME commit; 390×844 `scrollWidth` 390, 0 page errors.

**What slice A found that the plan did not predict:** (1) the export is **fast** — 0.95 s for 945 notes and 31.4 MB — so the "becomes a background job" contingency is dead, and the route builds synchronously; (2) the restore surfaced a **live data anomaly**, notes #11/#13 carrying `zzz-deep`, a tag the closed taxonomy has never held (State → Known debt); (3) two active notes homed only to a **project** cannot restore onto a box without that project, and four task/schedule links cannot survive a bare restore — both are warned per row rather than dropped silently, and both are now accepted limits in Knowledge.

### Slice B — Vault (implementer + independent reviewer + security-review) — **SHIPPED 2026-09-10** ✅ (`125ad58`)
Scope: `vault_notes`, `vault_meta` (wrapped keys), `vault_audit`. A random data key wrapped **twice** — `scrypt(passphrase)` for the read path, escrow key for root-only recovery. AES-GCM over title/body/Thoughts/attachment bytes. Session unlock; locked list = lock + date; unlocked session decrypts titles. `backend/vault.py` owns all crypto. `wm vault` CLI including the root-only `recover`. Move-to-vault per decision 6. **Drain the 4 quarantined secrets** into the vault, mark the quarantine rows drained, remove the 0600 files. Declare `cryptography` in `pyproject.toml`.

**DoD:** vault note round-trips through lock/unlock; **wrong passphrase refused**; locked list shows no title; **agent denial proven by enumerating every route and every `wm note` command against `vault_notes`**; **escrow key absent from a fresh `wm backup`** (test); the root `recover` CLI opens the vault with the passphrase unknown; move-to-vault leaves **0 rows in `notes`, 0 in FTS, 0 in `note_revisions`** with the audit row present; **grep of `hq.db` AND of a fresh REAL backup finds 0 hits** for `PRIVATE KEY` and `aws_access_key_id` while all 4 secrets stay readable in the vault; an **export taken while locked contains 0 vault files**; suite green; 390×844 unlock + create + move, 0 page errors; `wm check` Integrity OK, health 200. **Take a `wm backup` before this slice touches the live DB.**

### Slice C — Reminders push on due (direct) — **SHIPPED 2026-09-10** ✅
Scope: a fired reminder adds a notification so the dispatcher's existing `push.sync_and_push()` delivers it. Today `_spawn_from_schedule` mints the task and records the firing and that is all.

**DoD:** a one-shot reminder firing produces **exactly one** notification and one push; a `late` catch-up firing does **not** double-notify (`source_key` dedupe, the pattern the schedule-error notification already uses); **skipped and error firings notify nothing as due**; test + live proof with a real one-shot reminder.

### Slice D — Revision history UI (direct) — **SHIPPED 2026-09-10** ✅
Scope: store read fn + `GET /api/brain/note/{id}/revisions` + a drawer listing edits (who/when) with a diff against current, over the **942 rows already written** (400 at plan time; the 2026-09-10 queue drain added the rest).

**DoD:** a librarian-edited note lists its snapshots with `edited_by` and timestamps and a readable diff; vault notes have no revision path at all (structural — they are not in `notes`); suite green; 390×844, 0 page errors.

## Out of scope
Wiki compile and the `wiki-update` proposal kind (own plan later — **and the `wiki` type name is already occupied by 13 reference notes**, so that plan either renames them or picks another name). Backlinks pane — `related_notes` already resolves `note_links` in **both** directions and renders them; body-wikilink parsing is rejected on evidence (2 real cases against 3 bash false positives, so a naive parser mints junk links). Vault search/FTS. Vault in the librarian's sight. Vault content inside the ordinary export. Transcription. Note deletion outside the move-to-vault carve-out. E2E-only encryption (escrow exists by decision).

## Risks
- **FTS5 deletion must match how the notes index is maintained**, or the plaintext survives a "destroyed" move. Highest-consequence line in the phase.
- Export of ~31 MB / 945 notes zipped synchronously could stall the single-process service — measured in slice A's DoD, becomes a job if it does.
- A vault that is annoying to unlock becomes write-only (mitigated by decision 4).
- `cryptography` undeclared until slice B — a fresh install could lack it.
- **Accepted, restated not removed:** root can still decrypt (escrow). The 2026-09-04 limit stands; what the passphrase adds is that a live session on an unlocked phone cannot open the vault, and a stolen `hq.db` alone is useless.

## Verification method
Suite + live Playwright at 390×844 on the REAL library + **DB-level greps of `hq.db` and of a FRESH real backup** (the discipline that proved import quarantine) + a scratch-DB round trip for export. Backup before slice B. No vault work touches the live DB before the export ships.

## Sequencing note
The 515 pending proposals were Phase 3's last DoD clause and **the owner cleared them on 2026-09-10 — 0 pending, Phase 3 fully COMPLETE**. They never blocked this phase; no slice here touches the proposal path.
