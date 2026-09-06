"""wm_store.py — shared SQLite store for the Hermes Work Manager.

Pure stdlib (sqlite3, os, time). No side effects on import; the only side
effect is the module-level DEFAULT_DB_PATH constant. All functions open
their own connection and perform writes inside explicit transactions.

Runtime home: <HERMES_HOME>/hermes-hq/hq.db (see hq_home())
Prerequisites per connection:
  PRAGMA journal_mode=WAL
  PRAGMA foreign_keys=ON
  PRAGMA busy_timeout=5000
"""

import json
import os
import re
import shutil
import sqlite3
import sys
import time


def hermes_home():
    """Root Hermes home (never a profile-shaped dir)."""
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def hq_home():
    """Where hermes-hq keeps its own state: <HERMES_HOME>/hermes-hq."""
    return os.environ.get("HERMES_HQ_HOME") or os.path.join(hermes_home(), "hermes-hq")


DEFAULT_DB_PATH = os.path.join(hq_home(), "hq.db")


def resolve_db():
    return os.environ.get("WM_DB") or DEFAULT_DB_PATH


SCHEMA_VERSION = "2"

# Task statuses (validated by the CLI; promoted/held at the app layer).
#   planned           backlog / spec, NOT released. NEVER auto-runs.
#   waiting_approval  part of a released plan whose deps are not all done,
#                     OR explicitly held pending an approval gate. NEVER
#                     auto-runs. Blocks only itself + its dependents.
#   ready             released + eligible (deps done). The dispatcher claims.
#   running / needs_review / rework / done / failed / stalled / blocked / manual
TASK_STATUSES = (
    "planned", "waiting_approval", "ready", "running", "needs_review", "rework",
    "done", "failed", "stalled", "blocked", "manual",
)

# Goal lifecycle (Phase 6.5). A goal is the approval/release unit and it now
# passes through a planning stage before it can be approved:
#   draft     just created. Has no agreed decomposition yet. NOT releasable.
#   planning  `wm goal plan <id>` ran: a backlog `Plan goal #N` task is parked
#             for the Orchestrator to decompose the goal into real tasks.
#   planned   decomposition agreed — the plan is ready for the approval gate.
#   released  approved by a human/Orchestrator (`wm goal release <id>`);
#             terminal, and eligible child tasks may proceed.
GOAL_STATUSES = ("draft", "planning", "planned", "released")

# Whitelisted goal status edges for set_goal_status(). `planned -> released` is
# deliberately absent: release is the approval gate and belongs to
# release_goal(), which also re-gates the goal's children. `released` is
# terminal — nothing leaves it.
GOAL_TRANSITIONS = (
    ("draft", "planning"),      # `wm goal plan` / POST /api/goal/{id}/plan
    ("planning", "planned"),    # decomposition agreed
    ("planning", "draft"),      # abandon the planning attempt
)

# Project slugs are identity + route keys (`#project/<slug>`): lowercase
# alphanumeric plus dashes, never leading with a dash.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# ---------------------------------------------------------------------------
# Runtime paths (T2). All are overridable via env so tests can redirect the
# real launcher/db/agent at self-contained scratch paths. The dispatcher
# passes these same values into the wrapper's environment so a background
# run wrapper and its parent dispatcher always agree on paths (even under a
# test DB). Defaults are the canonical v1 locations.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(hq_home(), "runs")
PROFILES_DIR = os.path.join(hermes_home(), "profiles")
HERMES = shutil.which("hermes") or "/opt/hermes/bin/hermes"
HERMES_PY = sys.executable
WM_RUN_AGENT = os.path.join(_THIS_DIR, "wm_run_agent.py")


def resolve_runs_dir():
    return os.environ.get("WM_RUNS_DIR") or RUNS_DIR


def resolve_profiles_dir():
    return os.environ.get("WM_PROFILES_DIR") or PROFILES_DIR


# The reserved identity that runs on Hermes' DEFAULT profile: the Orchestrator.
# `orchestrator` is NOT a profile directory under `profiles/` — Hermes spells
# the root profile `default`, so every Hermes invocation for this identity uses
# `--profile default` (DEFAULT_PROFILE) and the ROOT Hermes home. Its sessions
# are captured from <root>/state.db (e.g. /opt/data/state.db), not from a
# (nonexistent) profiles/orchestrator/state.db — see agent_session_db_path().
ORCHESTRATOR_AGENT = "orchestrator"

# Hermes' own name for the root profile. `hermes --profile default ...` resolves
# HERMES_HOME to the root Hermes home even when the caller's environment already
# points at a specialist profile (hermes_cli/profiles.resolve_profile_env: a
# profile-shaped HERMES_HOME means the root is its grandparent). `--profile
# orchestrator` would abort at launch — there is no such profile.
DEFAULT_PROFILE = "default"

# The canonical roster the engine coordinates with: the six dispatchable
# specialist profiles (real directories under `profiles/`) plus the reserved
# `orchestrator` identity. Used to validate an assignee at the write path so a
# malformed name can never be handed to `hermes --profile <name>` as if it were
# a real specialist profile.
SPECIALIST_PROFILES = ("analyst", "writer", "marketer", "coder", "uiux",
                       "reviewer", "librarian")

# Second Brain: the reserved HUMAN assignee. A task assigned to `owner` is the
# owner's own todo — the dispatcher's claim/candidate predicates skip it
# unconditionally (it is NOT a Hermes profile and must never reach
# `hermes --profile owner`). Owner tasks sit `ready` until the owner closes
# them from the dashboard. Deliberately NOT in ASSIGNEE_PROFILES: that tuple
# is the roster of REAL agents (gateways, Agents page, chat, session stores
# all iterate it) — `owner` is assignable, never an agent.
OWNER_ASSIGNEE = "owner"
ASSIGNEE_PROFILES = (ORCHESTRATOR_AGENT,) + SPECIALIST_PROFILES
ASSIGNABLE = ASSIGNEE_PROFILES + (OWNER_ASSIGNEE,)


def validate_assignee(assignee):
    """Return a validated assignee_profile, or raise ValueError.

    `None`/empty means "unassigned" and is preserved verbatim — legacy rows and
    fixtures intentionally carry a null assignee, and the dispatcher falls back
    to its DEFAULT_ASSIGNEE for those. Anything else must be an EXACT member of
    the canonical roster: matching is case- and whitespace-sensitive on purpose,
    because a near-miss (`Coder`, `orchestrator `, `orch`) would otherwise be
    routed straight into `hermes --profile <name>` and die at launch.
    """
    if assignee is None:
        return None
    if not isinstance(assignee, str):
        raise ValueError("assignee_profile must be a string, got %r"
                         % (assignee,))
    if assignee == "":
        return None
    if assignee not in ASSIGNABLE:
        raise ValueError(
            "unknown assignee profile %r — must be one of %s (or unassigned)"
            % (assignee, ", ".join(ASSIGNABLE)))
    return assignee


def profile_hermes_home(profiles_dir, agent):
    """The HERMES_HOME an agent's Hermes process runs under, given a profiles
    dir. Single canonical mapping — symlinks are resolved ONCE here so every
    caller (engine, run wrapper, dashboard reader) derives byte-identical paths
    and no two views of the same store diverge.

    Specialist -> <profiles_dir>/<agent>   (i.e. `hermes --profile <agent>`)
    Orchestrator -> the ROOT home, the parent of the profiles dir
                    (/opt/data/profiles -> /opt/data), i.e. `--profile default`.
    """
    root = os.path.realpath(profiles_dir)
    if agent == ORCHESTRATOR_AGENT:
        return os.path.dirname(root)
    return os.path.join(root, agent)


def profile_state_db(profiles_dir, agent):
    """The Hermes state.db under an agent's home — canonical, symlink-resolved.

    Shared by the engine (agent_session_db_path) and the dashboard reader
    (wm_dash.reader._profile_db) so a probe, a liveness check and a session
    listing can never disagree about which file they are reading.
    """
    return os.path.join(profile_hermes_home(profiles_dir, agent), "state.db")


def hermes_root_home():
    """The ROOT Hermes home — the `default` profile's HERMES_HOME."""
    return profile_hermes_home(resolve_profiles_dir(), ORCHESTRATOR_AGENT)


def agent_hermes_home(agent):
    """HERMES_HOME for an agent, against the configured profiles dir."""
    return profile_hermes_home(resolve_profiles_dir(), agent)


def hermes_profile_arg(agent):
    """The value to pass to `hermes --profile <...>` for an agent.

    The reserved Orchestrator maps to Hermes' `default` profile; every
    specialist is its own profile name. Callers ALWAYS pass `--profile` — an
    explicit profile is what stops an inherited specialist `HERMES_HOME` or a
    sticky `active_profile` from silently capturing an orchestrator run.
    """
    return DEFAULT_PROFILE if agent == ORCHESTRATOR_AGENT else agent


def agent_session_db_path(agent):
    """The Hermes state.db holding an agent's sessions, or None-safe path.

    Every specialist agent runs `hermes --profile <agent>`, so its sessions
    live in `profiles/<agent>/state.db`. The Orchestrator runs on Hermes'
    DEFAULT profile (`--profile default`), so its sessions live in the DEFAULT
    store — the sibling of the profiles dir (/opt/data/profiles ->
    /opt/data/state.db). Misdirecting orchestrator probes into
    profiles/orchestrator/state.db (which does not exist) was the root cause of
    orchestrator runs not appearing live and their session_id never being
    captured.
    """
    return profile_state_db(resolve_profiles_dir(), agent)


def resolve_hermes():
    return os.environ.get("WM_HERMES") or HERMES


def resolve_py():
    return os.environ.get("WM_PY") or HERMES_PY


def resolve_projects_root():
    """Projects root boundary for the dashboard's path-containment rule, or
    None when no boundary is configured (raw CLI mode)."""
    return os.environ.get("WM_PROJECTS_ROOT") or None


def _require_path_contained(path):
    """Defensive F-1 containment: when a projects_root is configured, reject
    a primary_path that resolves outside it. When no root is configured
    (CLI-only, no dashboard env), there is no boundary to enforce."""
    root = resolve_projects_root()
    if not root:
        return
    root = os.path.realpath(root)
    if not (path == root or path.startswith(root + os.sep)):
        raise ValueError(
            "primary_path %r is outside projects_root %r" % (path, root))


def ensure_runs_dir():
    os.makedirs(resolve_runs_dir(), exist_ok=True)


def completion_path(run_id):
    return os.path.join(resolve_runs_dir(), "%d.completion.json" % run_id)


def brief_path(run_id):
    return os.path.join(resolve_runs_dir(), "%d.brief.txt" % run_id)


def answer_path(run_id):
    """Owner answers to a running run's question (Group 10): appended by the
    dashboard, read by the agent between steps (the brief names this path)."""
    return os.path.join(resolve_runs_dir(), "%d.answer.txt" % run_id)


def run_log_path(run_id):
    return os.path.join(resolve_runs_dir(), "%d.log" % run_id)

REVIEW_POLICIES = ("none", "required", "optional")


def _connect(db_path=None):
    if db_path is None:
        db_path = resolve_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    # WAL is a persistent property of the database file, but set it each
    # connect to be safe (no-op after the first time).
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wm_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY,
    slug          TEXT UNIQUE,
    name          TEXT,
    description   TEXT,
    primary_path  TEXT,
    created_at    REAL,
    archived      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER REFERENCES projects(id),
    title              TEXT,
    description        TEXT,
    acceptance_criteria TEXT,
    status             TEXT,
    created_at         REAL,
    updated_at         REAL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES projects(id),
    goal_id            INTEGER REFERENCES goals(id),
    title              TEXT,
    description        TEXT,
    definition_of_done TEXT,
    assignee_profile   TEXT,
    status             TEXT DEFAULT 'planned',
    review_policy      TEXT DEFAULT 'none',
    owner_approval     INTEGER DEFAULT 0,
    is_code            INTEGER DEFAULT 0,
    result_path        TEXT,
    result_paths       TEXT,
    feedback           TEXT,
    summary            TEXT,
    claimed_at         REAL,
    heartbeat_at       REAL,
    created_at         REAL,
    updated_at         REAL
);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id            INTEGER REFERENCES tasks(id),
    depends_on_task_id INTEGER REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    task_id       INTEGER REFERENCES tasks(id),
    agent_profile TEXT,
    session_id    TEXT,
    status        TEXT DEFAULT 'running',
    started_at    REAL,
    finished_at   REAL,
    heartbeat_at  REAL,
    exit_code     INTEGER,
    error         TEXT,
    notes         TEXT,
    completion    TEXT,
    result_paths  TEXT,
    workdir       TEXT,
    branch        TEXT,
    pid           INTEGER,
    brief_path    TEXT,
    review_id     INTEGER
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY,
    task_id         INTEGER REFERENCES tasks(id),
    reviewer_profile TEXT,
    status          TEXT,
    session_id      TEXT,
    verdict         TEXT,
    comments        TEXT,
    requested_at    REAL,
    decided_at      REAL,
    review_policy   TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id           INTEGER PRIMARY KEY,
    ts           REAL,
    project_id   INTEGER REFERENCES projects(id),
    goal_id      INTEGER REFERENCES goals(id),
    task_id      INTEGER REFERENCES tasks(id),
    run_id       INTEGER REFERENCES runs(id),
    agent_profile TEXT,
    session_id   TEXT,
    action       TEXT,
    detail       TEXT,
    model        TEXT
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER REFERENCES tasks(id),
    run_id      INTEGER REFERENCES runs(id),
    ts          REAL,
    from_status TEXT,
    to_status   TEXT,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          INTEGER PRIMARY KEY,
    profile     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    title       TEXT,
    created_at  REAL,
    UNIQUE (profile, session_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    ts          REAL,
    kind        TEXT,
    title       TEXT,
    body        TEXT,
    href        TEXT,
    task_id     INTEGER,
    run_id      INTEGER,
    project_id  INTEGER,
    source_key  TEXT UNIQUE,
    read_at     REAL
);

