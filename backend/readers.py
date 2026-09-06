"""backend.readers — read-only query layer (ported from wm_dash.reader).

Every function opens its own SQLite connection in mode=ro (read-only), so the
dashboard can NEVER write wm.db or a profile store, and never extends their
write-ahead log. This mirrors the data exactly as it lives in the real store
and the real Hermes profile stores — nothing is synthesised.

Endpoints served here (all authenticated, JSON):
  projects       /api/projects, /api/project/{slug}
  goals          /api/goals, /api/goal/{id}
  tasks          /api/tasks, /api/task/{id}
  activity       /api/activity
  transitions    /api/transitions
  reviews        /api/reviews
  agents         /api/agents, /api/agent/{name}/sessions
  session        /api/session/{profile}/{id}   (profile store transcript+usage)
  overview       /api/overview                 (aggregate for the console)
  files          -> backend/files.py (Group 5: /api/files/*, /api/project/{slug}/artifacts)
  chats          /api/sessions                 (all sessions, managed/direct)

Generic over paths so tests can pass a throwaway wm.db and a scratch profiles
dir without ever touching the live store or profile stores.

stdlib only.
"""

import json
import os
import sqlite3
import time

from core import wm_store as store

# The managed team profiles the dashboard may read agent/session data from.
# NOTE: the Orchestrator is NOT one of these — it lives outside the specialist
# dispatch roster. It is surfaced separately (see _profile_db / ORCHESTRATOR)
# so the dashboard can show when the Orchestrator is genuinely working, while
# still reporting its REAL run/task aggregates like any other assignee.
# Sourced from the engine so the dashboard roster can never drift from the one
# the dispatcher and the run wrapper coordinate with.
AGENT_PROFILES = store.SPECIALIST_PROFILES
ORCHESTRATOR = store.ORCHESTRATOR_AGENT
ORCHESTRATOR_ACTIVE_WINDOW_S = 150  # a live orchestrator session this fresh = actively working

# Every identity whose sessions the dashboard may read a Hermes store for: the
# six specialists plus the Orchestrator's default-profile store. Session
# enrichment and the profile allowlist both key off this one tuple so a chat
# owned by the Orchestrator is never silently dropped from a view.
_SESSION_STORE_PROFILES = store.ASSIGNEE_PROFILES


