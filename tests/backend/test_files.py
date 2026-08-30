"""Group 5 file API: containment, listing, read kinds, write/409, mkdir/rename/delete/upload, artifacts."""
import io, json, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HQ_HOME", str(tmp_path / "hq"))
    monkeypatch.setenv("HERMES_HQ_PASSWORD", "pw-test")
    monkeypatch.delenv("WM_PROJECTS_ROOT", raising=False)
    for m in list(sys.modules):
        if m.startswith(("core", "backend")):
            del sys.modules[m]
    from core import wm_store as store
    os.makedirs(store.hq_home(), exist_ok=True)
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    projects = tmp_path / "projects"           # <hq home>/../projects
    alpha = projects / "alpha"
    (alpha / "src").mkdir(parents=True)
    (alpha / "README.md").write_text("# Alpha\n")
    (alpha / "src" / "main.py").write_text("print('hi')\n")
    (alpha / ".env").write_text("SECRET=1\n")
    (alpha / "node_modules").mkdir()
    (alpha / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    (alpha / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "outside.txt").write_text("nope\n")
    os.symlink(tmp_path / "outside.txt", alpha / "escape.txt")
    os.symlink(tmp_path, alpha / "escape_dir")
    store.create_project("alpha", "Alpha", "", str(alpha), db_path=store.DEFAULT_DB_PATH)
    from fastapi.testclient import TestClient
    from backend.app import create_app
    with TestClient(create_app(dispatcher_enabled=False)) as c:
        r = c.post("/api/login", json={"password": "pw-test"})
        c.headers.update({"x-csrf": r.json()["csrf"]})
        yield c, store, tmp_path


def test_roots(env):
    c, store, tp = env
    r = c.get("/api/files/roots").json()["roots"]
    assert r[0] == {"root": "projects", "label": "Projects", "path": str(tp / "projects"), "exists": True}
    assert r[1]["root"] == "project:alpha" and r[1]["exists"] and r[1]["slug"] == "alpha"
    assert c.get("/api/files/list", params={"root": "project:nope"}).status_code == 404
    assert c.get("/api/files/list", params={"root": "etc"}).status_code == 404


def test_containment(env):
    c, _, _ = env
    for bad in ["../", "../../hq", "/etc/passwd", "src/../../", "escape.txt", "escape_dir"]:
        r = c.get("/api/files/read" if "." in bad else "/api/files/list", params={"root": "project:alpha", "path": bad})
        assert r.status_code == 403, (bad, r.status_code, r.text)
    assert c.get("/api/files/list", params={"root": "projects", "path": "alpha/../"}).status_code == 200  # stays inside
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "../x.txt", "content": "x"}).status_code == 403
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "escape.txt", "content": "x", "force": True}).status_code == 403
    assert c.post("/api/files/mkdir", json={"root": "project:alpha", "path": "escape_dir/new"}).status_code == 403
    assert c.post("/api/files/rename", json={"root": "project:alpha", "path": "README.md", "to": "../README.md"}).status_code == 403


def test_list(env):
    c, _, _ = env
    r = c.get("/api/files/list", params={"root": "project:alpha"}).json()
    names = [e["name"] for e in r["entries"]]
    assert names == ["escape_dir", "src", "blob.bin", "escape.txt", "img.png", "README.md"], names   # dirs first, case-insensitive, hidden out
    assert next(e for e in r["entries"] if e["name"] == "escape_dir")["outside"]
    esc = next(e for e in r["entries"] if e["name"] == "escape.txt")
    assert esc["symlink"] and esc["outside"]
    r = c.get("/api/files/list", params={"root": "project:alpha", "hidden": 1}).json()
    names = [e["name"] for e in r["entries"]]
    assert ".env" in names and "node_modules" in names and "escape_dir" in names
    assert not r["truncated"]
    r = c.get("/api/files/list", params={"root": "projects"}).json()
    assert [e["name"] for e in r["entries"]] == ["alpha"]
    assert c.get("/api/files/list", params={"root": "project:alpha", "path": "README.md"}).status_code == 400


def test_read_kinds_and_cap(env):
    c, _, tp = env
    r = c.get("/api/files/read", params={"root": "project:alpha", "path": "src/main.py"}).json()
    assert r["kind"] == "text" and r["content"] == "print('hi')\n" and r["mtime"] > 0
    assert c.get("/api/files/read", params={"root": "project:alpha", "path": "img.png"}).json()["kind"] == "image"
    b = c.get("/api/files/read", params={"root": "project:alpha", "path": "blob.bin"}).json()
    assert b["kind"] == "binary" and "content" not in b
    (tp / "projects/alpha/big.txt").write_text("x" * 1_000_001)
    big = c.get("/api/files/read", params={"root": "project:alpha", "path": "big.txt"}).json()
    assert big["too_large"] and "content" not in big
    assert c.get("/api/files/read", params={"root": "project:alpha", "path": "missing"}).status_code == 404
    raw = c.get("/api/files/raw", params={"root": "project:alpha", "path": "README.md"})
    assert raw.status_code == 200 and raw.text == "# Alpha\n" and "inline" in raw.headers["content-disposition"]
    dl = c.get("/api/files/raw", params={"root": "project:alpha", "path": "README.md", "download": 1})
    assert dl.headers["content-disposition"].startswith("attachment")