CREATE TABLE IF NOT EXISTS schedules (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    cron               TEXT NOT NULL,
    zone               TEXT NOT NULL DEFAULT 'Asia/Karachi',
    project_id         INTEGER NOT NULL REFERENCES projects(id),
    title              TEXT NOT NULL,
    description        TEXT DEFAULT '',
    definition_of_done TEXT DEFAULT '',
    assignee_profile   TEXT,
    goal_id            INTEGER REFERENCES goals(id),
    review_policy      TEXT DEFAULT 'none',
    is_code            INTEGER DEFAULT 0,
    overlap            TEXT DEFAULT 'skip',      -- skip | always
    one_shot           INTEGER DEFAULT 0,        -- fire once, then disable (Second Brain one-time reminders)
    heartbeat          TEXT DEFAULT '',          -- named cheap pre-fire check; nothing new => skipped, no task
    enabled            INTEGER DEFAULT 1,
    created_at         REAL,
    updated_at         REAL,
    last_fired_at      REAL,
    next_fire_at       REAL,
    last_task_id       INTEGER REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS schedule_runs (
    id          INTEGER PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES schedules(id),
    ts          REAL,
    kind        TEXT,                            -- fired | late | skipped | manual | error
    task_id     INTEGER REFERENCES tasks(id),
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY,
    endpoint    TEXT UNIQUE NOT NULL,
    keys_json   TEXT NOT NULL,
    user_agent  TEXT,
    created_at  REAL,
    last_ok_at  REAL,
    failures    INTEGER DEFAULT 0
);

-- Second Brain (intent/SecondBrainPlan.md, Phase 1) -------------------------
CREATE TABLE IF NOT EXISTS areas (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES areas(id),
    position   INTEGER DEFAULT 0,
    created_at REAL,
    archived   INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_areas_name
    ON areas(name, COALESCE(parent_id, 0));

CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    body         TEXT DEFAULT '',
    type         TEXT DEFAULT 'note',      -- note | playbook | wiki
    status       TEXT DEFAULT 'inbox',     -- inbox | active | archived
    area_id      INTEGER REFERENCES areas(id),
    project_id   INTEGER REFERENCES projects(id),
    tags         TEXT DEFAULT '[]',        -- JSON list of strings
    authored_by  TEXT DEFAULT 'owner',     -- owner | librarian | import
    content_hash TEXT,                     -- sha256 of imported source body
    pinned       INTEGER DEFAULT 0,
    disputed     INTEGER DEFAULT 0,        -- 2b: contradiction approved — keep-both, never silently reconciled

    created_at   REAL,
    updated_at   REAL
);

CREATE TABLE IF NOT EXISTS note_entries (
    id         INTEGER PRIMARY KEY,
    note_id    INTEGER NOT NULL REFERENCES notes(id),
    body       TEXT NOT NULL,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS note_revisions (
    id         INTEGER PRIMARY KEY,
    note_id    INTEGER NOT NULL REFERENCES notes(id),
    title      TEXT,
    body       TEXT,
    tags       TEXT,
    edited_by  TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS note_links (
    note_id    INTEGER NOT NULL REFERENCES notes(id),
    kind       TEXT NOT NULL,              -- task | schedule | note (disputed pair)
    target_id  INTEGER NOT NULL,
    created_at REAL,
    PRIMARY KEY (note_id, kind, target_id)
);

-- Second Brain Phase 2a: the librarian's ONLY write surface. A proposal is a
-- suggested change to the Library; nothing here touches the note tables until
-- the OWNER approves it (approve_proposal). Agents write via `wm note
-- propose-*`; the review queue reads/decides via the owner-session HTTP API.
CREATE TABLE IF NOT EXISTS proposals (
    id             INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL,               -- split | file | contradiction | new_task (P4 adds wiki_update)
    note_id        INTEGER REFERENCES notes(id),
    payload        TEXT NOT NULL DEFAULT '{}',  -- JSON, kind-specific (validated in create_proposal)
    summary        TEXT DEFAULT '',
    classification TEXT DEFAULT 'needs_attention', -- routine | needs_attention (routine bulk-approves)
    status         TEXT DEFAULT 'pending',      -- pending | approved | rejected | superseded
    author         TEXT DEFAULT 'librarian',
    feedback       TEXT,                        -- owner feedback on reject; librarian reads it on revision
    result         TEXT,                        -- JSON record of what approval produced (e.g. {"note_ids": [...]})
    created_at     REAL,
    decided_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_note   ON proposals(note_id);

-- 2b-ii: closed tag taxonomy. The OWNER is the authority: owner/import writes
-- auto-register their tags; agent proposals must use registered tags or
-- explicitly declare coinage (payload new_tags), registered at owner approval.
CREATE TABLE IF NOT EXISTS note_tag_taxonomy (
    tag        TEXT PRIMARY KEY,
    added_by   TEXT DEFAULT 'owner',
    created_at REAL
);

CREATE INDEX IF NOT EXISTS idx_notes_status   ON notes(status);
CREATE INDEX IF NOT EXISTS idx_notes_area     ON notes(area_id);
CREATE INDEX IF NOT EXISTS idx_notes_project  ON notes(project_id);
CREATE INDEX IF NOT EXISTS idx_entries_note   ON note_entries(note_id);
CREATE INDEX IF NOT EXISTS idx_revisions_note ON note_revisions(note_id);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_goal    ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_deps_task     ON task_deps(task_id);
CREATE INDEX IF NOT EXISTS idx_deps_dep      ON task_deps(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_runs_task     ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_activity_task ON activity(task_id);
"""


def _table_columns(conn, table):
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def _migrate(conn):
    """Idempotent in-place migrations for pre-existing stores.

    Runs apply to the *live* DB without a destructive table rebuild. Only
    columns/constraints that can be added cheaply come through here; schema
    changes that require a rebuild are reflected in SCHEMA_SQL for fresh
    databases and otherwise enforced at the app layer.
    """
    if "completion" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN completion TEXT")
    if "pid" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN pid INTEGER")
    if "brief_path" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN brief_path TEXT")
    if "review_id" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN review_id INTEGER")
    if "is_code" not in _table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN is_code INTEGER DEFAULT 0")
    if "result_paths" not in _table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN result_paths TEXT")
    # Phase 6.5.2: readable owner/reviewer feedback on the task itself (the
    # full-text field surfaced next to Description in the dashboard), written
    # by owner_feedback / review_verdict(changes_requested). This is the ONLY
    # schema change in 6.5.2.
    if "feedback" not in _table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN feedback TEXT")
    if "result_paths" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN result_paths TEXT")
    if "workdir" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN workdir TEXT")
    if "branch" not in _table_columns(conn, "runs"):
        conn.execute("ALTER TABLE runs ADD COLUMN branch TEXT")
    # Phase 6 (CU-5): goals become editable, so they need the same
    # last-touched stamp tasks already carry. Existing rows keep NULL until
    # their first edit — a NULL updated_at means "never edited since create".
    if "updated_at" not in _table_columns(conn, "goals"):
        conn.execute("ALTER TABLE goals ADD COLUMN updated_at REAL")
    # Group 7: tasks spawned by a schedule keep the link for the list chip / detail back-link.
    if "schedule_id" not in _table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN schedule_id INTEGER REFERENCES schedules(id)")
    # Approval gate: a task flagged owner_approval lands on `manual`
    # ("Awaiting approval") instead of `done` — the owner closes or redirects.
    if "owner_approval" not in _table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN owner_approval INTEGER DEFAULT 0")
    # Second Brain P1.1: one-time reminders — a one_shot schedule disables
    # itself after its first real firing.
    if "one_shot" not in _table_columns(conn, "schedules"):
        conn.execute("ALTER TABLE schedules ADD COLUMN one_shot INTEGER DEFAULT 0")
    # Second Brain P2a: a schedule can name a cheap deterministic pre-fire
    # check (heartbeat). When the check says "nothing new", fire_due records a
    # skipped run and mints NO task — so no agent run and no model call.
    if "heartbeat" not in _table_columns(conn, "schedules"):
        conn.execute("ALTER TABLE schedules ADD COLUMN heartbeat TEXT DEFAULT ''")
    # Second Brain P2b-i: an approved contradiction proposal flags BOTH notes
    # disputed (keep-both; the owner clears the flag once resolved).
    if "disputed" not in _table_columns(conn, "notes"):
        conn.execute("ALTER TABLE notes ADD COLUMN disputed INTEGER DEFAULT 0")
    # Second Brain P2b-ii: seed the closed taxonomy from tags already in use
    # (idempotent: only when the taxonomy is empty and notes carry tags).
    if conn.execute("SELECT 1 FROM note_tag_taxonomy LIMIT 1").fetchone() is None:
        seen = set()
        for r in conn.execute("SELECT tags FROM notes"):
            try:
                seen.update(t.strip() for t in json.loads(r["tags"] or "[]")
                            if isinstance(t, str) and t.strip())
            except ValueError:
                continue
        now = time.time()
        for t in sorted(seen):
            conn.execute("INSERT OR IGNORE INTO note_tag_taxonomy(tag, added_by, created_at) "
                         "VALUES(?, 'owner', ?)", (t, now))


def init_db(db_path=None):
    """Create the full schema and seed wm_meta. Idempotent."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
            _migrate(conn)
            meta = {
                "schema_version": SCHEMA_VERSION,
                "concurrency_cap": "3",
                "stall_seconds": "300",
                "paused": "0",
                "retention_days": "180",
                "backup_dir": os.path.join(resolve_runs_dir(), "backups"),
                "backup_interval_hours": "24",
                "code_worktree": "1",
            }
            for key, value in meta.items():
                conn.execute(
                    "INSERT OR IGNORE INTO wm_meta(key, value) VALUES(?, ?)",
                    (key, value))
            # Force the running schema version to the code's current version so
            # an in-place migration (v1 -> v2) is reflected in meta.
            conn.execute(
                "UPDATE wm_meta SET value=? WHERE key='schema_version'",
                (SCHEMA_VERSION,))
            _init_notes_fts(conn)
            _seed_areas(conn)
    finally:
        conn.close()


# Life areas seeded once (empty table only) from the owner's kamran-focus
# domains — the interviewed starting taxonomy (intent/SecondBrainPlan.md).
SEED_AREAS = ("AI Workflow", "Career", "Content", "Family", "Finance",
              "Health", "Home", "Journal", "Side Project", "Study", "Work")

# Set False when this SQLite build lacks FTS5; note search then degrades to
# LIKE. Probed once per process in _init_notes_fts.
NOTES_FTS = True


def _init_notes_fts(conn):
    """Create the notes FTS5 index; degrade gracefully without FTS5.

    A plain (not external-content) FTS5 table keyed by note id via rowid.
    unicode61 handles Arabic-script (Urdu) tokens acceptably — accepted in
    the plan; a trigram index is the later fix if recall disappoints.
    """
    global NOTES_FTS
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
            "title, body, tags, tokenize='unicode61')")
        NOTES_FTS = True
    except sqlite3.OperationalError:
        NOTES_FTS = False


def _seed_areas(conn):
    if conn.execute("SELECT COUNT(*) AS n FROM areas").fetchone()["n"]:
        return
    now = time.time()
    for i, name in enumerate(SEED_AREAS):
        conn.execute(
            "INSERT INTO areas(name, parent_id, position, created_at) "
            "VALUES(?, NULL, ?, ?)", (name, i, now))


# ---------------------------------------------------------------------------
# wm_meta
# ---------------------------------------------------------------------------
def append_meta(key, value, db_path=None):
    """Upsert a wm_meta key (preserving an existing value unless key present)."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO wm_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    finally:
        conn.close()


def get_meta(key, default=None, db_path=None):
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM wm_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------
def create_project(slug, name, description="", primary_path="", db_path=None):
    if not _SLUG_RE.fullmatch(str(slug)):
        raise ValueError(
            "slug must match ^[a-z0-9][a-z0-9-]*$ (got %r)" % (slug,))
    if not primary_path or not str(primary_path).strip():
        raise ValueError(
            "project requires a non-empty --path / primary_path (the canonical "
            "working path)")
    if not os.path.isabs(primary_path):
        raise ValueError("primary_path must be an absolute directory path")
    primary_path = os.path.realpath(primary_path)
    _require_path_contained(primary_path)
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO projects(slug, name, description, primary_path, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (slug, name, description, primary_path, time.time()),
            )
            pid = cur.lastrowid
        log_activity(action="project_create", project_id=pid,
                     agent_profile="cli", detail="slug=%s" % slug, db_path=db_path)
        return pid
    finally:
        conn.close()


def update_project(slug, name=None, description=None, primary_path=None,
                   db_path=None):
    """Update a project's identity metadata (name / description / primary_path).

    The slug is the immutable identity + route key — it is NEVER updated here
    (changing it would break `#project/<slug>` links, breadcrumbs and the
    primary_path convention). Validation mirrors create_project. Every change
    is a real `project_update` activity row.

    Returns the updated project row as a plain dict. Raises ValueError for a
    missing slug or invalid field values. A call with no editable fields is a
    no-op (returns the current row unchanged, no activity).
    """
    conn = _connect(db_path)
    try:
        row = _require_project(conn, slug)
        updates = {}
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValueError("name must be non-empty")
            if len(name) > 120:
                raise ValueError("name must be 120 characters or fewer")
            updates["name"] = name
        if description is not None:
            updates["description"] = str(description).strip()
        if primary_path is not None:
            p = str(primary_path).strip()
            if not p:
                raise ValueError("primary_path must be non-empty")
            if not os.path.isabs(p):
                raise ValueError("primary_path must be an absolute directory path")
            rp = os.path.realpath(p)
            _require_path_contained(rp)
            updates["primary_path"] = rp
        out = dict(row)
        if not updates:
            return out
        cols = ", ".join("%s = ?" % k for k in updates)
        with conn:
            conn.execute("UPDATE projects SET %s WHERE id = ?" % cols,
                         list(updates.values()) + [row["id"]])
        out.update(updates)
        log_activity(action="project_update", project_id=row["id"],
                     agent_profile="cli",
                     detail="updated %s" % ", ".join(updates.keys()),
                     db_path=db_path)
        return out
    finally:
        conn.close()


def set_project_archived(slug, flag, db_path=None):
    """Set a project's archived visibility flag (0 = active, 1 = archived).

    Archiving is an organisation / visibility flag only — it NEVER mutates
    task / goal / run rows (a dormant project keeps its real status). Logs a
    real `project_archive` (flag=1) or `project_restore` (flag=0) activity row.

    Returns the updated project row as a plain dict. Raises ValueError for a
    missing slug.
    """
    flag = 1 if flag else 0
    conn = _connect(db_path)
    try:
        row = _require_project(conn, slug)
        with conn:
            conn.execute("UPDATE projects SET archived = ? WHERE id = ?",
                         (flag, row["id"]))
        out = dict(row)
        out["archived"] = flag
        log_activity(action="project_archive" if flag else "project_restore",
                     project_id=row["id"], agent_profile="cli",
                     detail="archived=%d" % flag, db_path=db_path)
        return out
    finally:
        conn.close()


def list_projects(db_path=None):
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM projects ORDER BY archived ASC, created_at ASC"
        ).fetchall()
    finally:
        conn.close()


def get_project(project_id=None, slug=None, db_path=None):
    conn = _connect(db_path)
    try:
        if slug is not None:
            row = conn.execute(
                "SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return row
    finally:
        conn.close()


def _require_project(conn, slug):
    row = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise ValueError("no project with slug '%s'" % slug)
    return row


# ---------------------------------------------------------------------------
# goals
# ---------------------------------------------------------------------------
def create_goal(project_slug, title, description="", acceptance_criteria="",
                db_path=None):
    """Create a goal in `draft` (Phase 6.5).

    A brand-new goal has no agreed decomposition, so it is NOT releasable: it
    must go through `request_goal_planning` -> `planning` -> `planned` before
    `release_goal` will accept it. This is the single create-path status.
    """
    conn = _connect(db_path)
    try:
        with conn:
            proj = _require_project(conn, project_slug)
            now = time.time()
            cur = conn.execute(
                "INSERT INTO goals(project_id, title, description, "
                "acceptance_criteria, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (proj["id"], title, description, acceptance_criteria,
                 "draft", now, now),
            )
            gid = cur.lastrowid
        log_activity(action="goal_create", project_id=proj["id"], goal_id=gid,
                     agent_profile="cli", detail=title, db_path=db_path)
        return gid
    finally:
        conn.close()


def update_goal(goal_id, title=None, description=None, acceptance_criteria=None,
                db_path=None):
    """Update a DRAFT goal's plan text (title / description / acceptance_criteria).

    The goal's `status` is NEVER touched here — release is the only status
    transition and it belongs to `release_goal` (the approval gate).

    Phase 6.5.1 (owner decision 1): the goal text is editable ONLY while the
    goal is `draft`. Once it has been sent to planning the text is the brief the
    Orchestrator decomposed against, so silently rewriting it would leave the
    goal's child tasks implementing a scope nobody agreed to — and for a
    `released` goal it would move the very thing the approval gate approved. A
    scope change after that point is a NEW goal, not an edit. To re-open a
    `planning` goal for editing, abandon it back to draft
    (`set_goal_status(id, 'draft')`).

    Every change bumps `updated_at` and writes a real `goal_update` activity row.

    Returns the updated goal row as a plain dict. Raises ValueError for a
    missing goal, a non-`draft` goal, or invalid field values. A call with no
    editable fields is a no-op (returns the current row unchanged, no activity,
    no timestamp bump) — but it is still refused on a non-draft goal, so the
    caller never gets a 200-shaped answer for an edit the engine would refuse.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise ValueError("no goal with id %s" % goal_id)
        if row["status"] != "draft":
            raise ValueError("goal %d is '%s' — edit is only allowed while the "
                             "goal is 'draft'" % (goal_id, row["status"]))
        updates = {}
        if title is not None:
            title = str(title).strip()
            if not title:
                raise ValueError("title must be non-empty")
            if len(title) > 200:
                raise ValueError("title must be 200 characters or fewer")
            updates["title"] = title
        if description is not None:
            updates["description"] = str(description).strip()
        if acceptance_criteria is not None:
            updates["acceptance_criteria"] = str(acceptance_criteria).strip()
        out = dict(row)
        if not updates:
            return out
        updates["updated_at"] = time.time()
        cols = ", ".join("%s = ?" % k for k in updates)
        with conn:
            conn.execute("UPDATE goals SET %s WHERE id = ?" % cols,
                         list(updates.values()) + [row["id"]])
        out.update(updates)
        log_activity(action="goal_update", project_id=row["project_id"],
                     goal_id=row["id"], agent_profile="cli",
                     detail="updated %s" % ", ".join(
                         k for k in updates if k != "updated_at"),
                     db_path=db_path)
        return out
    finally:
        conn.close()


def set_goal_status(goal_id, new_status, detail=None, db_path=None, force=False):
    """The ONE whitelisted goal-status mutator (Phase 6.5).

    Moves a goal along the lifecycle `draft -> planning -> planned` (plus the
    `planning -> draft` abandon edge) and writes a real `goal_status` activity
    row for every flip. `planned -> released` is NOT here: release is the
    approval gate and belongs to release_goal(), which also re-gates the goal's
    child tasks. `released` is terminal.

    `force=True` bypasses the EDGE whitelist only — it is for the one-shot
    Phase-6.5 `wm goal backfill-draft --apply` (planned-with-0-tasks -> draft),
    which must still be audited. It never bypasses the status-name validation
    and it never writes a status outside GOAL_STATUSES.

    Returns the updated goal row as a plain dict. Raises ValueError for an
    unknown goal, an unknown status, or a disallowed edge.
    """
    if new_status not in GOAL_STATUSES:
        raise ValueError("unknown goal status '%s' — must be one of %s"
                         % (new_status, ", ".join(GOAL_STATUSES)))
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise ValueError("no goal with id %s" % goal_id)
        old_status = row["status"]
        if not force and (old_status, new_status) not in GOAL_TRANSITIONS:
            raise ValueError(
                "goal %d cannot go '%s' -> '%s' (allowed: %s%s)"
                % (goal_id, old_status, new_status,
                   "; ".join("%s -> %s" % e for e in GOAL_TRANSITIONS),
                   "; planned -> released via release_goal"))
        now = time.time()
        closed_task_id = None
        with conn:
            conn.execute("UPDATE goals SET status=?, updated_at=? WHERE id=?",
                         (new_status, now, goal_id))
            # F-1 (Phase 6.5 review): leaving `planning` ENDS the decomposition
            # work item, so close the goal's open `Plan goal #N` task in the
            # same transaction as the flip. Left open it stays `planned` as a
            # child of this goal, and release_goal — which promotes EVERY child
            # in ('planned','waiting_approval') — would make it `ready`, at
            # which point the dispatcher claims it and launches a real
            # orchestrator run that re-decomposes an already-released goal.
            # `done` is outside that set, so release skips it.
            if old_status == "planning" and new_status != "planning":
                open_task = _open_planning_task(conn, goal_id)
                if open_task is not None:
                    _set_task_status(
                        open_task["id"], "done", _conn=conn,
                        detail="goal #%d left planning (-> %s); planning task "
                               "closed" % (goal_id, new_status))
                    closed_task_id = open_task["id"]
        out = dict(row)
        out["status"] = new_status
        out["updated_at"] = now
        log_activity(action="goal_status", project_id=row["project_id"],
                     goal_id=goal_id, agent_profile="cli",
                     detail=detail or ("goal #%d %s -> %s"
                                       % (goal_id, old_status, new_status)),
                     db_path=db_path)
        if closed_task_id is not None:
            log_activity(action="goal_planning_closed",
                         project_id=row["project_id"], goal_id=goal_id,
                         task_id=closed_task_id,
                         agent_profile=PLANNING_TASK_PROFILE,
                         detail="planning task #%d marked `done`: goal #%d went "
                                "%s -> %s, so the decomposition work item is "
                                "finished and must never become dispatchable"
                                % (closed_task_id, goal_id, old_status,
                                   new_status),
                         db_path=db_path)
        return out
    finally:
        conn.close()


def delete_goal(goal_id, db_path=None):
    """Delete a goal row — ONLY a 'draft' goal nothing references. Tasks and
    runs are never deleted (session markers re-attach on id reuse); goal ids
    have no such external markers, so a guarded delete is safe. Audited via a
    `goal_deleted` activity row carrying the title. Returns the deleted title."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise ValueError("no goal with id %s" % goal_id)
        if row["status"] != "draft":
            raise ValueError("goal %d is '%s' — only 'draft' goals can be "
                             "deleted" % (goal_id, row["status"]))
        tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE goal_id=?",
                             (goal_id,)).fetchone()[0]
        scheds = conn.execute("SELECT COUNT(*) FROM schedules WHERE goal_id=?",
                              (goal_id,)).fetchone()[0]
        if tasks or scheds:
            raise ValueError("goal %d is referenced (%d task(s), %d schedule(s))"
                             " — repoint or close them first" % (goal_id, tasks, scheds))
        with conn:
            # activity FK-references goals; history rows stay (their detail
            # text names the goal) but the linkage detaches with the row.
            conn.execute("UPDATE activity SET goal_id=NULL WHERE goal_id=?",
                         (goal_id,))
            conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
    finally:
        conn.close()
    log_activity(action="goal_deleted", project_id=row["project_id"],
                 agent_profile="cli",
                 detail="draft goal #%d deleted: %s" % (goal_id, row["title"] or ""),
                 db_path=db_path)
    return row["title"]


# A planning task is the Orchestrator's decomposition work item for a goal. It
# is identified by (goal, assignee, title prefix) — no schema change needed.
PLANNING_TASK_PREFIX = "Plan goal #"
PLANNING_TASK_PROFILE = "orchestrator"
# Statuses that mean "this planning task is finished" — anything else counts as
# an OPEN planning task and makes a second `plan` request idempotent.
_PLANNING_TASK_CLOSED = ("done", "failed")


def _open_planning_task(conn, goal_id):
    """The goal's open Orchestrator planning task row, or None.

    ACCEPTED HEURISTIC (F-4, Phase 6.5 review): the match is by shape —
    (goal_id, assignee_profile='orchestrator', title LIKE 'Plan goal #%') — not
    by an explicit marker, because Phase 6.5 ships with no schema change (no
    `is_planning_task` column, no migration). A HUMAN-authored task that happens
    to have all three properties is therefore indistinguishable from a real
    planning task: a genuine `/plan` on that goal would report
    `already_planning` and adopt the human's task, and leaving `planning` would
    mark it `done`. Judged acceptable — the title prefix + the reserved
    orchestrator assignee are an unlikely accident — but a future phase that
    adds a column should replace this predicate, not extend it.
    """
    return conn.execute(
        "SELECT * FROM tasks WHERE goal_id=? AND assignee_profile=? "
        "AND title LIKE ? AND status NOT IN (%s) ORDER BY id LIMIT 1"
        % ",".join("?" * len(_PLANNING_TASK_CLOSED)),
        (goal_id, PLANNING_TASK_PROFILE, PLANNING_TASK_PREFIX + "%")
        + _PLANNING_TASK_CLOSED).fetchone()


def request_goal_planning(goal_id, db_path=None):
    """PLAN a draft goal: park a real decomposition task + flip -> `planning`.

    ONE store ENTRY POINT (Phase 6.5 CU-D) so the HTTP and CLI layers never have
    to sequence the writes themselves — but NOT one atomic transaction (F-2):
    create_task, set_goal_status and log_activity each open their own
    connection, so this creates the planning task and flips the status as three
    separate store calls:

      1. create_task(... assignee 'orchestrator' ...) — a real `Plan goal #N`
         task. The goal is still `draft` (i.e. NOT released) at this point, so
         create_task's own release gate gives it init_status `planned`
         (backlog). That is load-bearing: a planning task must NEVER be `ready`,
         because the dispatcher would then auto-run it. set_goal_status closes
         the task again (-> `done`) when the goal later leaves `planning`, so
         release_goal cannot promote it.
      2. set_goal_status(goal_id, 'planning') — audited via `goal_status`.
      3. a `goal_plan_requested` activity row tying the goal to its task.

    Because those steps are not one transaction, a crash between 1 and 2 leaves
    an orphan `Plan goal #N` task with the goal still `draft`. The idempotency
    guard is therefore keyed on the OPEN PLANNING TASK, not on the status: a
    goal that already has one returns it with already=True and creates no
    second task, whether the goal is `planning` (normal repeat) or still
    `draft` (crash retry — the interrupted status flip is completed here). A
    goal whose planning task was closed gets a fresh one (already=False).

    Returns (goal_row_dict, planning_task_id, already). Raises ValueError for an
    unknown goal or one that is neither `draft` nor `planning`.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if row is None:
            raise ValueError("no goal with id %s" % goal_id)
        status = row["status"]
        if status not in ("draft", "planning"):
            raise ValueError("goal %d is '%s' — only a draft goal can be sent "
                             "to planning" % (goal_id, status))
        # F-2: the guard is keyed on the open planning task for BOTH source
        # statuses, not just `planning`. A crash between create_task and
        # set_goal_status leaves the task parked with the goal still `draft`;
        # guarding only the `planning` branch let the retry create a SECOND
        # planning task for the same goal.
        existing = _open_planning_task(conn, goal_id)
        existing_tid = existing["id"] if existing is not None else None
        slug = title = desc = None
        if existing_tid is None:
            proj = conn.execute("SELECT * FROM projects WHERE id=?",
                                (row["project_id"],)).fetchone()
            if proj is None:
                raise ValueError("goal %d has no project" % goal_id)
            slug = proj["slug"]
            title = row["title"] or "-"
            desc = "\n\n".join(p for p in (
                (row["description"] or "").strip(),
                ("Acceptance criteria:\n" + row["acceptance_criteria"].strip())
                if (row["acceptance_criteria"] or "").strip() else "") if p)
    finally:
        conn.close()

    if existing_tid is not None and status == "planning":
        # Plain repeat of a request already fully applied: nothing to write.
        return dict(row), existing_tid, True

    # create_task / set_goal_status / log_activity each open their own
    # connection, so nothing above may still hold a write transaction here.
    if existing_tid is not None:
        # Crash retry: adopt the orphan task and finish the interrupted flip
        # (status is still `draft`, so steps 2 and 3 never ran).
        tid, already = existing_tid, True
    else:
        tid, already = create_task(
            slug,
            "%s%d: %s" % (PLANNING_TASK_PREFIX, goal_id, title),
            description=desc,
            definition_of_done="Goal #%d decomposed into tasks with specialist "
                               "assignees; goal set to `planned`." % goal_id,
            assignee_profile=PLANNING_TASK_PROFILE,
            goal_id=goal_id,
            db_path=db_path), False
    if status == "draft":
        out = set_goal_status(goal_id, "planning",
                              detail="planning requested (task #%d)" % tid,
                              db_path=db_path)
    else:
        out = dict(row)
    log_activity(action="goal_plan_requested", project_id=row["project_id"],
                 goal_id=goal_id, task_id=tid,
                 agent_profile=PLANNING_TASK_PROFILE,
                 detail="goal #%d -> planning; planning task #%d %s in the "
                        "backlog (no agent starts automatically)"
                        % (goal_id, tid,
                           "adopted (crash retry)" if already else "parked"),
                 db_path=db_path)
    return out, tid, already


def get_goal(goal_id, db_path=None):
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    finally:
        conn.close()


def goal_is_released(goal_id, db_path=None):
    """A goal is 'released' (approved) when its status is 'released'."""
    if goal_id is None:
        return False
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT status FROM goals WHERE id=?", (goal_id,)).fetchone()
        return bool(row and row["status"] == "released")
    finally:
        conn.close()


def release_goal(goal_id, db_path=None):
    """Approve/release a Goal plan -> its eligible child tasks may proceed.

    Sets the goal to 'released'. For each child task of the goal:
      - deps all done -> becomes `ready` immediately (release is the gate);
      - deps not done -> becomes `waiting_approval` (released plan; the
        dispatcher promotes it to `ready` automatically once deps complete).
    A `planned` task under a *released* goal no longer needs a separate per-task
    approval to be eligible. Un-released goals leave their tasks `planned`.

    Returns (goal_status, [(task_id, new_status), ...]). Refuses a nonexistent
    goal, and (Phase 6.5) any goal that is not `planned` — a `draft` or
    `planning` goal has no agreed plan to approve. An already-`released` goal
    is NOT refused: explicit re-release is a no-op returning its tasks' current
    eligible state.
    """
    conn = _connect(db_path)
    try:
        with conn:
            g = conn.execute("SELECT * FROM goals WHERE id=?",
                             (goal_id,)).fetchone()
            if g is None:
                raise ValueError("no goal with id %s" % goal_id)
            if g["status"] == "released":
                # F-2: idempotent — a re-release is a no-op that reports the
                # current state of the children without touching them.
                children = conn.execute(
                    "SELECT id FROM tasks WHERE goal_id=? ORDER BY id",
                    (goal_id,)).fetchall()
                out = []
                for c in children:
                    row = conn.execute("SELECT status FROM tasks WHERE id=?",
                                       (c["id"],)).fetchone()
                    out.append((c["id"], row["status"] if row else "planned"))
                log_activity(action="goal_release", goal_id=goal_id,
                             agent_profile="cli",
                             detail="goal #%d already released; no-op (idempotent)"
                                    % goal_id,
                             db_path=db_path)
                return "released", out
            # CU-E (Phase 6.5): valid-source guard. Release is the approval gate
            # for an AGREED plan, so only a `planned` goal may pass it. A
            # `draft`/`planning` goal has no decomposition yet — releasing it
            # approved nothing and (pre-6.5) silently made an empty goal look
            # shipped.
            if g["status"] != "planned":
                raise ValueError(
                    "goal %d is '%s' — it must be planned before release"
                    % (goal_id, g["status"]))
            conn.execute("UPDATE goals SET status='released' WHERE id=?",
                         (goal_id,))
            children = conn.execute(
                "SELECT id FROM tasks WHERE goal_id=? ORDER BY id",
                (goal_id,)).fetchall()
            out = []
            for c in children:
                row = conn.execute("SELECT status, owner_approval FROM tasks WHERE id=?",
                                   (c["id"],)).fetchone()
                cur_st = row["status"] if row else None
                new_st = cur_st
                if cur_st in ("planned", "waiting_approval"):
                    ns = ("ready" if not row["owner_approval"]
                          and deps_done(c["id"], db_path=db_path)
                          else "waiting_approval")
                    _set_task_status(c["id"], ns, db_path=db_path, run_id=None,
                                     detail="goal %d released" % goal_id,
                                     _conn=conn)
                    new_st = ns
                out.append((c["id"], new_st))
        log_activity(action="goal_release", goal_id=goal_id,
                     agent_profile="cli",
                     detail="goal #%d released/approved; children -> %s"
                            % (goal_id, "; ".join("#%d=%s" % (i, s)
                                                  for i, s in out) or "-"),
                     db_path=db_path)
        return "released", out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------
def create_task(project_slug, title, description="", definition_of_done="",
                assignee_profile=None, goal_id=None, review_policy="none",
                is_code=False, owner_approval=False, db_path=None):
    if review_policy not in REVIEW_POLICIES:
        raise ValueError("review_policy must be one of %s" % (REVIEW_POLICIES,))
    if not project_slug or not str(project_slug).strip():
        raise ValueError("task requires a project (project_slug is required; goal is optional)")
    # L1: the assignee is what the dispatcher hands to `hermes --profile <p>`,
    # so a malformed name must be rejected HERE (one write path) rather than
    # silently routed as if it were a specialist profile. Null stays null.
    assignee_profile = validate_assignee(assignee_profile)
    conn = _connect(db_path)
    try:
        with conn:
            proj = _require_project(conn, project_slug)
            released = False
            if goal_id is not None:
                g = conn.execute(
                    "SELECT * FROM goals WHERE id=? AND project_id=?",
                    (goal_id, proj["id"])).fetchone()
                if not g:
                    raise ValueError(
                        "goal %s does not belong to project '%s'" % (goal_id, project_slug))
                released = (g["status"] == "released")
            # Backlog/release gate: a task under an UNRELEASED goal (or goal-less,
            # i.e. not yet part of an approved plan) is created `planned` — parked,
            # never auto-runs. Under a RELEASED (approved) goal it is created
            # `waiting_approval` and becomes `ready` automatically once deps done
            # (eligible child tasks may continue automatically).
            now = time.time()
            init_status = "waiting_approval" if released else "planned"
            cur = conn.execute(
                "INSERT INTO tasks(project_id, goal_id, title, description, "
                "definition_of_done, assignee_profile, status, review_policy, "
                "owner_approval, is_code, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (proj["id"], goal_id, title, description, definition_of_done,
                 assignee_profile, init_status, review_policy,
                 1 if owner_approval else 0, 1 if is_code else 0, now, now),
            )
            tid = cur.lastrowid
            conn.execute(
                "INSERT INTO state_transitions(task_id, run_id, ts, from_status, "
                "to_status, detail) VALUES(?,NULL,?,NULL,?,?)",
                (tid, now, init_status, "created (goal released=%s)" % released))
        log_activity(action="task_create", project_id=proj["id"], goal_id=goal_id,
                     task_id=tid, agent_profile=assignee_profile,
                     detail=title, db_path=db_path)
        return tid
    finally:
        conn.close()


def list_tasks(project_slug=None, status=None, db_path=None):
    conn = _connect(db_path)
    try:
        sql = ("SELECT t.*, p.slug AS project_slug, g.title AS goal_title "
               "FROM tasks t "
               "LEFT JOIN projects p ON p.id = t.project_id "
               "LEFT JOIN goals g ON g.id = t.goal_id WHERE 1=1")
        params = []
        if project_slug is not None:
            sql += " AND p.slug = ?"
            params.append(project_slug)
        if status is not None:
            sql += " AND t.status = ?"
            params.append(status)
        sql += " ORDER BY t.status, t.created_at ASC"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def get_task(task_id, db_path=None):
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT t.*, p.slug AS project_slug, g.title AS goal_title "
            "FROM tasks t "
            "LEFT JOIN projects p ON p.id = t.project_id "
            "LEFT JOIN goals g ON g.id = t.goal_id "
            "WHERE t.id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def list_deps(depends_on_task_id, db_path=None):
    """Rows where the given task is the PREDECESSOR (things that depend on it)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT task_id FROM task_deps WHERE depends_on_task_id=?",
            (depends_on_task_id,)).fetchall()
    finally:
        conn.close()


def list_task_deps(task_id, db_path=None):
    """Rows where the given task is the DEPENDENT (things it depends on)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT depends_on_task_id FROM task_deps WHERE task_id=?",
            (task_id,)).fetchall()
    finally:
        conn.close()


def add_task_dep(task_id, depends_on_task_id, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_deps(task_id, depends_on_task_id) "
                "VALUES(?, ?)", (task_id, depends_on_task_id))
            conn.execute(
                "UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), task_id))
        log_activity(action="task_dep", task_id=task_id,
                     detail="depends_on=%d" % depends_on_task_id, db_path=db_path)
    finally:
        conn.close()


def create_phased_tasks(project_slug, title, description="", definition_of_done="",
                        assignee_profile=None, goal_id=None, owner_approval=False,
                        db_path=None):
    """Create a plan/build pair using the normal task and dependency writes."""
    plan_id = create_task(
        project_slug, "%s — plan" % title, description, definition_of_done,
        assignee_profile=assignee_profile, goal_id=goal_id,
        review_policy="none", is_code=False, owner_approval=True,
        db_path=db_path)
    build_id = create_task(
        project_slug, "%s — build" % title, description, definition_of_done,
        assignee_profile=assignee_profile, goal_id=goal_id,
        review_policy="required", is_code=True, owner_approval=owner_approval,
        db_path=db_path)
    add_task_dep(build_id, plan_id, db_path=db_path)
    return plan_id, build_id


def remove_task_dep(task_id, depends_on_task_id, db_path=None):
    """Remove one dependency edge, then re-check the dependent's eligibility —
    release-gate semantics unchanged (promotion only under a released goal with
    every remaining dep done). Returns True when the edge existed."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM task_deps WHERE task_id=? AND depends_on_task_id=?",
                (task_id, depends_on_task_id))
            if cur.rowcount == 0:
                return False
            conn.execute(
                "UPDATE tasks SET updated_at=? WHERE id=?", (time.time(), task_id))
            _mark_ready_if_deps_done(conn, task_id)
        log_activity(action="task_undep", task_id=task_id,
                     detail="removed depends_on=%d" % depends_on_task_id,
                     db_path=db_path)
        return True
    finally:
        conn.close()


def assign_task(task_id, profile, db_path=None):
    """Re-assign a task, validating against the canonical roster.

    L1: `create_task` is not the only write path for `assignee_profile` — this
    UPDATE is the other one, and what it stores is what the dispatcher hands to
    `hermes --profile <p>` (wm_dispatch: `t["assignee_profile"] or
    DEFAULT_ASSIGNEE`). Validating here too means no caller — CLI, script or
    future HTTP route — can park a malformed name on a task and have it reach
    dispatch. Null/empty is preserved as "unassigned" exactly as on create.
    """
    profile = validate_assignee(profile)
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET assignee_profile=?, updated_at=? WHERE id=?",
                (profile, time.time(), task_id))
            if cur.rowcount == 0:
                raise ValueError("no task with id %s" % task_id)
        log_activity(action="task_assign", task_id=task_id,
                     agent_profile=profile, db_path=db_path)
    finally:
        conn.close()


def deps_done(task_id, db_path=None):
    """True when every predecessor of task_id has status 'done'."""
    conn = _connect(db_path)
    try:
        deps = conn.execute(
            "SELECT d.depends_on_task_id AS dep, t.status AS status "
            "FROM task_deps d "
            "LEFT JOIN tasks t ON t.id = d.depends_on_task_id "
            "WHERE d.task_id = ?", (task_id,)).fetchall()
        return all(r["status"] == "done" for r in deps)
    finally:
        conn.close()


def mark_ready(task_id, db_path=None):
    """Set a task to 'ready' only if all deps are done; else raise ValueError.
    A task already 'ready' is a silent no-op (no duplicate transition/activity);
    a 'running' task is refused — re-releasing a claimed task would let the
    next dispatcher tick claim it a second time. An owner-gated task
    (owner_approval=1) is refused outright: the dispatcher never claims gated
    tasks, so 'ready' would be an undispatchable dead end — approval (clearing
    the gate) is the one sanctioned release and queues it automatically."""
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["status"] == "running":
        raise ValueError(
            "task %d is 'running' (claimed by the dispatcher) — it cannot be "
            "re-released while a run owns it" % task_id)
    if t["owner_approval"]:
        # Found live on #175/#183 (2026-09-04): mark-ready pushed gated tasks
        # to 'ready' where the dispatch safeguard silently never claims them.
        raise ValueError(
            "task %d is owner-gated — mark-ready cannot queue it. Approve it "
            "instead (clear the owner gate on the task page, or `wm task edit "
            "%d --owner-approval 0`); it then queues automatically."
            % (task_id, task_id))
    if t["status"] == "ready":
        return
    if not deps_done(task_id, db_path=db_path):
        deps = list_task_deps(task_id, db_path=db_path)
        raise ValueError(
            "task %d cannot be marked ready: not all dependencies are done "
            "(pending deps: %s)" % (task_id, ", ".join(str(d["depends_on_task_id"]) for d in deps)))
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='ready', updated_at=? WHERE id=? "
                "AND status NOT IN ('done', 'running', 'ready')",
                (time.time(), task_id))
            if cur.rowcount == 0:
                raise ValueError(
                    "task %d was not released (done, or its status changed "
                    "concurrently)" % task_id)
            _record_transition_conn(conn, task_id, "ready", from_status="*",
                                    detail="explicit release (mark-ready)")
        log_activity(action="task_ready", task_id=task_id, db_path=db_path)
    finally:
        conn.close()


def claim_task(task_id, db_path=None):
    """Atomically claim a 'ready'/'rework' task -> 'running'.

    Returns True iff exactly one row was updated (someone else did not
    win the race). Runs inside a single transaction. 'rework' is claimable
    so a changes_requested task is automatically re-run on the next tick.
    """
    conn = _connect(db_path)
    try:
        now = time.time()
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='running', claimed_at=?, heartbeat_at=? "
                "WHERE id=? AND status IN ('ready','rework') "
                "AND owner_approval=0 "
                "AND COALESCE(assignee_profile,'') != 'owner'",
                (now, now, task_id))
            rowcount = cur.rowcount
            if rowcount == 1:
                _record_transition_conn(conn, task_id, "running",
                                        from_status="*", detail="claimed by dispatcher")
        return rowcount == 1
    finally:
        conn.close()


def next_ready_tasks(cap, db_path=None):
    """Return up to `cap` ready/rework tasks, oldest first (by claimed/created)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE status IN ('ready','rework') "
            "AND owner_approval=0 "
            "AND COALESCE(assignee_profile,'') != 'owner' "
            "ORDER BY COALESCE(claimed_at, created_at), created_at ASC LIMIT ?",
            (cap,)).fetchall()
    finally:
        conn.close()


def complete_run(task_id, status="done", result_path=None, summary=None,
                 error=None, session_id=None, result_paths=None,
                 db_path=None, _conn=None, run_id=None):
    """Record completion of a task's run + the task's resulting status.

    Stores ALL produced artifact paths (not just the first) into
    tasks.result_paths (JSON) alongside the display `result_path`, and logs a
    state transition for the task status change.
    """
    import json as _json
    if status not in ("done", "failed", "needs_review", "rework", "stalled",
                      "blocked", "manual"):
        raise ValueError("invalid completion status: %s" % status)
    rp = list(result_paths or [])
    if result_path is None and rp:
        result_path = rp[0]

    def _apply(conn):
        old = conn.execute("SELECT status FROM tasks WHERE id=?",
                           (task_id,)).fetchone()
        from_status = old["status"] if old else None
        updates, params = ["status=?", "updated_at=?"], [status, time.time()]
        if result_path is not None:
            updates.append("result_path=?"); params.append(result_path)
        if rp:
            updates.append("result_paths=?")
            params.append(_json.dumps(rp))
        if summary is not None:
            updates.append("summary=?"); params.append(summary)
        params.append(task_id)
        cur = conn.execute(
            "UPDATE tasks SET %s WHERE id=?" % ", ".join(updates), params)
        if cur.rowcount > 0:
            conn.execute(
                "INSERT INTO state_transitions(task_id, run_id, ts, "
                "from_status, to_status, detail) VALUES(?,?,?,?,?,?)",
                (task_id, run_id, time.time(), from_status, status,
                 "completion contract"))

    if _conn is not None:
        # F-15: the caller owns the transaction. The activity row is written on
        # the SAME connection — a second connection would block on the caller's
        # open write transaction.
        _apply(_conn)
        _conn.execute(
            "INSERT INTO activity(ts, project_id, goal_id, task_id, run_id, "
            "agent_profile, session_id, action, detail, model) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), None, None, task_id, run_id, None, session_id,
             "task_%s" % status,
             error or summary or result_path or status, None))
        return
    conn = _connect(db_path)
    try:
        with conn:
            _apply(conn)
        log_activity(action="task_%s" % status, task_id=task_id, run_id=run_id,
                     session_id=session_id,
                     detail=error or summary or result_path or status,
                     db_path=db_path)
    finally:
        conn.close()


def log_activity(action, project_id=None, goal_id=None, task_id=None,
                 run_id=None, agent_profile=None, session_id=None,
                 detail=None, model=None, ts=None, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO activity(ts, project_id, goal_id, task_id, run_id, "
                "agent_profile, session_id, action, detail, model) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts if ts is not None else time.time(), project_id, goal_id,
                 task_id, run_id, agent_profile, session_id, action, detail, model))
    finally:
        conn.close()


def record_transition(task_id, to_status, run_id=None, from_status=None,
                      detail=None, db_path=None):
    """Append a row to state_transitions (a durable, non-prunable by default
    record of every meaningful task state change: becoming ready, released,
    waiting for approval, done, etc.).

    This is the authoritative 'meaningful state transition' log (requirement 7).
    It is NOT swept by `wm prune` unless the matching run is very old (see
    prune_history) — it survives activity-retention cleanup.
    """
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO state_transitions(task_id, run_id, ts, from_status, "
                "to_status, detail) VALUES(?,?,?,?,?,?)",
                (task_id, run_id, time.time(), from_status, to_status, detail))
    finally:
        conn.close()


def list_transitions(task_id=None, limit=50, db_path=None):
    """State-transition history for a task (newest first), or globally."""
    conn = _connect(db_path)
    try:
        if task_id is not None:
            return conn.execute(
                "SELECT * FROM state_transitions WHERE task_id=? ORDER BY id DESC LIMIT ?",
                (task_id, limit)).fetchall()
        return conn.execute(
            "SELECT * FROM state_transitions ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    finally:
        conn.close()


def _set_task_status(task_id, new_status, db_path=None, run_id=None,
                     detail=None, _conn=None):
    """Update a task's status + updated_at and record a transition.

    Supports being passed an already-open connection (_conn) so callers inside
    a larger transaction record the transition atomically with the state change.
    Returns True if the row was updated.
    """
    if _conn is not None:
        old = _conn.execute("SELECT status FROM tasks WHERE id=?",
                            (task_id,)).fetchone()
        from_status = old["status"] if old else None
        cur = _conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (new_status, time.time(), task_id))
        changed = cur.rowcount > 0
        if changed:
            _record_transition_conn(_conn, task_id, new_status, run_id=run_id,
                                    from_status=from_status, detail=detail)
        return changed
    conn = _connect(db_path)
    try:
        old = conn.execute("SELECT status FROM tasks WHERE id=?",
                           (task_id,)).fetchone()
        from_status = old["status"] if old else None
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (new_status, time.time(), task_id))
            changed = cur.rowcount > 0
            if changed:
                conn.execute(
                    "INSERT INTO state_transitions(task_id, run_id, ts, "
                    "from_status, to_status, detail) VALUES(?,?,?,?,?,?)",
                    (task_id, run_id, time.time(), from_status, new_status, detail))
        return changed
    finally:
        conn.close()


