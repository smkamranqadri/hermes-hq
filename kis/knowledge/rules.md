# hermes-hq — Working Rules
- Don't copy features from the three source projects without asking; `knowledge/project.md` lineage table is the allow-list.
- Real data only in the UI; empty/error states are honest, never faked.
- Owner-dependent questions are held, never guessed by agents.
- Prove work with real command output before marking done.
- Communication: short, lead with the decision, label choices A/B/C.
- Every list/detail page shows a content-shaped skeleton while loading and a spinner on busy buttons (`Skeleton`, `Btn busy`); applies to all future pages.
- Every new/changed page is checked at 390×844 with mobile emulation before done: tab bar clearance, safe areas, 16 px fields, `scrollWidth` 390 (missing `min-w-0` is the usual overflow cause).
- Owner changes to requirements must be edited into the task **description/DoD** — mid-run guidance (answer files, chat) does not bind the reviewer (learned 2026-09-02, task #138).
- **Anything an agent proposes or does that awaits the owner must be visible on EVERY surface the owner naturally lands on** — the list row, the detail page, AND the dedicated queue; a queue alone is not enough. (Learned 2026-09-06: three consecutive owner confusions over one librarian proposal that only the Review queue showed — fixed with the inbox `pending_proposal_id` chip and the note-page ProposalBanner. Apply the same test to future proposal kinds, lint findings, and run questions.)

- Never assume hermes-hq runs inside the Hermes docker/s6 container (owner, 2026-08-29). Detect the platform (installed service vs. none) and fall back to owning the process; no hardcoded `/run/service`, `s6-svc`, `/opt/data` or container users in code.
