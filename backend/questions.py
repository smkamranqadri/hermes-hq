"""Group 10: mid-run question detection for dispatched runs.

Dispatched runs are one-shot `hermes chat -Q` processes: when an agent needs the
owner mid-run it can only write the question into its own transcript (the brief
teaches it the ```hq-options fence — see wm_store._ASK_OWNER_LINES). This module
is the other half: on every dispatcher tick, scan the transcripts of running
runs for NEW assistant messages and turn questions into 'question' notifications
(Inbox + the existing Web Push pipeline), so the owner's phone pings while the
run is still alive and an answer can be delivered via the answer file
(wm_store.answer_path(run_id) — see writes.run_answer and the brief's
ASKING THE OWNER section).

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
# An UNTERMINATED fence still counts. Observed live 2026-09-07 (run 453): the
# coder emitted a complete hq-options block whose closing delimiter was stored
# as ``` minus a backtick, so a strict regex found nothing, no notification
# fired, and the agent polled its answer file until the run died — the exact
# failure this loop exists to prevent. The JSON parse is the real validator.
FENCE_RE = re.compile(r"```hq-options\s*\n(.*?)(?:```|\Z)", re.S)
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


def _fence_body(match):
    """The JSON body of a fence match, minus any partial closing delimiter."""
    return match.group(1).strip().rstrip("`").strip()


def _fence_question(text):
    """The question string from an hq-options fence, or None (no/garbled fence
    is reported by the caller with a generic body — a fence is always intent)."""
    m = FENCE_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(_fence_body(m))
        q = d.get("question")
        return q.strip() if isinstance(q, str) and q.strip() else ""
    except Exception:
        return ""


def _fence_payload(text):
    """The full parsed hq-options payload, or None. Options are normalised to
    {label, detail} dicts and capped — the UI renders one button per option."""
    m = FENCE_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(_fence_body(m))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    q = d.get("question")
    opts = []
    for o in (d.get("options") or [])[:6]:
        if isinstance(o, str) and o.strip():
            opts.append({"label": o.strip()[:120], "detail": ""})
        elif isinstance(o, dict) and str(o.get("label") or "").strip():
            opts.append({"label": str(o["label"]).strip()[:120],
                         "detail": str(o.get("detail") or "").strip()[:300]})
    return {"question": (q.strip() if isinstance(q, str) else "")[:_MAX_BODY],
            "mode": "multi" if d.get("mode") == "multi" else "single",
            "options": opts}


def latest_question(run, db_path=None):
    """The newest hq-options question asked by this run, or None.

    Re-parsed from the run's own transcript on demand (read-only) rather than
    persisted: the transcript is the single source of truth, and the scanner's
    notification only ever carried a text body. Never raises — a missing or
    unreadable session db just means "no question to show".
    """
    dbp = store.agent_session_db_path(run["agent_profile"])
    if not dbp or not os.path.exists(dbp):
        return None
    try:
        con = _connect_ro(dbp)
    except Exception:
        return None
    try:
        sid = _session_for(con, run)
        if not sid:
            return None
        rows = con.execute(
            "SELECT * FROM messages WHERE session_id=? AND role='assistant' "
            "AND (active IS NULL OR active=1) ORDER BY id DESC LIMIT ?",
            (sid, _MAX_MSGS_PER_SCAN)).fetchall()
    except Exception:
        return None
    finally:
        con.close()
    for m in rows:                       # newest first — first fence wins
        payload = _fence_payload("\n".join(_visible_texts(m))[:_MAX_MSG_CHARS])
        if payload:
            payload["message_id"] = m["id"]
            return payload
    return None


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


def awaiting_answer(con, run_ids):
    """{run_id: question_text} for runs that asked the owner and are unanswered.

    The BOARD-level signal. `latest_question` opens a foreign profile state.db
    and is far too expensive per list row; this reads the notification the scan
    already wrote (its body IS the parsed question) plus one stat of the answer
    file — cheap enough to run on every task in the list.

    Waiting means the newest `question` notification is NEWER than the answer
    file's mtime, so a second question asked after an earlier answer still
    counts. Read-only and total: any failure yields {} rather than breaking a
    list endpoint.

    Why this exists (2026-09-07): a run parked on the owner's answer classified
    as plain "Working", identical to a run making progress — the owner had no
    way to know he was being asked anything except the Inbox ping. That breaks
    the standing rule that anything awaiting the owner must show on the list
    row, the detail page AND the queue.
    """
    ids = [int(r) for r in run_ids if r]
    if not ids:
        return {}
    try:
        marks = ",".join("?" * len(ids))
        rows = con.execute(
            "SELECT n.run_id, n.ts, n.body FROM notifications n JOIN ("
            "  SELECT run_id, MAX(id) AS mid FROM notifications"
            "  WHERE kind='question' AND run_id IN (%s) GROUP BY run_id"
            ") m ON m.mid = n.id" % marks, ids).fetchall()
    except Exception:
        log.exception("awaiting_answer query failed")
        return {}
    out = {}
    for r in rows:
        try:
            rid = int(r["run_id"])
            asked_at = float(r["ts"] or 0)
            path = store.answer_path(rid)
            answered_at = os.path.getmtime(path) if os.path.exists(path) else 0.0
            if asked_at > answered_at:
                out[rid] = (r["body"] or "").strip() or None
        except Exception:
            log.exception("awaiting_answer check failed for run %s", r["run_id"])
    return out
