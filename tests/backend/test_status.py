import json, os, re, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
from backend import status as hs


def t(status, **kw):
    return hs.classify({"status": status}, kw.get("deps", []), kw.get("goal"), kw.get("run"))


def test_plain_mapping():
    assert t("planned")["state"] == "backlog" and t("planned")["action"] == "mark_ready"
    assert t("ready")["state"] == "queued"
    assert t("running")["state"] == "working"
    assert t("needs_review") == {"state": "working", "reason": "reviewer checking", "action": None}
    assert t("done")["state"] == "done" and t("manual")["state"] == "done"
    assert t("manual")["label"] == "Handed over" and "label" not in t("done")


def test_owner_approval_manual_is_awaiting_approval():
    gated = hs.classify({"status": "manual", "owner_approval": 1}, [], None, None)
    assert gated["state"] == "needsyou"
    assert gated["label"] == "Awaiting approval"
    assert gated["reason"] == "awaiting your approval"
    plain = hs.classify({"status": "manual", "owner_approval": 0}, [], None, None)
    assert plain["state"] == "done" and plain["label"] == "Handed over"


def test_manual_plan_has_distinct_approve_plan_action():
    plan = hs.classify({"status": "manual", "owner_approval": 1,
                        "assignee_profile": "orchestrator",
                        "title": "Plan goal #29", "goal_id": 29},
                       [], "planned", None)
    assert plan["state"] == "needsyou"
    assert plan["action"] == "approve_plan"
    # a gated task that is actually running stays under working
    assert hs.classify({"status": "running", "owner_approval": 1}, [], None,
                       None)["state"] == "working"


def test_gated_queueable_states_need_you():
    """Gated ready/rework/waiting_approval surface the real blocker: the
    dispatcher refuses gated claims, so 'Queued' would be a silent dead end."""
    for st in ("ready", "rework"):
        r = hs.classify({"status": st, "owner_approval": 1}, [], "released", None)
        assert r["state"] == "needsyou", st
        assert r["label"] == "Awaiting approval"
        assert r["reason"] == "awaiting your approval"
    r = hs.classify({"status": "waiting_approval", "owner_approval": 1},
                    [{"id": 5, "status": "done"}], "released", None)
    assert r["state"] == "needsyou" and r["label"] == "Awaiting approval"
    # unmet deps: approval can be given early; the dep info is kept in reason
    r = hs.classify({"status": "waiting_approval", "owner_approval": 1},
                    [{"id": 5, "status": "running"}], "released", None)
    assert r["state"] == "needsyou"
    assert r["reason"] == "awaiting your approval · then waiting on #5"
    # an unreleased goal outranks the gate: release is the structural blocker
    r = hs.classify({"status": "waiting_approval", "owner_approval": 1},
                    [], "planned", None)
    assert r["state"] == "needsyou" and r["action"] == "release_goal"
    # non-gated behavior is byte-identical
    assert t("ready") == {"state": "queued", "reason": None, "action": None}
    assert t("rework") == {"state": "queued", "reason": "rework requested",
                           "action": None}


def test_waiting_approval_is_queued_or_needs_you():
    unmet = t("waiting_approval", deps=[{"id": 81, "status": "done"}, {"id": 83, "status": "running"}], goal="released")
    assert unmet == {"state": "queued", "reason": "waiting on #83", "action": None}
    held = t("waiting_approval", deps=[{"id": 81, "status": "done"}], goal="planned")
    assert held["state"] == "needsyou" and held["action"] == "release_goal"


def test_stuck_states_need_you_with_run_error():
    r = t("failed", run={"error": "HTTP 429 rate limited\nmore"})
    assert r["state"] == "needsyou" and r["reason"] == "failed: HTTP 429 rate limited" and r["action"] == "retry"
    assert t("blocked")["action"] == "unblock" and t("stalled")["action"] == "retry"


def test_ui_mirror_agrees():
    """frontend/src/status.ts must map every engine status to the same human state."""
    ts = open(os.path.join(ROOT, "frontend", "src", "status.ts")).read()
    for eng, human in hs.HUMAN_STATE.items():
        m = re.search(r"case '%s'[^\n]*state: '([a-z]+)'" % eng, ts)
        assert m, "status.ts has no case for %s" % eng
        assert m.group(1) == human, "%s: ts=%s py=%s" % (eng, m.group(1), human)
    # display-label parity: manual carries the distinct chip text in both layers
    assert re.search(r"case 'manual'[^\n]*label: 'Handed over'", ts)


