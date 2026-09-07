"""Engine status -> human state + reason. Single source of truth; the UI's
status.ts mirrors HUMAN_STATE and a test asserts they agree.

Human states (in display order): needsyou, working, queued, backlog, done.
"""

# Mirrors core.wm_store.REWORK_CAP_MARKER (this module stays dependency-free —
# a test asserts the two agree, same contract as HUMAN_STATE vs status.ts).
REWORK_CAP_MARKER = "[rework cap]"

# Mirrors core.wm_store.OWNER_ASSIGNEE (same dependency-free contract; a test
# asserts the two agree). A task assigned to the owner is a HUMAN todo: the
# dispatcher's claim/candidate predicates exclude it unconditionally, so the
# queue vocabulary ("Queued", "Handed over") lies about what happens next.
OWNER_ASSIGNEE = "owner"

HUMAN_STATE = {
    "planned": "backlog", "draft": "backlog",
    "ready": "queued", "rework": "queued", "waiting_approval": "queued",
    "running": "working", "needs_review": "working",
    "blocked": "needsyou", "failed": "needsyou", "stalled": "needsyou",
    "done": "done", "manual": "done",
}
ORDER = ("needsyou", "working", "queued", "backlog", "done")
LABEL = {"needsyou": "Needs you", "working": "Working", "queued": "Queued",
         "backlog": "Backlog", "done": "Done"}


def _get(row, key):
    """Field access that works for both dicts and sqlite3.Row."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def classify(task, deps=(), goal_status=None, last_run=None):
    """Return {state, reason, action}.

    task: row with 'status'; deps: iterable of rows with id/status for what this
    task waits on; goal_status: status of its goal or None; last_run: latest run
    row (for error text) or None.
    """
    st = task["status"]
    state = HUMAN_STATE.get(st, "backlog")
    reason, action, label = None, None, None
    gated = bool(_get(task, "owner_approval"))
    owner_task = _get(task, "assignee_profile") == OWNER_ASSIGNEE
    if st == "waiting_approval":
        unmet = [d for d in deps if d["status"] != "done"]
        if unmet:
            reason = "waiting on " + ", ".join("#%d" % d["id"] for d in unmet)
            if gated:
                # The owner's approval is pending too and can be given early;
                # once cleared, dependency completion promotes it normally.
                state, label = "needsyou", "Awaiting approval"
                reason = "awaiting your approval · then " + reason
        elif goal_status and goal_status != "released":
            state, reason, action = "needsyou", "goal not released", "release_goal"
        elif gated:
            state, reason, label = "needsyou", "awaiting your approval", \
                "Awaiting approval"
        else:
            reason = "promoting"
    elif st in ("ready", "rework") and gated:
        # The dispatcher refuses to claim gated tasks, so these would sit
        # "Queued" forever — surface the real blocker: the owner's approval.
        state, reason, label = "needsyou", "awaiting your approval", \
            "Awaiting approval"
    elif st in ("ready", "rework") and owner_task:
        # The owner's own todo. The dispatcher excludes the `owner` assignee
        # from both the claim UPDATE and next_ready_tasks, so "Queued" promises
        # a pickup that can never come — it reads as agent work in flight.
        state, label = "needsyou", "Yours to do"
    elif st == "rework":
        reason = "rework requested"
    elif st == "needs_review":
        reason = "reviewer checking"
    elif st == "planned":
        action = "mark_ready"
    elif st in ("blocked", "failed", "stalled"):
        reason = st
        err = (last_run or {}).get("error") if last_run else None
        if err:
            reason += ": " + err.strip().splitlines()[0][:120]
        action = "retry" if st in ("failed", "stalled") else "unblock"
    elif st == "manual":
        # Grouped/sorted under done, but the chip must not claim "Done": the
        # owner took it out of the queue — a distinct display label only.
        # An owner_approval task that landed here is DIFFERENT: it is waiting
        # on the owner's decision, so it surfaces under needsyou instead.
        is_plan = (_get(task, "assignee_profile") == "orchestrator" and
                   (_get(task, "title") or "").startswith("Plan goal #") and
                   _get(task, "goal_id") and goal_status == "planned")
        if is_plan:
            state, reason, action = "needsyou", "plan awaiting your approval", \
                "approve_plan"
        elif _get(task, "owner_approval"):
            state, reason, label = "needsyou", "awaiting your approval", \
                "Awaiting approval"
        elif str(_get(task, "feedback") or "").startswith(REWORK_CAP_MARKER):
            # The rework cap parked this — the owner did NOT shelve it, and
            # burying it under "done" is how a stuck loop goes unnoticed.
            state, label = "needsyou", "Needs you"
            reason = str(task["feedback"])[len(REWORK_CAP_MARKER):].strip()[:160]
        elif owner_task:
            # "Handed over" describes taking work OFF an agent. An owner task
            # was never on one — parking it under done hides an open todo,
            # which is exactly how the owner's Second Brain reminders got lost.
            state, label = "needsyou", "Yours to do"
        else:
            label = "Handed over"
    elif st not in HUMAN_STATE:
        reason = st
    out = {"state": state, "reason": reason, "action": action}
    if label:
        out["label"] = label
    return out
