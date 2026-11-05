# Changelog

## 0.1.0 (unreleased)

First public release.

- Work engine: projects, goals and tasks in SQLite, a dispatcher that runs each task as a real
  Hermes session, the completion contract, and a reviewer gate. Code tasks run in their own git
  worktree, and approved work lands on `main`.
- Owner flow: a "Needs you" list with one action per item, answers that resume the same run,
  owner approval gates, and feedback that sends a task back for rework.
- Web UI: overview, projects, tasks (list and board), chat with any agent, schedules and
  reminders, a web terminal, file browser, and agent memory, skills and MCP views. Works on phones
  and installs as a web app, with Web Push.
- Second Brain: text, photo and voice capture, a librarian agent that only proposes, a review
  queue, Markdown export, importers, and a passphrase-sealed vault.
- `install.sh` and `hermes-hq service` for systemd or s6, with update and daily auto-update.
- Time zone for schedules and auto-update is set by `HERMES_HQ_TZ` (default UTC).