def _record_transition_conn(conn, task_id, to_status, run_id=None,
                            from_status=None, detail=None):
    conn.execute(
        "INSERT INTO state_transitions(task_id, run_id, ts, from_status, "
        "to_status, detail) VALUES(?,?,?,?,?,?)",
        (task_id, run_id, time.time(), from_status, to_status, detail))


def set_paused(paused, db_path=None):
    append_meta("paused", "1" if paused else "0", db_path=db_path)
    log_activity(action="pause" if paused else "resume",
                 agent_profile="cli", db_path=db_path)


# ---------------------------------------------------------------------------
# T2 — run lifecycle
# ---------------------------------------------------------------------------
def get_run(run_id, db_path=None):
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    finally:
        conn.close()


def get_task_last_run(task_id, db_path=None):
    """Latest run for a task that actually captured a session_id (or None).

    Used for handoff context and `wm session`: the parent's resume target is
    the most recent run that carries a real session. Ordering by id DESC is
    deterministic per task (id is monotonically increasing), NOT a global
    'newest row' guess.
    """
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE task_id=? AND session_id IS NOT NULL "
            "AND session_id != '' AND review_id IS NULL ORDER BY id DESC LIMIT 1",
            (task_id,)).fetchone()
    finally:
        conn.close()


def get_resume_command(agent, session_id):
    """Resume command for a session: `hermes --profile <p> --resume <sid>`.

    `<p>` is the agent's REAL Hermes profile: a specialist is its own profile,
    and the reserved Orchestrator is Hermes' `default` profile. Emitting
    `--profile orchestrator` produced a command that aborts at launch ("Profile
    'orchestrator' does not exist"), so the owner could never open an
    orchestrator run's session from a brief, `wm session` or the dashboard.

    Returns None when either half is missing rather than interpolating a `None`
    token into a command the owner might paste.
    """
    if not session_id or not agent:
        return None
    return "hermes --profile %s --resume %s" % (
        hermes_profile_arg(agent), session_id)


def start_run(task_id, agent_profile, db_path=None):
    """Insert a running run row for a task. Returns the new run id."""
    conn = _connect(db_path)
    now = time.time()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO runs(task_id, agent_profile, status, started_at, "
                "heartbeat_at) VALUES(?,?,?,?,?)",
                (task_id, agent_profile, "running", now, now))
            run_id = cur.lastrowid
        return run_id
    finally:
        conn.close()


def set_run_pid(run_id, pid, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE runs SET pid=? WHERE id=?",
                         (pid, run_id))
    finally:
        conn.close()


def set_run_brief(run_id, brief_path, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE runs SET brief_path=? WHERE id=?",
                         (brief_path, run_id))
    finally:
        conn.close()


def set_run_result_paths(run_id, result_paths, db_path=None, _conn=None):
    import json as _json
    if _conn is not None:
        # F-15: run inside the caller's open transaction.
        return _conn.execute(
            "UPDATE runs SET result_paths=? WHERE id=?",
            (_json.dumps(list(result_paths or [])), run_id))
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE runs SET result_paths=? WHERE id=?",
                         (_json.dumps(list(result_paths or [])), run_id))
    finally:
        conn.close()


def set_run_workdir(run_id, workdir, branch=None, db_path=None):
    """Record a run's effective work directory + (for isolated code runs) the
    git branch/worktree it executed in, so retries never write into the same
    tree unknowingly."""
    conn = _connect(db_path)
    try:
        with conn:
            if branch is not None:
                conn.execute("UPDATE runs SET workdir=?, branch=? WHERE id=?",
                             (workdir, branch, run_id))
            else:
                conn.execute("UPDATE runs SET workdir=? WHERE id=?",
                             (workdir, run_id))
    finally:
        conn.close()


def running_runs(db_path=None):
    """All runs still in 'running' status (the set the dispatcher livens)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE status='running' "
            "ORDER BY started_at ASC").fetchall()
    finally:
        conn.close()


def running_count(db_path=None):
    """Number of tasks currently 'running' (used to size dispatch capacity)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
    finally:
        conn.close()


def running_run_count(db_path=None):
    """Total live agent processes = running WORK runs + running REVIEW runs.

    The dispatcher sizes capacity against this (not just running tasks) so
    reviews genuinely count toward the concurrency cap and cannot overshoot it.
    """
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
    finally:
        conn.close()


def _append_run_answer(run_id, message):
    """Append an owner answer after the DB claim has succeeded."""
    ensure_runs_dir()
    with open(answer_path(run_id), "a", encoding="utf-8") as f:
        f.write("[owner %s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))


def resume_blocked_run(run_id, message, db_path=None):
    """Atomically claim a blocked owner-question run and reopen it in place."""
    message = str(message or "").strip()
    if not message:
        raise ValueError("answer message must be non-empty")
    conn = _connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT r.id, r.task_id, r.agent_profile "
                "FROM runs r JOIN tasks t ON t.id=r.task_id "
                "JOIN notifications n ON n.run_id=r.id "
                "WHERE r.id=? AND r.status='blocked' AND t.status='blocked' "
                "AND n.kind='question' AND n.source_key LIKE 'runq:%' "
                "AND n.read_at IS NULL AND NOT EXISTS ("
                " SELECT 1 FROM notifications newer WHERE newer.run_id=n.run_id "
                " AND newer.kind='question' AND newer.source_key LIKE 'runq:%' "
                " AND newer.read_at IS NULL AND newer.id>n.id) "
                "ORDER BY n.id DESC LIMIT 1", (run_id,)).fetchone()
            if row is None:
                raise ValueError(
                    "run %s is not a blocked run with a current unanswered owner question"
                    % run_id)
            now = time.time()
            if conn.execute(
                    "UPDATE runs SET status='running', finished_at=NULL, completion=NULL, "
                    "error=NULL, exit_code=NULL, notes=NULL, heartbeat_at=? "
                    "WHERE id=? AND status='blocked'", (now, run_id)).rowcount != 1:
                raise ValueError("run %s was resumed concurrently" % run_id)
            if conn.execute(
                    "UPDATE tasks SET status='running', updated_at=?, claimed_at=?, "
                    "heartbeat_at=? WHERE id=? AND status='blocked'",
                    (now, now, now, row["task_id"])).rowcount != 1:
                raise ValueError("task %s was changed concurrently" % row["task_id"])
            _record_transition_conn(
                conn, row["task_id"], "running", run_id=run_id,
                from_status="blocked", detail="owner answered; same run resumed")
            # Consume every open question for this run. A new question created
            # after the resumed process starts gets a newer unread row.
            conn.execute(
                "UPDATE notifications SET read_at=? WHERE run_id=? "
                "AND kind='question' AND source_key LIKE 'runq:%' AND read_at IS NULL",
                (now, run_id))
        _append_run_answer(run_id, message)
        try:
            os.unlink(completion_path(run_id))
        except FileNotFoundError:
            pass
        log_activity(action="run_resumed", task_id=row["task_id"], run_id=run_id,
                     agent_profile=row["agent_profile"], detail="owner answer accepted",
                     db_path=db_path)
        return dict(row)
    finally:
        conn.close()


def fail_run(run_id, task_id, error, db_path=None, session_id=None,
             exit_code=None):
    """Finalize a run and its task as failed in one transaction."""
    conn = _connect(db_path)
    try:
        with conn:
            changed = finish_run(run_id, status="failed", error=error,
                                 session_id=session_id, exit_code=exit_code,
                                 db_path=db_path, _conn=conn)
            if changed:
                complete_run(task_id, status="failed", error=error,
                             session_id=session_id, run_id=run_id,
                             db_path=db_path, _conn=conn)
        return changed
    finally:
        conn.close()


def finish_run(run_id, status, session_id=None, completion=None, exit_code=None,
               error=None, notes=None, db_path=None, _conn=None):
    """Finalize a run row. Guarded: only transitions a run still 'running',
    so the dispatcher's stall-marking and the wrapper's completion can never
    double-finalize the same run. Returns 1 if it took effect, else 0."""
    if status not in ("done", "failed", "blocked", "stalled", "manual"):
        raise ValueError("invalid run final status: %s" % status)
    if _conn is not None:
        # F-15: run inside the caller's open transaction.
        updates = ["status=?", "finished_at=?"]
        params = [status, time.time()]
        if session_id is not None:
            updates.append("session_id=?"); params.append(session_id)
        if completion is not None:
            updates.append("completion=?"); params.append(completion)
        if exit_code is not None:
            updates.append("exit_code=?"); params.append(exit_code)
        if error is not None:
            updates.append("error=?"); params.append(error)
        if notes is not None:
            updates.append("notes=?"); params.append(notes)
        cur = _conn.execute(
            "UPDATE runs SET %s, heartbeat_at=? WHERE id=? AND status='running'"
            % ", ".join(updates), params + [time.time(), run_id])
        return cur.rowcount
    conn = _connect(db_path)
    try:
        updates = ["status=?", "finished_at=?"]
        params = [status, time.time()]
        if session_id is not None:
            updates.append("session_id=?"); params.append(session_id)
        if completion is not None:
            updates.append("completion=?"); params.append(completion)
        if exit_code is not None:
            updates.append("exit_code=?"); params.append(exit_code)
        if error is not None:
            updates.append("error=?"); params.append(error)
        if notes is not None:
            updates.append("notes=?"); params.append(notes)
        with conn:
            cur = conn.execute(
                "UPDATE runs SET %s, heartbeat_at=? WHERE id=? AND status='running'"
                % ", ".join(updates), params + [time.time(), run_id])
            return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T2 — dependents promotion
# ---------------------------------------------------------------------------
def _mark_ready_if_deps_done(conn, task_id):
    """Promote a task to 'ready' ONLY when it is already released AND deps done.

    Release gate semantics (fix #1/#2):
      - `planned` tasks are NEVER auto-promoted. Work starts only when the plan
        is explicitly released (goal release) or a task is `mark_ready`.
      - `waiting_approval` tasks under a RELEASED goal become `ready` as soon as
        their deps complete — dependency completion makes them ELIGIBLE and the
        (already-given) approval lets them proceed; it never bypasses the gate.
      - `waiting_approval` tasks whose goal is NOT released (explicitly held /
        not part of an approved plan) stay parked: they block only themselves
        and their dependents.
    """
    row = conn.execute(
        "SELECT goal_id, status, owner_approval FROM tasks WHERE id=?",
        (task_id,)).fetchone()
    if row is None or row["status"] != "waiting_approval":
        return False
    if row["owner_approval"]:
        return False
    g = conn.execute("SELECT status FROM goals WHERE id=?",
                     (row["goal_id"],)).fetchone() if row["goal_id"] else None
    if not (g and g["status"] == "released"):
        return False
    deps = conn.execute(
        "SELECT d.depends_on_task_id AS dep, t.status AS status "
        "FROM task_deps d LEFT JOIN tasks t ON t.id = d.depends_on_task_id "
        "WHERE d.task_id=?", (task_id,)).fetchall()
    if all(r["status"] == "done" for r in deps):
        cur = conn.execute(
            "UPDATE tasks SET status='ready', updated_at=? "
            "WHERE id=? AND status='waiting_approval'",
            (time.time(), task_id))
        if cur.rowcount > 0:
            _record_transition_conn(conn, task_id, "ready",
                                    from_status="waiting_approval",
                                    detail="deps done on released goal")
            return True
    return False


def promote_waiting_approval_ready(db_path=None):
    """Promote eligible `waiting_approval` tasks (released goal + deps done)
    -> `ready`. `planned` tasks are NEVER touched (they require explicit
    release). Returns the ids promoted."""
    conn = _connect(db_path)
    promoted = []
    try:
        with conn:
            rows = conn.execute(
                "SELECT id FROM tasks WHERE status='waiting_approval'").fetchall()
            for r in rows:
                if _mark_ready_if_deps_done(conn, r["id"]):
                    promoted.append(r["id"])
        return promoted
    finally:
        conn.close()


def promote_dependents(task_id, db_path=None):
    """Promote tasks that depend on `task_id` (now done) to 'ready'.
    Returns the ids promoted."""
    conn = _connect(db_path)
    promoted = []
    try:
        with conn:
            deps = conn.execute(
                "SELECT task_id FROM task_deps WHERE depends_on_task_id=?",
                (task_id,)).fetchall()
            for d in deps:
                if _mark_ready_if_deps_done(conn, d["task_id"]):
                    promoted.append(d["task_id"])
        return promoted
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T2 — completion contract + liveness + session capture
# ---------------------------------------------------------------------------
def record_completion(run_id, task_id, completed, summary="", result_paths=None,
                      blocker=None, session_id=None, exit_code=None, db_path=None):
    """Apply the Completion contract for a finished run.

    Only a valid `completed == 'done'` marks the run+task done. `blocked` /
    `failed` map 1:1; `manual` (agent-initiated hand-over) lands directly on
    'manual' except for a required review, which must resolve first. A 'done'
    on an `owner_approval` task lands on 'manual' — "Awaiting approval" — for
    the owner to close or redirect; anything else (missing/invalid) -> run+task
    failed. Returns the resulting (task_status, run_status); raises ValueError
    on an unrecognized task id so the wrapper can record an error."""
    result_paths = result_paths or []
    if not isinstance(result_paths, list):
        result_paths = [result_paths]
    # T5 review routing: a clean 'done' work run on a task whose review_policy
    # is 'required'/'optional' must NOT reach 'done' directly — it enters
    # 'needs_review' and the system auto-creates a review (SINGLE review model:
    # never a separately hand-created review task). The work RUN itself is done.
    routed_to_review = False
    task = store_get_task_or_none(task_id, db_path=db_path)
    if task is None:
        raise ValueError("no task with id %s" % task_id)
    if completed == "done":
        run_status = "done"
        if task["review_policy"] in ("required", "optional"):
            task_status = "needs_review"
            routed_to_review = True
        elif task["owner_approval"]:
            # Approval gate: engine-enforced, regardless of the agent's own
            # verdict. With a review policy the gate fires at the verdict
            # instead (review_verdict), so review still precedes the owner.
            task_status = "manual"
        else:
            task_status = "done"
    elif completed == "manual":
        # A required review is still a mandatory gate even when the agent
        # hands over. Optional review keeps its established direct-to-manual
        # behavior; it is explicitly non-blocking unless a clean done verdict
        # requests the normal optional review path above.
        run_status = "done"
        if task["review_policy"] == "required":
            task_status = "needs_review"
            routed_to_review = True
        else:
            task_status = "manual"
    elif completed == "blocked":
        run_status = task_status = "blocked"
    elif completed == "failed":
        run_status = task_status = "failed"
    else:
        run_status = task_status = "failed"
    result_path = (result_paths[0] if result_paths else None)
    # F-15: task completion, review creation and run finalization commit as ONE
    # transaction on ONE connection.
    conn = _connect(db_path)
    try:
        with conn:
            complete_run(task_id, status=task_status, result_path=result_path,
                         summary=summary or None, error=blocker,
                         session_id=session_id, result_paths=result_paths,
                         db_path=db_path, _conn=conn, run_id=run_id)
            if completed == "manual" and not routed_to_review:
                # The agent declared an approval gate: make it the task's
                # truth so the landing reads "Awaiting approval" and the
                # continuation stays gated until the owner untoggles it.
                conn.execute("UPDATE tasks SET owner_approval=1 WHERE id=?",
                             (task_id,))
            if routed_to_review:
                # Create the review inside the same transaction so a watcher can
                # never observe run=done while the review row is still missing.
                create_review(task_id, review_policy=task["review_policy"],
                              db_path=db_path, _conn=conn)
            finish_run(run_id, status=run_status, session_id=session_id,
                       completion=json_dumps(completed, summary, result_paths,
                                             blocker),
                       exit_code=exit_code, error=blocker or None,
                       db_path=db_path, _conn=conn)
            set_run_result_paths(run_id, result_paths, db_path=db_path,
                                 _conn=conn)
        return (task_status, run_status)
    finally:
        conn.close()