def connect_ro(db_path, timeout=5.0):
    """Open a read-only handle; raises sqlite3.OperationalError if absent."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _fetchall(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


def _fetchone(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None


def _parse_paths(value):
    """result_paths come back as a JSON string column; returns a list."""
    if not value:
        return []
    try:
        v = json.loads(value)
        return v if isinstance(v, list) else [str(v)]
    except Exception:
        return [str(value)]


def _curated(row, keys):
    """Pull only the given keys out of a row dict (missing keys -> None)."""
    return {k: row.get(k) for k in keys}


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def list_projects(db_path, profiles_dir=None):
    """Every project + real aggregates for the Projects bento (§5.1).

    Enriches each row with honest SQLite aggregates over the live store (nothing
    synthesised): keeps tasks_total/tasks_done, adds goals_total/planned/released
    (group-by over goals status; unknown statuses count toward goals_total but are
    never invented into the two known buckets), runs_total (runs joined to the
    project's tasks), active_agents (DISTINCT agent_profile where a run is
    genuinely 'running', else []) and last_activity (latest activity row for the
    project, or None). `profiles_dir` is accepted for signature parity with the
    profile-aware readers; active_agents derive from the real runs table, never
    from fabrication, so a dormant project simply shows [].

    Sort keeps the existing archived-asc / created-asc ordering.
    """
    con = connect_ro(db_path)
    try:
        rows = _fetchall(con, "SELECT * FROM projects ORDER BY archived ASC, created_at ASC, id ASC")
        ids = [r["id"] for r in rows]

        # F-13: collapse the old ~7 queries-per-project loop into a handful of
        # set-based GROUP BY queries, joined in Python. Results are identical,
        # but the cost is ~6 total queries regardless of project count.
        def _group_counts(sql, params=()):
            out = {}
            for row in con.execute(sql, params):
                out[row["g"]] = row["n"]
            return out

        tasks_total = _group_counts(
            "SELECT project_id AS g, COUNT(*) AS n FROM tasks GROUP BY project_id")
        tasks_done = _group_counts(
            "SELECT project_id AS g, COUNT(*) AS n FROM tasks "
            "WHERE status='done' GROUP BY project_id")
        runs_total = _group_counts(
            "SELECT t.project_id AS g, COUNT(*) AS n FROM runs r "
            "JOIN tasks t ON t.id=r.task_id GROUP BY t.project_id")
        goals_total = _group_counts(
            "SELECT project_id AS g, COUNT(*) AS n FROM goals GROUP BY project_id")

        # Phase 6.5: four goal buckets. Without draft/planning a project whose
        # goals are all drafts would render "3 goals · 0 planned · 0 released".
        goals_draft = {}
        goals_planning = {}
        goals_planned = {}
        goals_released = {}
        _goal_buckets = {"draft": goals_draft, "planning": goals_planning,
                         "planned": goals_planned, "released": goals_released}
        for row in con.execute(
                "SELECT project_id AS g, status, COUNT(*) AS n FROM goals "
                "GROUP BY project_id, status"):
            bucket = _goal_buckets.get(row["status"])
            if bucket is not None:
                bucket[row["g"]] = row["n"]

        # active_agents: DISTINCT genuinely-'running' run agents per project.
        active_agents = {}
        for row in con.execute(
                "SELECT DISTINCT t.project_id AS g, r.agent_profile FROM runs r "
                "JOIN tasks t ON t.id=r.task_id "
                "WHERE r.status='running' AND r.agent_profile IS NOT NULL"):
            active_agents.setdefault(row["g"], []).append(row["agent_profile"])

        # last_activity: newest (highest-id) activity row per project.
        last_activity = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for row in con.execute(
                    "SELECT project_id AS g, action, ts, agent_profile, detail "
                    "FROM activity WHERE project_id IN (%s) ORDER BY id DESC" % marks,
                    ids):
                g = row["g"]
                if g in last_activity:
                    continue
                last_activity[g] = {
                    "action": row["action"], "ts": row["ts"],
                    "agent_profile": row["agent_profile"],
                    "detail": row["detail"]}

        for r in rows:
            pid = r["id"]
            r["tasks_total"] = tasks_total.get(pid, 0)
            r["tasks_done"] = tasks_done.get(pid, 0)
            r["runs_total"] = runs_total.get(pid, 0)
            r["goals_total"] = goals_total.get(pid, 0)
            r["goals_draft"] = goals_draft.get(pid, 0)
            r["goals_planning"] = goals_planning.get(pid, 0)
            r["goals_planned"] = goals_planned.get(pid, 0)
            r["goals_released"] = goals_released.get(pid, 0)
            r["active_agents"] = active_agents.get(pid, [])
            act = last_activity.get(pid)
            if act:
                r["last_activity"] = act
                r["has_activity"] = True
            else:
                r["last_activity"] = None
                r["has_activity"] = False
        return rows
    finally:
        con.close()


def get_project(db_path, slug):
    con = connect_ro(db_path)
    try:
        p = _fetchone(con, "SELECT * FROM projects WHERE slug=?", (slug,))
        return p
    finally:
        con.close()


def project_detail(db_path, slug, profiles_dir=None):
    """Project row + its goals, tasks, runs, activity AND the project-scoped
    aggregates every one of the nine project-dashboard panes renders from
    (PROJECT_DASHBOARD_DESIGN.md §8). Nothing is synthesised: task_status,
    goals_done, agents, chats and reviews are all real SQL aggregates over the
    live store filtered to this project.

    `profiles_dir` is optional (same as list_agents/all_sessions): when
    provided, chat rows try to enrich model/last-activity from the matching
    profile state.db — only when that store is reachable, and never inventing
    values when it isn't.
    """
    con = connect_ro(db_path)
    try:
        p = _fetchone(con, "SELECT * FROM projects WHERE slug=?", (slug,))
        if p is None:
            return None
        pid = p["id"]
        p["goals"] = _fetchall(con,
            "SELECT * FROM goals WHERE project_id=? ORDER BY id", (pid,))
        for g in p["goals"]:
            g["tasks_total"] = _count(con,
                "SELECT COUNT(*) AS n FROM tasks WHERE goal_id=?", (g["id"],))
            g["tasks_done"] = _count(con,
                "SELECT COUNT(*) AS n FROM tasks WHERE goal_id=? AND status='done'",
                (g["id"],))
        p["tasks"] = _task_rows(con, project_id=pid)
        p["runs"] = _fetchall(con,
            "SELECT * FROM runs WHERE task_id IN "
            "(SELECT id FROM tasks WHERE project_id=?) ORDER BY id DESC LIMIT 200",
            (pid,))
        for r in p["runs"]:
            r["result_paths"] = _parse_paths(r.get("result_paths"))
        p["activity"] = _fetchall(con,
            "SELECT * FROM activity WHERE project_id=? ORDER BY id DESC LIMIT 100",
            (pid,))
        # ---- nine-pane aggregates (§8) ------------------------------------
        p["goals_done"] = _goal_status_counts(con, pid)
        p["task_status"] = _task_status_counts(con, pid)
        p["tasks_total"] = len(p.get("tasks") or [])
        p["tasks_done"] = p["task_status"].get("done", 0) if p.get("task_status") else 0
        p["task_status_meta"] = {"total": p["tasks_total"]}
        p["agents"] = _project_agents(con, pid)
        p["reviews"] = _project_reviews(con, pid)
        p["chats"] = _project_chats(con, profiles_dir, pid)
        return p
    finally:
        con.close()


def _task_status_counts(con, project_id):
    """Counting per task status for a project (§4.2 by-status distribution)."""
    counts = {}
    for r in con.execute(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE project_id=? "
            "GROUP BY status", (project_id,)):
        counts[r["status"]] = r["n"]
    return counts


def _goal_status_counts(con, project_id):
    """draft/planning/planned/released goal buckets (§4.1/§4.2; Phase 6.5 grew
    this from two buckets to the full lifecycle). Unknown statuses are NOT
    invented into a bucket — they simply don't count toward any of them."""
    counts = {"draft": 0, "planning": 0, "planned": 0, "released": 0}
    for r in con.execute(
            "SELECT status, COUNT(*) AS n FROM goals WHERE project_id=? "
            "GROUP BY status", (project_id,)):
        if r["status"] in counts:
            counts[r["status"]] = r["n"]
    return counts


