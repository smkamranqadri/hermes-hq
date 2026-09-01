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
