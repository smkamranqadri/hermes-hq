#!/usr/bin/env python3
"""wm_cli.py — entry point for the Hermes Work Manager `wm` CLI.

Usage: python3 wm_cli.py <command> ...  (or via the ./wm wrapper)
Stdlib only. Reads project data from the DB (single source of truth).
"""

import argparse
import sys

try:
    from core import wm_store as store
except ImportError:  # run as a bare script from the engine dir
    import wm_store as store

TASK_GROUP_ORDER = [
    ("running", "RUNNING"),
    ("failed", "FAILED"),
    ("stalled", "STALLED"),
    ("needs_review", "NEEDS REVIEW"),
    ("rework", "REWORK"),
    ("done", "DONE"),
]


def _p(s):
    print(s)


def cmd_init(args):
    store.init_db()
    _p("Schema initialised at %s (schema_version=%s)"
       % (store.DEFAULT_DB_PATH, store.get_meta("schema_version")))


def cmd_project_create(args):
    pid = store.create_project(args.slug, args.name, args.description or "",
                               args.path or "")
    _p("Created project #%d '%s' (slug=%s)" % (pid, args.name, args.slug))


def cmd_project_list(args):
    rows = store.list_projects()
    if not rows:
        _p("No projects.")
        return
    for r in rows:
        archived = " [archived]" if r["archived"] else ""
        _p("#%-3d %-20s %s%s" % (r["id"], r["slug"], r["name"] or "",
                                 archived))
        if r["primary_path"]:
            _p("       path: %s" % r["primary_path"])


def cmd_project_show(args):
    r = store.get_project(slug=args.slug)
    if not r:
        sys.exit("error: no project with slug '%s'" % args.slug)
    _p("Project #%d: %s (slug=%s)" % (r["id"], r["name"], r["slug"]))
    _p("  description : %s" % (r["description"] or "-"))
    _p("  primary_path: %s" % (r["primary_path"] or "-"))
    _p("  created     : %s" % r["created_at"])
    _p("  archived    : %s" % r["archived"])
    goals = _conn_goals(r["id"])
    tasks = store.list_tasks(project_slug=r["slug"])
    _p("  goals       : %d" % len(goals))
    for g in goals:
        _p("    #%-2d %-30s [%s]" % (g["id"], g["title"] or "-", g["status"]))
    _p("  tasks       : %d" % len(tasks))
    for t in tasks:
        _p("    #%-3d %-30s [%-12s] %s" % (t["id"], t["title"] or "-",
                                           t["status"],
                                           t["assignee_profile"] or ""))


def _conn_goals(project_id):
    conn = store._connect()
    try:
        return conn.execute("SELECT * FROM goals WHERE project_id=? "
                            "ORDER BY id", (project_id,)).fetchall()
    finally:
        conn.close()


def cmd_goal_create(args):
    gid = store.create_goal(args.project_slug, args.title,
                            args.description or "", args.acceptance_criteria or "")
    _p("Created goal #%d in project '%s'  [status=draft — NOT planned]"
       % (gid, args.project_slug))
    _p("  Send it to planning with: `wm goal plan %d`" % gid)
    _p("  (a draft goal cannot be released — it must reach `planned` first)")


def cmd_goal_plan(args):
    """PLAN a draft goal: park an Orchestrator decomposition task + -> planning."""
    g = store.get_goal(args.id)
    if not g:
        _p("error: no goal with id %s" % args.id)
        return 1
    try:
        goal, tid, already = store.request_goal_planning(args.id)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    t = store.get_task(tid)
    if already:
        _p("Goal #%d ('%s') is already in planning — no new task created."
           % (g["id"], g["title"] or "-"))
    else:
        _p("Goal #%d ('%s') -> PLANNING." % (g["id"], g["title"] or "-"))
    _p("  planning task #%d [%s] assignee=%s"
       % (tid, t["status"] if t else "?", (t["assignee_profile"] if t else "?") or "-"))
    _p("  Parked in the backlog: NO agent starts automatically.")
    _p("  When the goal is decomposed, run: `wm goal planned %d`" % g["id"])
    return 0


def cmd_goal_planned(args):
    """Decomposition agreed: planning -> planned (ready for the approval gate)."""
    g = store.get_goal(args.id)
    if not g:
        _p("error: no goal with id %s" % args.id)
        return 1
    if g["status"] == "planned":
        _p("Goal #%d ('%s') is already planned — no change."
           % (g["id"], g["title"] or "-"))
        return 0
    try:
        out = store.set_goal_status(args.id, "planned")
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Goal #%d ('%s') -> PLANNED (was %s)."
       % (g["id"], g["title"] or "-", g["status"]))
    _p("  Approve the plan with: `wm goal release %d`" % out["id"])
    return 0


def cmd_goal_abandon(args):
    """Abandon & re-plan: planning -> draft (Phase 6.5.1).

    The decomposition attempt was wrong, so the goal goes back to `draft` where
    its text is editable again. set_goal_status also closes the open
    `Plan goal #N` task, so nothing is left parked for an abandoned attempt.
    """
    g = store.get_goal(args.id)
    if not g:
        _p("error: no goal with id %s" % args.id)
        return 1
    if g["status"] != "planning":
        _p("Refused: goal #%d is '%s' — only a goal in planning can be "
           "abandoned back to draft." % (g["id"], g["status"]))
        if g["status"] == "draft":
            _p("  It is already a draft: `wm goal plan %d` to (re-)plan it."
               % g["id"])
        else:
            _p("  A %s goal carries an agreed decomposition — a scope change "
               "there is a NEW goal, not an abandon." % g["status"])
        return 1
    try:
        out = store.set_goal_status(
            args.id, "draft",
            detail="goal #%d abandoned: planning -> draft (re-plan)" % args.id)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Goal #%d ('%s') -> planning -> draft (ABANDONED)."
       % (out["id"], out["title"] or "-"))
    _p("  Its open `Plan goal #%d` task (if any) was closed `done`." % out["id"])
    _p("  The goal text is editable again; re-plan with: `wm goal plan %d`"
       % out["id"])
    return 0


def cmd_goal_release(args):
    g = store.get_goal(args.id)
    if not g:
        _p("error: no goal with id %s" % args.id)
        return 1
    try:
        status, children = store.release_goal(args.id)
    except ValueError as e:
        # Phase 6.5: release_goal now refuses a goal that is not `planned`.
        # Report the engine's real reason; never crash on it.
        _p("Refused: %s" % e)
        if g["status"] == "draft":
            _p("  Send it to planning first: `wm goal plan %d`" % g["id"])
        elif g["status"] == "planning":
            _p("  Mark the decomposition done first: `wm goal planned %d`"
               % g["id"])
        return 1
    _p("Goal #%d ('%s') -> RELEASED (approved)." % (g["id"], g["title"] or "-"))
    if children:
        for tid, ns in children:
            _p("  task #%-4d -> %s" % (tid, ns))
    else:
        _p("  (no child tasks yet — tasks added later start under this approved "
           "goal and run automatically once their deps are done.)")
    return 0