def _project_agents(con, project_id):
    """Real agents that ran on this project — GROUP BY runs.agent_profile over
    the project's runs (§4.5). `running` only ever reflects a genuinely-
    'running' run. Agents with zero runs on the project are omitted."""
    rows = con.execute(
        "SELECT r.agent_profile AS agent_profile, COUNT(*) AS runs, "
        "SUM(CASE WHEN r.status='running' THEN 1 ELSE 0 END) AS running, "
        "SUM(CASE WHEN r.status='done' THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) AS failed, "
        "SUM(CASE WHEN r.status='blocked' THEN 1 ELSE 0 END) AS blocked, "
        "SUM(CASE WHEN r.status='stalled' THEN 1 ELSE 0 END) AS stalled, "
        "MAX(r.started_at) AS last_run_at, "
        "(SELECT rr.session_id FROM runs rr "
        " WHERE rr.task_id IN (SELECT id FROM tasks WHERE project_id=?) "
        "   AND rr.agent_profile = r.agent_profile "
        "   AND rr.session_id IS NOT NULL "
        " ORDER BY rr.id DESC LIMIT 1) AS last_session_id "
        "FROM runs r "
        "WHERE r.task_id IN (SELECT id FROM tasks WHERE project_id=?) "
        "AND r.agent_profile IS NOT NULL "
        "GROUP BY r.agent_profile ORDER BY runs DESC, r.agent_profile",
        (project_id, project_id)).fetchall()
    out = []
    for row in rows:
        a = dict(row)
        for k in ("running", "done", "failed", "blocked", "stalled"):
            a[k] = a.get(k) or 0
        out.append(a)
    return out


def _project_reviews(con, project_id):
    """All reviews for this project's tasks, newest first — mirrors list_reviews'
    join and shape, but keyed to the project (§4.7 queue + verdict history)."""
    rows = con.execute(
        "SELECT r.*, t.title AS task_title, t.status AS task_status, "
        "p.slug AS project_slug "
        "FROM reviews r "
        "LEFT JOIN tasks t ON t.id = r.task_id "
        "LEFT JOIN projects p ON p.id = t.project_id "
        "WHERE t.project_id=? ORDER BY r.id DESC LIMIT 500", (project_id,))
    return [_review_projection(dict(x)) for x in rows]


def _project_chats(con, profiles_dir, project_id):
    """Distinct managed session_ids on this project's runs, newest run first
    (§4.6). model/last_activity are enriched from the owning profile's state.db
    ONLY when that store is reachable; otherwise the row still carries the id +
    agent + bound task with null model (never an invented token/cost)."""
    chats = []
    seen = set()
    rows = con.execute(
        "SELECT r.session_id AS session_id, r.agent_profile AS agent_profile, "
        "r.task_id AS task_id, t.title AS task_title "
        "FROM runs r LEFT JOIN tasks t ON t.id = r.task_id "
        "WHERE r.task_id IN (SELECT id FROM tasks WHERE project_id=?) "
        "AND r.session_id IS NOT NULL ORDER BY r.id DESC", (project_id,))
    for r in rows:
        sid = r["session_id"]
        if sid in seen:
            continue
        seen.add(sid)
        chat = {"session_id": sid, "agent_profile": r["agent_profile"],
                "task_id": r["task_id"], "task_title": r["task_title"],
                "model": None, "last_activity_at": None}
        name = chat["agent_profile"]
        # M1: an orchestrator run's session lives in the default-profile store,
        # so it enriches through the SAME mapping as a specialist's. Excluding
        # it here left every dispatched planning chat on this project showing a
        # null model/last-activity even though its store was readable.
        if (name in _SESSION_STORE_PROFILES and profiles_dir
                and os.path.isfile(_profile_db(profiles_dir, name))):
            try:
                scon = connect_ro(_profile_db(profiles_dir, name))
                try:
                    m = _fetchone(scon, "SELECT model, last_activity_at, "
                                        "started_at FROM sessions WHERE id=?",
                                  (sid,))
                finally:
                    scon.close()
                if m:
                    chat["model"] = m.get("model")
                    chat["last_activity_at"] = (
                        m.get("last_activity_at") or m.get("started_at"))
            except Exception:
                pass  # unreachable store -> honest nulls, no fabrication
        chats.append(chat)
    return chats


# ---------------------------------------------------------------------------
# goals
# ---------------------------------------------------------------------------

def list_goals(db_path, project=None):
    con = connect_ro(db_path)
    try:
        sql = ("SELECT g.*, p.slug AS project_slug "
               "FROM goals g LEFT JOIN projects p ON p.id = g.project_id")
        params = []
        if project:
            sql += " WHERE p.slug = ?"
            params.append(project)
        sql += " ORDER BY g.id"
        goals = _fetchall(con, sql, params)
        for g in goals:
            g["tasks_total"] = _count(con,
                "SELECT COUNT(*) AS n FROM tasks WHERE goal_id=?", (g["id"],))
            g["tasks_done"] = _count(con,
                "SELECT COUNT(*) AS n FROM tasks WHERE goal_id=? AND status='done'",
                (g["id"],))
        return goals
    finally:
        con.close()


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

