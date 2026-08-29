"""Tasks list per the approved Tasks-tab spec, keyed by human state.

Server-side scope (active projects only), project/state/search filters,
newest-first ordering, and an honest envelope: counts are over the filtered
set before paging; stateOptions ignore the selected state filter.
"""
import sqlite3

from backend import status as hs
from backend.readers import connect_ro, _fetchall, _parse_paths

ORDER_INDEX = {s: i for i, s in enumerate(hs.ORDER)}


def _base(project=None, q=None, archived=False):
    sql = ("SELECT t.*, p.slug AS project_slug, p.name AS project_name, "
           "g.title AS goal_title, g.status AS goal_status "
           "FROM tasks t JOIN projects p ON p.id = t.project_id "
           "LEFT JOIN goals g ON g.id = t.goal_id WHERE 1=1")
    params = []
    if not archived:
        sql += " AND p.archived = 0"
    if project:
        sql += " AND p.slug = ?"; params.append(project)
    if q:
        like = "%" + q.strip() + "%"
        sql += (" AND (LOWER(t.title) LIKE LOWER(?) OR LOWER(t.description) LIKE LOWER(?)"
                " OR CAST(t.id AS TEXT) LIKE ? OR LOWER(t.assignee_profile) LIKE LOWER(?)"
                " OR LOWER(p.slug) LIKE LOWER(?) OR LOWER(g.title) LIKE LOWER(?))")
        params += [like] * 6
    return sql, params


def _enrich(con, rows):
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    deps = {}
    for d in con.execute(
            "SELECT d.task_id, t.id, t.status, t.title FROM task_deps d "
            "JOIN tasks t ON t.id = d.depends_on_task_id WHERE d.task_id IN (%s)" % marks, ids):
        deps.setdefault(d["task_id"], []).append({"id": d["id"], "status": d["status"], "title": d["title"]})
    last_run = {}
    for r in con.execute(
            "SELECT r.* FROM runs r JOIN (SELECT task_id, MAX(id) AS mid FROM runs "
            "WHERE task_id IN (%s) GROUP BY task_id) m ON m.mid = r.id" % marks, ids):
        last_run[r["task_id"]] = dict(r)
    for r in rows:
        r["deps"] = deps.get(r["id"], [])
        lr = last_run.get(r["id"])
        r["last_run"] = ({"id": lr["id"], "status": lr["status"], "session_id": lr["session_id"],
                          "agent_profile": lr["agent_profile"], "started_at": lr["started_at"],
                          "finished_at": lr["finished_at"], "error": lr["error"]} if lr else None)
        r["result_paths"] = _parse_paths(r.get("result_paths"))
        r["human"] = hs.classify(r, r["deps"], r.get("goal_status"), lr)
    return rows


def list_tasks(db_path, project=None, state=None, q=None, limit=None, offset=0, archived=False):
    con = connect_ro(db_path)
    try:
        sql, params = _base(project, q, archived)
        rows = _enrich(con, _fetchall(con, sql, params))
    finally:
        con.close()
    state_options = sorted({r["human"]["state"] for r in rows}, key=lambda s: ORDER_INDEX[s])
    if state:
        rows = [r for r in rows if r["human"]["state"] == state]
    counts = {}
    for r in rows:
        counts[r["human"]["state"]] = counts.get(r["human"]["state"], 0) + 1
    rows.sort(key=lambda r: (ORDER_INDEX[r["human"]["state"]], -(r["updated_at"] or r["created_at"] or 0), -r["id"]))
    total = len(rows)
    if limit is not None:
        rows = rows[int(offset or 0): int(offset or 0) + int(limit)]
    return {"tasks": rows, "total": total, "stateCounts": counts,
            "stateOptions": state_options, "limit": limit, "offset": offset or 0}


def task_detail(db_path, task_id):
    from backend import readers
    t = readers.task_detail(db_path, task_id)
    if t is None:
        return None
    con = connect_ro(db_path)
    try:
        g = con.execute("SELECT status FROM goals WHERE id=?", (t["goal_id"],)).fetchone() if t.get("goal_id") else None
    finally:
        con.close()
    t["deps"] = [{"id": d["task_id"], "status": d["status"], "title": d["title"]} for d in t["deps"]]
    t["dependents"] = [{"id": d["task_id"], "status": d["status"], "title": d["title"]} for d in t["dependents"]]
    t["human"] = hs.classify(t, t["deps"], g["status"] if g else None, t["runs"][0] if t["runs"] else None)
    return t