def cmd_goal_show(args):
    g = store.get_goal(args.id)
    if not g:
        _p("error: no goal with id %s" % args.id)
        return 1
    _p("Goal #%d: %s" % (g["id"], g["title"] or "-"))
    _p("  project    : %s" % g["project_id"])
    _p("  status     : %s" % g["status"])
    _p("  description: %s" % (g["description"] or "-"))
    _p("  acceptance : %s" % (g["acceptance_criteria"] or "-"))
    return 0


# Phase-6.5 backfill selection: goals left `planned` by the pre-6.5 create path
# that never got decomposed (zero child tasks). Deliberately NOT in
# wm_store._migrate — this is a one-shot, explicitly-invoked data fix, so a
# legacy DB opened by init_db is never silently rewritten.
_BACKFILL_SQL = (
    "SELECT g.id, g.title, g.status, p.slug AS slug FROM goals g "
    "LEFT JOIN projects p ON p.id = g.project_id "
    "WHERE g.status = 'planned' AND NOT EXISTS "
    "(SELECT 1 FROM tasks t WHERE t.goal_id = g.id) ORDER BY g.id")


def _backfill_candidates():
    conn = store._connect()
    try:
        return conn.execute(_BACKFILL_SQL).fetchall()
    finally:
        conn.close()


def cmd_goal_backfill_draft(args):
    """One-shot Phase-6.5 backfill: planned-with-0-tasks goals -> draft.

    Dry-run by default. `--apply` backs the DB up first, flips each goal
    through store.set_goal_status (so every flip leaves a real `goal_status`
    activity row), then re-runs the selection query and prints the result as
    proof that the set is now empty.
    """
    rows = _backfill_candidates()
    _p("Phase 6.5 backfill — goals that are 'planned' with ZERO tasks:")
    if not rows:
        _p("  (none — nothing to backfill)")
        return 0
    for r in rows:
        _p("  #%-3d [%s] %-50s project=%s"
           % (r["id"], r["status"], (r["title"] or "-")[:50], r["slug"] or "-"))
    _p("  %d goal(s) selected." % len(rows))

    if not args.apply:
        _p("\nDRY RUN — nothing was changed. Re-run with --apply to flip them "
           "to 'draft'.")
        return 0

    # Explicit db_path: backup_db() falls back to DEFAULT_DB_PATH, not
    # resolve_db(), so a bare call would back up the WRONG file whenever WM_DB
    # is set — i.e. the safety net would not cover the DB we are about to edit.
    backup = store.backup_db(db_path=store.resolve_db())
    _p("\nBackup written: %s" % backup)
    detail = "phase 6.5 backfill: planned with 0 tasks -> draft"
    for r in rows:
        before = r["status"]
        # force=True: `planned -> draft` is off the normal edge whitelist on
        # purpose. This one-shot repair is the sanctioned exception and it is
        # still fully audited (set_goal_status writes `goal_status`).
        out = store.set_goal_status(r["id"], "draft", detail=detail, force=True)
        _p("  goal #%-3d %-8s -> %-8s  %s"
           % (r["id"], before, out["status"], (r["title"] or "-")[:50]))
    _p("  %d goal(s) flipped." % len(rows))

    remaining = _backfill_candidates()
    _p("\nProof — re-running the selection query:")
    if remaining:
        _p("  STILL SELECTED (backfill did NOT fully apply):")
        for r in remaining:
            _p("    #%-3d [%s] %s" % (r["id"], r["status"], r["title"] or "-"))
        return 1
    _p("  0 rows — no 'planned' goal with zero tasks remains.")
    return 0


def cmd_task_create(args):
    # L1: fail fast with a readable roster instead of letting a typo'd
    # `--assignee` reach the dispatcher, which would hand it to
    # `hermes --profile <typo>` and die at launch. The engine revalidates —
    # this is only the friendlier CLI message.
    try:
        store.validate_assignee(args.assignee)
    except ValueError as e:
        _p("error: %s" % e)
        return 1
    if args.phased:
        plan_id, build_id = store.create_phased_tasks(
            args.project_slug, args.title, args.description or "",
            args.definition_of_done or "", assignee_profile=args.assignee,
            goal_id=args.goal, owner_approval=args.owner_approval)
        _p("Created phased tasks #%d (plan) and #%d (build) in project '%s'"
           % (plan_id, build_id, args.project_slug))
        return 0
    tid = store.create_task(
        args.project_slug, args.title, args.description or "",
        args.definition_of_done or "", assignee_profile=args.assignee,
        goal_id=args.goal, review_policy=args.review_policy,
        is_code=args.is_code, owner_approval=args.owner_approval)
    t = store.get_task(tid)
    _p("Created task #%d in project '%s'  [status=%s]"
       % (tid, args.project_slug, t["status"]))
    if t["status"] == "planned":
        _p("  Backlog: task will NOT run until released (goal release or "
           "`wm task mark-ready %d`)." % tid)
    elif t["status"] == "waiting_approval":
        _p("  Under an approved goal: will run automatically once deps done.")


def cmd_task_list(args):
    rows = store.list_tasks(project_slug=args.project, status=args.status)
    if not rows:
        _p("No tasks%s%s."
           % (" for project '%s'" % args.project if args.project else "",
              " with status '%s'" % args.status if args.status else ""))
        return
    _p("Tasks%s%s:"
       % (" [project=%s]" % args.project if args.project else "",
          " [status=%s]" % args.status if args.status else ""))
    grouped = {}
    for r in rows:
        grouped.setdefault(r["status"], []).append(r)
    # Group by the actual statuses present, ordered by canonical order.
    canonical = [s for s, _ in TASK_GROUP_ORDER]
    for st in canonical:
        if st not in grouped:
            continue
        _p("\n  [%s] (%d)" % (st.upper(), len(grouped[st])))
        for r in grouped[st]:
            _p("    #%-3d %-32s proj=%-14s assignee=%s" %
               (r["id"], (r["title"] or "")[:32],
                r["project_slug"] or "-", r["assignee_profile"] or "-"))
    # Any status outside the canonical list (e.g. plain 'ready' handled in waiting)
    known = set(canonical)
    for st, entries in grouped.items():
        if st not in known:
            _p("\n  [%s] (%d)" % (st.upper(), len(entries)))
            for r in entries:
                _p("    #%-3d %-32s assignee=%s" %
                   (r["id"], (r["title"] or "")[:32],
                    r["assignee_profile"] or "-"))