def test_write_conflicts(env):
    c, _, tp = env
    f = tp / "projects/alpha/README.md"
    loaded = c.get("/api/files/read", params={"root": "project:alpha", "path": "README.md"}).json()
    ok = c.post("/api/files/write", json={"root": "project:alpha", "path": "README.md", "content": "# New\n", "mtime": loaded["mtime"]})
    assert ok.status_code == 200 and f.read_text() == "# New\n" and not ok.json()["created"]
    # stale mtime -> 409; force -> 200
    r = c.post("/api/files/write", json={"root": "project:alpha", "path": "README.md", "content": "# Stale\n", "mtime": loaded["mtime"]})
    assert r.status_code == 409 and f.read_text() == "# New\n"
    r = c.post("/api/files/write", json={"root": "project:alpha", "path": "README.md", "content": "# Forced\n", "mtime": loaded["mtime"], "force": True})
    assert r.status_code == 200 and f.read_text() == "# Forced\n"
    # create new: ok once, 409 second time without mtime
    r = c.post("/api/files/write", json={"root": "project:alpha", "path": "src/new.txt", "content": "n"})
    assert r.status_code == 200 and r.json()["created"]
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "src/new.txt", "content": "n"}).status_code == 409
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "nodir/x.txt", "content": "n"}).status_code == 404
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "src", "content": "n"}).status_code == 400
    assert c.post("/api/files/write", json={"root": "project:alpha", "path": "huge.txt", "content": "x" * 1_000_001}).status_code == 413
    assert not list(tp.glob("projects/alpha/**/.hq-tmp*"))
    # mtime given but file gone -> 409 "deleted on disk"
    r = c.post("/api/files/write", json={"root": "project:alpha", "path": "gone.txt", "content": "x", "mtime": 1.0})
    assert r.status_code == 409 and "deleted" in r.text


def test_write_never_follows_planted_tmp_symlink(env):
    """Review 2026-08-30 #1: a pre-planted `<name>.hq-tmp` symlink must not be written through."""
    c, _, tp = env
    a = tp / "projects/alpha"
    victim = tp / "victim.txt"; victim.write_text("keep me\n")
    os.symlink(victim, a / "planted.txt.hq-tmp")
    r = c.post("/api/files/write", json={"root": "project:alpha", "path": "planted.txt", "content": "pwn"})
    assert r.status_code == 200 and (a / "planted.txt").read_text() == "pwn" and not (a / "planted.txt").is_symlink()
    assert victim.read_text() == "keep me\n"
    os.symlink(victim, a / "planted2.txt.hq-tmp")
    r = c.post("/api/files/upload", data={"root": "project:alpha", "path": ""}, files=[("files", ("planted2.txt", b"pwn", "text/plain"))])
    assert r.status_code == 200 and (a / "planted2.txt").read_bytes() == b"pwn" and victim.read_text() == "keep me\n"
    assert not [p for p in a.iterdir() if p.name.startswith(".hq-tmp")]
    (a / "planted.txt.hq-tmp").unlink(); (a / "planted2.txt.hq-tmp").unlink()


def test_raw_active_content_and_dangling(env):
    c, _, tp = env
    a = tp / "projects/alpha"
    (a / "page.html").write_text("<script>1</script>")
    (a / "pic.svg").write_text("<svg/>")
    for n in ("page.html", "pic.svg"):
        r = c.get("/api/files/raw", params={"root": "project:alpha", "path": n})
        assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment"), n
        assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    # sandboxed preview: inline, scripts allowed, no network back to the app
    r = c.get("/api/files/raw", params={"root": "project:alpha", "path": "page.html", "preview": 1})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    csp = r.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp and "connect-src 'none'" in csp and "allow-same-origin" not in csp
    r = c.get("/api/files/raw", params={"root": "project:alpha", "path": "page.html", "preview": 1, "download": 1})
    assert r.headers["content-disposition"].startswith("attachment")
    os.symlink(a / "missing", a / "dangling")
    assert c.get("/api/files/read", params={"root": "project:alpha", "path": "dangling"}).status_code == 404
    assert c.get("/api/files/raw", params={"root": "project:alpha", "path": "dangling"}).status_code == 404
    (a / 'we"ird.txt').write_text("q")
    r = c.get("/api/files/raw", params={"root": "project:alpha", "path": 'we"ird.txt'})
    assert r.status_code == 200 and "filename*=UTF-8''we%22ird.txt" in r.headers["content-disposition"]