def mark_stalled(run_id, task_id, error, db_path=None, label="liveness"):
    """Run -> failed, task -> stalled. `label` names the verdict's origin in the
    transition detail: "liveness" for the dispatcher's scan (default), "owner
    stop" when the owner killed the run (backend/stop.py) — an owner stop is
    not a liveness failure and must not read like one in Task history."""
    finish_run(run_id, status="failed", error=error, db_path=db_path)
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='stalled', updated_at=? WHERE id=? "
                "AND status='running'", (time.time(), task_id))
            if cur.rowcount > 0:
                _record_transition_conn(conn, task_id, "stalled",
                                        from_status="running",
                                        detail="%s: %s" % (label, error or ""))
        log_activity(action="task_stalled", task_id=task_id, run_id=run_id,
                     detail=error, db_path=db_path)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T5 — SINGLE review model (auto-created review; verdict via `wm review`)
# ---------------------------------------------------------------------------
# The review is a distinct, SYSTEM-created item owned by Reviewer. It is
# recorded in `reviews` (task_id = the ORIGIN task, reviewer_profile='reviewer')
# and executed as a real run (runs.review_id links the review run). There is
# NEVER a separately hand-created review task — a required/optional task, on
# completion, is auto-routed through review via record_completion().
REVIEW_OPEN = ("pending", "running", "reviewed")
REVIEW_FINAL = ("done", "changes_requested", "waived", "blocked", "failed")


def create_review(task_id, review_policy=None, reviewer_profile="reviewer",
                  db_path=None, _conn=None):
    """Auto-create a review for an origin task that completed done.

    Called by the completion finalizer (record_completion) when the origin's
    review_policy is 'required'/'optional'. Inserts a 'pending' reviews row
    bound to the ORIGIN task id. Each completion that needs review creates a
    NEW review row, so a changes_requested task's re-completion produces a
    fresh re-review. Returns the review id.
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    policy = review_policy or t["review_policy"]
    if policy not in REVIEW_POLICIES:
        raise ValueError("invalid review_policy %r" % (policy,))
    if _conn is not None:
        # F-15: insert inside the caller's open transaction so the review row
        # commits together with the completion that requested it.
        cur = _conn.execute(
            "INSERT INTO reviews(task_id, reviewer_profile, status, "
            "requested_at, review_policy) VALUES(?,?,?,?,?)",
            (task_id, reviewer_profile, "pending", time.time(), policy))
        rid = cur.lastrowid
        # Same connection for the activity row: a second connection would block
        # on the caller's open write transaction.
        _conn.execute(
            "INSERT INTO activity(ts, project_id, goal_id, task_id, run_id, "
            "agent_profile, session_id, action, detail, model) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), None, None, task_id, None, reviewer_profile, None,
             "review_created",
             "review #%d auto-created (policy=%s)" % (rid, policy), None))
        return rid
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO reviews(task_id, reviewer_profile, status, "
                "requested_at, review_policy) VALUES(?,?,?,?,?)",
                (task_id, reviewer_profile, "pending", time.time(), policy))
            rid = cur.lastrowid
        log_activity(action="review_created", task_id=task_id,
                     agent_profile=reviewer_profile,
                     detail="review #%d auto-created (policy=%s)" % (rid, policy),
                     db_path=db_path)
        return rid
    finally:
        conn.close()


def set_run_review(run_id, review_id, db_path=None):
    """Link a run row to the review it executes (runs.review_id)."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE runs SET review_id=? WHERE id=? AND review_id IS NULL",
                (review_id, run_id))
            return cur.rowcount == 1
    finally:
        conn.close()


def claim_review(review_id, db_path=None):
    """Atomically claim a PENDING review -> 'running'.

    Returns True iff exactly one row flipped ('pending' -> 'running'). Two
    overlapping dispatcher ticks can therefore NEVER spawn two Reviewer runs
    for the same review: whichever claims first wins, the other's claim returns
    False and is skipped (fix #3/#4 — atomic review claiming).
    """
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE reviews SET status='running' WHERE id=? AND status='pending'",
                (review_id,))
            return cur.rowcount == 1
    finally:
        conn.close()


def get_review(review_id, db_path=None):
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    finally:
        conn.close()


def get_open_review(task_id, db_path=None):
    """The most recent not-yet-decided review for a task, or None."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM reviews WHERE task_id=? AND status IN ('pending','running','reviewed') "
            "ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    finally:
        conn.close()


def list_reviews(task_id=None, db_path=None):
    """All review rows (newest first), optionally for one task."""
    conn = _connect(db_path)
    try:
        sql = ("SELECT r.*, t.title AS task_title, t.status AS task_status "
               "FROM reviews r LEFT JOIN tasks t ON t.id = r.task_id")
        params = []
        if task_id is not None:
            sql += " WHERE r.task_id = ?"
            params.append(task_id)
        sql += " ORDER BY r.id DESC"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def pending_reviews(db_path=None):
    """Reviews awaiting a Reviewer dispatch, joined with their origin task."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT r.*, t.title AS task_title, t.status AS task_status, "
            "t.result_path, t.summary FROM reviews r "
            "JOIN tasks t ON t.id = r.task_id "
            "WHERE r.status='pending' ORDER BY r.requested_at").fetchall()
    finally:
        conn.close()


def set_review_status(review_id, status, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE reviews SET status=? WHERE id=?",
                         (status, review_id))
    finally:
        conn.close()


def latest_review_comments(task_id, db_path=None):
    """Most recent review comments for a task (for re-run brief injection)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT comments FROM reviews WHERE task_id=? AND comments IS NOT NULL "
            "AND comments != '' ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        return (row["comments"] if row else None)
    finally:
        conn.close()


def record_review_completion(run_id, review_id, completed, summary="",
                             result_paths=None, blocker=None, session_id=None,
                             exit_code=None, db_path=None):
    """Finalize a REVIEW run (Reviewer's real session) under the Completion
    contract, WITHOUT touching the origin task's status.

    The Reviewer's verdict is applied separately via `wm review` (review_verdict).
    So a completed=='done' review run marks the review as 'reviewed' (awaiting
    the verdict) — it NEVER makes the origin task 'done'. A blocked/failed
    review run finalizes the review accordingly. decided (done/changes_requested/)
    verdicts are never overwritten.
    """
    result_paths = result_paths or []
    if not isinstance(result_paths, list):
        result_paths = [result_paths]
    if completed == "done":
        run_status, review_status = "done", "reviewed"
    elif completed == "blocked":
        run_status = review_status = "blocked"
    else:
        run_status = review_status = "failed"
    finish_run(run_id, status=run_status, session_id=session_id,
               completion=json_dumps(completed, summary, result_paths, blocker),
               exit_code=exit_code, error=blocker or None, db_path=db_path)
    set_run_result_paths(run_id, result_paths, db_path=db_path)
    conn = _connect(db_path)
    try:
        with conn:
            # Always backfill the reviewer session id for traceability (the
            # reviewer may have already recorded a verdict via `wm review`,
            # flipping status to done/changes_requested BEFORE this finalizer
            # runs — so never let that guard block session capture). Only the
            # STATUS update is protected so a decided verdict is never overwritten.
            if session_id:
                conn.execute("UPDATE reviews SET session_id=? WHERE id=?",
                             (session_id, review_id))
            conn.execute(
                "UPDATE reviews SET status=? "
                " WHERE id=? AND status NOT IN ('done','changes_requested','waived')",
                (review_status, review_id))
    finally:
        conn.close()
    log_activity(action="review_run_%s" % review_status,
                 run_id=run_id, task_id=_review_task_id(review_id, db_path=db_path),
                 agent_profile="reviewer", session_id=session_id,
                 detail=blocker or ("reviewer finished; verdict via `wm review`"),
                 db_path=db_path)
    return review_status


def _review_task_id(review_id, db_path=None):
    r = get_review(review_id, db_path=db_path)
    return r["task_id"] if r else None


def review_verdict(task_id, verdict, comment=None, db_path=None):
    """Record a Reviewer verdict on an auto-created review for `task_id`.

    - 'approved'         -> origin task 'done', verdict+comments stored on the
                            review, dependents auto-promote. (SINGLE review: no
                            second review is ever created.)
    - 'changes_requested'-> origin task 'rework' (re-claimable/dispatched), the
                            comments are stored (and injected into the next re-run
                            brief), and re-completion auto-creates a re-review.
    Returns (task_status, review_status, promoted_task_ids).
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if verdict not in ("approved", "changes_requested"):
        raise ValueError("verdict must be 'approved' or 'changes_requested'")
    review = get_open_review(task_id, db_path=db_path)
    if review is None:
        raise ValueError("no open (undecided) review for task %s" % task_id)
    now = time.time()
    promoted = []
    reviews_status = "done" if verdict == "approved" else "changes_requested"
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE reviews SET status=?, verdict=?, comments=?, decided_at=? "
                "WHERE id=?",
                (reviews_status, verdict, comment or "", now, review["id"]))
            if verdict == "approved":
                # Approval gate: an approved review on an owner_approval task
                # stops one step short of done — the OWNER closes or redirects.
                final = "manual" if t["owner_approval"] else "done"
                conn.execute("UPDATE tasks SET status=?, updated_at=? "
                             "WHERE id=?", (final, now, task_id))
                _record_transition_conn(
                    conn, task_id, final, from_status="needs_review",
                    detail="review approved" if final == "done"
                    else "review approved — awaiting owner approval")
            else:
                conn.execute("UPDATE tasks SET status='rework', feedback=?, "
                             "updated_at=? WHERE id=?", (comment or "", now, task_id))
                _record_transition_conn(conn, task_id, "rework",
                                        from_status="needs_review",
                                        detail="changes requested")
    finally:
        conn.close()
    review_status = reviews_status
    if verdict == "approved" and not t["owner_approval"]:
        # Gated tasks promote nothing yet — close_by_owner does, on approval.
        promoted = promote_dependents(task_id, db_path=db_path)
    log_activity(action="review_%s" % review_status, task_id=task_id,
                 agent_profile="reviewer",
                 detail="review #%d %s: %s" % (review["id"], verdict, comment or ""),
                 db_path=db_path)
    return ((("manual" if t["owner_approval"] else "done")
             if verdict == "approved" else "rework"), review_status, promoted)


