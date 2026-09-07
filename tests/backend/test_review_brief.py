"""Reviewer briefs use the Hermes HQ CLI and receive its runtime environment."""
import os
import shutil
import sys

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hq = tmp_path / "hq"
    project = tmp_path / "project"
    profiles = tmp_path / "profiles"
    hq.mkdir()
    project.mkdir()
    profiles.mkdir()
    cli = tmp_path / "cli-bin" / "hermes-hq"
    cli.parent.mkdir()
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setenv("HERMES_HQ_HOME", str(hq))
    monkeypatch.setenv("HERMES_HQ_REPO", str(project))
    monkeypatch.setenv("HERMES_HQ_CLI", str(cli))
    monkeypatch.setenv("WM_PROFILES_DIR", str(profiles))
    for name in list(sys.modules):
        if name.startswith(("core", "backend")):
            del sys.modules[name]
    from core import wm_store as store
    store.init_db(db_path=store.DEFAULT_DB_PATH)
    store.create_project("demo", "Demo", "", str(project), db_path=store.DEFAULT_DB_PATH)
    return store, project, cli


def test_review_brief_uses_only_current_hq_review_command(env):
    store, _, _ = env
    task_id = store.create_task(
        "demo", "Review me", "Inspect the change", "Everything passes",
        assignee_profile="coder", review_policy="required",
        db_path=store.DEFAULT_DB_PATH,
    )
    store.claim_task(task_id, db_path=store.DEFAULT_DB_PATH)
    review_id = store.create_review(task_id, db_path=store.DEFAULT_DB_PATH)
    run_id = store.start_run(task_id, "reviewer", db_path=store.DEFAULT_DB_PATH)
    store.set_run_review(run_id, review_id, db_path=store.DEFAULT_DB_PATH)
    brief = store.render_brief(run_id, db_path=store.DEFAULT_DB_PATH)
    assert "hermes-hq wm review" in brief
    assert "  wm review" not in brief
    assert "hermes kanban" not in brief.lower()


def test_reviewer_environment_exposes_hq_cli_context(env):
    store, project, cli = env
    from core import wm_run_agent

    inherited = {"PATH": "/usr/bin", "HERMES_HOME": "/wrong/profile-home"}
    actual = wm_run_agent._agent_env("reviewer", inherited)
    assert actual["HERMES_HQ_REPO"] == str(project)
    assert str(cli.parent) in actual["PATH"].split(os.pathsep)
    assert shutil.which("hermes-hq", path=actual["PATH"]) == str(cli)
    assert actual["HERMES_HOME"] == "/wrong/profile-home"
