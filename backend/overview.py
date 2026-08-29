"""Overview + activity aggregates. Real data only, derived from the same
human-state task query the Tasks page uses."""
import os
import time

from backend import tasks as tq
from backend.readers import connect_ro, _fetchall
from core import wm_store as store


def overview(db_path):
    env = tq.list_tasks(db_path)
    by = {}
    for t in env["tasks"]:
        by.setdefault(t["human"]["state"], []).append(t)
    day_ago = time.time() - 86400
    con = connect_ro(db_path)
    try:
        open_reviews = con.execute("SELECT COUNT(*) FROM reviews WHERE status IN ('pending','running')").fetchone()[0]
        done_today = con.execute("SELECT COUNT(*) FROM state_transitions WHERE to_status='done' AND ts>=?", (day_ago,)).fetchone()[0]
        activity = _fetchall(con, "SELECT a.*, p.slug AS project_slug, t.title AS task_title FROM activity a "
                             "LEFT JOIN projects p ON p.id=a.project_id LEFT JOIN tasks t ON t.id=a.task_id "
                             "ORDER BY a.id DESC LIMIT 25")
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM wm_meta")}
    finally:
        con.close()
    return {
        "stats": {"needsyou": len(by.get("needsyou", [])), "working": len(by.get("working", [])),
                  "queued": len(by.get("queued", [])), "backlog": len(by.get("backlog", [])),
                  "done_today": done_today, "open_reviews": open_reviews,
                  "paused": meta.get("paused") == "1", "cap": int(meta.get("concurrency_cap") or 0)},
        "needsyou": by.get("needsyou", []),
        "working": by.get("working", []),
        "queued": by.get("queued", [])[:10],
        "activity": activity,
        "ts": time.time(),
    }


def activity(db_path, project=None, agent=None, task_id=None, before=None, limit=100):
    """Unified timeline: activity rows + state transitions, newest first.
    `before` is a unix ts cursor for paging."""
    limit = max(1, min(int(limit or 100), 500))
    con = connect_ro(db_path)
    try:
        a_sql = ("SELECT 'activity' AS kind, a.id, a.ts, a.task_id, a.run_id, a.agent_profile, a.action, a.detail, "
                 "p.slug AS project_slug, t.title AS task_title FROM activity a "
                 "LEFT JOIN projects p ON p.id=a.project_id LEFT JOIN tasks t ON t.id=a.task_id WHERE 1=1")
        t_sql = ("SELECT 'transition' AS kind, s.id, s.ts, s.task_id, s.run_id, NULL AS agent_profile, "
                 "(s.from_status || ' -> ' || s.to_status) AS action, s.detail, p.slug AS project_slug, t.title AS task_title "
                 "FROM state_transitions s LEFT JOIN tasks t ON t.id=s.task_id LEFT JOIN projects p ON p.id=t.project_id WHERE 1=1")
        ap, tp = [], []
        if project:
            a_sql += " AND p.slug=?"; ap.append(project); t_sql += " AND p.slug=?"; tp.append(project)
        if agent:
            a_sql += " AND a.agent_profile=?"; ap.append(agent); t_sql += " AND 0"  # transitions carry no agent
        if task_id:
            a_sql += " AND a.task_id=?"; ap.append(task_id); t_sql += " AND s.task_id=?"; tp.append(task_id)
        if before:
            a_sql += " AND a.ts<?"; ap.append(float(before)); t_sql += " AND s.ts<?"; tp.append(float(before))
        rows = _fetchall(con, a_sql + " ORDER BY a.ts DESC LIMIT ?", ap + [limit]) + \
               _fetchall(con, t_sql + " ORDER BY s.ts DESC LIMIT ?", tp + [limit])
    finally:
        con.close()
    rows.sort(key=lambda r: (-(r["ts"] or 0), -r["id"]))
    rows = rows[:limit]
    return {"events": rows, "next_before": rows[-1]["ts"] if len(rows) == limit else None}


def run_log(run_id, offset=0, max_bytes=256 * 1024):
    """Incremental tail of runs/<id>.log. Returns bytes from `offset`."""
    path = os.path.join(store.resolve_runs_dir(), "%d.log" % int(run_id))
    if not os.path.isfile(path):
        return {"exists": False, "offset": 0, "size": 0, "data": ""}
    size = os.path.getsize(path)
    offset = max(0, min(int(offset or 0), size))
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
    return {"exists": True, "offset": offset, "size": size, "next": offset + len(chunk),
            "data": chunk.decode("utf-8", errors="replace"), "truncated": offset + len(chunk) < size}
