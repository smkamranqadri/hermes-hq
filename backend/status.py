"""Engine status -> human state + reason. Single source of truth; the UI's
status.ts mirrors HUMAN_STATE and a test asserts they agree.

Human states (in display order): needsyou, working, queued, backlog, done.
"""

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
    if st == "waiting_approval":
        unmet = [d for d in deps if d["status"] != "done"]
        if unmet:
            reason = "waiting on " + ", ".join("#%d" % d["id"] for d in unmet)
        elif goal_status and goal_status != "released":
            state, reason, action = "needsyou", "goal not released", "release_goal"
        else:
            reason = "promoting"
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
        if _get(task, "owner_approval"):
            state, reason, label = "needsyou", "awaiting your approval", \
                "Awaiting approval"
        else:
            label = "Handed over"
    elif st not in HUMAN_STATE:
        reason = st
    out = {"state": state, "reason": reason, "action": action}
    if label:
        out["label"] = label
    return out