def cmd_task_show(args):
    r = store.get_task(args.id)
    if not r:
        sys.exit("error: no task with id %s" % args.id)
    _p("Task #%d" % r["id"])
    _p("  project        : %s" % r["project_slug"])
    _p("  goal           : %s" % ("#%d %s" % (r["goal_id"], r["goal_title"])
                                  if r["goal_id"] else "-"))
    _p("  title          : %s" % r["title"])
    _p("  description    : %s" % (r["description"] or "-"))
    _p("  definition_done: %s" % (r["definition_of_done"] or "-"))
    _p("  status         : %s" % r["status"])
    _p("  assignee       : %s" % (r["assignee_profile"] or "-"))
    _p("  review_policy  : %s" % r["review_policy"])
    _p("  is_code        : %s" % ("yes" if r["is_code"] else "no"))
    _p("  result_path    : %s" % (r["result_path"] or "-"))
    _p("  result_paths   : %s" % (r["result_paths"] or "-"))
    _p("  summary        : %s" % (r["summary"] or "-"))
    goal = store.get_goal(r["goal_id"]) if r["goal_id"] else None
    _p("  goal_status    : %s" % (goal["status"] if goal else "- (no goal)"))
    _p("  released       : %s" % ("yes" if (goal and goal["status"] == "released") or r["status"] in ("ready","running","done","needs_review","rework","blocked","manual") else "no (planned/holding)"))
    latest = store.get_task_latest_run(r["id"])
    if latest and latest["workdir"]:
        _p("  last_run_workdir: %s%s" % (latest["workdir"],
                                         ("  branch=%s" % latest["branch"]) if latest["branch"] else ""))
    _p("  created        : %s" % r["created_at"])
    _p("  updated        : %s" % r["updated_at"])
    deps = store.list_task_deps(r["id"])
    _p("  depends_on     : %s" % (", ".join(str(d["depends_on_task_id"]) for d in deps) or "-"))
    _p("  deps_done      : %s" % store.deps_done(r["id"]))


def cmd_task_assign(args):
    t = store.get_task(args.id)
    if not t:
        sys.exit("error: no task with id %s" % args.id)
    try:
        store.assign_task(args.id, args.profile)
    except ValueError as e:
        _p("error: %s" % e)
        return 1
    if args.profile:
        _p("Assigned task #%d to '%s'" % (args.id, args.profile))
    else:
        _p("Unassigned task #%d (dispatcher will use its default assignee)"
           % args.id)


def cmd_task_mark_ready(args):
    t = store.get_task(args.id)
    if not t:
        sys.exit("error: no task with id %s" % args.id)
    try:
        store.mark_ready(args.id)
        _p("Task #%d ('%s') is now READY." % (args.id, t["title"]))
    except ValueError as e:
        _p("Refused: %s" % e)
        sys.exit(1)


def cmd_task_feedback(args):
    """Owner feedback: send a task back to `rework` with a written reason.

    The comment is threaded into the NEXT run's brief (render_brief -> OWNER
    FEEDBACK), so the assignee sees exactly what to fix.
    """
    t = store.get_task(args.id)
    if not t:
        _p("error: no task with id %s" % args.id)
        return 1
    try:
        status, feedback, closed_review, demoted = store.owner_feedback(
            args.id, args.comment)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Task #%d ('%s') -> %s (was %s) — sent back by the owner."
       % (args.id, t["title"] or "-", status.upper(), t["status"]))
    _p("  feedback: %s" % feedback)
    if demoted:
        _p("  demoted dependents back to waiting_approval (they gated on this "
           "task): %s" % ", ".join("#%d" % d for d in demoted))
    if closed_review is not None:
        _p("  open review #%d closed `changes_requested` (no Reviewer run will "
           "be dispatched for it)." % closed_review)
    _p("  Recorded in the state-transition ledger; the NEXT run's brief carries "
       "it under 'OWNER FEEDBACK'.")
    return 0


def cmd_task_depend(args):
    if not store.get_task(args.id) or not store.get_task(args.depends_on_id):
        sys.exit("error: task id(s) not found")
    store.add_task_dep(args.id, args.depends_on_id)
    _p("Added dependency: task #%d depends on task #%d"
       % (args.id, args.depends_on_id))


def cmd_task_undepend(args):
    if not store.get_task(args.id):
        sys.exit("error: no task with id %s" % args.id)
    if not store.remove_task_dep(args.id, args.depends_on_id):
        _p("No such dependency: task #%d does not depend on task #%d"
           % (args.id, args.depends_on_id))
        return 1
    t = store.get_task(args.id)
    _p("Removed dependency: task #%d no longer depends on task #%d%s"
       % (args.id, args.depends_on_id,
          " — task promoted to READY (released goal, all deps done)"
          if t["status"] == "ready" else ""))
    return 0


def cmd_task_close(args):
    if not args.by_owner:
        sys.exit("error: only --by-owner closes exist (agent closes go "
                 "through runs and reviews)")
    t = store.get_task(args.id)
    if not t:
        sys.exit("error: no task with id %s" % args.id)
    try:
        promoted = store.close_by_owner(args.id, note=args.comment)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Task #%d ('%s') closed by owner -> done. Promoted dependents: %s"
       % (args.id, t["title"] or "-",
          ", ".join(str(p) for p in promoted) or "-"))
    return 0


def cmd_task_edit(args):
    try:
        changed = store.edit_task(args.id, description=args.description,
                                  definition_of_done=args.dod,
                                  owner_approval=args.owner_approval)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    if not changed:
        _p("No change: the given text matches the current fields.")
        return 0
    _p("Task #%d edited (%s) — audited; the next run's brief carries the new "
       "text." % (args.id, ", ".join(changed)))
    return 0


def cmd_goal_delete(args):
    try:
        title = store.delete_goal(args.id)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Draft goal #%d ('%s') deleted (audited)." % (args.id, title or "-"))
    return 0


def cmd_dispatch(args):
    # T2: manual trigger runs the SAME dispatcher the cron auto-tick uses
    # (single-flight lock prevents overlap — see wm_dispatch.run_dispatch).
    import wm_dispatch
    summary = wm_dispatch.run_dispatch()
    if summary.get("skipped"):
        _p("wm dispatch: another tick is already running (single-flight lock) — "
           "this tick skipped. No duplicate work.")
        return 0
    if summary["paused"]:
        _p("wm dispatch: paused — no work dispatched.")
        return 0
    if summary["promoted"]:
        _p("Promoted to ready (released, deps done): %s"
           % ", ".join(str(i) for i in summary["promoted"]))
    if summary["dispatched"]:
        _p("Dispatched runs: %s" % ", ".join(str(i) for i in summary["dispatched"]))
    else:
        _p("No ready tasks dispatched. (running=%d, cap=%d, ready_candidates=%d)"
           % (summary["running_count"], summary["cap"],
              summary["ready_candidates"]))
    if summary["reviews_dispatched"]:
        _p("Dispatched review runs: %s"
           % ", ".join(str(i) for i in summary["reviews_dispatched"]))
    if summary["stalled"]:
        _p("Stalled runs (marked failed): %s"
           % ", ".join(str(i) for i in summary["stalled"]))
    if summary.get("backup"):
        _p("db backup written: %s" % summary["backup"])
    for e in summary["errors"]:
        _p("error: %s" % e)
    return 0