def waive_review(task_id, comment=None, db_path=None):
    """Non-blocking path for an `optional`-policy task: mark it done + waive the
    review without an approval. `required` tasks cannot be waived.
    Returns (task_status, review_status, promoted_task_ids).
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["review_policy"] != "optional":
        raise ValueError("only optional-policy reviews may be waived (task %s "
                         "is '%s')" % (task_id, t["review_policy"]))
    review = get_open_review(task_id, db_path=db_path)
    if review is None:
        raise ValueError("no open review for task %s" % task_id)
    now = time.time()
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE reviews SET status='waived', verdict='waived', "
                         "comments=?, decided_at=? WHERE id=?",
                         (comment or "", now, review["id"]))
            conn.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?",
                         (now, task_id))
    finally:
        conn.close()
    promoted = promote_dependents(task_id, db_path=db_path)
    log_activity(action="review_waived", task_id=task_id,
                 agent_profile="reviewer",
                 detail="review #%d waived: %s" % (review["id"], comment or ""),
                 db_path=db_path)
    return ("done", "waived", promoted)


def close_orphan_review(task_id, comment=None, db_path=None):
    """Close a review left open after its task already reached 'done' (e.g. the
    reviewer run finished but no verdict ever landed). The review becomes
    'waived' with an audit-trail activity row; the task is untouched.
    Returns the closed review id."""
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["status"] != "done":
        raise ValueError("task %s is '%s', not 'done' — only reviews orphaned "
                         "by an already-done task can be closed this way"
                         % (task_id, t["status"]))
    review = get_open_review(task_id, db_path=db_path)
    if review is None:
        raise ValueError("no open review for task %s" % task_id)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE reviews SET status='waived', verdict='waived', "
                         "comments=?, decided_at=? WHERE id=?",
                         (comment or "orphan review closed (task already done)",
                          time.time(), review["id"]))
    finally:
        conn.close()
    log_activity(action="review_orphan_closed", task_id=task_id,
                 agent_profile="owner",
                 detail="orphaned review #%d closed on done task: %s"
                        % (review["id"], comment or ""),
                 db_path=db_path)
    return review["id"]


# ---------------------------------------------------------------------------
# Phase 6.5.1 — OWNER feedback (the human's own "send this back" channel)
# ---------------------------------------------------------------------------
# The Reviewer's `changes_requested` verdict is an AGENT decision. Owner
# feedback is the human equivalent: Kamran looks at a finished (or under-review)
# task, says what is wrong in his own words, and the task goes back to `rework`
# with those words threaded into the next run's brief.
#
# It carries NO schema change. The durable record is a `state_transitions` row
# (the non-prunable ledger) whose `detail` is prefixed with OWNER_FEEDBACK_MARKER
# so the text is machine-recoverable, plus a `task_feedback` activity row.
OWNER_FEEDBACK_MARKER = "owner feedback: "

# Statuses a task may receive owner feedback from. All three mean "the assignee
# has produced something to react to": it is awaiting review, it is already
# being reworked, or it was accepted and the owner changed his mind. A task that
# has not produced anything yet (planned/waiting_approval/ready/running/...) has
# nothing to give feedback ON — sending it to `rework` would either kill a live
# run's bookkeeping or fabricate a rework state for work that never happened.
# hermes-hq (2026-08-29): a task that stopped to ask the owner something
# (blocked) or died (failed/stalled) is exactly where owner words are most
# useful — feedback re-queues it as rework with the answer in the next brief.
OWNER_FEEDBACK_SOURCE_STATUSES = ("needs_review", "rework", "done",
                                  "blocked", "failed", "stalled", "manual")


def owner_feedback(task_id, comment, db_path=None):
    """Owner sends a task back for rework with a written reason.

    - The task flips to `rework` (re-claimable/dispatchable) and the transition
      row records `owner feedback: <comment>` so `latest_owner_feedback` — and
      therefore `render_brief` — can hand the assignee the exact words.
    - A real `task_feedback` activity row is written (agent_profile
      'orchestrator': this is an owner/Orchestrator action, not an agent's).
    - If the task still has an OPEN review (pending/running/reviewed), that
      review is decided `changes_requested` in the same transaction. Leaving it
      open would let the dispatcher launch a Reviewer run for a verdict the
      owner has already pre-empted, and `get_open_review` would keep reporting a
      review that no longer matches the task's state. The review's `comments`
      column is deliberately NOT overwritten: it belongs to the REVIEWER, and
      forging the owner's words into it would both misattribute them and make
      `latest_review_comments` echo the same text the OWNER FEEDBACK brief
      section already carries. The reason lives in the transition ledger + a
      `review_changes_requested` activity row that names the owner.

    Returns (task_status, comment, closed_review_id|None, demoted_dependents).
    Raises ValueError for
    an unknown task, an empty comment, or a source status outside
    OWNER_FEEDBACK_SOURCE_STATUSES.
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    comment = str(comment or "").strip()
    if not comment:
        raise ValueError("feedback comment must be non-empty")
    status = t["status"]
    if status not in OWNER_FEEDBACK_SOURCE_STATUSES:
        raise ValueError(
            "task %d is '%s' — owner feedback is only accepted on a %s task"
            % (task_id, status,
               " / ".join("'%s'" % s for s in OWNER_FEEDBACK_SOURCE_STATUSES)))
    review = get_open_review(task_id, db_path=db_path)
    now = time.time()
    demoted = []
    conn = _connect(db_path)
    try:
        with conn:
            if review is not None:
                conn.execute(
                    "UPDATE reviews SET status='changes_requested', "
                    "verdict='changes_requested', decided_at=? WHERE id=?",
                    (now, review["id"]))
            conn.execute("UPDATE tasks SET status='rework', feedback=?, "
                         "updated_at=? WHERE id=?", (comment, now, task_id))
            _record_transition_conn(conn, task_id, "rework", from_status=status,
                                    detail=OWNER_FEEDBACK_MARKER + comment)
            # A parent that was `done` had its dependents promoted to `ready`
            # (promote_dependents). Sending it back to `rework` invalidates that
            # result, so demote any STILL-UNCLAIMED `ready` dependent back to
            # `waiting_approval` (awaiting its dependency). Only status=='done'
            # parents ever promoted dependents, so this is the exact window.
            if status == "done":
                for d in conn.execute(
                        "SELECT task_id FROM task_deps "
                        "WHERE depends_on_task_id=?", (task_id,)).fetchall():
                    child = conn.execute(
                        "SELECT status FROM tasks WHERE id=?",
                        (d["task_id"],)).fetchone()
                    if child and child["status"] == "ready":
                        conn.execute(
                            "UPDATE tasks SET status='waiting_approval', "
                            "updated_at=? WHERE id=?", (now, d["task_id"]))
                        _record_transition_conn(
                            conn, d["task_id"], "waiting_approval",
                            from_status="ready",
                            detail="owner rework: parent #%d went back to rework"
                                   % task_id)
                        demoted.append(d["task_id"])
    finally:
        conn.close()
    log_activity(action="task_feedback", project_id=t["project_id"],
                 goal_id=t["goal_id"], task_id=task_id,
                 agent_profile="orchestrator", detail=comment, db_path=db_path)
    if review is not None:
        log_activity(action="review_changes_requested", project_id=t["project_id"],
                     goal_id=t["goal_id"], task_id=task_id,
                     agent_profile="orchestrator",
                     detail="review #%d closed changes_requested by OWNER "
                            "feedback (no Reviewer verdict was recorded): %s"
                            % (review["id"], comment), db_path=db_path)
    return ("rework", comment, review["id"] if review is not None else None,
            demoted)


def latest_owner_feedback(task_id, db_path=None):
    """The owner's words behind the task's CURRENT rework state, or None.

    Reads the most recent `-> rework` row in the state_transitions ledger and
    returns its comment only when that row carries OWNER_FEEDBACK_MARKER.

    Deviation from the brief's literal "latest row where to_status='rework' AND
    detail startswith 'owner feedback:'": the newest rework transition is
    inspected, not the newest OWNER one. Both find the same row in the normal
    case, but the literal form keeps re-injecting an old owner comment forever
    once a later Reviewer `changes_requested` has become the reason the task is
    in rework — i.e. it would tell the agent to address feedback that has
    already been dealt with. Anchoring on the latest transition means the brief
    only ever shows the feedback that actually put the task where it is.
    """
    conn = _connect(db_path)
    try:
        # Only surface owner feedback while the task is CURRENTLY in rework —
        # or was just claimed straight out of it: the dispatcher claims the task
        # (rework -> running) BEFORE it renders the brief, so at render time the
        # status is already 'running'. Once the task has moved on through any
        # other transition, the feedback was addressed and is not re-injected.
        cur = get_task(task_id, db_path=db_path)
        if cur is None:
            return None
        rows = conn.execute(
            "SELECT to_status, detail FROM state_transitions WHERE task_id=? "
            "ORDER BY id DESC LIMIT 2", (task_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    if cur["status"] == "rework" and rows[0]["to_status"] == "rework":
        row = rows[0]
    elif (cur["status"] == "running" and rows[0]["to_status"] == "running"
          and len(rows) > 1 and rows[1]["to_status"] == "rework"):
        row = rows[1]
    else:
        return None
    detail = row["detail"] or ""
    if not detail.startswith(OWNER_FEEDBACK_MARKER):
        return None
    return detail[len(OWNER_FEEDBACK_MARKER):].strip() or None


# ---------------------------------------------------------------------------
# T4 — recovery surface (safe; NEVER silent auto-retry)
# ---------------------------------------------------------------------------
def get_task_latest_run(task_id, db_path=None):
    """Latest run row for a task (by id DESC — deterministic per task)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE task_id=? AND review_id IS NULL "
            "ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    finally:
        conn.close()


def retry_task(task_id, db_path=None):
    """Re-open a failed/stalled/blocked task to 'ready' for a fresh run.

    Prior run rows are kept intact (history preserved). Does NOT spawn a
    process — the next dispatcher tick claims and relaunches the fresh run.
    Refuses while the task is currently 'running' (and never touches a 'done'
    task).
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["status"] == "running":
        raise ValueError("cannot retry task %d while it is running" % task_id)
    if t["status"] == "done":
        raise ValueError("cannot retry task %d: already done" % task_id)
    if t["owner_approval"]:
        # The dispatcher never claims gated tasks, so 'ready' would be an
        # undispatchable dead end (the #175/#183 trap, 2026-09-04).
        raise ValueError(
            "task %d is owner-gated — retry cannot re-queue it. Approve it "
            "first (clear the owner gate on the task page, or `wm task edit "
            "%d --owner-approval 0`); then retry re-opens it to ready."
            % (task_id, task_id))
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='ready', updated_at=? WHERE id=?",
                (time.time(), task_id))
            if cur.rowcount == 0:
                raise ValueError("no task %d" % task_id)
            _record_transition_conn(conn, task_id, "ready",
                                    from_status=t["status"],
                                    detail="manual retry (old runs kept)")
    finally:
        conn.close()
    log_activity(action="task_retry", task_id=task_id,
                 detail="old status=%s; reopened to ready" % t["status"],
                 db_path=db_path)
    return task_id


def mark_manual(task_id, note=None, db_path=None):
    """A human acknowledges a stuck task and takes it out of the queue.

    status -> 'manual'. Prior run rows and activity are preserved. Never
    downgrades a 'done' task.
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["status"] == "done":
        raise ValueError("cannot mark task %d manual: already done" % task_id)
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='manual', updated_at=? WHERE id=?",
                (time.time(), task_id))
            if cur.rowcount > 0:
                _record_transition_conn(conn, task_id, "manual",
                                        from_status=t["status"], detail=("ack: %s" % note) if note else "acknowledged out of queue")
    finally:
        conn.close()
    log_activity(action="task_manual", task_id=task_id,
                 detail=note or ("old status=%s" % t["status"]),
                 db_path=db_path)
    return task_id


def edit_task(task_id, description=None, definition_of_done=None,
              owner_approval=None, db_path=None):
    """Audited owner edit of the fields that shape the agent's brief/gating.

    Refused while 'running' (the brief was already rendered at claim) and on
    'done' (historical record). No status change — the audit trail is a
    `task_edited` activity row carrying each changed field's OLD value.
    Returns the list of field names actually changed."""
    if description is None and definition_of_done is None \
            and owner_approval is None:
        raise ValueError("nothing to edit: pass description, "
                         "definition_of_done and/or owner_approval")
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if t["status"] in ("running", "done"):
        raise ValueError("task %d is '%s' — edits are refused while running "
                         "(brief already rendered) and on done tasks (history)"
                         % (task_id, t["status"]))
    updates, params, changed, audit = [], [], [], []
    for field, new in (("description", description),
                       ("definition_of_done", definition_of_done)):
        if new is None or (t[field] or "") == new:
            continue
        updates.append("%s=?" % field); params.append(new); changed.append(field)
        audit.append("%s was: %s" % (field, (t[field] or "")[:200]))
    if owner_approval is not None:
        new_flag = 1 if owner_approval else 0
        if int(t["owner_approval"] or 0) != new_flag:
            updates.append("owner_approval=?"); params.append(new_flag)
            changed.append("owner_approval")
            audit.append("owner_approval was: %s" % int(t["owner_approval"] or 0))
    if not changed:
        return []
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE tasks SET %s, updated_at=? WHERE id=?"
                         % ", ".join(updates),
                         params + [time.time(), task_id])
    finally:
        conn.close()
    log_activity(action="task_edited", task_id=task_id,
                 detail="owner edited %s. %s" % (", ".join(changed),
                                                 " | ".join(audit)),
                 db_path=db_path)
    # Removing the owner gate is an approval event. If the task was waiting
    # on completed dependencies under a released goal, make it ready now so
    # the next dispatcher tick can pick it up without another manual action.
    if owner_approval is False and "owner_approval" in changed:
        _promote_after_approval = _connect(db_path)
        try:
            with _promote_after_approval:
                _mark_ready_if_deps_done(_promote_after_approval, task_id)
        finally:
            _promote_after_approval.close()
    return changed


def close_by_owner(task_id, note=None, db_path=None):
    """The owner declares work finished outside WM runs. Only a 'manual' task
    qualifies (Take over first) — the sanctioned two-step keeps the dispatcher
    lifecycle and owner overrides apart. ONE exception: a task ASSIGNED TO
    `owner` (Second Brain todo) is never in the dispatcher's queue — its
    'ready' IS the owner's list, so it closes in one step. Records the done
    transition (so the integrity audit passes), waives any open review (so it
    never orphans), then promotes dependents. Returns the promoted task ids."""
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    owner_task = (t["assignee_profile"] == OWNER_ASSIGNEE)
    if t["status"] != "manual" and not (
            owner_task and t["status"] in ("ready", "rework", "planned")):
        raise ValueError(
            "task %d is '%s', not 'manual' — take it over first "
            "(owner-close only applies to tasks out of the queue)"
            % (task_id, t["status"]))
    now = time.time()
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?",
                         (now, task_id))
            _record_transition_conn(
                conn, task_id, "done", from_status=t["status"],
                detail=("closed by owner: %s" % note) if note else "closed by owner")
            conn.execute(
                "UPDATE reviews SET status='waived', verdict='waived', "
                "comments=COALESCE(NULLIF(comments,''),'waived on owner-close'), "
                "decided_at=? WHERE task_id=? AND status IN ('pending','running','reviewed')",
                (now, task_id))
    finally:
        conn.close()
    log_activity(action="task_closed_by_owner", task_id=task_id,
                 detail=note or "", db_path=db_path)
    return promote_dependents(task_id, db_path=db_path)


def approve_plan(task_id, note=None, db_path=None):
    """Approve a completed goal plan and hand its released work to the dispatcher.

    This is deliberately separate from ``close_by_owner``: a stranded planning
    task is an approval surface for the goal, not an arbitrary manual task. The
    goal must still be ``planned`` and the task must still be ``manual``; these
    guards make an old approval action harmless after feedback, re-planning, or
    an earlier approval. The goal release is the durable approval record.
    """
    t = get_task(task_id, db_path=db_path)
    if t is None:
        raise ValueError("no task with id %s" % task_id)
    if (t["assignee_profile"] != PLANNING_TASK_PROFILE or
            not (t["title"] or "").startswith(PLANNING_TASK_PREFIX) or
            not t["goal_id"]):
        raise ValueError("task %d is not a planning task" % task_id)
    if t["status"] != "manual":
        raise ValueError("task %d is '%s', not 'manual' — plan approval is stale"
                         % (task_id, t["status"]))
    goal = get_goal(t["goal_id"], db_path=db_path)
    if goal is None:
        raise ValueError("no goal with id %s" % t["goal_id"])
    if goal["status"] != "planned":
        raise ValueError("goal %d is '%s', not 'planned' — plan approval is stale"
                         % (goal["id"], goal["status"]))

    # release_goal owns the full released-goal eligibility policy and its audit.
    # It runs before the planning row is closed so dependents can see the
    # planning work as the dependency that is being approved.
    release_goal(goal["id"], db_path=db_path)
    now = time.time()
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tasks SET status='done', owner_approval=0, updated_at=? "
                "WHERE id=? AND status='manual'", (now, task_id))
            if cur.rowcount != 1:
                raise ValueError("task %d approval is stale" % task_id)
            _record_transition_conn(
                conn, task_id, "done", from_status="manual",
                detail=("plan approved: goal #%d released%s" %
                        (goal["id"], (" — " + str(note).strip()) if note else "")))
    finally:
        conn.close()
    log_activity(action="plan_approved", project_id=t["project_id"],
                 goal_id=goal["id"], task_id=task_id,
                 agent_profile="orchestrator",
                 detail=note or "goal plan approved and released",
                 db_path=db_path)
    # The planning row became the dependency only after the goal release was
    # evaluated, so re-check its direct children now that it is done.
    return promote_dependents(task_id, db_path=db_path)


def get_session_activity(agent, session_id, db_path=None):
    """Read a session's last_activity_at from a Hermes profile state.db.

    Scoped by the agent profile's own state.db (never 'newest row' across
    profiles). Returns a dict {id,last_activity_at,title} or None."""
    sdb = agent_session_db_path(agent)
    if not os.path.exists(sdb):
        return None
    try:
        conn = _connect(sdb)
    except sqlite3.Error:
        return None
    try:
        try:
            row = conn.execute(
                "SELECT id,last_activity_at,title FROM sessions WHERE id=?",
                (session_id,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        laa = row["last_activity_at"]
        return {"id": row["id"], "last_activity_at": laa,
                "title": row["title"]}
    finally:
        conn.close()


def get_run_session_activity(agent, run_id, session_id=None, db_path=None):
    """Locate the live session for a run for liveness.

    Preference order (never 'newest row'):
      1. the run's recorded session_id (set by the wrapper on completion);
      2. the deterministic marker title 'wm-run-<run_id>' planted at launch
         (lets the dispatcher judge activity of a still-running agent whose
         session_id the wrapper has not yet captured).
    Returns a dict {id,last_activity_at,title} or None."""
    if session_id:
        found = get_session_activity(agent, session_id, db_path=db_path)
        if found:
            return found
    sdb = agent_session_db_path(agent)
    marker = "wm-run-%s" % run_id
    if not os.path.exists(sdb):
        return None
    try:
        conn = _connect(sdb)
    except sqlite3.Error:
        return None
    try:
        try:
            row = conn.execute(
                "SELECT id,last_activity_at,title FROM sessions "
                "WHERE title=? ORDER BY started_at DESC LIMIT 1",
                (marker,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {"id": row["id"], "last_activity_at": row["last_activity_at"],
                "title": row["title"]}
    finally:
        conn.close()


def capture_session_id(run_id, agent, preferred=None, db_path=None):
    """Deterministic, concurrency-safe session-id capture for a run.

    `--pass-session-id` behavior (verified against the CLI + hermes source,
    2026-08-25): `hermes chat ... --pass-session-id` sets the *launch-layer*
    env flag HERMES_TUI_PASS_SESSION_ID=1 (an enable toggle, NOT the id
    itself — so the wrapper cannot read the id from any --pass-session-id env
    variable), and it injects the live session id into the agent's system
    prompt as `Session ID: <sid>`. That sid IS the real session created for
    this run (title 'wm-run-<run_id>').

    Capture rule (preferred cross-check first, unique-marker fallback last —
    NEVER 'newest row'):
      1. PREFERRED cross-check: when the executing agent self-reports its
         session id (the 'Session ID:' line it can now see via
         --pass-session-id, e.g. in the completion JSON), use it IF a session
         with that id exists in THIS profile's state.db. Self-reported ids are
         correct by construction (the agent only ever sees its OWN session),
         so this is deterministic and concurrency-safe.
      2. RELIABLE FALLBACK (authoritative): look up the agent profile's own
         state.db for the session whose title equals the unique per-run marker
         'wm-run-<run_id>'. Because the marker is unique per run id, two
         concurrent runs can never collide and each resolves to its own
         session — this never 'grabs the newest row'.
    Always scoped to the run's OWN agent profile state.db.
    """
    sdb = agent_session_db_path(agent)
    marker = "wm-run-%s" % run_id
    if not os.path.exists(sdb):
        return None
    try:
        conn = _connect(sdb)
    except sqlite3.Error:
        return None
    try:
        # 1. Preferred cross-check: self-reported id that really exists here.
        if preferred:
            try:
                row = conn.execute(
                    "SELECT id FROM sessions WHERE id=?", (preferred,)).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                return row["id"]
        # 2. Reliable fallback: unique per-run marker title.
        try:
            row = conn.execute(
                "SELECT id FROM sessions WHERE title=? "
                "ORDER BY started_at DESC LIMIT 1", (marker,)).fetchone()
        except sqlite3.Error:
            return None
        return row["id"] if row else None
    finally:
        conn.close()


def json_dumps(completed, summary, result_paths, blocker):
    """Canonical completion-JSON text stored against the run."""
    import json
    return json.dumps({
        "completed": completed,
        "summary": summary or "",
        "result_paths": list(result_paths or []),
        "blocker": blocker if blocker is not None else "",
    }, indent=2)


def store_get_task_or_none(task_id, db_path=None):
    try:
        return get_task(task_id, db_path=db_path)
    except Exception:
        return None


def _completion_contract_lines(cpath):
    """The canonical Completion-contract instruction (REQUIRED last action).

    Shared verbatim by the work brief and the orchestrator planning brief so the
    agent's closing instruction never drifts between render paths.
    """
    return [
        "COMPLETION CONTRACT (REQUIRED — your LAST action):",
        "Write a JSON file to %s" % cpath,
        "with EXACTLY this structure:",
        '{"completed": "done|blocked|failed|manual", "summary": "...", '
        '"result_paths": ["..."], "blocker": "...", '
        '"session_id": "<optional>"}',
        "  - completed: 'done' if you fully finished the task; "
        "'blocked' if you hit an external blocker; 'failed' if you "
        "could not finish; 'manual' ONLY when the task explicitly "
        "requires the OWNER's decision before work can continue (e.g. "
        "a plan awaiting approval) — the task is handed to the owner, "
        "and blocker must say exactly what you need decided.",
        "  - summary: a prose summary of what you did / the result.",
        "  - result_paths: ABSOLUTE paths to each artifact/file you "
        "actually produced (empty list if none).",
        "  - blocker: the reason, REQUIRED (non-empty) iff "
        "completed != 'done'.",
        "  - session_id (OPTIONAL): if your system prompt contains a "
        "'Session ID:' line, copy that id here so the work manager can "
        "cross-check it.",
        "Process exit does NOT count as completion — the only thing "
        "that marks this task done is a valid {completed:'done'} JSON "
        "written to the path above as your last step.",
    ]


def _feedback_sections(task, db_path):
    """Extra brief lines carrying the feedback that put the task where it is.

    Reviewer `changes_requested` comments first, owner feedback second — the
    canonical order (different authors, neither instead of the other). Shared by
    the work brief and the orchestrator planning brief so a re-run always sees
    exactly what was sent back.
    """
    lines = []
    rcomments = latest_review_comments(task["id"], db_path=db_path)
    if rcomments:
        lines.append("")
        lines.append("REVIEW REWORK COMMENTS (address these — from the last "
                     "review)")
        lines.append("-" * 40)
        lines.append(rcomments)
    ofeedback = latest_owner_feedback(task["id"], db_path=db_path)
    if ofeedback:
        lines.append("")
        lines.append("OWNER FEEDBACK (address these — the owner sent this task "
                     "back for rework)")
        lines.append("-" * 40)
        lines.append(ofeedback)
    return lines


def _render_orchestrator_planning_brief(run, task, project, primary_path,
                                        cpath, db_path):
    """The ORCHESTRATOR PLANNING brief for a `Plan goal #N` run (6.5.2).

    An orchestrator-assigned run acts on the goal's DECOMPOSITION, not on a
    deliverable. Two clearly-delimited cases:

      DECOMPOSE (goal still `planning`/`draft`): read the goal + project, break
        it into real tasks via the `wm` CLI, then `wm goal planned <id>` (which
        closes this plan task) and write the Completion contract.
      REVISION (goal already `planned`; plan task sent back to `rework` with
        OWNER FEEDBACK): revise the EXISTING breakdown via the `wm` CLI per the
        feedback — edit/add/remove tasks, DO NOT duplicate — keep the goal
        `planned`, and write the Completion contract (this closes the plan
        task).

    Both paths keep the Completion contract and the OWNER FEEDBACK / REVIEW
    REWORK COMMENTS sections verbatim via the shared helpers.
    """
    goal = get_goal(task["goal_id"], db_path=db_path) if task["goal_id"] else None
    revision = bool(goal and goal["status"] == "planned")
    slug = (project["slug"] if project else "?")
    gid = (goal["id"] if goal else "?")
    lines = []
    lines.append("WORK MANAGER ORCHESTRATOR PLANNING BRIEF")
    lines.append("=" * 40)
    lines.append("Task #%s: %s" % (task["id"], task["title"] or "-"))
    if goal:
        lines.append("Goal #%s: %s   [goal status: %s]"
                     % (goal["id"], goal["title"] or "-", goal["status"]))
    lines.append("Project primary_path: %s" % primary_path)
    lines.append("Project slug: %s" % slug)
    lines.append("Assignee profile: %s" % (run["agent_profile"] or "-"))
    # L3: the wrapper launches this run with cwd=run["workdir"] when one is
    # recorded, exactly as for a deliverable run — so the brief must name the
    # SAME directory the process actually starts in, not the project's
    # primary_path. Mirrors the work brief's "Working directory:" line.
    lines.append("Working directory: %s" % (run["workdir"] or primary_path))
    if run["branch"]:
        lines.append("Git branch/worktree: %s (isolated run — do not touch "
                     "other branches)" % run["branch"])
    lines.append("")
    lines.append("AUTHORIZATION")
    lines.append("-" * 40)
    lines.append("The Work Manager state lives in " + DEFAULT_DB_PATH + ". "
                 "Do NOT edit wm.db (or any database) directly via SQL — it is "
                 "owned and integrity-checked by the system. Report your result "
                 "ONLY through the `wm` CLI commands below and the Completion "
                 "contract JSON. Editing the database directly triggers a "
                 "tamper audit.")
    lines.append("")
    if not revision:
        lines.append("INSTRUCTIONS — DECOMPOSE THIS GOAL INTO TASKS")
        lines.append("-" * 40)
        lines.append("This goal is not decomposed yet. Read its description and "
                     "acceptance criteria below, then break it into a coherent "
                     "set of REAL tasks using the `wm` CLI (NO raw SQL):")
        lines.append("  `wm task create <project-slug> <title> --goal %s "
                     "--assignee <profile> --review-policy <none|required|optional>`"
                     % gid)
        lines.append("  - assignee must be one of: analyst | writer | marketer "
                     "| coder | uiux | reviewer")
        lines.append("  - wire prerequisite ordering with "
                     "`wm task depend <dependent-task-id> <dependency-task-id>`")
        lines.append("  - give every task a concrete, testable "
                     "definition_of_done.")
        lines.append("  - only decompose the REAL project below; do not invent "
                     "a different scope.")
        lines.append("")
        lines.append("GOAL")
        lines.append("-" * 40)
        lines.append("Title: %s" % (goal["title"] if goal else "-"))
        lines.append("Description: %s"
                     % (goal["description"] if goal and goal["description"] else "-"))
        if goal and goal["acceptance_criteria"]:
            lines.append("Acceptance criteria: %s" % goal["acceptance_criteria"])
        lines.append("")
        lines.append("After the breakdown is agreed, run `wm goal planned %s` — "
                     "this closes THIS planning task and moves the goal to "
                     "`planned` (it does NOT release it)." % gid)
    else:
        lines.append("INSTRUCTIONS — REVISE THE EXISTING TASK BREAKDOWN")
        lines.append("-" * 40)
        lines.append("The goal is already `planned` with an existing breakdown. "
                     "This planning task was sent back to rework by the owner "
                     "to revise it, not to decompose again:")
        lines.append("  - REVIEW the current tasks under goal %s and revise the "
                     "breakdown via the `wm` CLI per the OWNER FEEDBACK below "
                     "(edit / add / remove) — DO NOT duplicate existing tasks."
                     % gid)
        lines.append("  - keep the goal `planned` — do NOT call `wm goal "
                     "planned` again (it is a no-op that revises nothing).")
    lines.append("")
    lines += _feedback_sections(task, db_path)
    lines.append("")
    lines += _completion_contract_lines(cpath)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Brief rendering (single render entry point). Includes the task's goal/message,
# DoD, primary_path, the Completion-contract instruction, and — for any
# completed parents (task_deps) — automatic handoff context (their result_path,
# summary, and a --resume line for their latest sessioned run).
# ---------------------------------------------------------------------------
def _ask_owner_lines(run_id):
    return [
        "ASKING THE OWNER",
        "-" * 40,
        "If you hit a decision only the owner can make, put ONE fenced block in "
        "your reply text tagged hq-options containing JSON {\"question\": str, "
        "\"mode\": \"single\"|\"multi\", \"options\": [{\"label\": str, "
        "\"detail\": str}]} (2-6 options, labels short, detail optional). The "
        "owner is notified on their phone and can send an answer while you run. "
        "Write the block as PLAIN REPLY TEXT in your message — never through a "
        "tool, a terminal command, or a file (tool output is not scanned). "
        "IMPORTANT: keep the same reply going — after the block, immediately "
        "continue with tool calls (e.g. keep working, or poll the answer file "
        "with `sleep 30` + `cat`). In this one-shot mode, a reply that ends "
        "after the question ends your whole run before anyone can answer.",
        "After asking, keep working on everything that does not depend on the "
        "answer, and CHECK THE FILE %s between steps — it may not exist yet; "
        "when it appears, its contents are the owner's answer/guidance. Act on "
        "it. If no answer arrives, continue with your best-judgment default and "
        "note it in your summary. If you truly cannot proceed without the "
        "answer, finish with completed=\"blocked\" and a precise blocker "
        "instead." % answer_path(run_id),
        "",
    ]


def render_brief(run_id, db_path=None):
    """Render the brief text an agent executes for a run.

    Includes the task's goal/message and definition of done, the project's
    canonical primary_path, and the required Completion-contract instruction
    (write <run_id>.completion.json as the agent's LAST action). Pure text
    assembly (no file writes); the caller persists it to brief_path(run_id).
    """
    run = get_run(run_id, db_path=db_path)
    if run is None:
        raise ValueError("no run %s" % run_id)
    task = get_task(run["task_id"], db_path=db_path)
    if task is None:
        raise ValueError("no task for run %s" % run_id)
    project = get_project(task["project_id"], db_path=db_path)
    primary_path = (project["primary_path"] if project else None) or os.getcwd()
    cpath = completion_path(run_id)

    # T5: a REVIEW run (runs.review_id set) gets an auto-generated reviewer brief
    # instead of the work brief — deliverable path + policy + origin context +
    # the completion-JSON instruction. The Reviewer executes in a REAL session.
    review_id = run["review_id"]
    if review_id is not None:
        review = get_review(review_id, db_path=db_path)
        # Origin work run's result_paths (from its completion JSON) if available.
        work_run = get_task_latest_run(task["id"], db_path=db_path)
        deliverable = task["result_path"] or "-"
        rpaths = []
        if work_run and work_run["completion"]:
            try:
                import json as _json
                rpaths = _json.loads(work_run["completion"]).get("result_paths") or []
            except Exception:
                rpaths = []
        delim = "=" * 40
        rl = [
            "WORK MANAGER REVIEW BRIEF (auto-created by the system)",
            delim,
            "Reviewing task #%s: %s" % (task["id"], task["title"] or "-"),
            "This review was automatically created because the task's "
            "review_policy is '%s' — there is NO separately hand-created "
            "review task." % task["review_policy"],
            "Project primary_path (your working directory): %s" % primary_path,
            "Assignee profile: %s" % (task["assignee_profile"] or "-") + (
                "   |   Reviewer profile: reviewer"),
            "Review policy: %s" % task["review_policy"],
            "",
            "ORIGIN TASK (what the assignee produced)",
            "-" * 40,
            "Description: %s" % (task["description"] or "-"),
            "Definition of done: %s" % (task["definition_of_done"] or "-"),
            "Deliverable result_path: %s" % deliverable,
            "Deliverable result_paths (from completion): %s"
            % (", ".join(rpaths) if rpaths else "-"),
            "Assignee summary: %s" % (task["summary"] or "-"),
        ]
        rl += [
            "",
            "INSTRUCTIONS",
            "-" * 40,
            "Inspect the deliverable(s) at the result_path(s) above against the "
            "origin task's definition_of_done. Decide whether it is approved or "
            "needs changes. Then record your verdict by running, in this session:",
            "  wm review %s --verdict approved --comment \"...\"   OR" % task["id"],
            "  wm review %s --verdict changes_requested --comment \"...\"" % task["id"],
            "  (approved -> origin done + dependents fire; changes_requested -> "
            "origin rework + your comments feed the next run's brief.)",
            "",
        ] + _ask_owner_lines(run_id) + [
            "COMPLETION CONTRACT (REQUIRED — your LAST action):",
            "Write a JSON file to %s" % cpath,
            "with EXACTLY this structure:",
            '{"completed": "done|blocked|failed", "summary": "...", '
            '"result_paths": ["..."], "blocker": "...", '
            '"session_id": "<optional>"}',
            "  - completed: 'done' iff you completed the review and recorded the "
            "verdict above.",
            "  - session_id (OPTIONAL): if your system prompt contains a 'Session "
            "ID:' line, copy it here.",
            "Process exit does NOT count as completion — a valid "
            "{completed:'done'} JSON written to the path above as your last step, "
            "combined with a recorded verdict, is what completes this review.",
        ]
        return "\n".join(rl)

    # Phase 6.5.2: an orchestrator-assigned run on a goal's decomposition task
    # gets the ORCHESTRATOR PLANNING brief (decompose or revise) instead of a
    # generic deliverable work brief. Only plan tasks (they carry a goal_id)
    # take this path; a general orchestrator task falls through unchanged.
    if run["agent_profile"] == ORCHESTRATOR_AGENT and task["goal_id"]:
        return _render_orchestrator_planning_brief(
            run, task, project, primary_path, cpath, db_path)

    # Automatic handoff context: for each completed parent (from task_deps)
    # carry forward the parent's result_path + summary + a --resume command for
    # that parent's latest sessioned run. No Orchestrator copy-paste needed.
    handoff = []
    for d in list_task_deps(task["id"], db_path=db_path):
        pt = get_task(d["depends_on_task_id"], db_path=db_path)
        if pt is None or pt["status"] != "done":
            continue
        last = get_task_last_run(pt["id"], db_path=db_path)
        handoff.append((pt["id"], pt, last))
    lines = []
    lines.append("WORK MANAGER TASK BRIEF")
    lines.append("=" * 40)
    lines.append("Task #%s: %s" % (task["id"], task["title"] or "-"))
    lines.append("Project primary_path (your working directory): %s"
                 % primary_path)
    if task["description"]:
        lines.append("Description: %s" % task["description"])
    if task["definition_of_done"]:
        lines.append("Definition of done: %s" % task["definition_of_done"])
    if task["owner_approval"]:
        lines.append("OWNER APPROVAL GATE: this task ends at the OWNER, not "
                     "at you — after your work (and any review) it lands on "
                     "the owner's desk for approval instead of closing. Do "
                     "not treat your completion as final sign-off.")
    lines.append("Assignee profile: %s" % (run["agent_profile"] or "-"))
    lines.append("Working directory: %s" % (run["workdir"] or primary_path))
    if run["branch"]:
        lines.append("Git branch/worktree: %s (isolated code run — do not "
                     "touch other branches)" % run["branch"])
    lines.append("")
    lines.append("AUTHORIZATION")
    lines.append("-" * 40)
    lines.append("The Work Manager state lives in " + DEFAULT_DB_PATH + ". "
                 "Do NOT edit wm.db (or any database) directly via SQL — it is "
                 "owned and integrity-checked by the system. Report your result "
                 "ONLY through the Completion contract JSON below (and, for "
                 "reviews, the `wm review` command). Editing the database "
                 "directly triggers a tamper audit.")
    lines.append("")
    lines.append("INSTRUCTIONS")
    lines.append("-" * 40)
    lines.append("Work in the project's Working directory above. Complete the task.")
    lines.append("")
    lines += _ask_owner_lines(run_id)
    lines += _completion_contract_lines(cpath)
    if handoff:
        lines.append("")
        lines.append("HANDOFF CONTEXT FROM COMPLETED PARENTS "
                     "(use these; carries forward automatically)")
        lines.append("-" * 40)
        for len_, pt, last in handoff:
            sid = last["session_id"] if last else None
            agent = (last["agent_profile"] if last else None) \
                or pt["assignee_profile"]
            lines.append("Parent task #%s: %s"
                         % (pt["id"], pt["title"] or "-"))
            lines.append("  result_path : %s" % (pt["result_path"] or "-"))
            lines.append("  summary     : %s" % (pt["summary"] or "-"))
            resume = get_resume_command(agent, sid)
            lines.append("  resume      : %s" % (resume or "-"))
    # T5 + Phase 6.5.1: inject the feedback that put this task in `rework` —
    # the Reviewer's `changes_requested` comments and the OWNER FEEDBACK — so
    # the re-run brief carries them end-to-end, in canonical order.
    lines += _feedback_sections(task, db_path)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fix #5 — DB authorization + integrity audit
# ---------------------------------------------------------------------------
# In this private single-user system every process (dispatcher, wrappers, the
# six specialist agents, the Orchestrator) runs under the SAME OS uid, so a
# filesystem permission wall CANNOT hard-block an agent from writing wm.db
# (they can always `chmod`/write the file they own). The "smallest useful
# protection" is therefore two things:
#   1. A strong convention enforcement point (the task/review brief tells every
#      agent to report ONLY through `wm ...` commands — never raw SQL), and
#   2. A cheap, always-runnable consistency audit (`wm check`) that DETECTS
#      state changes that could only have come from raw DB tampering (a task
#      flipped to done with no corresponding run/transition; a decided review
#      with no reviewer session; a task marked done directly; an unknown status
#      that the sanctioned state machine cannot produce).
# This makes bypasses visible to the Orchestrator instead of silently
# accepted — the practical guard for a trusted, single-human system.

def check_integrity(db_path=None):
    """Run the DB-consistency audit. Returns {ok: bool, findings: [str]}.

    Catches typical raw-SQL tamper/bypass and internal drift:
      - a task whose status is not in the sanctioned TASK_STATUSES set;
      - a 'done' task whose newest transition or activity was NOT via a done
        run/review approve (i.e. flipped directly);
      - a decided review (done/changes_requested) with no reviewer session_id;
      - a review that is 'running' but has no linked run;
      - a run still 'running' whose task is not 'running' (desync).
    """
    conn = _connect(db_path)
    findings = []
    try:
        bad_statuses = conn.execute(
            "SELECT id,status FROM tasks WHERE status NOT IN (%s)"
            % ",".join("?" * len(TASK_STATUSES)), TASK_STATUSES).fetchall()
        for r in bad_statuses:
            findings.append("task #%s has an unknown/unsanctioned status %r "
                            "(possible raw SQL)" % (r["id"], r["status"]))
        # done tasks: last transition should be -> done (done run or approve)
        # Approximate: any 'done' task with NO state_transition to 'done' and
        # no done run and no approved review is suspicious.
        done = conn.execute(
            "SELECT id,result_path,summary FROM tasks WHERE status='done'").fetchall()
        for t in done:
            tr = conn.execute(
                "SELECT 1 FROM state_transitions WHERE task_id=? AND to_status='done' LIMIT 1",
                (t["id"],)).fetchone()
            if tr:
                continue
            rrun = conn.execute(
                "SELECT 1 FROM runs WHERE task_id=? AND status='done' LIMIT 1",
                (t["id"],)).fetchone()
            if rrun:
                continue
            passed = conn.execute(
                "SELECT 1 FROM reviews WHERE task_id=? AND verdict='approved' LIMIT 1",
                (t["id"],)).fetchone()
            if not passed:
                findings.append("task #%s is 'done' but has NO done-run, "
                                "approved-review, or recorded transition — "
                                "status likely set directly (tamper?)" % t["id"])
            if not tr:
                findings.append("task #%s: 'done' reached without a "
                                "state_transition record" % t["id"])
        decided_no_session = conn.execute(
            "SELECT id,task_id,status FROM reviews "
            "WHERE status IN ('done','changes_requested') AND "
            "(session_id IS NULL OR session_id='')").fetchall()
        for r in decided_no_session:
            # The sanctioned `wm review ... --verdict` CLI decides a review
            # WITHOUT launching a reviewer run — so a missing session_id is NOT
            # itself tamper. A genuine CLI decision logs an activity entry
            # (review_done / review_changes_requested / review_waived); a raw
            # SQL status/verdict write leaves NO such audit trail. Flag only
            # when there is no session AND no sanctioned activity record.
            acted = conn.execute(
                "SELECT 1 FROM activity WHERE task_id=? AND action IN "
                "('review_done','review_changes_requested','review_waived') "
                "LIMIT 1", (r["task_id"],)).fetchone()
            if acted:
                continue
            findings.append("review #%s (task #%s) decided %r with NO reviewer "
                            "session_id AND no sanctioned `wm review` activity "
                            "— verdict likely written directly via SQL"
                            % (r["id"], r["task_id"], r["status"]))
        running_rev_no_run = conn.execute(
            "SELECT id,task_id FROM reviews WHERE status='running' AND NOT EXISTS "
            "(SELECT 1 FROM runs WHERE runs.review_id=reviews.id)").fetchall()
        for r in running_rev_no_run:
            findings.append("review #%s (task #%s) is 'running' but has no "
                            "linked Reviewer run" % (r["id"], r["task_id"]))
        orphan_reviews = conn.execute(
            "SELECT r.id, r.task_id, r.status FROM reviews r "
            "JOIN tasks t ON t.id = r.task_id "
            "WHERE r.status IN ('pending','running','reviewed') AND t.status='done'").fetchall()
        for r in orphan_reviews:
            findings.append("review #%s (task #%s) is still open (%r) but its "
                            "task is already 'done' — orphaned review; close it "
                            "with `wm review %s --close-orphan`"
                            % (r["id"], r["task_id"], r["status"], r["task_id"]))
        desync = conn.execute(
            "SELECT id,task_id,status FROM runs WHERE status='running' AND NOT EXISTS "
            "(SELECT 1 FROM tasks WHERE tasks.id=runs.task_id AND tasks.status='running')").fetchall()
        for r in desync:
            findings.append("run #%s (task #%s) is 'running' but its task is not "
                            "'running' — state desync" % (r["id"], r["task_id"]))
    finally:
        conn.close()
    return {"ok": not findings, "findings": findings}


# ---------------------------------------------------------------------------
# Fix #8 — backup + retention
# ---------------------------------------------------------------------------
def backup_db(db_path=None, backup_dir=None):
    """Online backup of the live wm.db via the SQLite backup API (safe under
    WAL). Returns the backup file path. Never modifies the live DB."""
    db_path = db_path or DEFAULT_DB_PATH
    backup_dir = backup_dir or resolve_backup_dir(db_path=db_path)
    os.makedirs(backup_dir, exist_ok=True)
    name = "wm-%s.backup.db" % time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, name)
    src = sqlite3.connect(db_path)
    try:
        b = sqlite3.connect(dest)
        try:
            src.backup(b)
        finally:
            b.close()
    finally:
        src.close()
    return dest


def resolve_backup_dir(db_path=None):
    m = get_meta("backup_dir", db_path=db_path)
    if not m:
        m = os.path.join(resolve_runs_dir(), "backups")
    return m


def maybe_auto_backup(db_path=None):
    """Backup if none created within backup_interval_hours (cheap per-tick
    guard; typically a daily backup driven from the dispatch tick). Returns the
    backup path if one was made, else None."""
    import glob
    bdir = resolve_backup_dir(db_path=db_path)
    interval = float(get_meta("backup_interval_hours", db_path=db_path) or 24)
    newest = None
    newest_mtime = None
    if os.path.isdir(bdir):
        for f in glob.glob(os.path.join(bdir, "wm-*.backup.db")):
            m = os.path.getmtime(f)
            if newest_mtime is None or m > newest_mtime:
                newest = f
                newest_mtime = m
    if newest is not None and (time.time() - newest_mtime) < interval * 3600:
        return None
    p = backup_db(db_path=db_path, backup_dir=bdir)
    log_activity(action="db_backup", agent_profile="system",
                 detail="backup written to %s" % p, db_path=db_path)
    return p


def prune_history(retention_days=None, keep_transitions=True, db_path=None):
    """Retention/cleanup for runtime history WITHOUT deleting task/project/
    goal/review identity or current-state data.

    - prunes `activity` rows older than retention_days (the chatty event log);
    - prunes old run log/brief/completion files from runs/ for runs older than
      retention_days (filesystem hygiene);
    - optionally prunes old `state_transitions` for runs older than
      retention_days (keep_transitions=False). By default the durable
      transition log is KEPT so meaningful state history survives cleanup.
    Rows describing live resources — tasks, projects, goals, reviews, and runs
    for open/current work — are never touched.
    Returns a count summary dict.
    """
    import glob
    retention_days = retention_days if retention_days is not None else \
        int(get_meta("retention_days", db_path=db_path) or 180)
    cutoff = time.time() - retention_days * 86400
    counts = {"activity": 0, "transitions": 0, "files": 0}
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM activity WHERE ts < ?", (cutoff,))
        counts["activity"] = cur.fetchone()[0]
        with conn:
            conn.execute("DELETE FROM activity WHERE ts < ?", (cutoff,))
        if not keep_transitions:
            # Only prune transitions linked to old runs; never a task's most
            # recent current-state line (we keep all by default).
            cur = conn.execute(
                "SELECT COUNT(*) FROM state_transitions WHERE run_id IS NOT NULL "
                "AND ts < ?", (cutoff,))
            counts["transitions"] = cur.fetchone()[0]
            with conn:
                conn.execute(
                    "DELETE FROM state_transitions WHERE run_id IS NOT NULL AND ts < ?",
                    (cutoff,))
    finally:
        conn.close()
    # filesystem hygiene for old runs (log/brief/completion), never deleting
    # currently-running or recent run artifacts.
    rdir = resolve_runs_dir()
    if os.path.isdir(rdir):
        for pat in ("*.log", "*.brief.txt", "*.completion.json"):
            for f in glob.glob(os.path.join(rdir, pat)):
                try:
                    if os.path.getmtime(f) < cutoff:
                        os.remove(f)
                        counts["files"] += 1
                except OSError:
                    pass
    log_activity(action="prune", agent_profile="system",
                 detail="retention_days=%s removed=%s" % (retention_days, counts),
                 db_path=db_path)
    return counts

# ---- Group 4: direct chat scopes -------------------------------------------
# A chat session started from a project or task page is linked here so the
# scope can list/resume it. Titles are copied at creation time (each profile's
# transcript lives in its own Hermes state.db; no cross-DB join).

def link_chat_session(profile, session_id, project_id=None, task_id=None, title=None, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_sessions (profile, session_id, project_id, task_id, title, created_at) "
                "VALUES (?,?,?,?,?,?)", (profile, session_id, project_id, task_id, title, time.time()))
        return dict(conn.execute("SELECT * FROM chat_sessions WHERE profile=? AND session_id=?",
                                 (profile, session_id)).fetchone())
    finally:
        conn.close()


def unlink_chat_session(profile, session_id, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM chat_sessions WHERE profile=? AND session_id=?", (profile, session_id))
    finally:
        conn.close()


def retitle_chat_session(profile, session_id, title, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE chat_sessions SET title=? WHERE profile=? AND session_id=?", (title, profile, session_id))
    finally:
        conn.close()


_CHAT_SCOPE_SQL = ("SELECT c.*, p.slug AS project_slug, p.name AS project_name, t.title AS task_title "
                   "FROM chat_sessions c LEFT JOIN projects p ON p.id=c.project_id LEFT JOIN tasks t ON t.id=c.task_id ")


def chat_sessions_for_project(project_id, db_path=None):
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(_CHAT_SCOPE_SQL + "WHERE c.project_id=? ORDER BY c.created_at DESC", (project_id,))]
    finally:
        conn.close()


def chat_sessions_for_task(task_id, db_path=None):
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(_CHAT_SCOPE_SQL + "WHERE c.task_id=? ORDER BY c.created_at DESC", (task_id,))]
    finally:
        conn.close()


def chat_session_scopes(profile, db_path=None):
    """{session_id: scope row} for every linked session of a profile."""
    conn = _connect(db_path)
    try:
        return {r["session_id"]: dict(r) for r in conn.execute(_CHAT_SCOPE_SQL + "WHERE c.profile=?", (profile,))}
    finally:
        conn.close()


_CHAT_BRIEF_FOOTER = [
    "",
    "This is a conversation with the owner, NOT a dispatched task: do not start work, "
    "change task status or write completion files unless the owner asks in this chat.",
    "New work you bring in chat — bug reports, change requests, or feature ideas — must go through the `orchestrator-intake` skill's grilling interview and become a managed task through the Hermes HQ wm pipeline; never use ad-hoc `hermes --profile <agent>` specialist dispatch.",
    "Acknowledge in one short line and wait for the owner's question.",
]


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def render_project_brief(project_id, db_path=None):
    """Opening turn for a project-scoped chat: identity, path, goals, open tasks (≤15)."""
    project = get_project(project_id, db_path=db_path)
    if project is None:
        raise ValueError("no project %s" % project_id)
    conn = _connect(db_path)
    try:
        goals = conn.execute("SELECT id, title, status FROM goals WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        open_tasks = conn.execute(
            "SELECT id, title, status, assignee_profile FROM tasks WHERE project_id=? AND status!='done' "
            "ORDER BY updated_at DESC, id DESC LIMIT 16", (project_id,)).fetchall()
        n_open = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=? AND status!='done'", (project_id,)).fetchone()[0]
    finally:
        conn.close()
    lines = [
        "PROJECT CHAT — %s" % project["name"],
        "=" * 40,
        "Project: %s (slug %s)" % (project["name"], project["slug"]),
        "Primary path: %s" % (project["primary_path"] or "-"),
        "Description: %s" % (_clip(project["description"], 600) or "-"),
        "",
        "GOALS (%d)" % len(goals),
    ]
    lines += ["- #%s %s [%s]" % (g["id"], _clip(g["title"], 90), g["status"]) for g in goals] or ["- none"]
    lines += ["", "OPEN TASKS (%d%s)" % (n_open, ", newest 15" if n_open > 15 else "")]
    lines += ["- #%s %s [%s%s]" % (t["id"], _clip(t["title"], 90), t["status"], (", " + t["assignee_profile"]) if t["assignee_profile"] else "")
              for t in open_tasks[:15]] or ["- none"]
    return "\n".join(lines + _CHAT_BRIEF_FOOTER)


def render_task_brief(task_id, db_path=None):
    """Opening turn for a task-scoped chat: the task, its DoD, project path, latest run."""
    task = get_task(task_id, db_path=db_path)
    if task is None:
        raise ValueError("no task %s" % task_id)
    project = get_project(task["project_id"], db_path=db_path)
    run = get_task_latest_run(task_id, db_path=db_path)
    lines = [
        "TASK CHAT — #%s %s" % (task["id"], task["title"] or "-"),
        "=" * 40,
        "Project: %s" % (project["name"] if project else "-"),
        "Primary path: %s" % ((project["primary_path"] if project else None) or "-"),
        "Status: %s   |   Assignee: %s   |   Review policy: %s" % (task["status"], task["assignee_profile"] or "-", task["review_policy"]),
        "",
        "Description: %s" % (_clip(task["description"], 800) or "-"),
        "Definition of done: %s" % (_clip(task["definition_of_done"], 500) or "-"),
        "Result path: %s" % (task["result_path"] or "-"),
        "Latest summary: %s" % (_clip(task["summary"], 500) or "-"),
        "Owner/reviewer feedback: %s" % (_clip(task["feedback"], 500) or "-"),
    ]
    if run:
        lines.append("Latest run: #%s by %s [%s]%s" % (run["id"], run["agent_profile"], run["status"],
                     (", session " + run["session_id"]) if run["session_id"] else ""))
    return "\n".join(lines + _CHAT_BRIEF_FOOTER)


# ---- Group 4b-5: notifications -----------------------------------------------
# Derived from state_transitions (the engine already records every status change) plus
# client-originated events (chat reply finished off-screen, agent asked a question).
NOTIFY_NEEDS_YOU = {"blocked", "failed", "stalled", "waiting_approval", "needs_review", "manual"}
NOTIFY_INFO = {"done", "rework"}
_NOTIF_WATERMARK = "notif_last_transition_id"


def _set_meta(key, value, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO wm_meta(key, value) VALUES (?, ?)", (key, str(value)))
    finally:
        conn.close()


def mark_run_questions_read(run_id, db_path=None):
    """Mark a run's open question notifications read (the owner just answered)."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE notifications SET read_at=? WHERE run_id=? AND "
                "source_key LIKE 'runq:%' AND read_at IS NULL",
                (time.time(), run_id))
            return cur.rowcount
    finally:
        conn.close()


def add_notification(kind, title, body=None, href=None, task_id=None, run_id=None, project_id=None, source_key=None, ts=None, db_path=None):
    """Insert one notification; a repeated source_key is a no-op (idempotent). Returns the row id or None."""
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notifications(ts, kind, title, body, href, task_id, run_id, project_id, source_key) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (ts or time.time(), kind, title, body, href, task_id, run_id, project_id, source_key))
            return cur.lastrowid if cur.rowcount else None
    finally:
        conn.close()


