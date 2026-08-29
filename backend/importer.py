"""Import a legacy Work Manager directory (wm.db + runs/) into the hermes-hq home.

Copies the database with SQLite's backup API (safe while the old WM is live),
copies runs/ minus worktrees/, and rewrites the old absolute path prefix in the
runs table. Never edits task/goal/project data.
"""
import os
import shutil
import sqlite3
import time

from core import wm_store as store

# (table, column) pairs that hold paths or JSON containing paths. Free-text
# columns (runs.error, runs.notes) are historical messages and are left alone.
PATH_COLUMNS = (("runs", "brief_path"), ("runs", "workdir"), ("runs", "result_paths"),
                ("runs", "completion"), ("tasks", "result_path"), ("tasks", "result_paths"))


class ImportError_(Exception):
    pass


def _row_count(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def import_wm(src_dir, dest_home=None, force=False, copy_runs=True):
    src_db = os.path.join(src_dir, "wm.db")
    src_runs = os.path.join(src_dir, "runs")
    if not os.path.isfile(src_db):
        raise ImportError_("no wm.db in %s" % src_dir)
    dest_home = dest_home or store.hq_home()
    dest_db = os.path.join(dest_home, "hq.db")
    dest_runs = os.path.join(dest_home, "runs")
    os.makedirs(dest_home, exist_ok=True)
    if os.path.exists(dest_db) and _row_count(dest_db, "tasks") > 0 and not force:
        raise ImportError_("%s already has %d tasks; pass --force to replace it"
                           % (dest_db, _row_count(dest_db, "tasks")))
    backup = None
    if os.path.exists(dest_db) and _row_count(dest_db, "tasks") > 0:
        backup = dest_db + ".pre-import-%s" % time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(dest_db, backup)

    # 1. consistent DB snapshot (works while the source is being written)
    tmp = dest_db + ".importing"
    src = sqlite3.connect("file:%s?mode=ro" % src_db, uri=True)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    for suffix in ("", "-wal", "-shm"):
        p = dest_db + suffix
        if os.path.exists(p):
            os.remove(p)
    os.replace(tmp, dest_db)

    # 2. rewrite legacy absolute paths so runs resolve under the new home
    old_prefix = os.path.abspath(src_dir).rstrip("/") + "/"
    new_prefix = os.path.abspath(dest_home).rstrip("/") + "/"
    con = sqlite3.connect(dest_db)
    rewritten = 0
    with con:
        for table, col in PATH_COLUMNS:
            cur = con.execute(
                "UPDATE %s SET %s = REPLACE(%s, ?, ?) WHERE %s LIKE ?" % (table, col, col, col),
                (old_prefix, new_prefix, "%" + old_prefix + "%"))
            rewritten += cur.rowcount
        con.execute("INSERT OR REPLACE INTO wm_meta(key, value) VALUES('imported_from', ?)", (src_dir,))
        con.execute("INSERT OR REPLACE INTO wm_meta(key, value) VALUES('imported_at', ?)", (str(time.time()),))
    counts = {t: con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
              for t in ("projects", "goals", "tasks", "runs", "reviews", "activity")}
    con.close()

    # 3. runs artifacts (briefs, logs, completions). Worktrees are git checkouts
    #    whose .git file points back at the project repo, so they are LINKED,
    #    not copied: rewritten result_paths/workdir must still resolve for
    #    reviewers reading a prior run's deliverables.
    copied = linked = 0
    if copy_runs and os.path.isdir(src_runs):
        os.makedirs(dest_runs, exist_ok=True)
        for name in os.listdir(src_runs):
            sp = os.path.join(src_runs, name)
            if name == "worktrees" or os.path.isdir(sp):
                continue
            shutil.copy2(sp, os.path.join(dest_runs, name))
            copied += 1
        src_wt = os.path.join(src_runs, "worktrees")
        if os.path.isdir(src_wt):
            dest_wt = os.path.join(dest_runs, "worktrees")
            os.makedirs(dest_wt, exist_ok=True)
            for name in os.listdir(src_wt):
                target = os.path.join(dest_wt, name)
                if not os.path.lexists(target):
                    os.symlink(os.path.join(src_wt, name), target)
                    linked += 1
    return {"db": dest_db, "backup": backup, "counts": counts,
            "paths_rewritten": rewritten, "run_files_copied": copied, "worktrees_linked": linked}