def cmd_session(args):
    """T3: print a task's latest real session_id + a working --resume command."""
    t = store.get_task(args.id)
    if not t:
        sys.exit("error: no task with id %s" % args.id)
    run = store.get_task_last_run(args.id)
    if not run or not run["session_id"]:
        _p("No captured session id yet for task #%s."
           % args.id)
        return 0
    sid = run["session_id"]
    agent = run["agent_profile"] or t["assignee_profile"]
    _p("Task #%s ('%s')" % (t["id"], t["title"] or "-"))
    _p("  session_id : %s" % sid)
    _p("  resume     : %s" % store.get_resume_command(agent, sid))
    return 0


def cmd_review(args):
    """Record a Reviewer verdict on the task's auto-created review (T5)."""
    t = store.get_task(args.id)
    if not t:
        _p("error: no task with id %s" % args.id)
        return 1
    try:
        if getattr(args, "close_orphan", False):
            rid = store.close_orphan_review(args.id, comment=args.comment)
            _p("Review #%d (task #%d) closed as orphaned — task stays done, "
               "verdict recorded 'waived'." % (rid, t["id"]))
            return 0
        if args.waive:
            ts, rs, prom = store.waive_review(args.id, comment=args.comment)
        else:
            if not args.verdict:
                _p("error: --verdict approved|changes_requested (or --waive) "
                   "is required")
                return 1
            ts, rs, prom = store.review_verdict(args.id, args.verdict,
                                                comment=args.comment)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Task #%d ('%s') -> %s  [review -> %s]. Promoted dependents: %s"
       % (t["id"], t["title"] or "-", ts, rs,
          ", ".join(str(p) for p in prom) or "-"))
    return 0


def cmd_reviews(args):
    """List review rows (newest first), optionally for one task."""
    rows = store.list_reviews(task_id=args.task)
    if not rows:
        _p("No reviews%s." % (" for task %d" % args.task if args.task else ""))
        return 0
    _p("Reviews (auto-created; SINGLE review model — never hand-created):")
    for r in rows:
        _p("  #%-3d task=%-4d [%-16s] policy=%-8s verdict=%-17s %s"
           % (r["id"], r["task_id"], r["status"] or "-",
              r["review_policy"] or "-", r["verdict"] or "-",
              (r["task_title"] or "")[:30]))
        if r["comments"]:
            _p("        comments: %s" % r["comments"])
    return 0


# ---------------------------------------------------------------------------
# Second Brain `wm note` group (Phase 2a) — the LIBRARIAN'S surface.
# Reads (inbox/show/areas/tags/proposals) orient the librarian; the only
# writes are the propose-* verbs (file/split/contradiction/task), which touch the proposals table
# alone. There is deliberately NO note-writing command here: notes change
# only when the owner approves a proposal in the dashboard.
# ---------------------------------------------------------------------------
def _note_line(n, pending_ids):
    tags = (" [%s]" % ", ".join(n["tags"])) if n.get("tags") else ""
    mark = "  (pending proposal — skip)" if n["id"] in pending_ids else ""
    return "  #%-4d %s%s%s" % (n["id"], n["title"], tags, mark)


def cmd_note_inbox(args):
    notes = store.list_notes(status="inbox", limit=args.limit)
    if not notes:
        _p("Inbox is empty — nothing to triage.")
        return 0
    pending = {p["note_id"] for p in store.list_proposals(status="pending", limit=500)}
    _p("Inbox notes (%d):" % len(notes))
    for n in notes:
        _p(_note_line(n, pending))
        if args.full:
            full = store.get_note(n["id"])
            for line in (full["body"] or "").splitlines():
                _p("      | " + line)
    return 0


def cmd_note_show(args):
    n = store.get_note(args.id)
    if n is None:
        _p("error: no such note: %d" % args.id)
        return 1
    area = n.get("area")
    _p("Note #%d: %s" % (n["id"], n["title"]))
    _p("  type=%s status=%s authored_by=%s" % (n["type"], n["status"], n["authored_by"]))
    _p("  area: %s" % ((area["parent"] + " / " if area and area["parent"] else "")
                       + area["name"] if area else "-"))
    _p("  project: %s" % (n["project"]["slug"] if n.get("project") else "-"))
    _p("  tags: %s" % (", ".join(n["tags"]) or "-"))
    if n.get("disputed"):
        others = ["#%s" % l["target_id"] for l in (n.get("links") or [])
                  if l["kind"] == "note"]
        _p("  DISPUTED: conflicts with %s — the owner already adjudicated this "
           "pair; do not re-propose it" % (", ".join(others) or "another note"))
    _p("  body:")
    for line in (n["body"] or "").splitlines():
        _p("    | " + line)
    for e in n.get("entries") or []:
        _p("  entry #%d:" % e["id"])
        for line in e["body"].splitlines():
            _p("    | " + line)
    return 0


def cmd_note_areas(args):
    areas = store.list_areas()
    top = [a for a in areas if a["parent_id"] is None]
    kids = {}
    for a in areas:
        if a["parent_id"] is not None:
            kids.setdefault(a["parent_id"], []).append(a)
    _p("Areas (two-level; use the id in propose payloads):")
    for a in top:
        _p("  #%-3d %s" % (a["id"], a["name"]))
        for k in kids.get(a["id"], []):
            _p("      #%-3d %s" % (k["id"], k["name"]))
    return 0


def cmd_note_tags(args):
    tags = store.list_note_tags()
    if not tags:
        _p("No tags in use yet.")
        return 0
    _p("Tags in use (reuse these; coin new ones sparingly):")
    for t in tags:
        _p("  %-30s %d" % (t["tag"], t["count"]))
    return 0


def cmd_note_proposals(args):
    rows = store.list_proposals(status=args.status)
    if not rows:
        _p("No proposals%s." % (" with status %r" % args.status if args.status else ""))
        return 0
    for p in rows:
        _p("  #%-4d %-6s %-16s [%-15s] note #%s: %s"
           % (p["id"], p["kind"], p["status"], p["classification"],
              p["note_id"], (p["summary"] or "")[:60]))
        if p["feedback"]:
            _p("        owner feedback: %s" % p["feedback"])
    return 0