def sync_notifications(db_path=None):
    """Turn new state_transitions into notifications. The first call only sets the watermark (no backfill of
    history). Returns the ids of the notifications created."""
    conn = _connect(db_path)
    try:
        last = conn.execute("SELECT value FROM wm_meta WHERE key=?", (_NOTIF_WATERMARK,)).fetchone()
        top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM state_transitions").fetchone()[0]
        if last is None:
            with conn:
                conn.execute("INSERT OR REPLACE INTO wm_meta(key, value) VALUES (?, ?)", (_NOTIF_WATERMARK, str(top)))
            return []
        rows = conn.execute(
            "SELECT s.id, s.task_id, s.run_id, s.ts, s.from_status, s.to_status, s.detail, t.title AS task_title, t.project_id, "
            "t.assignee_profile FROM state_transitions s LEFT JOIN tasks t ON t.id = s.task_id WHERE s.id > ? ORDER BY s.id",
            (int(last["value"]),)).fetchall()
        made = []
        with conn:
            for r in rows:
                if r["task_id"] is not None:
                    # The task moved on: attention rows raised before this
                    # transition are stale. ts-bound so a question scanned
                    # after the transition (same tick) is never swallowed.
                    conn.execute(
                        "UPDATE notifications SET read_at=? WHERE task_id=? "
                        "AND kind IN ('needs_you','question') AND read_at IS "
                        "NULL AND ts <= ?",
                        (time.time(), r["task_id"], r["ts"] or time.time()))
                to = r["to_status"]
                if to in NOTIFY_NEEDS_YOU:
                    kind = "needs_you"; title = "Task #%s needs you — %s" % (r["task_id"], to.replace("_", " "))
                elif to in NOTIFY_INFO:
                    kind = "done" if to == "done" else "info"; title = "Task #%s %s" % (r["task_id"], "is done" if to == "done" else "sent back for rework")
                else:
                    continue
                body = "%s%s" % (r["task_title"] or "", (" · " + r["detail"][:160]) if r["detail"] else "")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO notifications(ts, kind, title, body, href, task_id, run_id, project_id, source_key) VALUES (?,?,?,?,?,?,?,?,?)",
                    (r["ts"] or time.time(), kind, title, body.strip(" ·"), "/tasks/%s" % r["task_id"], r["task_id"], r["run_id"], r["project_id"], "transition:%s" % r["id"]))
                if cur.rowcount:
                    made.append(cur.lastrowid)
            conn.execute("INSERT OR REPLACE INTO wm_meta(key, value) VALUES (?, ?)", (_NOTIF_WATERMARK, str(top)))
        return made
    finally:
        conn.close()


def list_notifications(limit=50, unread_only=False, db_path=None):
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM notifications" + (" WHERE read_at IS NULL" if unread_only else "") + " ORDER BY ts DESC, id DESC LIMIT ?"
        rows = [dict(r) for r in conn.execute(sql, (limit,))]
        unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
        return rows, unread
    finally:
        conn.close()


def mark_notifications_read(ids=None, db_path=None, source_key=None):
    """ids=None → everything (unless source_key is given). Returns rows affected."""
    conn = _connect(db_path)
    try:
        with conn:
            if source_key is not None:
                return conn.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL AND source_key=?", (time.time(), source_key)).rowcount
            if ids is None:
                return conn.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (time.time(),)).rowcount
            ids = [int(i) for i in ids]
            if not ids:
                return 0
            return conn.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL AND id IN (%s)" % ",".join("?" * len(ids)),
                                [time.time()] + ids).rowcount
    finally:
        conn.close()


def get_notifications(ids, db_path=None):
    ids = [int(i) for i in ids]
    if not ids:
        return []
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE id IN (%s) ORDER BY id" % ",".join("?" * len(ids)), ids)]
    finally:
        conn.close()