def _task_rows(con, project_id=None, goal_id=None, status=None, q=None,
               limit=None, offset=0, slug=None):
    """Core tasks query over the live store (mode=ro).

    Filters: project_id / goal_id / status / project slug / free-text `q`
    (search over title, description, definition_of_done, assignee, project slug
    and goal title — case-insensitive, like a true kanban search box).

    Returns a bare list by default (keeps legacy callers/tests unchanged), but
    returns a `{tasks, total, statusCounts, limit, offset}` envelope whenever pagination
    (`limit`) or search (`q`) is requested, so the kanban can show an honest
    total and a Load-more affordance. `total` = size of the *filtered* set
    (before LIMIT/OFFSET), never fabricated.
    """
    sql = ("SELECT t.*, p.slug AS project_slug, g.title AS goal_title "
           "FROM tasks t "
           "LEFT JOIN projects p ON p.id = t.project_id "
           "LEFT JOIN goals g ON g.id = t.goal_id WHERE 1=1")
    params = []
    if slug is not None:
        sql += " AND p.slug = ?"; params.append(slug)
    if project_id is not None:
        sql += " AND t.project_id = ?"; params.append(project_id)
    if goal_id is not None:
        sql += " AND t.goal_id = ?"; params.append(goal_id)
    if status is not None:
        sql += " AND t.status = ?"; params.append(status)
    if q:
        like = "%" + q.strip() + "%"
        sql += (" AND (LOWER(t.title) LIKE LOWER(?)"
                " OR LOWER(t.description) LIKE LOWER(?)"
                " OR LOWER(t.definition_of_done) LIKE LOWER(?)"
                " OR LOWER(t.assignee_profile) LIKE LOWER(?)"
                " OR LOWER(p.slug) LIKE LOWER(?)"
                " OR LOWER(g.title) LIKE LOWER(?))")
        params += [like] * 6
    order = " ORDER BY t.status, t.created_at ASC, t.id ASC"
    envelope = (limit is not None) or bool(q)
    total = _count(con, "SELECT COUNT(*) AS n FROM (" + sql + order + ")",
                   params)
    # task #70 (lane honesty): per-status counts over the SAME filtered WHERE,
    # before LIMIT/OFFSET, so the kanban can badge every lane with the real
    # filtered count (never the loaded-page slice).
    status_counts = {}
    for row in con.execute(
            "SELECT status, COUNT(*) AS n FROM (" + sql + order + ") GROUP BY status",
            params):
        status_counts[row["status"] or "?"] = row["n"]
    rows_sql = sql + order
    rows_params = params
    if limit is not None:
        rows_sql += " LIMIT ? OFFSET ?"
        rows_params = params + [int(limit), int(offset or 0)]
    rows = _fetchall(con, rows_sql, rows_params)
    for r in rows:
        r["result_paths"] = _parse_paths(r.get("result_paths"))
    if envelope:
        return {"tasks": rows, "total": total,
                "statusCounts": status_counts,
                "limit": limit, "offset": (offset or 0)}
    return rows


def list_tasks(db_path, project=None, status=None, q=None, limit=None, offset=0):
    con = connect_ro(db_path)
    try:
        return _task_rows(con, slug=project, status=status, q=q,
                          limit=limit, offset=offset)
    finally:
        con.close()


