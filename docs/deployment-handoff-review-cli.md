# Deployment handoff: reviewer CLI migration

This change is prepared for the normal review and live-service deployment stage. It has **not** been deployed; no service restart or live code replacement is part of this task.

## Pre-deployment safety backup

Before deployment, from the checkout that will be deployed, create an online SQLite backup with the Hermes HQ CLI (do not copy or edit the database with SQL):

```text
hermes-hq wm backup --dir /opt/data/hermes-hq/runs/worktrees/run-456/deploy-backups
```

The verified handoff backup created for this change is `/opt/data/hermes-hq/runs/worktrees/run-456/deploy-backups/wm-20260907-082248.backup.db`. Verify the backup before any deployment action:

```text
WM_DB=/path/to/the/backup hermes-hq wm check
```

The expected result is `Integrity OK — no anomalies (tasks, reviews, runs all consistent).` Preserve the backup until the deployed service has passed health and review-run smoke checks. If deployment is rejected, do not roll back by editing SQLite; use the repository/service rollback procedure after owner approval.

## Deployment handoff

1. Reviewer checks this branch and the generated-brief/runtime regression tests.
2. Owner-approved deployment stage runs the repository's normal update/build procedure from `/opt/data/projects/hermes-hq`.
3. Create and verify the pre-deployment backup above immediately before changing the live checkout.
4. Deploy through the existing supervisor procedure (`hermes-hq service update` or the approved repository update path), then restart only through the detected supervisor if required.
5. Verify `hermes-hq wm --help` exposes `review`, run `hermes-hq wm check`, and perform a reviewer-run smoke check confirming the review brief uses `hermes-hq wm review` and the reviewer can resolve that command.
6. Verify service health and inspect the service log. Deployment remains gated behind the normal review and owner authorization.

## Scope and safeguards

- Reviewer briefs use only the current `hermes-hq wm review` command.
- Reviewer child processes receive the current checkout context and its CLI directory on `PATH`.
- No `wm.db` or database was edited directly, and this task does not deploy or restart the live service.
