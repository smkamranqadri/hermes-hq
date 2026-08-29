#!/usr/bin/env python3
"""Extract agent templates from live Hermes profiles into agents/<name>/.

Template = the parts of a profile that are *ours* and role-defining:
  agent.yaml                          name, description (from profile.yaml), skills list
  SOUL.md                             verbatim
  skills/<name>-specialist/SKILL.md   verbatim (only the specialist skill; bundled
                                      Hermes skills are re-created by `hermes profile create`)
Never copied: .env, config.yaml, state.db, sessions, memories, caches.

Re-run after editing a live SOUL/skill; it is idempotent (git diff shows drift).
    .venv/bin/python scripts/extract_agent_templates.py [--profiles-dir DIR] [--out agents]
"""
import argparse, os, shutil, sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import wm_store as store  # noqa: E402

SPECIALISTS = tuple(a for a in store.ASSIGNEE_PROFILES if a != store.ORCHESTRATOR_AGENT)


def extract_one(name, profiles_dir, out_dir):
    src = os.path.join(profiles_dir, name)
    if not os.path.isdir(src):
        return "skip %s: no profile dir %s" % (name, src)
    with open(os.path.join(src, "profile.yaml")) as f:
        prof = yaml.safe_load(f) or {}
    skill = "%s-specialist" % name
    skill_src = os.path.join(src, "skills", skill, "SKILL.md")
    dst = os.path.join(out_dir, name)
    os.makedirs(os.path.join(dst, "skills", skill), exist_ok=True)
    shutil.copyfile(os.path.join(src, "SOUL.md"), os.path.join(dst, "SOUL.md"))
    if os.path.exists(skill_src):
        shutil.copyfile(skill_src, os.path.join(dst, "skills", skill, "SKILL.md"))
        skills = [skill]
    else:
        skills = []
    meta = {"name": name, "description": (prof.get("description") or "").strip(),
            "soul": "SOUL.md", "skills": skills, "overlay": False}
    with open(os.path.join(dst, "agent.yaml"), "w") as f:
        yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True, width=100)
    return "ok %s (%d skill)" % (name, len(skills))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles-dir", default=store.resolve_profiles_dir())
    ap.add_argument("--out", default=os.path.join(ROOT, "agents"))
    a = ap.parse_args(argv)
    for name in SPECIALISTS:
        print(extract_one(name, a.profiles_dir, a.out))


if __name__ == "__main__":
    main()