def _proposal_result(pid, summary):
    _p("Filed proposal #%d: %s" % (pid, summary or "-"))
    _p("The owner decides it in the dashboard review queue. Do NOT edit notes "
       "directly; if this proposal is rejected, read the feedback with "
       "`wm note proposals --status rejected` before re-proposing.")


def cmd_note_lint(args):
    """Deterministic Library hygiene report — the lint lane's read surface.
    Fix findings via proposals only (refile orphans, split oversized dumps,
    propose-file --archive stale junk); tag duplicates go to the owner."""
    rows = store.lint_library()
    if not rows:
        _p("Library lint: clean — no findings.")
        return 0
    _p("Library lint: %d finding(s)" % len(rows))
    for f in rows:
        ref = ("note #%s" % f["note_id"]) if f["note_id"] else "taxonomy"
        title = (" (%s)" % f["title"][:50]) if f.get("title") else ""
        _p("  [%-13s] %s%s: %s" % (f["check"], ref, title, f["detail"]))
    return 0


def cmd_note_propose_file(args):
    payload = {}
    if args.area_id is not None:
        payload["area_id"] = args.area_id
    if args.project:
        proj = store.get_project(slug=args.project)
        if proj is None:
            _p("error: no such project: %s" % args.project)
            return 1
        payload["project_id"] = proj["id"]
    if args.tags:
        payload["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.type:
        payload["type"] = args.type
    if args.archive:
        payload["archive"] = True
    if args.new_tags:
        payload["new_tags"] = [t.strip() for t in args.new_tags.split(",") if t.strip()]
    pid = store.create_proposal(
        "file", args.id, payload, summary=args.summary,
        classification="routine" if args.routine else "needs_attention")
    _proposal_result(pid, args.summary)
    return 0


def cmd_note_propose_contradiction(args):
    payload = {"other_note_id": args.other}
    if args.explain:
        payload["explanation"] = args.explain
    pid = store.create_proposal(
        "contradiction", args.id, payload, summary=args.summary,
        classification="needs_attention")     # always — enforced in the store too
    _proposal_result(pid, args.summary)
    return 0


def cmd_note_propose_task(args):
    payload = {"title": args.title}
    if args.desc:
        payload["description"] = args.desc
    if args.project:
        proj = store.get_project(slug=args.project)
        if proj is None:
            _p("error: no such project: %s" % args.project)
            return 1
        payload["project_id"] = proj["id"]
    if args.assignee:
        payload["assignee"] = args.assignee
    pid = store.create_proposal(
        "new_task", args.id, payload, summary=args.summary,
        classification="needs_attention")     # always — enforced in the store too
    _proposal_result(pid, args.summary)
    return 0


def cmd_note_propose_split(args):
    import json as _json
    raw = sys.stdin.read() if args.parts == "-" else open(args.parts).read()
    try:
        parts = _json.loads(raw)
    except ValueError as e:
        _p("error: parts file is not valid JSON: %s" % e)
        return 1
    if isinstance(parts, dict):
        parts = parts.get("parts")
    payload = {"parts": parts, "archive_original": not args.keep_original}
    if args.new_tags:
        payload["new_tags"] = [t.strip() for t in args.new_tags.split(",") if t.strip()]
    pid = store.create_proposal(
        "split", args.id, payload, summary=args.summary,
        classification="routine" if args.routine else "needs_attention")
    _proposal_result(pid, args.summary)
    return 0


def cmd_status(args):
    meta = {k: store.get_meta(k) for k in
            ("schema_version", "concurrency_cap", "stall_seconds", "paused")}
    _p("Work Manager status  (schema v%s | concurrency_cap=%s | "
       "stall_seconds=%s | paused=%s)"
       % (meta["schema_version"], meta["concurrency_cap"],
          meta["stall_seconds"], meta["paused"]))
    rows = store.list_tasks()
    if not rows:
        _p("\nNo tasks yet. Create a project + task to begin.")
        return
    grouped = {}
    for r in rows:
        grouped.setdefault(r["status"], []).append(r)

    def _header(st, label):
        lst = grouped.get(st, [])
        if not lst:
            return False
        _p("\n%s — %d: " % (label, len(lst)))
        return True

    def _emit(lst):
        for r in lst:
            _p("  #%-4d %-36s [proj=%s] assignee=%s%s"
               % (r["id"], (r["title"] or "")[:36],
                  r["project_slug"] or "-", r["assignee_profile"] or "-",
                  "  deps_incomplete" if r["status"] == "planned"
                  and not store.deps_done(r["id"]) and
                  store.list_task_deps(r["id"]) else ""))
            displayed.add(r["id"])

    displayed = set()

    presets = [("running", "RUNNING"), ("failed", "FAILED"),
               ("stalled", "STALLED"), ("blocked", "BLOCKED"),
               ("needs_review", "NEEDS REVIEW"), ("rework", "REWORK"),
               ("manual", "MANUAL"),
               ("waiting_approval", "WAITING FOR APPROVAL"),
               ("ready", "READY")]
    for st, label in presets:
        if _header(st, label):
            _emit(grouped[st])

    # Backlog bucket: `planned` = NOT released. These NEVER auto-run until
    # explicitly released (goal release or `wm task mark-ready`).
    planned = grouped.get("planned", [])
    if planned:
        _p("\nPLANNED (backlog — not released; will NOT auto-run) — %d:"
           % len(planned))
        for r in planned:
            _p("  #%-4d %-36s assignee=%s%s"
               % (r["id"], (r["title"] or "")[:36], r["assignee_profile"] or "-",
                  "  (deps pending)" if not store.deps_done(r["id"]) else ""))
            displayed.add(r["id"])

    if _header("done", "DONE"):
        _emit(grouped["done"])
    # Leftovers: rows whose status wasn't shown in any bucket above.
    leftovers = [r for r in rows if r["id"] not in displayed]
    if leftovers:
        _p("\nOTHER — %d:" % len(leftovers))
        _emit(leftovers)

    # T5: surface reviews (auto-created) + needs_review/rework already shown above.
    _reviews = store.list_reviews()
    if _reviews:
        open_rs = [r for r in _reviews if r["status"] in store.REVIEW_OPEN]
        _p("\nREVIEWS (auto-created — %d total, %d open):"
           % (len(_reviews), len(open_rs)))
        for r in open_rs:
            _p("  review #%-3d for task #%-4d [%-9s] policy=%s  %s"
               % (r["id"], r["task_id"], r["status"], r["review_policy"] or "-",
                  (r["task_title"] or "")[:36]))
        decided = [r for r in _reviews if r["status"] in store.REVIEW_FINAL]
        for r in reversed(decided[-3:]):
            v = r["verdict"] or r["status"]
            _p("  review #%-3d for task #%-4d [done] verdict=%s%s"
               % (r["id"], r["task_id"], v,
                  ("  (%s)" % r["comments"]) if r["comments"] else ""))


def cmd_pause(args):
    store.set_paused(True)
    _p("Paused. Dispatch will skip new work while paused=1.")


def cmd_resume(args):
    """`wm resume` (no id) unpauses dispatch; `wm resume <task_id>` prints the
    task's real `--resume` command so a human can continue that exact session.
    """
    if args.id is None:
        store.set_paused(False)
        _p("Resumed. Dispatch is active.")
        return 0
    return _cmd_resume_session(args)


def cmd_serve(args):
    """Run the private Work Manager dashboard server (`wm serve`)."""
    try:
        from wm_dash import server as dash_server
    except ImportError as e:
        _p("dashboard not deployed: %s" % e)
        return 1
    cfg = dash_server.DashConfig(host=getattr(args, "host", None),
                                 port=getattr(args, "port", None))
    dash_server.serve(cfg)
    return 0


def _cmd_resume_session(args):
    t = store.get_task(args.id)
    if not t:
        _p("error: no task with id %s" % args.id)
        return 1
    run = store.get_task_latest_run(args.id)
    if not run:
        _p("No run yet for task #%s." % args.id)
        return 0
    agent = run["agent_profile"] or t["assignee_profile"]
    # Prefer the recorded session_id; else resolve the run's live session via
    # its deterministic marker title (idle-hang/stalled runs may not yet have
    # captured the id).
    sid = run["session_id"]
    if not sid:
        found = store.get_run_session_activity(agent, run["id"])
        if found:
            sid = found["id"]
    if not sid:
        _p("No session located for task #%s (run #%s)."
           % (args.id, run["id"]))
        return 0
    _p("Task #%s ('%s') — run #%s [%s]"
       % (t["id"], t["title"] or "-", run["id"], run["status"] or "-"))
    _p("resume: %s" % store.get_resume_command(agent, sid))
    return 0


def cmd_retry(args):
    """Re-open a failed/stalled/blocked task to 'ready' (keeps old run)."""
    t = store.get_task(args.id)
    if not t:
        _p("error: no task with id %s" % args.id)
        return 1
    try:
        store.retry_task(args.id)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Task #%d ('%s') reopened to READY (old runs kept). Associated: "
       "a fresh run will be spawned on the next dispatch tick."
       % (args.id, t["title"] or "-"))
    return 0