def test_mkdir_rename_delete(env):
    c, store, tp = env
    a = tp / "projects/alpha"
    assert c.post("/api/files/mkdir", json={"root": "project:alpha", "path": "docs/notes"}).status_code == 200
    assert (a / "docs/notes").is_dir()
    assert c.post("/api/files/mkdir", json={"root": "project:alpha", "path": "docs"}).status_code == 409
    r = c.post("/api/files/rename", json={"root": "project:alpha", "path": "README.md", "to": "README.txt"})
    assert r.status_code == 200 and r.json()["path"] == "README.txt" and (a / "README.txt").exists()
    r = c.post("/api/files/rename", json={"root": "project:alpha", "path": "README.txt", "to": "docs/README.md"})
    assert r.status_code == 200 and (a / "docs/README.md").exists()
    assert c.post("/api/files/rename", json={"root": "project:alpha", "path": "docs/README.md", "to": "src/main.py"}).status_code == 409
    assert c.post("/api/files/delete", json={"root": "project:alpha", "path": "docs"}).status_code == 409  # not empty
    assert c.post("/api/files/delete", json={"root": "project:alpha", "path": "docs", "recursive": True}).status_code == 200
    assert not (a / "docs").exists()
    assert c.post("/api/files/delete", json={"root": "project:alpha", "path": "escape.txt"}).status_code == 403
    assert (tp / "outside.txt").exists()
    assert c.post("/api/files/delete", json={"root": "project:alpha", "path": ""}).status_code == 400
    # primary_path guard from the projects root
    assert c.post("/api/files/delete", json={"root": "projects", "path": "alpha", "recursive": True}).status_code == 409
    assert c.post("/api/files/rename", json={"root": "projects", "path": "alpha", "to": "beta"}).status_code == 409
    assert a.is_dir()
    # ancestor of a primary_path is guarded too (review #11)
    monorepo = tp / "projects/mono"; (monorepo / "pkg").mkdir(parents=True)
    store.create_project("mono", "Mono", "", str(monorepo / "pkg"), db_path=store.DEFAULT_DB_PATH)
    assert c.post("/api/files/delete", json={"root": "projects", "path": "mono", "recursive": True}).status_code == 409
    assert c.post("/api/files/rename", json={"root": "projects", "path": "mono", "to": "mono2"}).status_code == 409
    assert (monorepo / "pkg").is_dir()
    # rename into its own subtree -> 409 not 500
    assert c.post("/api/files/rename", json={"root": "project:alpha", "path": "src", "to": "src/inner"}).status_code == 409
    assert c.post("/api/files/mkdir", json={"root": "project:alpha", "path": "src/main.py/x"}).status_code == 409


def test_upload(env):
    c, _, tp = env
    r = c.post("/api/files/upload", data={"root": "project:alpha", "path": "src"},
               files=[("files", ("a.txt", b"aaa", "text/plain")), ("files", ("../b.txt", b"bbb", "text/plain"))])
    assert r.status_code == 200, r.text
    assert (tp / "projects/alpha/src/a.txt").read_bytes() == b"aaa"
    assert (tp / "projects/alpha/src/b.txt").read_bytes() == b"bbb"     # basename only
    assert not (tp / "projects/alpha/b.txt").exists()
    r = c.post("/api/files/upload", data={"root": "project:alpha", "path": "src"}, files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 409
    assert (tp / "projects/alpha/src/a.txt").read_bytes() == b"aaa"


def test_artifacts(env):
    c, store, tp = env
    db = store.DEFAULT_DB_PATH
    tid = store.create_task("alpha", "t", db_path=db)
    (tp / "projects/alpha/out.md").write_text("o")
    (tp / "elsewhere.txt").write_text("e")
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO runs (task_id, agent_profile, status, result_paths) VALUES (?,?,?,?)",
                (tid, "coder", "succeeded", json.dumps([str(tp / "projects/alpha/out.md"), str(tp / "elsewhere.txt"), "/nope"])))
    con.commit(); con.close()
    r = c.get("/api/project/alpha/artifacts").json()
    got = {a["name"]: a for a in r["artifacts"]}
    assert set(got) == {"out.md", "elsewhere.txt"}
    assert got["out.md"]["inside_primary"] and got["out.md"]["rel"] == "out.md" and got["out.md"]["agent"] == "coder"
    assert not got["elsewhere.txt"]["inside_primary"] and got["elsewhere.txt"]["rel"] is None
    assert c.get("/api/project/zzz/artifacts").status_code == 404
