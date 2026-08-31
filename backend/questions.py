"""Group 10: mid-run question detection for dispatched runs.

Dispatched runs are one-shot `hermes chat -Q` processes: when an agent needs the
owner mid-run it can only write the question into its own transcript (the brief
teaches it the ```hq-options fence — see wm_store._ASK_OWNER_LINES). This module
is the other half: on every dispatcher tick, scan the transcripts of running
runs for NEW assistant messages and turn questions into 'question' notifications
(Inbox + the existing Web Push pipeline), so the owner's phone pings while the
run is still alive and an answer can be steered into the session.

Rules (owner decisions 2026-08-31):
- an ```hq-options fenced block => one notification PER MESSAGE
  (source_key runq:<run>:<message_id>; body = the parsed question);
- no fence but the message's last non-empty line ends with "?" => at most ONE
  softer notification per run (source_key runq:<run>:heuristic) — the heuristic
  is deliberately noisy-capped;
- per-run watermark (last seen message id) lives in wm_meta under
  `question_scan_watermarks` as one JSON object, rewritten each scan with only
  the currently-running runs => finished runs are pruned automatically;
- the scan reads a foreign profile state.db READ-ONLY (uri mode=ro, bounded
  batch, content cap) and must never break the tick: every per-run failure is
  logged and skipped.
"""
import json
import logging
import os
import re
import sqlite3

from core import wm_store as store

log = logging.getLogger("backend.questions")

META_KEY = "question_scan_watermarks"
FENCE_RE = re.compile(r"```hq-options\s*\n(.*?)```", re.S)
_MAX_MSG_CHARS = 20000   # bound what we regex per message
_MAX_MSGS_PER_SCAN = 200  # bound one run's catch-up batch per tick
_MAX_BODY = 500


def _connect_ro(path):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def _session_for(con, run):
    """The run's session id: captured id, else the wm-run-<id> marker title."""
    if run["session_id"]:
        return run["session_id"]
    row = con.execute(
        "SELECT id FROM sessions WHERE title=? ORDER BY started_at DESC LIMIT 1",
        ("wm-run-%s" % run["id"],)).fetchone()
    return row["id"] if row else None


def _visible_texts(m):
    """All VISIBLE assistant text of one message row. `content` is empty when a
    message carries tool calls; codex-style models keep the pre-tool reply text
    in `codex_message_items` output_text entries instead (observed live on the
    coder profile) — both are the agent's deliberate reply text. Reasoning
    channels are internal and deliberately NOT read."""
    out = [(m["content"] or "")[:_MAX_MSG_CHARS]]
    if "codex_message_items" in m.keys() and m["codex_message_items"]:
        try:
            for item in json.loads(m["codex_message_items"]):
                for c in (item.get("content") or []):
                    if c.get("type") == "output_text" and c.get("text"):
                        out.append(c["text"][:_MAX_MSG_CHARS])
        except Exception:
            pass
    return [t for t in out if t]


def _fence_question(text):
    """The question string from an hq-options fence, or None (no/garbled fence
    is reported by the caller with a generic body — a fence is always intent)."""
    m = FENCE_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
        q = d.get("question")
        return q.strip() if isinstance(q, str) and q.strip() else ""
    except Exception:
        return ""


def _scan_one(run, marks, db_path):
    """Scan one running run's new assistant messages. Returns (last_seen_msg_id,
    [new notification ids])."""
    rid = run["id"]
    last = int(marks.get(rid, 0))
    dbp = store.agent_session_db_path(run["agent_profile"])
    if not dbp or not os.path.exists(dbp):
        return last, []
    con = _connect_ro(dbp)
    try:
        sid = _session_for(con, run)
        if not sid:
            return last, []
        rows = con.execute(
            "SELECT * FROM messages WHERE session_id=? AND id>? "
            "AND (active IS NULL OR active=1) ORDER BY id ASC LIMIT ?",
            (sid, last, _MAX_MSGS_PER_SCAN)).fetchall()
    finally:
        con.close()
    ids = []
    href = "/chat/%s/%s" % (run["agent_profile"], sid)
    for m in rows:
        last = max(last, m["id"])
        if m["role"] != "assistant":
            continue
        text = "\n".join(_visible_texts(m))[:_MAX_MSG_CHARS]
        fence = FENCE_RE.search(text)
        if fence:
            q = _fence_question(text) or "Question details are in the session."
            nid = store.add_notification(
                "question", "%s asked in run #%s" % (run["agent_profile"], rid),
                q[:_MAX_BODY], href, task_id=run["task_id"], run_id=rid,
                source_key="runq:%s:%s" % (rid, m["id"]), db_path=db_path)
        else:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not (lines and lines[-1].endswith("?")):
                continue
            nid = store.add_notification(
                "question", "%s may need you (run #%s)" % (run["agent_profile"], rid),
                lines[-1][:_MAX_BODY], href, task_id=run["task_id"], run_id=rid,
                source_key="runq:%s:heuristic" % rid, db_path=db_path)
        if nid:
            ids.append(nid)
    return last, ids


def scan_running_runs(db_path=None):
    """One pass over all running runs. Returns the count of NEW notifications.

    Called from the dispatcher tick (wrapped there too); new rows are pushed
    immediately through the existing Web Push pipeline (sync_and_push only
    covers transition-derived rows, so question rows push themselves — same
    pattern as chat turn notifications)."""
    runs = store.running_runs(db_path=db_path)
    raw = store.get_meta(META_KEY, db_path=db_path)
    try:
        marks = {int(k): int(v) for k, v in (json.loads(raw) or {}).items()} if raw else {}
    except Exception:
        marks = {}
    new_marks, new_ids = {}, []
    for run in runs:
        try:
            last, ids = _scan_one(run, marks, db_path)
            new_ids.extend(ids)
        except Exception:
            log.exception("question scan failed for run %s", run["id"])
            last = int(marks.get(run["id"], 0))
        new_marks[run["id"]] = last
    # rewriting with only running runs prunes finished ones
    store._set_meta(META_KEY, json.dumps(new_marks), db_path=db_path)
    if new_ids:
        try:
            from backend import push
            push.push_notifications(new_ids, db_path=db_path)
        except Exception:
            log.exception("pushing question notifications failed")
    return len(new_ids)