def _count(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    if not r:
        return 0
    # tolerate either an aliased 'n' column or a bare COUNT(*) / SUM(...) column
    try:
        return r["n"]
    except (IndexError, KeyError):
        return r[0] if r[0] is not None else 0


def task_detail(db_path, task_id):
    con = connect_ro(db_path)
    try:
        t = _fetchone(con,
            "SELECT t.*, p.slug AS project_slug, g.title AS goal_title "
            "FROM tasks t "
            "LEFT JOIN projects p ON p.id = t.project_id "
            "LEFT JOIN goals g ON g.id = t.goal_id WHERE t.id = ?", (task_id,))
        if t is None:
            return None
        t["result_paths"] = _parse_paths(t.get("result_paths"))
        t["deps"] = _fetchall(con,
            "SELECT d.depends_on_task_id AS task_id, d2.title AS title, "
            "d2.status AS status FROM task_deps d "
            "LEFT JOIN tasks d2 ON d2.id = d.depends_on_task_id "
            "WHERE d.task_id=?", (task_id,))
        t["dependents"] = _fetchall(con,
            "SELECT d.task_id AS task_id, d2.title AS title, d2.status AS status "
            "FROM task_deps d "
            "LEFT JOIN tasks d2 ON d2.id = d.task_id "
            "WHERE d.depends_on_task_id=?", (task_id,))
        t["transitions"] = _fetchall(con,
            "SELECT * FROM state_transitions WHERE task_id=? ORDER BY id DESC LIMIT 100",
            (task_id,))
        t["runs"] = _fetchall(con,
            "SELECT * FROM runs WHERE task_id=? ORDER BY id DESC", (task_id,))
        for r in t["runs"]:
            r["result_paths"] = _parse_paths(r.get("result_paths"))
        t["reviews"] = _fetchall(con,
            "SELECT * FROM reviews WHERE task_id=? ORDER BY id DESC", (task_id,))
        # Group 10: the newest live/blocked run's open question (unread row).
        t["question"] = None
        if t["runs"] and t["runs"][0].get("status") in ("running", "blocked"):
            t["question"] = _fetchone(con,
                "SELECT id, title, body, href, run_id FROM notifications "
                "WHERE run_id=? AND source_key LIKE 'runq:%' AND read_at IS NULL "
                "ORDER BY id DESC LIMIT 1", (t["runs"][0]["id"],))
        return t
    finally:
        con.close()


# ---------------------------------------------------------------------------
# activity & transitions
# ---------------------------------------------------------------------------

ACTIVITY_KEYS = ("id", "ts", "project_id", "goal_id", "task_id", "run_id",
                 "agent_profile", "session_id", "action", "detail", "model")


def list_activity(db_path, since_id=None, agent=None, project=None, limit=100):
    con = connect_ro(db_path)
    try:
        sql = ("SELECT a.* FROM activity a "
               "LEFT JOIN projects p ON p.id = a.project_id WHERE 1=1")
        params = []
        if since_id is not None:
            sql += " AND a.id > ?"; params.append(since_id)
        if agent is not None:
            sql += " AND a.agent_profile = ?"; params.append(agent)
        if project is not None:
            sql += " AND p.slug = ?"; params.append(project)
        limit = max(1, min(int(limit or 100), 500))
        sql += " ORDER BY a.id DESC LIMIT ?"; params.append(limit)
        return [_curated(dict(r), ACTIVITY_KEYS) for r in con.execute(sql, params)]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# reviews
# ---------------------------------------------------------------------------

REVIEW_KEYS = ("id", "task_id", "reviewer_profile", "status", "session_id",
               "verdict", "comments", "requested_at", "decided_at",
               "review_policy")


def _review_projection(r):
    """F-9: the shared review row shape used by BOTH list_reviews and
    _project_reviews — REVIEW_KEYS plus the joined task/project context, never
    the raw `reviews` table columns. The bug is fixed by returning this built
    projection instead of the old no-op `r = small` loop-variable rebind."""
    small = {k: r.get(k) for k in REVIEW_KEYS}
    small["task_title"] = r.get("task_title")
    small["task_status"] = r.get("task_status")
    small["project_slug"] = r.get("project_slug")
    return small


def list_reviews(db_path, status=None, task_id=None, limit=200):
    con = connect_ro(db_path)
    try:
        sql = ("SELECT r.*, t.title AS task_title, t.status AS task_status, "
               "p.slug AS project_slug "
               "FROM reviews r "
               "LEFT JOIN tasks t ON t.id = r.task_id "
               "LEFT JOIN projects p ON p.id = t.project_id WHERE 1=1")
        params = []
        if status is not None:
            sql += " AND r.status = ?"; params.append(status)
        if task_id is not None:
            sql += " AND r.task_id = ?"; params.append(task_id)
        limit = max(1, min(int(limit or 200), 1000))
        sql += " ORDER BY r.id DESC LIMIT ?"; params.append(limit)
        rows = _fetchall(con, sql, params)
        return [_review_projection(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# agents & sessions (profile stores)
# ---------------------------------------------------------------------------

def _profile_db(profiles_dir, name):
    """The agent's Hermes state.db — delegated to the engine's ONE mapping.

    M2: this used to hand-roll `dirname(profiles_dir.rstrip('/'))` for the
    Orchestrator while the engine used `dirname(realpath(profiles_dir))`. When
    the profiles dir is reached through a symlink the two spellings name
    DIFFERENT files, so a session the wrapper captured into the engine's store
    was invisible to the dashboard's liveness probe. Both now resolve through
    wm_store.profile_state_db, so they cannot diverge.
    """
    return store.profile_state_db(profiles_dir, name)


def _orchestrator_run_active(con):
    """True when wm.db holds a genuinely RUNNING orchestrator run.

    This is the managed signal, and it is what distinguishes "the Orchestrator
    is executing a dispatched planning run" from "the owner has a chat window
    open on the default profile" — the latter has no run row at all.
    """
    row = con.execute(
        "SELECT COUNT(*) FROM runs WHERE agent_profile=? AND status='running'",
        (ORCHESTRATOR,)).fetchone()
    return bool(row and row[0])


def _orchestrator_session_active(profiles_dir, window=ORCHESTRATOR_ACTIVE_WINDOW_S):
    """Honest Orchestrator liveness from its OWN store: an OPEN session in the
    default-profile state.db whose last activity is fresh.

    The freshness window is the anti-staleness guard: an owner chat left open
    for hours has `end_reason IS NULL` forever, so without the window every
    abandoned tab would read as "the Orchestrator is working". Never
    synthesised — reads the real store.
    """
    try:
        con = connect_ro(_profile_db(profiles_dir, ORCHESTRATOR))
    except Exception:
        return False
    try:
        row = con.execute(
            "SELECT last_activity_at FROM sessions "
            "WHERE end_reason IS NULL ORDER BY last_activity_at DESC LIMIT 1"
        ).fetchone()
        if not row or row[0] is None:
            return False
        return (time.time() - float(row[0])) <= window
    finally:
        con.close()


def _orchestrator_active(profiles_dir, con=None,
                         window=ORCHESTRATOR_ACTIVE_WINDOW_S):
    """Orchestrator liveness = a genuinely running MANAGED run (when a wm.db
    handle is available) OR a fresh open session on the default profile.

    The run signal is checked first and is authoritative: a dispatched planning
    run that is mid-flight must read active even if its session has been quiet
    longer than the window (a long tool call, a slow model). The session signal
    covers the operator working directly on the default profile, with the
    freshness window keeping a stale owner chat from impersonating one.
    """
    if con is not None:
        try:
            if _orchestrator_run_active(con):
                return True
        except Exception:  # pragma: no cover - defensive (RO handle races)
            pass
    return _orchestrator_session_active(profiles_dir, window=window)


def _db_exists(profiles_dir, name):
    return os.path.isfile(_profile_db(profiles_dir, name))


def _agent_run_aggregate(con, name):
    """Real run/task aggregate for ONE assignee, straight from wm.db.

    H3: the orchestrator used to be hard-coded to zeros here. Since 6.5.2 an
    `orchestrator`-assigned `Plan goal #N` task is a genuinely dispatched run
    like any other, so reporting 0 runs / 0 tasks was a fabricated number that
    contradicted the runs table the same page rendered elsewhere. One query,
    every assignee.
    """
    row = _fetchone(
        con,
        "SELECT COUNT(*) AS runs, "
        "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running, "
        "SUM(CASE WHEN status='done'   THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
        "MAX(started_at) AS last_run_at "
        "FROM runs WHERE agent_profile=?", (name,))
    agg = {
        "runs": row["runs"] if row and row["runs"] else 0,
        "runs_running": row["running"] if row and row["running"] else 0,
        "runs_done": row["done"] if row and row["done"] else 0,
        "runs_failed": row["failed"] if row and row["failed"] else 0,
        "last_run_at": row["last_run_at"] if row else None,
    }
    t = _fetchone(con,
        "SELECT COUNT(*) AS n FROM tasks WHERE assignee_profile=?", (name,))
    agg["tasks_assigned"] = t["n"] if t and t["n"] else 0
    return agg


def _agent_session_stats(profiles_dir, name):
    """Session count / last activity / cost from an agent's OWN Hermes store.

    For the Orchestrator that store is the default-profile state.db, reached
    through the same canonical mapping as everyone else (_profile_db), so the
    numbers here describe exactly the store its runs write into.
    """
    empty = {"sessions": 0, "last_active_at": None, "estimated_cost_usd": None}
    if not _db_exists(profiles_dir, name):
        return empty
    try:
        scon = connect_ro(_profile_db(profiles_dir, name))
    except Exception:  # pragma: no cover - defensive (store unreadable)
        return empty
    try:
        m = _fetchone(scon,
            "SELECT COUNT(*) AS n, MAX(last_activity_at) AS last_active, "
            "SUM(estimated_cost_usd) AS cost "
            "FROM sessions WHERE archived=0")
        return {
            "sessions": m["n"] if m and m["n"] else 0,
            "last_active_at": m["last_active"] if m else None,
            "estimated_cost_usd": m["cost"] if m and m["cost"] else None,
        }
    except Exception:  # pragma: no cover - defensive (schema drift)
        return empty
    finally:
        scon.close()


def list_agents(db_path, profiles_dir):
    """Per-agent summary: run aggregate from wm.db + session stats.

    Covers the six dispatchable specialists (role `agent`) and the reserved
    Orchestrator (role `operator`). The ROLE is what differs — the Orchestrator
    is not offered as a dispatch target in the crew UI — but its run/task
    aggregate is as real as anyone's, and its session stats come from the
    default-profile store.
    """
    con = connect_ro(db_path)
    try:
        agents = []
        for name in AGENT_PROFILES:
            a = {"name": name, "role": "agent",
                 "available": _db_exists(profiles_dir, name)}
            a.update(_agent_run_aggregate(con, name))
            a.update(_agent_session_stats(profiles_dir, name))
            agents.append(a)
        orch = {"name": ORCHESTRATOR, "role": "operator",
                "available": _db_exists(profiles_dir, ORCHESTRATOR)}
        orch.update(_agent_run_aggregate(con, ORCHESTRATOR))
        orch.update(_agent_session_stats(profiles_dir, ORCHESTRATOR))
        orch["active_now"] = _orchestrator_active(profiles_dir, con=con)
        agents.append(orch)
        return agents
    finally:
        con.close()


SESSION_SUMMARY_KEYS = (
    "id", "source", "model", "started_at", "ended_at", "end_reason",
    "message_count", "tool_call_count", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
    "estimated_cost_usd", "actual_cost_usd", "cost_status", "cwd",
    "git_branch", "title", "title_source", "last_activity_at", "profile_name",
    "pinned",
)


def _safe_profile(profiles_dir, name):
    if name not in _SESSION_STORE_PROFILES:
        raise ValueError("unknown profile %r" % name)
    if not _db_exists(profiles_dir, name):
        raise FileNotFoundError("no state.db for profile %r" % name)
    return _profile_db(profiles_dir, name)


def agent_sessions(profiles_dir, name, limit=100):
    """Recent sessions for one profile from its state.db (RO)."""
    db = _safe_profile(profiles_dir, name)
    con = connect_ro(db)
    try:
        limit = max(1, min(int(limit or 100), 500))
        rows = _fetchall(con,
            "SELECT * FROM sessions WHERE archived=0 "
            "ORDER BY COALESCE(pinned, 0) DESC, COALESCE(last_activity_at, started_at, id) DESC LIMIT ?",
            (limit,))
        out = []
        for r in rows:
            r["profile_name"] = name
            out.append(_curated(r, SESSION_SUMMARY_KEYS))
        return out
    finally:
        con.close()


def search_sessions(profiles_dir, profiles, q, limit=30):
    """Group 4b-2: case-insensitive substring search over session titles and message content of the
    given profiles (each state.db RO). Hermes has no search endpoint on this build. Returns rows with a
    ~160-char snippet around the first hit; newest activity first."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
    out = []
    for name in profiles:
        try:
            db = _safe_profile(profiles_dir, name)
            con = connect_ro(db)
        except (ValueError, FileNotFoundError, OSError):
            continue
        try:
            rows = _fetchall(con,
                "SELECT s.id, s.title, s.model, s.last_activity_at, s.started_at, "
                "  (SELECT m.content FROM messages m WHERE m.session_id=s.id AND m.role IN ('user','assistant') "
                "     AND m.content LIKE ? ESCAPE '\\' ORDER BY m.id LIMIT 1) AS hit, "
                "  (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND m.role IN ('user','assistant') "
                "     AND m.content LIKE ? ESCAPE '\\') AS hits "
                "FROM sessions s WHERE s.archived=0 AND (s.title LIKE ? ESCAPE '\\' OR EXISTS ("
                "  SELECT 1 FROM messages m WHERE m.session_id=s.id AND m.role IN ('user','assistant') AND m.content LIKE ? ESCAPE '\\')) "
                "ORDER BY COALESCE(s.last_activity_at, s.started_at) DESC LIMIT ?", (like, like, like, like, limit))
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        for r in rows:
            hit = r.get("hit") or ""
            i = hit.lower().find(q.lower())
            snippet = (("…" if i > 60 else "") + hit[max(0, i - 60): i + 100] + ("…" if i + 100 < len(hit) else "")) if i >= 0 else ""
            out.append({"profile": name, "id": r["id"], "title": r.get("title"), "model": r.get("model"),
                        "last_activity_at": r.get("last_activity_at") or r.get("started_at"), "hits": r.get("hits") or 0, "snippet": snippet.replace("\n", " ")})
    out.sort(key=lambda r: r["last_activity_at"] or 0, reverse=True)
    return out[:limit]


def _tool_calls(raw):
    """Hermes stores assistant tool calls as an OpenAI-style JSON list; keep name + arguments (clipped)."""
    if not raw:
        return None
    try:
        calls = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    out = []
    for c in calls if isinstance(calls, list) else []:
        fn = c.get("function") if isinstance(c, dict) else None
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args) if args is not None else ""
        out.append({"id": c.get("call_id") or c.get("id"), "name": fn.get("name") or "tool",
                    "arguments": args[:2000] + ("…" if len(args) > 2000 else "")})
    return out or None


def session_detail(profiles_dir, profile, session_id, transcript=True, limit=400):
    """One session + per-model usage + (optionally) transcript from state.db."""
    db = _safe_profile(profiles_dir, profile)
    con = connect_ro(db)
    try:
        s = _fetchone(con, "SELECT * FROM sessions WHERE id=?", (session_id,))
        if s is None:
            return None
        detail = _curated(s, SESSION_SUMMARY_KEYS)
        detail["usage"] = _fetchall(con,
            "SELECT model, task, api_call_count, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, reasoning_tokens, "
            "estimated_cost_usd, actual_cost_usd, cost_status "
            "FROM session_model_usage WHERE session_id=? "
            "ORDER BY COALESCE(last_seen, first_seen) DESC", (session_id,))
        # Group 4b: transcript size for the context estimate (chars/4 over active rows, incl. tool calls + reasoning)
        est = _fetchone(con, "SELECT COALESCE(SUM(LENGTH(COALESCE(content,'')) + LENGTH(COALESCE(tool_calls,'')) "
                             "+ LENGTH(COALESCE(reasoning_content, reasoning, ''))), 0) AS chars, COUNT(*) AS n "
                             "FROM messages WHERE session_id=? AND (active IS NULL OR active=1)", (session_id,))
        detail["transcript_chars"] = int(est["chars"] or 0) if est else 0
        detail["api_call_count"] = s.get("api_call_count")
        detail["transcript"] = []
        if transcript:
            cols = ["id", "role", "content", "timestamp", "tool_name",
                    "token_count", "display_kind", "active"]
            rows = _fetchall(con,
                "SELECT * FROM messages WHERE session_id=? "
                "ORDER BY id ASC LIMIT ?", (session_id, limit))
            for m in rows:
                cleaned = {k: m.get(k) for k in cols}
                c = m.get("content")
                if isinstance(c, str) and len(c) > 4000:
                    cleaned["content"] = c[:4000] + "…[truncated]"
                cleaned["tool_calls"] = _tool_calls(m.get("tool_calls"))
                r = m.get("reasoning_content") or m.get("reasoning")
                cleaned["reasoning"] = (r[:2000] + "…") if isinstance(r, str) and len(r) > 2000 else (r or None)
                detail["transcript"].append(cleaned)
        return detail
    finally:
        con.close()


# ---------------------------------------------------------------------------
# overview aggregate (KPI + attention + dispatcher + recent)
# ---------------------------------------------------------------------------

ATTENTION_STATUSES = ("planned", "waiting_approval", "ready", "running",
                      "needs_review", "rework", "done", "failed", "blocked",
                      "stalled", "manual")


def overview(db_path, profiles_dir):
    """Aggregate for the Overview console. Read-only; real data only."""
    con = connect_ro(db_path)
    try:
        counts = {}
        for st in ATTENTION_STATUSES:
            counts[st] = _count(con,
                "SELECT COUNT(*) AS n FROM tasks WHERE status=?", (st,))
        counts["projects"] = _count(con, "SELECT COUNT(*) AS n FROM projects"
                                   " WHERE archived=0")
        # Phase 6.5: all four lifecycle buckets. The Overview KPI card stays
        # 2-value (planned/released) by decision; draft/planning feed the
        # StatusStrip.
        counts["goals_draft"] = _count(con,
            "SELECT COUNT(*) AS n FROM goals WHERE status='draft'")
        counts["goals_planning"] = _count(con,
            "SELECT COUNT(*) AS n FROM goals WHERE status='planning'")
        counts["goals_planned"] = _count(con,
            "SELECT COUNT(*) AS n FROM goals WHERE status='planned'")
        counts["goals_released"] = _count(con,
            "SELECT COUNT(*) AS n FROM goals WHERE status='released'")
        # done % across tasks
        n_all = _count(con, "SELECT COUNT(*) AS n FROM tasks")
        n_done = counts["done"]
        done_pct = round((n_done / n_all) * 100) if n_all else 0

        # dispatcher health
        meta = dict(con.execute("SELECT key,value FROM wm_meta"))
        running = _count(con, "SELECT COUNT(*) FROM runs WHERE status='running'")
        try:
            cap = int(meta.get("concurrency_cap"))
        except (TypeError, ValueError):
            cap = None

        # agents active = specialists with a running run OR live state.db
        active_agents = []
        for name in AGENT_PROFILES:
            r = _count(con,
                "SELECT COUNT(*) AS n FROM runs WHERE agent_profile=? AND status='running'",
                (name,))
            if r:
                active_agents.append(name)
        # The Orchestrator counts as active on the SAME managed signal as a
        # specialist — a running run — and additionally when it is genuinely
        # working directly on the default profile (open session, fresh
        # activity). A stale owner chat fails the freshness window, so it does
        # not impersonate a managed run.
        if _orchestrator_active(profiles_dir, con=con):
            active_agents.append(ORCHESTRATOR)

        # recent sessions across profiles (managed + direct)
        sessions = all_sessions(db_path, profiles_dir, limit=12)

        # recent activity feed
        activity = list_activity(db_path, limit=12)

        # recent completed runs (for a compact run log)
        recent_runs = _fetchall(con,
            "SELECT * FROM runs ORDER BY id DESC LIMIT 8")

        return {
            "counts": counts,
            "done_pct": done_pct,
            "attention": {st: counts[st] for st in ATTENTION_STATUSES},
            "health": {
                "running": running,
                "cap": cap,
                "at_cap": running >= cap if cap else False,
                "paused": meta.get("paused") == "1",
                "schema_version": meta.get("schema_version"),
                "version": None,  # filled by server cfg
            },
            "agents_active": active_agents,
            "sessions": sessions,
            "activity": activity,
            "recent_runs": recent_runs,
        }
    finally:
        con.close()

# ---------------------------------------------------------------------------
# all-sessions aggregate (managed vs direct), for Chat
# ---------------------------------------------------------------------------

def all_sessions(db_path, profiles_dir, limit=200):
    """Every session across the managed profiles, each tagged managed=True if
    its session_id is referenced by a runs row (else direct)."""
    con = connect_ro(db_path)
    try:
        run_sessions = set()
        for r in con.execute("SELECT session_id FROM runs WHERE session_id IS NOT NULL"):
            if r["session_id"]:
                run_sessions.add(r["session_id"])
        # bound task/run info for managed sessions
        managed_info = {}
        for r in con.execute(
                "SELECT r.session_id AS sid, t.id AS task_id, t.title AS task_title, "
                "p.slug AS project_slug, r.agent_profile AS agent "
                "FROM runs r LEFT JOIN tasks t ON t.id=r.task_id "
                "LEFT JOIN projects p ON p.id=t.project_id "
                "WHERE r.session_id IS NOT NULL"):
            sid = r["sid"]
            if sid and sid not in managed_info:
                managed_info[sid] = {"task_id": r["task_id"],
                                     "task_title": r["task_title"],
                                     "project_slug": r["project_slug"],
                                     "agent": r["agent"]}

        out = []
        try:
            matches = con.execute(
                "SELECT session_id, agent_profile, task_id, project_id FROM activity "
                "WHERE session_id IS NOT NULL GROUP BY session_id").fetchall()
            _ = matches  # keep a RO reference; classification uses runs (below)
        except Exception:
            pass

        # One loop over every identity with a session store — the six
        # specialists AND the Orchestrator's default-profile store (so the
        # operator's own work and its dispatched planning runs both show in the
        # recent-sessions feed). Same mapping, same query, same per-store cap:
        # the Orchestrator used to be read through a duplicated block with a
        # different LIMIT, which quietly truncated its history relative to
        # everyone else's.
        for name in _SESSION_STORE_PROFILES:
            if not _db_exists(profiles_dir, name):
                continue
            try:
                scon = connect_ro(_profile_db(profiles_dir, name))
            except Exception:
                continue
            try:
                rows = _fetchall(scon,
                    "SELECT * FROM sessions WHERE archived=0 "
                    "ORDER BY COALESCE(last_activity_at, started_at, id) DESC LIMIT ?",
                    (100,))
            except Exception:  # pragma: no cover - defensive (schema drift)
                continue
            finally:
                scon.close()
            for s in rows:
                sid = s["id"]
                d = _curated(s, SESSION_SUMMARY_KEYS)
                d["profile_name"] = name
                d["managed"] = sid in run_sessions
                d.update(managed_info.get(sid, {}))
                out.append(d)
        # newest first by last activity
        out.sort(key=lambda s: s.get("last_activity_at") or s.get("started_at") or 0,
                 reverse=True)
        return out[:limit]
    finally:
        con.close()