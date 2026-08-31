"""Agent templates under agents/: one per assignee profile, well-formed, and the
extractor is idempotent against a fixture profile."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import yaml

AGENTS = os.path.join(ROOT, "agents")


def test_every_assignee_has_a_template():
    from core import wm_store as store
    for name in store.ASSIGNEE_PROFILES:
        d = os.path.join(AGENTS, name)
        meta = yaml.safe_load(open(os.path.join(d, "agent.yaml")))
        assert meta["name"] == name and meta["description"].strip()
        soul = open(os.path.join(d, meta["soul"])).read()
        assert name.lower() in soul.lower()[:200]
        for sk in meta["skills"]:
            p = os.path.join(d, "skills", sk, "SKILL.md")
            front = open(p).read().split("---")[1]
            assert yaml.safe_load(front)["name"] == sk
        if name == store.ORCHESTRATOR_AGENT:
            assert meta["overlay"] is True and meta["skills"] == ["hermes-hq-ops"]
        else:
            assert meta["overlay"] is False and meta["skills"] == ["%s-specialist" % name]


def test_extractor_idempotent_on_fixture(tmp_path):
    from scripts import extract_agent_templates as ex
    prof = tmp_path / "profiles" / "coder"
    (prof / "skills" / "coder-specialist").mkdir(parents=True)
    (prof / "profile.yaml").write_text("description: fixture coder\ndescription_auto: false\n")
    (prof / "SOUL.md").write_text("# Coder\nfixture soul\n")
    (prof / "skills" / "coder-specialist" / "SKILL.md").write_text("---\nname: coder-specialist\n---\nbody\n")
    (prof / ".env").write_text("API_KEY=secret\n")
    out = tmp_path / "agents"
    assert ex.extract_one("coder", str(tmp_path / "profiles"), str(out)).startswith("ok")
    files = sorted(os.path.relpath(os.path.join(r, f), out) for r, _, fs in os.walk(out) for f in fs)
    assert files == ["coder/SOUL.md", "coder/agent.yaml", "coder/skills/coder-specialist/SKILL.md"]  # no .env
    snap = {f: open(out / f).read() for f in files}
    ex.extract_one("coder", str(tmp_path / "profiles"), str(out))
    assert snap == {f: open(out / f).read() for f in files}
    assert ex.extract_one("ghost", str(tmp_path / "profiles"), str(out)).startswith("skip")