def cmd_mark_manual(args):
    t = store.get_task(args.id)
    if not t:
        _p("error: no task with id %s" % args.id)
        return 1
    try:
        store.mark_manual(args.id, note=args.note)
    except ValueError as e:
        _p("Refused: %s" % e)
        return 1
    _p("Task #%d ('%s') marked MANUAL (out of the queue). History kept."
       % (args.id, t["title"] or "-"))
    return 0


def cmd_config_set(args):
    store.append_meta(args.key, args.value)
    _p("config %s = %s" % (args.key, args.value))
    return 0


def cmd_config_get(args):
    v = store.get_meta(args.key)
    _p("%s = %s" % (args.key, v if v is not None else "<unset>"))
    return 0


def cmd_backup(args):
    try:
        p = store.backup_db(backup_dir=args.dir)
    except ValueError as e:
        _p("error: %s" % e)
        return 1
    _p("Backup written: %s" % p)
    return 0


def cmd_check(args):
    res = store.check_integrity()
    if res["ok"]:
        _p("Integrity OK — no anomalies (tasks, reviews, runs all consistent).")
        return 0
    _p("INTEGRITY FINDINGS (%d):" % len(res["findings"]))
    for f in res["findings"]:
        _p("  - %s" % f)
    _p("These indicate state changed outside the sanctioned wm commands "
       "(possible raw-SQL tamper). Investigate before continuing.")
    return 1


