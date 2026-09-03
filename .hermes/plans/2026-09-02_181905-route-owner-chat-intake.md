# Route Owner Chat Work Through Intake — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Ensure new owner-chat work is grilled and registered through Hermes HQ instead of being routed through retired direct-dispatch skills, while preserving the legacy skills in an auditable archive.

**Architecture:** Keep the owner-chat guard in the shared project/task brief footer, so both scoped chat contexts receive the same routing instruction. Make the repository template authoritative for the deployed orchestrator skills, update every live deployed reference to the retired skill names, and archive the two legacy skill directories outside the deployed tree before restarting the service.

**Tech Stack:** Python/FastAPI backend, pytest, repository Markdown skill templates, deployed skill tree under `/opt/data/skills`, Hermes HQ s6 service via the `hermes-hq-restarter` broker.

---

## PLAN-phase boundary

This document is the proposed implementation only. No code, deployed skill, archive, database, service, or branch state is changed in this phase. Build starts only after owner approval. The task's owner-approval gate remains open after build/review; live owner re-test belongs to the separate INTEGRATE/VERIFY task.

## Exact proposed edits

### 1. Shared project/task chat preamble

Modify `/opt/data/projects/hermes-hq/core/wm_store.py` in `_CHAT_BRIEF_FOOTER` immediately after the existing owner-chat warning. Add this exact sentence as its own footer line:

> New work you bring in chat — bug reports, change requests, or feature ideas — must go through the `orchestrator-intake` skill's grilling interview and become a managed task through the Hermes HQ wm pipeline; never use ad-hoc `hermes --profile <agent>` specialist dispatch.

The footer is shared by `render_project_brief` and `render_task_brief`, so no separate parallel-task/task-chat wording change is needed; both contexts inherit the same sentence. Preserve the existing "NOT a dispatched task" and acknowledgement lines.

Update `/opt/data/projects/hermes-hq/tests/backend/test_chat_scopes.py` to pin the complete routing sentence in the project-chat assertion. Keep the existing task-chat behavior assertion and add a direct assertion there as well if needed to prove both render paths contain the sentence.

### 2. Sharpen `orchestrator-intake` trigger description

Replace the frontmatter description in both files with this exact text:

> `Interview the owner (grilling rounds) whenever chat brings new work — a bug report, change request, or feature idea — before any task, goal, or dispatch is created.`

Files:

- `/opt/data/projects/hermes-hq/agents/orchestrator/skills/orchestrator-intake/SKILL.md`
- `/opt/data/skills/orchestrator-intake/SKILL.md`

After build, verify the files are byte-identical (empty `diff`) and that the sharpened description appears in both.

### 3. Remove live references to retired skills

The read-only sweep found these exact references under `/opt/data/skills`:

1. `/opt/data/skills/autonomous-ai-agents/orchestrator-router/SKILL.md`
   - The skill's own `name: orchestrator-router` metadata.
   - Handling: move the entire directory intact to the dated archive below; it is no longer in the deployed skills tree.
2. `/opt/data/skills/autonomous-ai-agents/specialist-dispatch/SKILL.md`
   - The skill's own `name: specialist-dispatch` metadata.
   - Its body also names `orchestrator-router` and describes the direct profile-dispatch flow.
   - Handling: move the entire directory intact to the dated archive below.
3. `/opt/data/skills/autonomous-ai-agents/hermes-multiagent-coordination/SKILL.md:11`
   - Body reference saying `specialist-dispatch` covers the current manual model.
   - Handling: replace with a reference to managed Hermes HQ dispatch through `hermes-hq-ops`, with `orchestrator-intake` governing new owner work.
4. `/opt/data/skills/autonomous-ai-agents/hermes-multiagent-coordination/SKILL.md:14`
   - Body reference directing one-off dispatches to `specialist-dispatch`.
   - Handling: replace with current Hermes HQ guidance: use `hermes-hq-ops` for managed dispatch mechanics and `orchestrator-intake` before new owner work.
5. `/opt/data/skills/hermes-hq-ops/SKILL.md:10`
   - `metadata.hermes.related_skills` lists both retired names.
   - Handling: remove both names, retaining `hermes-multiagent-coordination`.
6. `/opt/data/skills/hermes-hq-ops/SKILL.md:16`
   - The "Not for generic one-off single-agent delegation" parenthetical names both retired skills.
   - Handling: remove that parenthetical and retain the positive instruction to load `orchestrator-intake` for new owner work.

The repository template sweep found the corresponding two references only in:

- `/opt/data/projects/hermes-hq/agents/orchestrator/skills/hermes-hq-ops/SKILL.md:10` (`related_skills`)
- `/opt/data/projects/hermes-hq/agents/orchestrator/skills/hermes-hq-ops/SKILL.md:16` (legacy-dispatch parenthetical)

Handling: apply the same edits as the deployed `hermes-hq-ops` copy so fresh installs cannot resurrect the old references.