# ---- Web Push subscriptions (4b-5.3) -------------------------------------------
def add_push_subscription(endpoint, keys, user_agent=None, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO push_subscriptions(endpoint, keys_json, user_agent, created_at, failures) VALUES (?,?,?,?,0) "
                         "ON CONFLICT(endpoint) DO UPDATE SET keys_json=excluded.keys_json, user_agent=excluded.user_agent, failures=0",
                         (endpoint, json.dumps(keys), (user_agent or "")[:200], time.time()))
        return dict(conn.execute("SELECT * FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone())
    finally:
        conn.close()


def remove_push_subscription(endpoint, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            return conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,)).rowcount
    finally:
        conn.close()


def list_push_subscriptions(db_path=None):
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM push_subscriptions ORDER BY id")]
    finally:
        conn.close()


def push_subscription_ok(sid, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE push_subscriptions SET last_ok_at=?, failures=0 WHERE id=?", (time.time(), sid))
    finally:
        conn.close()


def push_subscription_failed(sid, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE push_subscriptions SET failures=COALESCE(failures,0)+1 WHERE id=?", (sid,))
            row = conn.execute("SELECT failures FROM push_subscriptions WHERE id=?", (sid,)).fetchone()
            return int(row["failures"]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schedules (Group 7): recurring WM tasks. The dispatcher calls fire_due();
# everything else is plain CRUD. A schedule is a standing approval, so the
# spawned task is marked ready immediately (create_task parks goal-less tasks).
# ---------------------------------------------------------------------------
OVERLAPS = ("skip", "always")
# Named pre-fire heartbeat checks a schedule may carry ("" = none). Each name
# maps to a deterministic predicate in heartbeat_check(); adding a check means
# adding it BOTH places. Kept closed so a typo cannot silently disable a run.
SCHEDULE_HEARTBEATS = ("", "librarian_ingest", "librarian_lint")
OPEN_TASK_STATUSES = ("planned", "waiting_approval", "ready", "running", "needs_review", "rework", "stalled", "blocked", "manual")


def _schedule_row(conn, sid):
    row = conn.execute("SELECT s.*, p.slug AS project_slug, p.name AS project_name FROM schedules s "
                       "JOIN projects p ON p.id = s.project_id WHERE s.id=?", (sid,)).fetchone()
    if row is None:
        raise ValueError("no schedule with id %s" % sid)
    return row


def create_schedule(name, cron, project_slug, title, description="", definition_of_done="",
                    assignee_profile=None, goal_id=None, review_policy="none", is_code=False,
                    zone=None, overlap="skip", enabled=True, one_shot=False,
                    heartbeat="", db_path=None):
    from core import schedule as sch
    zone = zone or sch.DEFAULT_ZONE
    sch.validate(cron, zone)
    if overlap not in OVERLAPS:
        raise ValueError("overlap must be one of %s" % (OVERLAPS,))
    if review_policy not in REVIEW_POLICIES:
        raise ValueError("review_policy must be one of %s" % (REVIEW_POLICIES,))
    if (heartbeat or "") not in SCHEDULE_HEARTBEATS:
        raise ValueError("heartbeat must be one of %s" % (SCHEDULE_HEARTBEATS,))
    if not (name or "").strip() or not (title or "").strip():
        raise ValueError("schedule needs a name and a task title")
    validate_assignee(assignee_profile)
    conn = _connect(db_path)
    try:
        with conn:
            proj = _require_project(conn, project_slug)
            if goal_id is not None and not conn.execute(
                    "SELECT 1 FROM goals WHERE id=? AND project_id=?", (goal_id, proj["id"])).fetchone():
                raise ValueError("goal %s does not belong to project '%s'" % (goal_id, project_slug))
            now = time.time()
            cur = conn.execute(
                "INSERT INTO schedules(name, cron, zone, project_id, title, description, definition_of_done, "
                "assignee_profile, goal_id, review_policy, is_code, overlap, one_shot, heartbeat, enabled, created_at, updated_at, next_fire_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name.strip(), cron, zone, proj["id"], title, description, definition_of_done,
                 assignee_profile, goal_id, review_policy, 1 if is_code else 0, overlap,
                 1 if one_shot else 0, heartbeat or "", 1 if enabled else 0, now, now,
                 sch.next_fires(cron, zone, 1, now)[0]))
            sid = cur.lastrowid
        log_activity(action="schedule_create", project_id=proj["id"], detail=name, db_path=db_path)
        return sid
    finally:
        conn.close()


def update_schedule(sid, db_path=None, **fields):
    from core import schedule as sch
    allowed = {"name", "cron", "zone", "title", "description", "definition_of_done",
               "assignee_profile", "goal_id", "review_policy", "is_code", "overlap",
               "one_shot", "heartbeat", "enabled"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError("cannot update %s" % ", ".join(sorted(bad)))
    if "overlap" in fields and fields["overlap"] not in OVERLAPS:
        raise ValueError("overlap must be one of %s" % (OVERLAPS,))
    if "heartbeat" in fields:
        fields["heartbeat"] = fields["heartbeat"] or ""
        if fields["heartbeat"] not in SCHEDULE_HEARTBEATS:
            raise ValueError("heartbeat must be one of %s" % (SCHEDULE_HEARTBEATS,))
    if "review_policy" in fields and fields["review_policy"] not in REVIEW_POLICIES:
        raise ValueError("review_policy must be one of %s" % (REVIEW_POLICIES,))
    if "assignee_profile" in fields:
        validate_assignee(fields["assignee_profile"])
    conn = _connect(db_path)
    try:
        with conn:
            row = _schedule_row(conn, sid)
            cron = fields.get("cron", row["cron"]); zone = fields.get("zone", row["zone"])
            sch.validate(cron, zone)
            now = time.time()
            sets = {k: (1 if v else 0) if k in ("is_code", "enabled", "one_shot") else v for k, v in fields.items()}
            sets["updated_at"] = now
            if "cron" in fields or "zone" in fields or fields.get("enabled"):
                sets["next_fire_at"] = sch.next_fires(cron, zone, 1, now)[0]
            conn.execute("UPDATE schedules SET %s WHERE id=?" % ", ".join("%s=?" % k for k in sets),
                         (*sets.values(), sid))
        return get_schedule(sid, db_path=db_path)
    finally:
        conn.close()


def delete_schedule(sid, db_path=None):
    conn = _connect(db_path)
    try:
        with conn:
            _schedule_row(conn, sid)
            conn.execute("UPDATE tasks SET schedule_id=NULL WHERE schedule_id=?", (sid,))
            conn.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (sid,))
            conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    finally:
        conn.close()


def get_schedule(sid, db_path=None):
    conn = _connect(db_path)
    try:
        return dict(_schedule_row(conn, sid))
    finally:
        conn.close()


def list_schedules(db_path=None):
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT s.*, p.slug AS project_slug, p.name AS project_name, "
            "t.status AS last_task_status, "
            "(SELECT kind FROM schedule_runs r WHERE r.schedule_id = s.id ORDER BY r.ts DESC LIMIT 1) AS last_run_kind, "
            "(SELECT ts FROM schedule_runs r WHERE r.schedule_id = s.id ORDER BY r.ts DESC LIMIT 1) AS last_run_ts "
            "FROM schedules s JOIN projects p ON p.id = s.project_id "
            "LEFT JOIN tasks t ON t.id = s.last_task_id ORDER BY s.name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_schedule_runs(sid, limit=50, db_path=None):
    conn = _connect(db_path)
    try:
        _schedule_row(conn, sid)
        rows = conn.execute(
            "SELECT r.*, t.title AS task_title, t.status AS task_status FROM schedule_runs r "
            "LEFT JOIN tasks t ON t.id = r.task_id WHERE r.schedule_id=? ORDER BY r.ts DESC LIMIT ?",
            (sid, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _record_schedule_run(conn, sid, kind, task_id=None, detail=None, ts=None):
    conn.execute("INSERT INTO schedule_runs(schedule_id, ts, kind, task_id, detail) VALUES(?,?,?,?,?)",
                 (sid, ts or time.time(), kind, task_id, detail))


def _spawn_from_schedule(row, kind, now, db_path=None):
    """Create the WM task for one firing; the schedule is the approval, so the task goes ready."""
    from core import schedule as sch
    ex = lambda t: sch.expand_tokens(t or "", row["zone"], now)
    tid = create_task(row["project_slug"], ex(row["title"]), ex(row["description"]),
                      ex(row["definition_of_done"]), assignee_profile=row["assignee_profile"],
                      goal_id=row["goal_id"], review_policy=row["review_policy"],
                      is_code=bool(row["is_code"]), db_path=db_path)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE tasks SET schedule_id=? WHERE id=?", (row["id"], tid))
            conn.execute("UPDATE schedules SET last_fired_at=?, last_task_id=?, updated_at=? WHERE id=?",
                         (now, tid, now, row["id"]))
            _record_schedule_run(conn, row["id"], kind, task_id=tid)
    finally:
        conn.close()
    try:
        mark_ready(tid, db_path=db_path)
    except ValueError:
        pass          # e.g. under an unreleased goal: stays parked, still linked
    return tid


def run_schedule_now(sid, db_path=None):
    """Owner's Run now: create the task regardless of overlap; does not move next_fire_at."""
    conn = _connect(db_path)
    try:
        row = dict(_schedule_row(conn, sid))
    finally:
        conn.close()
    return _spawn_from_schedule(row, "manual", time.time(), db_path=db_path)


LATE_AFTER = 90.0     # seconds past due before a firing is recorded as "late"


def fire_due(now=None, db_path=None):
    """One dispatcher-tick pass: fire every enabled schedule whose next_fire_at has passed.
    Catch-up is collapsed to at most one firing (recorded `late`); overlap=skip records
    `skipped` while the last spawned task is still open. Errors never stop the loop."""
    from core import schedule as sch
    now = now or time.time()
    conn = _connect(db_path)
    try:
        due = [dict(r) for r in conn.execute(
            "SELECT s.*, p.slug AS project_slug, t.status AS last_task_status "
            "FROM schedules s JOIN projects p ON p.id = s.project_id "
            "LEFT JOIN tasks t ON t.id = s.last_task_id "
            "WHERE s.enabled=1 AND s.next_fire_at IS NOT NULL AND s.next_fire_at <= ?", (now,)).fetchall()]
    finally:
        conn.close()
    results = []
    for row in due:
        sid = row["id"]
        hb, hb_idle, open_prev, kind = "", False, False, "error"
        try:
            # Heartbeat early-exit (Second Brain P2a): a named deterministic
            # check runs BEFORE any task is minted. "Nothing new" records a
            # skipped firing — no task, no agent run, no model call.
            hb = row.get("heartbeat") or ""
            if hb:
                has_work, hb_detail = heartbeat_check(hb, db_path=db_path)
                hb_idle = not has_work
            open_prev = (row["overlap"] == "skip" and row["last_task_id"] is not None
                         and row["last_task_status"] in OPEN_TASK_STATUSES)
            if hb_idle:
                kind = "skipped"
                conn = _connect(db_path)
                try:
                    with conn:
                        _record_schedule_run(conn, sid, "skipped",
                                             detail="heartbeat: nothing new (%s)" % hb_detail)
                finally:
                    conn.close()
                tid = None
            elif open_prev:
                kind = "skipped"
                conn = _connect(db_path)
                try:
                    with conn:
                        _record_schedule_run(conn, sid, "skipped", task_id=row["last_task_id"],
                                             detail="previous task #%s still %s" % (row["last_task_id"], row["last_task_status"]))
                finally:
                    conn.close()
                tid = None
            else:
                kind = "late" if now - row["next_fire_at"] > LATE_AFTER else "fired"
                tid = _spawn_from_schedule(row, kind, now, db_path=db_path)
            results.append((sid, kind, tid))
        except Exception as e:   # noqa: BLE001 - one broken schedule must not stop the rest
            results.append((sid, "error", None))
            conn = _connect(db_path)
            try:
                with conn:
                    _record_schedule_run(conn, sid, "error", detail=str(e)[:500])
            finally:
                conn.close()
            add_notification("needs_you", "Schedule '%s' failed" % row["name"], body=str(e)[:300],
                             href="/schedules", project_id=row["project_id"],
                             source_key="schedule-error:%s:%s" % (sid, int(row["next_fire_at"])), db_path=db_path)
        # always advance next_fire_at past now (collapses any backlog of missed
        # windows). A one_shot schedule that actually FIRED retires instead:
        # enabled=0, next_fire_at NULL (skipped/error keep it armed).
        fired_once = row.get("one_shot") and kind in ("fired", "late")
        try:
            nxt = None if fired_once else sch.next_fires(row["cron"], row["zone"], 1, now)[0]
        except Exception:
            nxt = None
        # 2b-i: a heartbeat schedule that skipped ONLY because the previous
        # task is still open has pending work (a capture nudge would otherwise
        # be silently consumed by this gate) — re-arm a short retry instead of
        # sleeping until cron, so the run starts soon after the task closes.
        # Heartbeat-idle skips still wait for cron.
        if nxt is not None and kind == "skipped" and hb and not hb_idle and open_prev:
            nxt = min(nxt, now + HEARTBEAT_NUDGE_SECONDS)
        conn = _connect(db_path)
        try:
            with conn:
                if fired_once:
                    conn.execute("UPDATE schedules SET enabled=0, next_fire_at=NULL, updated_at=? WHERE id=?",
                                 (now, sid))
                else:
                    conn.execute("UPDATE schedules SET next_fire_at=? WHERE id=?", (nxt, sid))
        finally:
            conn.close()
    return results


# ---------------------------------------------------------------------------
# Second Brain — areas, notes, entries, revisions, links, search
# (intent/SecondBrainPlan.md, Phase 1). Owner-facing writes only in P1: the
# librarian gets propose-* surfaces in Phase 2 and NEVER writes these tables
# directly — keep it that way.
# ---------------------------------------------------------------------------
NOTE_TYPES = ("note", "playbook", "wiki")
NOTE_STATUSES = ("inbox", "active", "archived")
NOTE_AUTHORS = ("owner", "librarian", "import")
NOTE_LINK_KINDS = ("task", "schedule", "note")


def _encode_tags(tags):
    """Validate and JSON-encode a tag list. Tags are short plain strings."""
    if tags is None:
        return "[]"
    if not isinstance(tags, (list, tuple)):
        raise ValueError("tags must be a list of strings")
    out = []
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            raise ValueError("tags must be non-empty strings, got %r" % (t,))
        if len(t) > 60:
            raise ValueError("tag too long (max 60): %r" % t[:70])
        s = t.strip()
        if s not in out:
            out.append(s)
    return json.dumps(out)


def _decode_note(row):
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except ValueError:
        d["tags"] = []
    return d


def _fts_sync(conn, note_id):
    """Refresh the FTS row for one note (title + body + entries + tags)."""
    if not NOTES_FTS:
        return
    row = conn.execute("SELECT title, body, tags FROM notes WHERE id=?",
                       (note_id,)).fetchone()
    conn.execute("DELETE FROM notes_fts WHERE rowid=?", (note_id,))
    if row is None:
        return
    entries = conn.execute(
        "SELECT body FROM note_entries WHERE note_id=? ORDER BY id",
        (note_id,)).fetchall()
    body = row["body"] or ""
    if entries:
        body = body + "\n" + "\n".join(e["body"] for e in entries)
    try:
        tags = " ".join(json.loads(row["tags"] or "[]"))
    except ValueError:
        tags = ""
    conn.execute(
        "INSERT INTO notes_fts(rowid, title, body, tags) VALUES(?,?,?,?)",
        (note_id, row["title"] or "", body, tags))


def create_area(name, parent_id=None, db_path=None):
    """Create a life area (or sub-area). Returns the new id."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("area name is required")
    name = name.strip()
    conn = _connect(db_path)
    try:
        with conn:
            if parent_id is not None:
                parent = conn.execute("SELECT id, parent_id FROM areas WHERE id=?",
                                      (parent_id,)).fetchone()
                if parent is None:
                    raise ValueError("no such parent area: %r" % parent_id)
                if parent["parent_id"] is not None:
                    raise ValueError("areas are two-level: cannot nest under a sub-area")
            try:
                cur = conn.execute(
                    "INSERT INTO areas(name, parent_id, position, created_at) "
                    "VALUES(?,?,COALESCE((SELECT MAX(position)+1 FROM areas "
                    "WHERE COALESCE(parent_id,0)=COALESCE(?,0)), 0),?)",
                    (name, parent_id, parent_id, time.time()))
            except sqlite3.IntegrityError:
                raise ValueError("area %r already exists at this level" % name)
            return cur.lastrowid
    finally:
        conn.close()


def list_areas(db_path=None, archived=False):
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM areas"
        if not archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY COALESCE(parent_id, id), parent_id IS NOT NULL, position, id"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def create_note(title, body="", note_type="note", status="inbox", area_id=None,
                project_id=None, tags=None, authored_by="owner",
                content_hash=None, db_path=None):
    """Create a note. Returns the new id. Logs to activity."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("note title is required")
    if note_type not in NOTE_TYPES:
        raise ValueError("invalid note type %r (one of %s)"
                         % (note_type, ", ".join(NOTE_TYPES)))
    if status not in NOTE_STATUSES:
        raise ValueError("invalid note status %r (one of %s)"
                         % (status, ", ".join(NOTE_STATUSES)))
    if authored_by not in NOTE_AUTHORS:
        raise ValueError("invalid authored_by %r" % (authored_by,))
    tags_json = _encode_tags(tags)
    conn = _connect(db_path)
    try:
        with conn:
            if area_id is not None and conn.execute(
                    "SELECT id FROM areas WHERE id=?", (area_id,)).fetchone() is None:
                raise ValueError("no such area: %r" % area_id)
            if project_id is not None and conn.execute(
                    "SELECT id FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise ValueError("no such project: %r" % project_id)
            now = time.time()
            cur = conn.execute(
                "INSERT INTO notes(title, body, type, status, area_id, project_id, "
                "tags, authored_by, content_hash, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (title.strip(), body or "", note_type, status, area_id,
                 project_id, tags_json, authored_by, content_hash, now, now))
            nid = cur.lastrowid
            _register_tags(conn, tags, authored_by)
            _fts_sync(conn, nid)
    finally:
        conn.close()
    log_activity("note_created", project_id=project_id,
                 detail="note #%d: %s" % (nid, title.strip()[:120]),
                 db_path=db_path)
    return nid


def get_note(note_id, db_path=None):
    """Full note dict with entries and links, or None."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            return None
        d = _decode_note(row)
        d["entries"] = [dict(e) for e in conn.execute(
            "SELECT * FROM note_entries WHERE note_id=? ORDER BY id DESC",
            (note_id,)).fetchall()]
        d["links"] = _note_links(conn, note_id)
        area = conn.execute("SELECT id, name, parent_id FROM areas WHERE id=?",
                            (d["area_id"],)).fetchone() if d["area_id"] else None
        if area is not None:
            parent = conn.execute("SELECT name FROM areas WHERE id=?",
                                  (area["parent_id"],)).fetchone() if area["parent_id"] else None
            d["area"] = {"id": area["id"], "name": area["name"],
                         "parent": parent["name"] if parent else None}
        else:
            d["area"] = None
        proj = conn.execute("SELECT slug, name FROM projects WHERE id=?",
                            (d["project_id"],)).fetchone() if d["project_id"] else None
        d["project"] = dict(proj) if proj else None
        return d
    finally:
        conn.close()


def _note_links(conn, note_id):
    out = []
    for l in conn.execute("SELECT * FROM note_links WHERE note_id=? ORDER BY created_at",
                          (note_id,)).fetchall():
        item = dict(l)
        if l["kind"] == "task":
            t = conn.execute("SELECT id, title, status FROM tasks WHERE id=?",
                             (l["target_id"],)).fetchone()
            item["target"] = dict(t) if t else None
        elif l["kind"] == "schedule":
            s = conn.execute("SELECT id, name, cron, enabled FROM schedules WHERE id=?",
                             (l["target_id"],)).fetchone()
            item["target"] = dict(s) if s else None
        elif l["kind"] == "note":
            n = conn.execute("SELECT id, title, status, disputed FROM notes WHERE id=?",
                             (l["target_id"],)).fetchone()
            item["target"] = dict(n) if n else None
        out.append(item)
    return out


def list_notes(status=None, area_id=None, project_id=None, note_type=None,
               authored_by=None, tag=None, limit=200, offset=0, db_path=None):
    """Filtered note list, newest-updated first. Bodies truncated for lists.
    Filters compose (2b-ii: tag/project within an area selection)."""
    sql, params = "SELECT * FROM notes WHERE 1=1", []
    if status is not None:
        if status not in NOTE_STATUSES:
            raise ValueError("invalid status filter %r" % status)
        sql += " AND status=?"; params.append(status)
    if tag is not None:
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("invalid tag filter %r" % (tag,))
        sql += (" AND EXISTS (SELECT 1 FROM json_each(notes.tags) "
                "WHERE json_each.value = ?)")
        params.append(tag.strip())
    if area_id is not None:
        sql += (" AND (area_id=? OR area_id IN "
                "(SELECT id FROM areas WHERE parent_id=?))")
        params += [area_id, area_id]
    if project_id is not None:
        sql += " AND project_id=?"; params.append(project_id)
    if note_type is not None:
        sql += " AND type=?"; params.append(note_type)
    if authored_by is not None:
        sql += " AND authored_by=?"; params.append(authored_by)
    sql += " ORDER BY pinned DESC, COALESCE(updated_at, created_at) DESC, id DESC"
    sql += " LIMIT ? OFFSET ?"; params += [int(limit), int(offset)]
    conn = _connect(db_path)
    try:
        rows = [_decode_note(r) for r in conn.execute(sql, params).fetchall()]
        pending = {r["note_id"]: r["pid"] for r in conn.execute(
            "SELECT note_id, MAX(id) AS pid FROM proposals "
            "WHERE status='pending' GROUP BY note_id")}
        for r in rows:
            if r["body"] and len(r["body"]) > 400:
                r["body"] = r["body"][:400]
                r["body_truncated"] = True
            r["entry_count"] = conn.execute(
                "SELECT COUNT(*) AS n FROM note_entries WHERE note_id=?",
                (r["id"],)).fetchone()["n"]
            # the review queue owns the decision: lists surface that a note is
            # already in the librarian's hands so the owner isn't double-filing
            r["pending_proposal_id"] = pending.get(r["id"])
        return rows
    finally:
        conn.close()


_NOTE_EDITABLE = ("title", "body", "area_id", "project_id", "tags", "pinned", "disputed")


def update_note(note_id, edited_by="owner", note_type=None, status=None,
                db_path=None, **fields):
    """Edit a note. Snapshots the previous content into note_revisions first.

    Enum moves (type/status) validate against the closed sets; unknown field
    names are rejected so a typo can never silently no-op.
    """
    bad = set(fields) - set(_NOTE_EDITABLE)
    if bad:
        raise ValueError("unknown note fields: %s" % ", ".join(sorted(bad)))
    if note_type is not None and note_type not in NOTE_TYPES:
        raise ValueError("invalid note type %r" % note_type)
    if status is not None and status not in NOTE_STATUSES:
        raise ValueError("invalid note status %r" % status)
    if edited_by not in NOTE_AUTHORS:
        raise ValueError("invalid edited_by %r" % edited_by)
    conn = _connect(db_path)
    try:
        with conn:
            old = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if old is None:
                raise ValueError("no such note: %r" % note_id)
            conn.execute(
                "INSERT INTO note_revisions(note_id, title, body, tags, edited_by, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (note_id, old["title"], old["body"], old["tags"], edited_by,
                 time.time()))
            sets, params = [], []
            for k, v in fields.items():
                if k == "tags":
                    _register_tags(conn, v, edited_by)
                    v = _encode_tags(v)
                if k == "area_id" and v is not None and conn.execute(
                        "SELECT id FROM areas WHERE id=?", (v,)).fetchone() is None:
                    raise ValueError("no such area: %r" % v)
                if k == "project_id" and v is not None and conn.execute(
                        "SELECT id FROM projects WHERE id=?", (v,)).fetchone() is None:
                    raise ValueError("no such project: %r" % v)
                if k == "title" and (not isinstance(v, str) or not v.strip()):
                    raise ValueError("note title is required")
                if k in ("pinned", "disputed"):
                    v = 1 if v else 0
                sets.append("%s=?" % k); params.append(v)
            if note_type is not None:
                sets.append("type=?"); params.append(note_type)
            if status is not None:
                sets.append("status=?"); params.append(status)
            if not sets:
                raise ValueError("nothing to update")
            sets.append("updated_at=?"); params.append(time.time())
            params.append(note_id)
            conn.execute("UPDATE notes SET %s WHERE id=?" % ", ".join(sets), params)
            _fts_sync(conn, note_id)
    finally:
        conn.close()
    log_activity("note_updated", detail="note #%d by %s" % (note_id, edited_by),
                 db_path=db_path)
    return get_note(note_id, db_path=db_path)


def add_note_entry(note_id, body, db_path=None):
    """Append a dated entry to a note (the 1:1 append-log pattern)."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError("entry body is required")
    conn = _connect(db_path)
    try:
        with conn:
            if conn.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone() is None:
                raise ValueError("no such note: %r" % note_id)
            now = time.time()
            cur = conn.execute(
                "INSERT INTO note_entries(note_id, body, created_at) VALUES(?,?,?)",
                (note_id, body.strip(), now))
            conn.execute("UPDATE notes SET updated_at=? WHERE id=?", (now, note_id))
            _fts_sync(conn, note_id)
            return cur.lastrowid
    finally:
        conn.close()


def link_note(note_id, kind, target_id, db_path=None):
    """Attach a created task/schedule to its source note (create-and-link)."""
    if kind not in NOTE_LINK_KINDS:
        raise ValueError("invalid link kind %r" % kind)
    conn = _connect(db_path)
    try:
        with conn:
            if conn.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone() is None:
                raise ValueError("no such note: %r" % note_id)
            table = {"task": "tasks", "schedule": "schedules", "note": "notes"}[kind]
            if conn.execute("SELECT id FROM %s WHERE id=?" % table,
                            (target_id,)).fetchone() is None:
                raise ValueError("no such %s: %r" % (kind, target_id))
            conn.execute(
                "INSERT OR IGNORE INTO note_links(note_id, kind, target_id, created_at) "
                "VALUES(?,?,?,?)", (note_id, kind, target_id, time.time()))
    finally:
        conn.close()


def notes_for_project(project_id, limit=100, db_path=None):
    return list_notes(project_id=project_id, limit=limit, db_path=db_path)


def notes_for_task(task_id, db_path=None):
    """Notes linked to a task (the task-detail back-link)."""
    conn = _connect(db_path)
    try:
        return [_decode_note(r) for r in conn.execute(
            "SELECT n.* FROM notes n JOIN note_links l ON l.note_id = n.id "
            "WHERE l.kind='task' AND l.target_id=? ORDER BY n.id", (task_id,)).fetchall()]
    finally:
        conn.close()


def search_notes(q, limit=50, db_path=None):
    """Global note search: FTS5 (bm25-ranked) with a LIKE fallback."""
    q = (q or "").strip()
    if not q:
        return []
    conn = _connect(db_path)
    try:
        if NOTES_FTS:
            try:
                # quote each term: user input must never hit FTS query syntax
                match = " ".join('"%s"' % t.replace('"', '""')
                                 for t in q.split() if t)
                rows = conn.execute(
                    "SELECT n.*, bm25(notes_fts) AS rank FROM notes_fts f "
                    "JOIN notes n ON n.id = f.rowid WHERE notes_fts MATCH ? "
                    "ORDER BY rank LIMIT ?", (match, int(limit))).fetchall()
                return [_decode_note(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # malformed query despite quoting — fall back to LIKE
        like = "%" + q + "%"
        rows = conn.execute(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?",
            (like, like, like, int(limit))).fetchall()
        return [_decode_note(r) for r in rows]
    finally:
        conn.close()


def notes_tree(db_path=None):
    """The Library tree: areas (two-level) with note counts, project counts,
    per-type counts, and the inbox count. One call feeds the whole sidebar."""
    conn = _connect(db_path)
    try:
        areas = [dict(r) for r in conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM notes n WHERE n.area_id = a.id "
            "AND n.status != 'archived') AS note_count "
            "FROM areas a WHERE a.archived=0 ORDER BY a.position, a.id").fetchall()]
        projects = [dict(r) for r in conn.execute(
            "SELECT p.id, p.slug, p.name, COUNT(n.id) AS note_count "
            "FROM projects p JOIN notes n ON n.project_id = p.id "
            "AND n.status != 'archived' WHERE p.archived=0 "
            "GROUP BY p.id ORDER BY p.name").fetchall()]
        counts = {r["k"]: r["n"] for r in conn.execute(
            "SELECT type AS k, COUNT(*) AS n FROM notes "
            "WHERE status != 'archived' GROUP BY type").fetchall()}
        counts["inbox"] = conn.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE status='inbox'").fetchone()["n"]
        counts["archived"] = conn.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE status='archived'").fetchone()["n"]
        counts["proposals_pending"] = conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE status='pending'").fetchone()["n"]
        return {"areas": areas, "projects": projects, "counts": counts}
    finally:
        conn.close()


def _register_tags(conn, tags, added_by):
    """Idempotently add tags to the closed taxonomy. Owner/import writes call
    this on every save (the owner IS the taxonomy authority); librarian tags
    arrive here only through owner-approved proposals."""
    now = time.time()
    for t in tags or []:
        s = t.strip()
        if s:
            conn.execute("INSERT OR IGNORE INTO note_tag_taxonomy(tag, added_by, created_at) "
                         "VALUES(?,?,?)", (s, added_by, now))


def taxonomy_tags(db_path=None):
    conn = _connect(db_path)
    try:
        return {r["tag"] for r in conn.execute("SELECT tag FROM note_tag_taxonomy")}
    finally:
        conn.close()


def list_note_tags(db_path=None):
    """The taxonomy with in-use counts — the librarian's orientation read.
    2b-ii: the taxonomy is CLOSED for agents (registered tags only; coinage
    must be declared via new_tags and lands at owner approval), so zero-count
    registered tags are listed too."""
    conn = _connect(db_path)
    try:
        counts = {r["tag"]: 0 for r in conn.execute("SELECT tag FROM note_tag_taxonomy")}
        for r in conn.execute("SELECT tags FROM notes WHERE status != 'archived'"):
            try:
                for t in json.loads(r["tags"] or "[]"):
                    counts[t] = counts.get(t, 0) + 1
            except ValueError:
                continue
        return sorted(({"tag": t, "count": n} for t, n in counts.items()),
                      key=lambda x: (-x["count"], x["tag"]))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Second Brain Phase 2a — librarian proposals.
#
# The librarian NEVER writes the note tables. Its entire write surface is
# create_proposal (via `wm note propose-*`); every mutation of notes happens
# only in approve_proposal, which the OWNER reaches through the cookie-session
# HTTP API. Keep it that way: do not add sanctioned agent paths into notes.
# ---------------------------------------------------------------------------
PROPOSAL_KINDS = ("split", "file", "contradiction", "new_task")
# Kinds that must ALWAYS cross the owner's eyes: never classified routine, so
# bulk-approve can't silently mint a task or flag a dispute. When adding a
# kind (P4: wiki_update), decide its place here explicitly.
OWNER_EYES_KINDS = ("contradiction", "new_task")
PROPOSAL_STATUSES = ("pending", "approved", "rejected", "superseded")
PROPOSAL_CLASSES = ("routine", "needs_attention")
MAX_SPLIT_PARTS = 50


def _validate_filing(conn, part, where, new_tags=frozenset()):
    """Validate the optional filing fields (area_id/project_id/tags/type) of a
    file payload or a split part. Raises ValueError with a located message.
    2b-ii: tags must be in the closed taxonomy unless declared in the
    payload's new_tags (agent coinage lands only via owner approval)."""
    if not isinstance(part, dict):
        raise ValueError("%s must be an object" % where)
    unknown = set(part) - {"title", "body", "area_id", "project_id", "tags", "type"}
    if unknown:
        raise ValueError("%s has unknown fields: %s" % (where, ", ".join(sorted(unknown))))
    if part.get("area_id") is not None:
        if conn.execute("SELECT id FROM areas WHERE id=?", (part["area_id"],)).fetchone() is None:
            raise ValueError("%s: no such area: %r" % (where, part["area_id"]))
    if part.get("project_id") is not None:
        if conn.execute("SELECT id FROM projects WHERE id=?", (part["project_id"],)).fetchone() is None:
            raise ValueError("%s: no such project: %r" % (where, part["project_id"]))
    if part.get("type") is not None and part["type"] not in NOTE_TYPES:
        raise ValueError("%s: invalid note type %r" % (where, part["type"]))
    if "tags" in part:
        _encode_tags(part.get("tags"))          # raises on bad shape
        known = {r["tag"] for r in conn.execute("SELECT tag FROM note_tag_taxonomy")}
        bad = sorted({t.strip() for t in (part.get("tags") or [])
                      if isinstance(t, str) and t.strip()
                      and t.strip() not in known and t.strip() not in new_tags})
        if bad:
            raise ValueError("%s: tag(s) not in the taxonomy: %s — reuse an existing "
                             "tag (wm note tags) or declare coinage with new_tags"
                             % (where, ", ".join(bad)))


def _validate_new_tags(payload, used_tags):
    """Validate an agent's declared tag coinage. Every declared tag must be
    non-empty, short, and actually used by the payload. Returns the set."""
    nt = payload.get("new_tags")
    if nt is None:
        return frozenset()
    if not isinstance(nt, (list, tuple)):
        raise ValueError("new_tags must be a list of strings")
    out = set()
    for t in nt:
        if not isinstance(t, str) or not t.strip():
            raise ValueError("new_tags must be non-empty strings, got %r" % (t,))
        if len(t) > 60:
            raise ValueError("new tag too long (max 60): %r" % t[:70])
        out.add(t.strip())
    unused = out - used_tags
    if unused:
        raise ValueError("new_tags declared but not used on anything: %s"
                         % ", ".join(sorted(unused)))
    return frozenset(out)


def _validate_proposal_payload(conn, kind, payload, note_id=None):
    if kind == "file":
        # `archive` (2b-i) marks a junk/museum capture: filing straight to
        # Archive instead of the Library. No separate kind — same review flow.
        archive = payload.get("archive")
        used = {t.strip() for t in (payload.get("tags") or []) if isinstance(t, str)}
        new_tags = _validate_new_tags(payload, used)
        _validate_filing(conn, {k: v for k, v in payload.items()
                                if k not in ("archive", "new_tags")},
                         "file payload", new_tags=new_tags)
        if archive is not None and not isinstance(archive, bool):
            raise ValueError("file payload: archive must be true/false")
        if payload.get("title") is not None or payload.get("body") is not None:
            raise ValueError("a file proposal moves a note; it cannot rewrite "
                             "title/body (that is a split part's job)")
        if not archive and not any(payload.get(k) is not None
                                   for k in ("area_id", "project_id", "tags", "type")):
            raise ValueError("file payload needs at least one of area_id, "
                             "project_id, tags, type (or archive: true)")
    elif kind == "split":
        parts = payload.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("split payload needs a non-empty parts list")
        if len(parts) > MAX_SPLIT_PARTS:
            raise ValueError("split payload has %d parts (max %d)" % (len(parts), MAX_SPLIT_PARTS))
        unknown = set(payload) - {"parts", "archive_original", "new_tags"}
        if unknown:
            raise ValueError("split payload has unknown fields: %s" % ", ".join(sorted(unknown)))
        used = {t.strip() for part in parts if isinstance(part, dict)
                for t in (part.get("tags") or []) if isinstance(t, str)}
        new_tags = _validate_new_tags(payload, used)
        for i, part in enumerate(parts):
            where = "split part %d" % (i + 1)
            _validate_filing(conn, part, where, new_tags=new_tags)
            title = part.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("%s needs a title" % where)
            if len(title) > 300:
                raise ValueError("%s: title too long (max 300)" % where)
            if part.get("body") is not None and not isinstance(part["body"], str):
                raise ValueError("%s: body must be a string" % where)
    elif kind == "contradiction":
        # Keep-both, never silently reconcile: approval flags BOTH notes
        # disputed and cross-links them; no content is merged or rewritten.
        unknown = set(payload) - {"other_note_id", "explanation"}
        if unknown:
            raise ValueError("contradiction payload has unknown fields: %s"
                             % ", ".join(sorted(unknown)))
        other = payload.get("other_note_id")
        if not isinstance(other, int):
            raise ValueError("contradiction payload needs other_note_id (int)")
        if note_id is not None and other == note_id:
            raise ValueError("a note cannot contradict itself")
        if conn.execute("SELECT id FROM notes WHERE id=?", (other,)).fetchone() is None:
            raise ValueError("contradiction payload: no such note: %r" % other)
        exp = payload.get("explanation")
        if exp is not None and (not isinstance(exp, str) or len(exp) > 2000):
            raise ValueError("contradiction explanation must be a string (max 2000)")
        if note_id is not None:
            # An adjudicated pair (both still disputed + cross-linked) must not
            # come back as a fresh proposal — the owner already decided it.
            mine = conn.execute("SELECT disputed FROM notes WHERE id=?",
                                (note_id,)).fetchone()
            theirs = conn.execute("SELECT disputed FROM notes WHERE id=?",
                                  (other,)).fetchone()
            linked = conn.execute(
                "SELECT 1 FROM note_links WHERE note_id=? AND kind='note' AND target_id=?",
                (note_id, other)).fetchone()
            if mine and theirs and linked and mine["disputed"] and theirs["disputed"]:
                raise ValueError("notes #%s and #%s are already flagged disputed "
                                 "(adjudicated) — do not re-propose this pair"
                                 % (note_id, other))
    elif kind == "new_task":
        # Graduation stays create-and-link: approval creates a real HQ task
        # linked to the note; the note stays a note.
        unknown = set(payload) - {"title", "description", "project_id", "assignee"}
        if unknown:
            raise ValueError("new_task payload has unknown fields: %s"
                             % ", ".join(sorted(unknown)))
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("new_task payload needs a title")
        if len(title) > 300:
            raise ValueError("new_task title too long (max 300)")
        if payload.get("description") is not None and not isinstance(payload["description"], str):
            raise ValueError("new_task description must be a string")
        if payload.get("project_id") is not None and conn.execute(
                "SELECT id FROM projects WHERE id=?", (payload["project_id"],)).fetchone() is None:
            raise ValueError("new_task payload: no such project: %r" % payload["project_id"])
        assignee = payload.get("assignee")
        if assignee is not None and assignee not in ASSIGNABLE:
            raise ValueError("new_task assignee %r not assignable (one of %s)"
                             % (assignee, ", ".join(ASSIGNABLE)))
        if payload.get("project_id") is None and note_id is not None:
            row = conn.execute("SELECT project_id FROM notes WHERE id=?",
                               (note_id,)).fetchone()
            if row is not None and row["project_id"] is None:
                raise ValueError("new_task payload needs a project_id — note #%s "
                                 "is not project-linked" % note_id)
    else:
        raise ValueError("invalid proposal kind %r (one of %s)"
                         % (kind, ", ".join(PROPOSAL_KINDS)))


def create_proposal(kind, note_id, payload, summary="", classification="needs_attention",
                    author="librarian", db_path=None):
    """File a librarian proposal against a note. Writes ONLY the proposals
    table. Supersedes any older pending proposal of the same kind on the same
    note (a re-read replaces, never stacks). Notifies the owner (needs_you)."""
    if classification not in PROPOSAL_CLASSES:
        raise ValueError("classification must be one of %s" % (PROPOSAL_CLASSES,))
    if kind in OWNER_EYES_KINDS and classification == "routine":
        raise ValueError("%s proposals are always needs_attention — "
                         "the owner reads them before anything happens" % kind)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    conn = _connect(db_path)
    try:
        with conn:
            note = conn.execute("SELECT id, title, status FROM notes WHERE id=?",
                                (note_id,)).fetchone()
            if note is None:
                raise ValueError("no such note: %r" % note_id)
            _validate_proposal_payload(conn, kind, payload, note_id=note_id)
            now = time.time()
            conn.execute(
                "UPDATE proposals SET status='superseded', decided_at=? "
                "WHERE note_id=? AND kind=? AND status='pending'", (now, note_id, kind))
            cur = conn.execute(
                "INSERT INTO proposals(kind, note_id, payload, summary, classification, "
                "author, created_at) VALUES(?,?,?,?,?,?,?)",
                (kind, note_id, json.dumps(payload), (summary or "").strip(),
                 classification, author, now))
            pid = cur.lastrowid
    finally:
        conn.close()
    log_activity("proposal_created", detail="proposal #%d (%s) on note #%d: %s"
                 % (pid, kind, note_id, (summary or "")[:120]), db_path=db_path)
    add_notification("needs_you", "Librarian: %s proposal on '%s'" % (kind, note["title"][:80]),
                     body=(summary or "")[:300] or None, href="/brain/review",
                     source_key="proposal:%d" % pid, db_path=db_path)
    return pid


def _decode_proposal(row):
    d = dict(row)
    for k in ("payload", "result"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except ValueError:
            d[k] = None
    return d


def list_proposals(status=None, classification=None, note_id=None, kind=None,
                   limit=100, offset=0, db_path=None):
    """Proposal list newest-first, with the subject note's title/status joined."""
    sql = ("SELECT p.*, n.title AS note_title, n.status AS note_status "
           "FROM proposals p LEFT JOIN notes n ON n.id = p.note_id WHERE 1=1")
    params = []
    if status is not None:
        if status not in PROPOSAL_STATUSES:
            raise ValueError("invalid status filter %r" % status)
        sql += " AND p.status=?"; params.append(status)
    if classification is not None:
        if classification not in PROPOSAL_CLASSES:
            raise ValueError("invalid classification filter %r" % classification)
        sql += " AND p.classification=?"; params.append(classification)
    if note_id is not None:
        sql += " AND p.note_id=?"; params.append(note_id)
    if kind is not None:
        if kind not in PROPOSAL_KINDS:
            raise ValueError("invalid kind filter %r" % kind)
        sql += " AND p.kind=?"; params.append(kind)
    sql += " ORDER BY p.id DESC LIMIT ? OFFSET ?"; params += [int(limit), int(offset)]
    conn = _connect(db_path)
    try:
        return [_decode_proposal(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_proposal(pid, db_path=None):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT p.*, n.title AS note_title, n.status AS note_status "
            "FROM proposals p LEFT JOIN notes n ON n.id = p.note_id WHERE p.id=?",
            (pid,)).fetchone()
        return _decode_proposal(row) if row else None
    finally:
        conn.close()


def proposal_counts(db_path=None):
    """Badge counts for the review queue: pending split by classification."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT classification AS k, COUNT(*) AS n FROM proposals "
            "WHERE status='pending' GROUP BY classification").fetchall()
        by = {r["k"]: r["n"] for r in rows}
        return {"pending": sum(by.values()),
                "routine": by.get("routine", 0),
                "needs_attention": by.get("needs_attention", 0)}
    finally:
        conn.close()


def approve_proposal(pid, payload_override=None, db_path=None):
    """OWNER approval: apply the proposed change to the Library.

    file          -> apply the filing to the note (inbox leaves for the
                     Library; `archive: true` sends it to Archive instead).
    split         -> create one note per part (verbatim owner text =>
                     authored_by 'owner'; a filed part lands 'active', an
                     unfiled one 'inbox'), then archive the original unless
                     archive_original is false.
    contradiction -> flag BOTH notes disputed and cross-link them (keep-both).
    new_task      -> create a real HQ task and link it (create-and-link).

    `payload_override` is the owner's edit-before-approve: the edited payload
    is validated, applied, and only then persisted onto the proposal row (so
    the record shows what was actually approved, while a FAILED approval
    leaves the librarian's original payload intact). Everything is
    re-validated against current state — the note or an area may have changed
    since the librarian proposed. Returns the decided proposal.
    """
    p = get_proposal(pid, db_path=db_path)
    if p is None:
        raise ValueError("no such proposal: %r" % pid)
    if p["status"] != "pending":
        raise ValueError("proposal #%d is %r, not pending" % (pid, p["status"]))
    note = get_note(p["note_id"], db_path=db_path)
    if note is None:
        raise ValueError("proposal #%d's note #%s no longer exists" % (pid, p["note_id"]))
    payload = p["payload"] or {}
    edited = payload_override is not None and payload_override != payload
    if edited:
        if not isinstance(payload_override, dict):
            raise ValueError("edited payload must be an object")
        payload = payload_override
        # The OWNER wrote this payload: their tags are taxonomy authority, so
        # register them before validation (agents still need new_tags).
        owner_tags = list(payload.get("tags") or []) + [
            t for part in (payload.get("parts") or []) if isinstance(part, dict)
            for t in (part.get("tags") or [])]
        if owner_tags:
            conn = _connect(db_path)
            try:
                with conn:
                    _register_tags(conn, [t for t in owner_tags if isinstance(t, str)],
                                   "owner")
            finally:
                conn.close()
    # Re-validate the whole payload up front so a stale area/project (or a bad
    # owner edit) fails the approval BEFORE any note is touched. The edited
    # payload is persisted only AFTER the apply succeeds — a failed approval
    # must leave the librarian's original payload intact on the pending row.
    conn = _connect(db_path)
    try:
        _validate_proposal_payload(conn, p["kind"], payload, note_id=p["note_id"])
    finally:
        conn.close()
    # Revisions/activity name whoever authored the applied payload.
    actor = "owner" if edited else "librarian"
    result = {}
    if p["kind"] == "file":
        fields = {k: payload[k] for k in ("area_id", "project_id", "tags")
                  if payload.get(k) is not None}
        archived = bool(payload.get("archive"))
        update_note(p["note_id"], edited_by=actor,
                    note_type=payload.get("type"),
                    status="archived" if archived else "active",
                    db_path=db_path, **fields)
        result = {"filed": True, "archived": archived}
    elif p["kind"] == "contradiction":
        other = payload["other_note_id"]
        for a, b in ((p["note_id"], other), (other, p["note_id"])):
            update_note(a, edited_by=actor, disputed=True, db_path=db_path)
            link_note(a, "note", b, db_path=db_path)
        result = {"disputed": [p["note_id"], other]}
    elif p["kind"] == "new_task":
        proj = None
        if payload.get("project_id") is not None:
            proj = get_project(payload["project_id"], db_path=db_path)
        elif note.get("project"):
            proj = note["project"]
        if proj is None:
            raise ValueError("new_task proposal #%d has no project and note #%d "
                             "is not project-linked — edit the proposal to pick one"
                             % (pid, p["note_id"]))
        desc = payload.get("description") or ("From note #%d: %s"
                                              % (p["note_id"], note["title"]))
        tid = create_task(proj["slug"], payload["title"].strip(), desc, "",
                          assignee_profile=payload.get("assignee") or OWNER_ASSIGNEE,
                          db_path=db_path)
        mark_ready(tid, db_path=db_path)
        link_note(p["note_id"], "task", tid, db_path=db_path)
        result = {"task_id": tid}
    else:                                        # split
        ids = []
        for part in payload["parts"]:
            filed = part.get("area_id") is not None or part.get("project_id") is not None
            ids.append(create_note(
                part["title"], body=part.get("body") or "",
                note_type=part.get("type") or "note",
                status="active" if filed else "inbox",
                area_id=part.get("area_id"), project_id=part.get("project_id"),
                tags=part.get("tags"), authored_by="owner", db_path=db_path))
        if payload.get("archive_original", True):
            update_note(p["note_id"], edited_by=actor, status="archived",
                        db_path=db_path)
        result = {"note_ids": ids}
        # Unfiled parts land back in the inbox as fresh untriaged notes — give
        # them the same ~2 min ingest latency a direct capture gets.
        if any(part.get("area_id") is None and part.get("project_id") is None
               for part in payload["parts"]):
            nudge_heartbeat_schedules("librarian_ingest", db_path=db_path)
    now = time.time()
    conn = _connect(db_path)
    try:
        with conn:
            if edited:
                conn.execute("UPDATE proposals SET payload=? WHERE id=?",
                             (json.dumps(payload), pid))
            conn.execute("UPDATE proposals SET status='approved', result=?, decided_at=? "
                         "WHERE id=?", (json.dumps(result), now, pid))
    finally:
        conn.close()
    if edited:
        log_activity("proposal_edited", detail="proposal #%d (%s) payload edited "
                     "by owner at approval" % (pid, p["kind"]), db_path=db_path)
    log_activity("proposal_approved", detail="proposal #%d (%s) on note #%d"
                 % (pid, p["kind"], p["note_id"]), db_path=db_path)
    return get_proposal(pid, db_path=db_path)


def reject_proposal(pid, feedback=None, db_path=None):
    """OWNER rejection. The feedback text is kept on the row — the librarian
    reads it (`wm note proposals --status rejected`) before re-proposing."""
    conn = _connect(db_path)
    try:
        with conn:
            row = conn.execute("SELECT id, status, kind, note_id FROM proposals WHERE id=?",
                               (pid,)).fetchone()
            if row is None:
                raise ValueError("no such proposal: %r" % pid)
            if row["status"] != "pending":
                raise ValueError("proposal #%d is %r, not pending" % (pid, row["status"]))
            conn.execute("UPDATE proposals SET status='rejected', feedback=?, decided_at=? "
                         "WHERE id=?", ((feedback or "").strip() or None, time.time(), pid))
    finally:
        conn.close()
    log_activity("proposal_rejected", detail="proposal #%d (%s) on note #%d"
                 % (pid, row["kind"], row["note_id"]), db_path=db_path)
    return get_proposal(pid, db_path=db_path)


def approve_routine_proposals(db_path=None):
    """Bulk-approve every pending routine proposal (oldest first). One failure
    never stops the rest; failures come back with their reasons."""
    ids = [p["id"] for p in list_proposals(status="pending", classification="routine",
                                           limit=500, db_path=db_path)]
    ids.sort()
    approved, failed = [], []
    for pid in ids:
        try:
            approve_proposal(pid, db_path=db_path)
            approved.append(pid)
        except ValueError as e:
            failed.append({"id": pid, "error": str(e)})
    return {"approved": approved, "failed": failed}


def heartbeat_check(name, db_path=None):
    """Run a named schedule heartbeat. Returns (has_work, detail).

    librarian_ingest: work exists when any inbox note has no pending proposal
    covering it — i.e. something captured that the librarian has not yet
    triaged. Deterministic SQL only; this is what makes a quiet ingest tick
    free (no task, no model call)."""
    if name == "librarian_ingest":
        conn = _connect(db_path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM notes n WHERE n.status='inbox' AND NOT EXISTS "
                "(SELECT 1 FROM proposals p WHERE p.note_id=n.id AND p.status='pending')"
            ).fetchone()["n"]
        finally:
            conn.close()
        return (n > 0, "%d untriaged inbox note(s)" % n)
    if name == "librarian_lint":
        # Deterministic hygiene sweep (2b-ii lint lane): a clean library skips
        # the run entirely; findings mint a librarian fix-via-proposals run AND
        # land as one daily-deduped owner notification (inside lint_library).
        findings = lint_library(notify=True, db_path=db_path)
        by = {}
        for f in findings:
            by[f["check"]] = by.get(f["check"], 0) + 1
        detail = ", ".join("%s ×%d" % (k, v) for k, v in sorted(by.items()))
        return (len(findings) > 0, detail or "library clean")
    # Unknown names fail OPEN (the run happens) — a heartbeat must never be
    # able to silently kill a schedule. create/update validate the closed set.
    return (True, "unknown heartbeat %r" % name)


HEARTBEAT_NUDGE_SECONDS = 120


def nudge_heartbeat_schedules(name, delay=HEARTBEAT_NUDGE_SECONDS, db_path=None):
    """Pull every enabled schedule with this heartbeat forward to ~now+delay.

    2b-i capture nudge: a fresh capture shouldn't wait out the full cron gap.
    Never moves a fire EARLIER than now+delay, so a burst of captures debounces
    into one run (the first capture sets the time; later ones see next_fire_at
    already <= target and leave it). Returns how many schedules moved.
    """
    target = time.time() + delay
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE schedules SET next_fire_at=? WHERE enabled=1 AND heartbeat=? "
                "AND next_fire_at IS NOT NULL AND next_fire_at > ?",
                (target, name, target))
            return cur.rowcount
    finally:
        conn.close()


LINT_STALE_INBOX_DAYS = 7
LINT_OVERSIZED_BODY = 20000


def lint_library(notify=False, db_path=None):
    """Deterministic Library hygiene checks (2b-ii lint lane) — pure SQL/code,
    never a model call. Returns findings as {check, note_id, title, detail}.
    The librarian reads this via `wm note lint` and fixes ONLY via proposals.
    With notify=True a non-empty report lands as one needs_you notification,
    deduped per day through source_key."""
    conn = _connect(db_path)
    findings = []
    add = lambda check, note_id, title, detail: findings.append(
        {"check": check, "note_id": note_id, "title": title, "detail": detail})
    try:
        now = time.time()
        for r in conn.execute("SELECT id, title FROM notes WHERE status='active' "
                              "AND area_id IS NULL AND project_id IS NULL ORDER BY id"):
            add("orphan", r["id"], r["title"], "active note filed nowhere (no area, no project)")
        cutoff = now - LINT_STALE_INBOX_DAYS * 86400
        for r in conn.execute("SELECT id, title FROM notes WHERE status='inbox' "
                              "AND created_at < ? ORDER BY id", (cutoff,)):
            add("stale_inbox", r["id"], r["title"],
                "sitting untriaged in the inbox for over %d days" % LINT_STALE_INBOX_DAYS)
        for kind, table in (("task", "tasks"), ("schedule", "schedules"), ("note", "notes")):
            for r in conn.execute(
                    "SELECT l.note_id, l.target_id FROM note_links l "
                    "LEFT JOIN %s t ON t.id = l.target_id "
                    "WHERE l.kind=? AND t.id IS NULL" % table, (kind,)):
                add("dangling_link", r["note_id"], None,
                    "linked %s #%s no longer exists" % (kind, r["target_id"]))
        if NOTES_FTS:
            for r in conn.execute("SELECT n.id, n.title FROM notes n WHERE NOT EXISTS "
                                  "(SELECT 1 FROM notes_fts f WHERE f.rowid = n.id) ORDER BY n.id"):
                add("missing_fts", r["id"], r["title"], "note absent from the search index")
        for r in conn.execute("SELECT id, title, LENGTH(body) AS n FROM notes "
                              "WHERE status != 'archived' AND LENGTH(body) > ? ORDER BY id",
                              (LINT_OVERSIZED_BODY,)):
            add("oversized", r["id"], r["title"],
                "body is %d chars — probably an unsplit dump" % r["n"])
        by_norm = {}
        for r in conn.execute("SELECT tag FROM note_tag_taxonomy"):
            norm = r["tag"].strip().lower().rstrip("s")
            by_norm.setdefault(norm, []).append(r["tag"])
        for norm, group in sorted(by_norm.items()):
            if len(group) > 1:
                add("tag_duplicates", None, None,
                    "near-duplicate tags in the taxonomy: %s" % ", ".join(sorted(group)))
    finally:
        conn.close()
    if notify and findings:
        by = {}
        for f in findings:
            by[f["check"]] = by.get(f["check"], 0) + 1
        day = time.strftime("%Y-%m-%d")
        add_notification(
            "needs_you", "Library lint: %d finding(s)" % len(findings),
            body=", ".join("%s ×%d" % (k, v) for k, v in sorted(by.items())),
            href="/brain/library", source_key="lint:%s" % day, db_path=db_path)
    return findings


def trigger_heartbeat_schedule(name, db_path=None):
    """Owner-triggered fire of the named heartbeat schedule ("Triage now").

    Applies the SAME honesty gates as fire_due — heartbeat idle and the
    schedule's own overlap policy — so the button can never silently spend a
    model run, and records a skipped firing in the run history like the
    dispatcher would. Returns {"queued", "task_id", "detail"}; raises
    ValueError when no enabled schedule carries this heartbeat.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT s.*, p.slug AS project_slug, t.status AS last_task_status "
            "FROM schedules s JOIN projects p ON p.id = s.project_id "
            "LEFT JOIN tasks t ON t.id = s.last_task_id "
            "WHERE s.enabled=1 AND s.heartbeat=? ORDER BY s.id LIMIT 1",
            (name,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError("no enabled schedule with heartbeat %r — create or "
                         "resume one under Schedules" % name)
    row = dict(row)
    has_work, detail = heartbeat_check(name, db_path=db_path)
    if not has_work:
        conn = _connect(db_path)
        try:
            with conn:
                _record_schedule_run(conn, row["id"], "skipped",
                                     detail="manual: heartbeat nothing new (%s)" % detail)
        finally:
            conn.close()
        return {"queued": False, "task_id": None,
                "detail": "nothing to triage (%s)" % detail}
    if (row["overlap"] == "skip" and row["last_task_id"] is not None
            and row["last_task_status"] in OPEN_TASK_STATUSES):
        conn = _connect(db_path)
        try:
            with conn:
                _record_schedule_run(conn, row["id"], "skipped", task_id=row["last_task_id"],
                                     detail="manual: previous task #%s still %s"
                                            % (row["last_task_id"], row["last_task_status"]))
        finally:
            conn.close()
        return {"queued": False, "task_id": row["last_task_id"],
                "detail": "ingest task #%s is already %s"
                          % (row["last_task_id"], row["last_task_status"])}
    tid = _spawn_from_schedule(row, "manual", time.time(), db_path=db_path)
    return {"queued": True, "task_id": tid, "detail": detail}