def cmd_prune(args):
    try:
        counts = store.prune_history(retention_days=args.days,
                                     keep_transitions=not args.drop_transitions)
    except Exception as e:
        _p("error: %s" % e)
        return 1
    _p("Pruned (retention_days=%s): %s" % (args.days, counts))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="wm", description="Hermes Work Manager")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the schema").set_defaults(fn=cmd_init)

    # project
    proj = sub.add_parser("project", help="Project management")
    proj_sub = proj.add_subparsers(dest="sub", required=True)
    pc = proj_sub.add_parser("create")
    pc.add_argument("slug")
    pc.add_argument("--name", required=True)
    pc.add_argument("--description", default="")
    pc.add_argument("--path", dest="path", default="")
    pc.set_defaults(fn=cmd_project_create)
    proj_sub.add_parser("list").set_defaults(fn=cmd_project_list)
    ps = proj_sub.add_parser("show")
    ps.add_argument("slug")
    ps.set_defaults(fn=cmd_project_show)

    # goal
    goal = sub.add_parser("goal", help="Goal management")
    goal_sub = goal.add_subparsers(dest="sub", required=True)
    gc = goal_sub.add_parser("create")
    gc.add_argument("project_slug")
    gc.add_argument("title")
    gc.add_argument("description", nargs="?")
    gc.add_argument("acceptance_criteria", nargs="?")
    gc.set_defaults(fn=cmd_goal_create)
    gp = goal_sub.add_parser("plan", help="send a draft Goal to planning "
                                          "(parks an Orchestrator decomposition "
                                          "task in the backlog)")
    gp.add_argument("id", type=int)
    gp.set_defaults(fn=cmd_goal_plan)
    gd = goal_sub.add_parser("planned", help="decomposition agreed: planning -> "
                                             "planned (ready for release)")
    gd.add_argument("id", type=int)
    gd.set_defaults(fn=cmd_goal_planned)
    ga = goal_sub.add_parser("abandon",
                             help="abandon & re-plan: planning -> draft "
                                  "(closes the open planning task; the goal "
                                  "text becomes editable again)")
    ga.add_argument("id", type=int)
    ga.set_defaults(fn=cmd_goal_abandon)
    gdel = goal_sub.add_parser("delete",
                               help="delete a DRAFT goal nothing references "
                                    "(tasks/schedules must be repointed first; "
                                    "audited)")
    gdel.add_argument("id", type=int)
    gdel.set_defaults(fn=cmd_goal_delete)
    gr = goal_sub.add_parser("release", help="approve/release a Goal plan "
                                             "(eligible child tasks may then run)")
    gr.add_argument("id", type=int)
    gr.set_defaults(fn=cmd_goal_release)
    gs = goal_sub.add_parser("show")
    gs.add_argument("id", type=int)
    gs.set_defaults(fn=cmd_goal_show)
    gb = goal_sub.add_parser("backfill-draft",
                             help="one-shot Phase-6.5 fix: flip 'planned' goals "
                                  "that have ZERO tasks to 'draft' (dry-run by "
                                  "default)")
    gb.add_argument("--dry-run", dest="dry_run", action="store_true",
                    default=True, help="list the goals only (default)")
    gb.add_argument("--apply", dest="apply", action="store_true", default=False,
                    help="actually flip them (backs the DB up first)")
    gb.set_defaults(fn=cmd_goal_backfill_draft)

    # task
    task = sub.add_parser("task", help="Task management")
    task_sub = task.add_subparsers(dest="sub", required=True)
    tc = task_sub.add_parser("create")
    tc.add_argument("project_slug")
    tc.add_argument("title")
    tc.add_argument("description", nargs="?")
    tc.add_argument("definition_of_done", nargs="?")
    tc.add_argument("--assignee", default=None,
                    choices=list(store.ASSIGNABLE),
                    help="one of: %s (omit for unassigned; 'owner' = the "
                         "human's own todo, never dispatched)"
                         % ", ".join(store.ASSIGNABLE))
    tc.add_argument("--goal", type=int, default=None)
    tc.add_argument("--is-code", action="store_true",
                    help="mark this as a CODE task: runs get their own git "
                         "worktree/branch so retries never clobber other runs")
    tc.add_argument("--review-policy", dest="review_policy", default="none",
                    choices=store.REVIEW_POLICIES)
    tc.add_argument("--owner-approval", dest="owner_approval",
                    action="store_true",
                    help="approval gate: completions land on 'manual' "
                         "(Awaiting approval) for the owner instead of done")
    tc.add_argument("--phased", action="store_true",
                    help="create linked plan and build tasks in one action")
    tc.set_defaults(fn=cmd_task_create)
    tl = task_sub.add_parser("list")
    tl.add_argument("--project", default=None)
    tl.add_argument("--status", default=None, choices=store.TASK_STATUSES)
    tl.set_defaults(fn=cmd_task_list)
    ts = task_sub.add_parser("show")
    ts.add_argument("id", type=int)
    ts.set_defaults(fn=cmd_task_show)
    ta = task_sub.add_parser("assign")
    ta.add_argument("id", type=int)
    # L1: same gate as `task create --assignee` — the stored value is what the
    # dispatcher hands to `hermes --profile <p>`, so a typo must die here, not
    # at launch. "" is kept as the explicit "unassign" spelling (engine parity:
    # validate_assignee maps empty -> NULL).
    ta.add_argument("profile", metavar="profile",
                    choices=list(store.ASSIGNABLE) + [""],
                    help="one of: %s (or '' to unassign)"
                         % ", ".join(store.ASSIGNABLE))
    ta.set_defaults(fn=cmd_task_assign)
    mr = task_sub.add_parser("mark-ready", help="Mark a task ready (deps must be done)")
    mr.add_argument("id", type=int)
    mr.set_defaults(fn=cmd_task_mark_ready)
    tf = task_sub.add_parser("feedback",
                             help="owner feedback: send a needs_review/rework/"
                                  "done task back to `rework` with a written "
                                  "reason (threaded into the next run's brief)")
    tf.add_argument("id", type=int)
    tf.add_argument("--comment", required=True,
                    help="what the assignee must fix (required)")
    tf.set_defaults(fn=cmd_task_feedback)
    td = task_sub.add_parser("depend", help="task <id> depends on <depends_on_id>")
    td.add_argument("id", type=int)
    td.add_argument("depends_on_id", type=int)
    td.set_defaults(fn=cmd_task_depend)
    tu = task_sub.add_parser("undepend",
                             help="remove one dependency edge (repoint = "
                                  "undepend + depend); re-checks the task's "
                                  "release eligibility")
    tu.add_argument("id", type=int)
    tu.add_argument("depends_on_id", type=int)
    tu.set_defaults(fn=cmd_task_undepend)
    tc = task_sub.add_parser("close",
                             help="owner-close a 'manual' task as done "
                                  "(audited; satisfies dependents' deps)")
    tc.add_argument("id", type=int)
    tc.add_argument("--by-owner", action="store_true", dest="by_owner",
                    help="required: the owner declares the work finished "
                         "outside WM runs")
    tc.add_argument("--comment", "-c", dest="comment", default=None)
    tc.set_defaults(fn=cmd_task_close)
    te = task_sub.add_parser("edit",
                             help="audited edit of description/definition-of-"
                                  "done (refused while running or done)")
    te.add_argument("id", type=int)
    te.add_argument("--description", dest="description", default=None)
    te.add_argument("--dod", dest="dod", default=None,
                    help="new definition of done")
    te.add_argument("--owner-approval", dest="owner_approval", default=None,
                    type=int, choices=(0, 1),
                    help="set (1) or clear (0) the owner-approval gate")
    te.set_defaults(fn=cmd_task_edit)

    sub.add_parser("status", help="Grouped text readout").set_defaults(
        fn=cmd_status)
    sub.add_parser("pause").set_defaults(fn=cmd_pause)
    res_add = sub.add_parser("resume",
                             help="unpause dispatch (no id) OR print a task's "
                                  "--resume command (wm resume <task_id>)")
    res_add.add_argument("id", nargs="?", type=int, default=None,
                         help="task id: print that task's resume command")
    res_add.set_defaults(fn=cmd_resume)
    rtry = sub.add_parser("retry",
                          help="re-open a failed/stalled/blocked task to ready "
                               "(keeps old runs; next tick spawns the fresh run)")
    rtry.add_argument("id", type=int)
    rtry.set_defaults(fn=cmd_retry)
    mark = sub.add_parser("mark",
                          help="acknowledge a stuck task and take it out of the queue")
    mark_sub = mark.add_subparsers(dest="sub", required=True)
    mman = mark_sub.add_parser("manual")
    mman.add_argument("id", type=int)
    mman.add_argument("note", nargs="?", default=None)
    mman.set_defaults(fn=cmd_mark_manual)
    cfg = sub.add_parser("config",
                         help="get/set work-manager config (wm_meta)")
    cfg_sub = cfg.add_subparsers(dest="sub", required=True)
    cset = cfg_sub.add_parser("set")
    cset.add_argument("key")
    cset.add_argument("value")
    cset.set_defaults(fn=cmd_config_set)
    cget = cfg_sub.add_parser("get")
    cget.add_argument("key")
    cget.set_defaults(fn=cmd_config_get)
    sub.add_parser("dispatch", help="Run the dispatcher immediately "
                                    "(identical logic to the cron auto-tick)"
                  ).set_defaults(fn=cmd_dispatch)
    sess = sub.add_parser("session",
                          help="print a task's real session id + resume command")
    sess.add_argument("id", type=int)
    sess.set_defaults(fn=cmd_session)

    rv = sub.add_parser("review",
                        help="record a Reviewer verdict on a task's auto-created "
                             "review (approved -> done; changes_requested -> rework)")
    rv.add_argument("id", type=int)
    rv.add_argument("--verdict", dest="verdict",
                    choices=["approved", "changes_requested"], default=None)
    rv.add_argument("--waive", action="store_true",
                    help="waive an optional-policy review (non-blocking -> done)")
    rv.add_argument("--close-orphan", action="store_true", dest="close_orphan",
                    help="close a review left open on an already-done task "
                         "(audited; the task is untouched)")
    rv.add_argument("--comment", "-c", dest="comment", default=None)
    rv.set_defaults(fn=cmd_review)
    rl = sub.add_parser("reviews", help="list review rows (auto-created)")
    rl.add_argument("--task", type=int, default=None)
    rl.set_defaults(fn=cmd_reviews)

    note = sub.add_parser("note", help="Second Brain: read notes, file librarian "
                                       "proposals (notes themselves are owner-only)")
    note_sub = note.add_subparsers(dest="sub", required=True)
    ni = note_sub.add_parser("inbox", help="untriaged captures (the ingest worklist)")
    ni.add_argument("--full", action="store_true", help="print full bodies")
    ni.add_argument("--limit", type=int, default=100)
    ni.set_defaults(fn=cmd_note_inbox)
    ns = note_sub.add_parser("show", help="one note with body + entries")
    ns.add_argument("id", type=int)
    ns.set_defaults(fn=cmd_note_show)
    note_sub.add_parser("areas", help="area tree with ids").set_defaults(fn=cmd_note_areas)
    note_sub.add_parser("tags", help="the closed tag taxonomy with in-use counts "
                                     "(propose only these; coin via --new-tags)"
                        ).set_defaults(fn=cmd_note_tags)
    note_sub.add_parser("lint", help="deterministic Library hygiene report "
                                     "(fix findings via proposals only)"
                        ).set_defaults(fn=cmd_note_lint)
    np_ = note_sub.add_parser("proposals", help="list proposals (rejected ones "
                                               "carry owner feedback — read it)")
    np_.add_argument("--status", default=None,
                     choices=["pending", "approved", "rejected", "superseded"])
    np_.set_defaults(fn=cmd_note_proposals)
    pf = note_sub.add_parser("propose-file",
                             help="propose filing a note (area/project/tags/type); "
                                  "the owner approves it in the review queue")
    pf.add_argument("id", type=int)
    pf.add_argument("--area-id", dest="area_id", type=int, default=None)
    pf.add_argument("--project", default=None, help="project slug")
    pf.add_argument("--tags", default=None, help="comma-separated")
    pf.add_argument("--type", default=None, choices=["note", "playbook", "wiki"])
    pf.add_argument("--summary", required=True,
                    help="one line the owner reads first — say WHY this filing")
    pf.add_argument("--routine", action="store_true",
                    help="classify routine (owner can bulk-approve); default "
                         "needs_attention")
    pf.add_argument("--archive", action="store_true",
                    help="junk/museum capture: file it straight to Archive "
                         "(searchable, reversible) instead of the Library")
    pf.add_argument("--new-tags", dest="new_tags", default=None,
                    help="comma-separated coinage declaration: tags used here "
                         "that are NOT yet in the taxonomy (owner approval "
                         "registers them)")
    pf.set_defaults(fn=cmd_note_propose_file)
    pc = note_sub.add_parser("propose-contradiction",
                             help="two notes disagree: propose flagging BOTH "
                                  "disputed (keep-both — never reconcile them "
                                  "yourself)")
    pc.add_argument("id", type=int)
    pc.add_argument("--other", type=int, required=True,
                    help="the note id this one contradicts")
    pc.add_argument("--explain", default=None,
                    help="what disagrees with what (the owner reads this)")
    pc.add_argument("--summary", required=True)
    pc.set_defaults(fn=cmd_note_propose_contradiction)
    pt = note_sub.add_parser("propose-task",
                             help="a note describes real work: propose creating "
                                  "a linked HQ task (the note stays a note)")
    pt.add_argument("id", type=int)
    pt.add_argument("--title", required=True)
    pt.add_argument("--desc", default=None, help="task description")
    pt.add_argument("--project", default=None,
                    help="project slug (default: the note's project)")
    pt.add_argument("--assignee", default=None,
                    help="assignee profile (default: owner — Kamran's own todo)")
    pt.add_argument("--summary", required=True)
    pt.set_defaults(fn=cmd_note_propose_task)
    psp = note_sub.add_parser("propose-split",
                              help="propose splitting one capture into N notes; "
                                   "parts JSON from a file or '-' (stdin)")
    psp.add_argument("id", type=int)
    psp.add_argument("--parts", required=True,
                     help="path to JSON list of parts ('-' = stdin): "
                          '[{"title", "body", "area_id"?, "project_id"?, '
                          '"tags"?, "type"?}, ...]')
    psp.add_argument("--summary", required=True)
    psp.add_argument("--routine", action="store_true")
    psp.add_argument("--keep-original", action="store_true", dest="keep_original",
                     help="do not archive the source note on approval")
    psp.add_argument("--new-tags", dest="new_tags", default=None,
                     help="comma-separated coinage declaration for tags used "
                          "in the parts but not yet in the taxonomy")
    psp.set_defaults(fn=cmd_note_propose_split)
    bk = sub.add_parser("backup", help="write an online backup of wm.db")
    bk.add_argument("--dir", dest="dir", default=None)
    bk.set_defaults(fn=cmd_backup)
    ck = sub.add_parser("check", help="run the DB-consistency / tamper audit")
    ck.set_defaults(fn=cmd_check)
    pr = sub.add_parser("prune", help="retention cleanup of activity/old run files "
                                      "(keeps task/project history)")
    pr.add_argument("--days", dest="days", type=int, default=None)
    pr.add_argument("--drop-transitions", action="store_true",
                    help="also prune old state_transitions (default: keep them)")
    pr.set_defaults(fn=cmd_prune)

    srv = sub.add_parser("serve", help="run the private dashboard (PWA) server")
    srv.add_argument("--host", dest="host", default=None)
    srv.add_argument("--port", dest="port", default=None, type=int)
    srv.set_defaults(fn=cmd_serve)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = getattr(args, "fn", None)
    if fn is None:
        build_parser().print_help()
        return 1
    try:
        rc = fn(args)
    except ValueError as e:
        _p("error: %s" % e)
        return 1
    return rc if rc is not None else 0


if __name__ == "__main__":
    sys.exit(main())