`/opt/data/skills/autonomous-ai-agents/content-pipeline-supervisor/SKILL.md` was inspected and contains no reference to either retired skill; no change is proposed there. No other textual reference was returned by the exact-name and hyphen/underscore variant sweeps. References inside the archived legacy directories remain preserved by design and are outside the deployed skills tree.

### Archive destination

Move, without editing or deleting contents:

- `/opt/data/skills/autonomous-ai-agents/orchestrator-router/`
- `/opt/data/skills/autonomous-ai-agents/specialist-dispatch/`

into:

- `/opt/data/.skills-trash-2026-09-02/autonomous-ai-agents/orchestrator-router/`
- `/opt/data/.skills-trash-2026-09-02/autonomous-ai-agents/specialist-dispatch/`

Build must create parent directories as needed, use a move (not a copy followed by deletion), and compare archived file manifests/content against the source before removal when possible. The archive is outside `/opt/data/skills`, following the existing dated parachute/trash pattern.

## Ordered build steps

### Task 1: Update repository source and tests

**Files:**

- Modify `core/wm_store.py`
- Modify `tests/backend/test_chat_scopes.py`
- Modify `agents/orchestrator/skills/hermes-hq-ops/SKILL.md`
- Modify `agents/orchestrator/skills/orchestrator-intake/SKILL.md`

Apply the exact text above and the corresponding template reference cleanup. Run the focused chat test first, then the full backend suite.

### Task 2: Review repository diff

Run `git diff --check`, inspect the complete diff, and verify only the four named repository files changed. Commit the repository changes on the run branch with a focused message. Do not commit deployed `/opt/data/skills` operations.

### Task 3: Archive and update deployed skills

After repository review approval, archive the two legacy directories, update the deployed `hermes-hq-ops` and `hermes-multiagent-coordination` files, and copy the repository orchestrator skill templates to their deployed counterparts as required. Verify the two intake files are identical and scan all deployed skill text for both retired names.

### Task 4: Restart and health-check

After backend changes, request restart only through the broker:

```text
touch /run/hermes-hq-restart/restart.request
```

Respect the required 30-second cooldown, then check `GET /api/health` on `127.0.0.1:9010` and require HTTP 200. Do not use direct supervisor commands in place of the broker.

### Task 5: Final agent-producible verification

Run:

```text
uv run pytest tests/backend
```

Record the actual passed count and zero failures. Also verify:

- neither retired directory exists under `/opt/data/skills/autonomous-ai-agents/`;
- both complete directories exist under `/opt/data/.skills-trash-2026-09-02/autonomous-ai-agents/` and remain intact;
- deployed skill scan returns no live reference to either name;
- repository/deployed template copies contain no retired references;
- repository and deployed `orchestrator-intake/SKILL.md` have an empty `diff`;
- the preamble sentence appears in both project and task briefs;
- git status/diff show the expected committed repository state and the run branch was merged to `main` only after review approval;
- health endpoint returns 200 after restart.

## Risks and open items

- **Legacy flow risk:** The archived skills encode direct headless `hermes --profile` dispatch and reviewer-profile loops. Any agent or script that still explicitly loads them will lose those instructions once archived; the deployed text sweep is intended to expose any remaining live pointer. Archived copies must not be placed anywhere scanned as deployed skills.
- **Coordination-skill wording risk:** `hermes-multiagent-coordination` currently describes the retired direct-dispatch skill as a complement. It must be rewritten rather than merely deleting the names, or its trigger could still steer an agent toward an undefined/manual flow.
- **Template drift risk:** Both `hermes-hq-ops` and `orchestrator-intake` have repo and deployed copies. Build must update both sides and verify byte identity for intake; otherwise reinstall or the running profile could regress.
- **Preamble scope risk:** The footer is shared by project and task scoped chats. This is intentional and keeps routing consistent; the instruction says owner chat's new work must be intake-routed, while the surrounding text still says scoped chat is not itself a dispatched task.
- **Service/re-login risk:** The required restart clears in-memory login sessions, so a human may need to log in again. Live owner retest is outside this plan task.
- **Owner approval:** This plan must be approved before Build; after review/merge and service restart, the owner still must perform the separate INTEGRATE/VERIFY live re-test.

## Review and verification commands

Focused check before full suite:

```text
uv run pytest tests/backend/test_chat_scopes.py -q
```

Required full backend check:

```text
uv run pytest tests/backend
```

Repository hygiene:

```text
git diff --check
git status --short
```

Deployed reference and archive checks should use read-only file inspection/search after the archive/update operations; do not access or mutate `/opt/data/hermes-hq/hq.db` directly.

## Deliverable to owner

The Build completion summary must list every changed repository and deployed file, the exact archive path, the exact preamble sentence, the exact intake description, the complete reference sweep, actual backend passed count, restart-broker evidence, and health HTTP 200. The task remains `manual`/owner-gated rather than receiving final owner sign-off from the implementing agent.