def test_running_run_waiting_on_the_owner_is_not_just_working():
    """A run parked on the owner's answer looks identical to a healthy run —
    'Working' either way — so the Inbox ping was the only signal it was asking
    anything. Standing rule: anything awaiting the owner shows on the row too."""
    asked = hs.classify({"status": "running"}, [], None,
                        {"id": 9, "status": "running",
                         "awaiting_answer": "Which lifecycle should we use?"})
    assert asked["state"] == "needsyou"
    assert asked["label"] == "Asked you"
    assert asked["reason"] == "Which lifecycle should we use?"
    # a run that asked nothing is untouched, and so is a run with no row at all
    assert hs.classify({"status": "running"}, [], None,
                       {"id": 9, "status": "running"})["state"] == "working"
    assert hs.classify({"status": "running"}, [], None, None)["state"] == "working"
    # the question is the reason, so an essay gets bounded like every other one
    long = hs.classify({"status": "running"}, [], None,
                       {"id": 9, "awaiting_answer": "x" * 400})
    assert len(long["reason"]) == 160


def test_owner_assignee_mirrors_the_engine():
    """Same dependency-free contract as REWORK_CAP_MARKER: status.py keeps its
    own copy of the reserved human assignee, so it must equal the engine's."""
    from core import wm_store as store
    assert hs.OWNER_ASSIGNEE == store.OWNER_ASSIGNEE


def test_owner_todo_never_reads_as_agent_queue_work():
    """The dispatcher excludes the `owner` assignee from both the claim UPDATE
    and next_ready_tasks, so an owner todo in ready/rework can never be picked
    up — 'Queued' would promise a pickup that never comes."""
    for st in ("ready", "rework"):
        out = hs.classify({"status": st, "owner_approval": 0,
                           "assignee_profile": "owner"}, [], "released", None)
        assert out["state"] == "needsyou", st
        assert out["label"] == "Yours to do", st
        # the label says it; a "yours to do · yours to do" chip is noise
        assert out["reason"] is None, st
    # an agent task in the same statuses is untouched
    for st in ("ready", "rework"):
        out = hs.classify({"status": st, "owner_approval": 0,
                           "assignee_profile": "coder"}, [], "released", None)
        assert out["state"] == "queued", st
        assert "label" not in out, st


def test_owner_todo_on_manual_is_not_buried_under_done():
    """`manual` groups under done and reads 'Handed over' — which describes
    taking work OFF an agent. An owner task was never on one, so parking it
    under done hides an open todo (how the owner's reminders got lost)."""
    out = hs.classify({"status": "manual", "owner_approval": 0,
                       "assignee_profile": "owner", "title": "test",
                       "goal_id": None, "feedback": None}, [], None, None)
    assert out["state"] == "needsyou"
    assert out["label"] == "Yours to do" and out["reason"] is None
    # an agent task taken over by the owner still reads 'Handed over'
    agent = hs.classify({"status": "manual", "owner_approval": 0,
                         "assignee_profile": "coder", "title": "BUILD: x",
                         "goal_id": None, "feedback": None}, [], None, None)
    assert agent["state"] == "done" and agent["label"] == "Handed over"
    # a DONE owner task is genuinely done — the owner closed it
    closed = hs.classify({"status": "done", "owner_approval": 0,
                          "assignee_profile": "owner"}, [], None, None)
    assert closed["state"] == "done" and "label" not in closed
    # backlog stays backlog: a planned owner task is not yet on the desk
    planned = hs.classify({"status": "planned", "owner_approval": 0,
                           "assignee_profile": "owner"}, [], None, None)
    assert planned["state"] == "backlog"


def test_gated_outranks_owner_assignee():
    """Both are needsyou, but 'Awaiting approval' carries a concrete Approve
    action, so it must win over the generic owner-todo label."""
    out = hs.classify({"status": "ready", "owner_approval": 1,
                       "assignee_profile": "owner"}, [], "released", None)
    assert out["state"] == "needsyou" and out["label"] == "Awaiting approval"


def test_rework_cap_marker_mirrors_the_engine():
    """status.py is dependency-free by design, so its copy of the marker must
    be asserted equal to the engine's (same contract as HUMAN_STATE/status.ts)."""
    from core import wm_store as store
    assert hs.REWORK_CAP_MARKER == store.REWORK_CAP_MARKER


def test_capped_task_surfaces_as_needs_you_not_done():
    """A capped task parks in `manual`, which otherwise groups under done and
    reads 'Handed over' — burying the very loop the cap exists to surface."""
    from core import wm_store as store
    capped = {"status": "manual", "owner_approval": 0, "assignee_profile": "coder",
              "title": "BUILD: something", "goal_id": None,
              "feedback": "%s stopped after 4 rework cycles — decide the way "
                          "forward. Last reviewer comment: fix the count"
                          % store.REWORK_CAP_MARKER}
    out = hs.classify(capped, [], None, None)
    assert out["state"] == "needsyou" and out["label"] == "Needs you"
    assert "4 rework cycles" in out["reason"]
    shelved = dict(capped, feedback="owner parked this")
    assert hs.classify(shelved, [], None, None)["state"] == "done"
