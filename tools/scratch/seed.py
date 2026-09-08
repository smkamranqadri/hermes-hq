#!/usr/bin/env python3
"""Seed a scratch hermes-hq server with a small, deterministic Second Brain library.

    tools/scratch/seed.py http://127.0.0.1:9013 [password=pw-test]

Creates (idempotent by title — re-running skips notes that already exist):
  areas     Work, Side Project, Finance, Journal, and child "Work / Contracts"
  projects  alpha (under Work), beta (no area link)
  notes     9 active Work notes with overlapping tags (one carries 6 tags, one has project alpha),
            2 in Work / Contracts, 3 Side Project, 1 Finance, 1 project-only (beta, no area),
            3 archived Work, 1 archived with no home, 3 inbox captures (one with 2 tags)
Prints a JSON map of ids so a check script can address rows by name.
"""
import json
import os
import sys
import urllib.request
from http.cookiejar import CookieJar

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:9013"
PW = sys.argv[2] if len(sys.argv) > 2 else "pw-test"

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
CSRF = ""


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if CSRF:
        req.add_header("x-csrf", CSRF)
    with opener.open(req) as r:
        return json.loads(r.read() or b"{}")


CSRF = call("POST", "/api/login", {"password": PW})["csrf"]

# ---- areas ---------------------------------------------------------------
existing = {a["name"]: a["id"] for a in call("GET", "/api/areas")["areas"]}
def area(name, parent=None):
    if name in existing:
        return existing[name]
    aid = call("POST", "/api/areas", {"name": name, "parent_id": parent})["id"]
    existing[name] = aid
    return aid

A = {n: area(n) for n in ("Work", "Side Project", "Finance", "Journal")}
A["Contracts"] = area("Contracts", A["Work"])

# ---- projects ------------------------------------------------------------
projects = {p["slug"]: p["id"] for p in call("GET", "/api/projects").get("projects", [])}
home = os.environ.get("HERMES_HQ_HOME") or "/opt/data/hh-scratch/seed"
def project(slug, name):
    if slug in projects:
        return projects[slug]
    pid = call("POST", "/api/projects", {"slug": slug, "name": name, "primary_path": os.path.join(home, "proj-" + slug)})["id"]
    projects[slug] = pid
    return pid

P = {"alpha": project("alpha", "Alpha Launch"), "beta": project("beta", "Beta Side")}

# ---- notes ---------------------------------------------------------------
have = {}
for n in call("GET", "/api/notes?limit=500&exclude_archived=false").get("notes", []):
    have[n["title"]] = n["id"]

def note(title, *, status="active", area=None, project=None, tags=(), body="", archive=False):
    if title in have:
        return have[title]
    payload = {"title": title, "body": body or f"{title} — seeded body text for search.", "status": status,
               "area_id": area, "project_id": project, "tags": list(tags)}
    nid = call("POST", "/api/notes", payload)["id"]
    if archive:
        call("POST", f"/api/note/{nid}/edit", {"status": "archived"})
    have[title] = nid
    return nid

N = {}
w = A["Work"]
N["work-1"] = note("Work: sprint planning", area=w, tags=["planning", "team", "meeting"])
N["work-2"] = note("Work: infra migration", area=w, tags=["infra", "planning"])
N["work-3"] = note("Work: hiring loop", area=w, tags=["team", "hiring"])
N["work-4"] = note("Work: BrightLane pricing", area=w, tags=["BrightLane", "finance-ops", "planning", "team", "meeting", "infra"])
N["work-5"] = note("Work: alpha launch checklist", area=w, project=P["alpha"], tags=["planning", "launch"])
N["work-6"] = note("Work: retro notes", area=w, tags=["meeting", "team"])
N["work-7"] = note("Work: oncall handbook", area=w, tags=["infra"])
N["work-8"] = note("Work: quarterly goals", area=w, tags=["planning"])
N["work-9"] = note("Work: vendor review", area=w, tags=["finance-ops"])
N["contract-1"] = note("Contracts: MSA template", area=A["Contracts"], tags=["legal"])
N["contract-2"] = note("Contracts: NDA tracker", area=A["Contracts"], tags=["legal", "planning"])
N["side-1"] = note("Side: retro emulator", area=A["Side Project"], tags=["emulator", "retro"])
N["side-2"] = note("Side: desk pet", area=A["Side Project"], tags=["shopping", "retro"])
N["side-3"] = note("Side: frappe ecosystem", area=A["Side Project"], tags=["Frappe", "SchoolBox"])
N["fin-1"] = note("Finance: tax filing 2026", area=A["Finance"], tags=["tax"])
N["proj-only"] = note("Beta: launch page copy", project=P["beta"], tags=["launch"])
N["arch-1"] = note("Work (old): 2024 roadmap", area=w, tags=["planning"], archive=True)
N["arch-2"] = note("Work (old): legacy infra", area=w, tags=["infra"], archive=True)
N["arch-3"] = note("Work (old): first retro", area=w, tags=["meeting"], archive=True)
N["arch-homeless"] = note("Loose: archived capture with no home", status="inbox", archive=True)
N["inbox-1"] = note("Capture: call dentist tomorrow", status="inbox")
N["inbox-2"] = note("Capture: idea for onboarding flow", status="inbox", tags=["planning", "team"])
N["inbox-3"] = note("Capture: اردو نوٹس", status="inbox")

print(json.dumps({"base": BASE, "areas": A, "projects": P, "notes": N}, indent=1))
