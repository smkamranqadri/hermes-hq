#!/usr/bin/env python3
"""Reconcile the Second Brain import dumps — the Phase 3 DoD baseline.

The DoD says the import's counts must reconcile "against the dump analysis".
This is that analysis, as a re-runnable tool rather than a number in prose: the
figures it prints are produced by the same parsing and pre-pass code the
importer uses (`backend/notes_import.py`), so the baseline cannot drift from
the thing it is meant to check.

    tools/analyze_notes_dump.py                       # table
    tools/analyze_notes_dump.py --json                # machine-readable
    tools/analyze_notes_dump.py --write-fixture P     # record the baseline
    tools/analyze_notes_dump.py --check P             # compare against it (CI)

Read-only: it never writes to the dumps and never touches the database.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend import notes_import as ni      # noqa: E402

DEFAULT_ROOT = "/opt/data/notes-dump"
DEFAULT_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "notes_dump_baseline.json")

HEAD = ("source", "total", "secret", "empty", "dup", "archived", "->lib")


def print_table(report):
    rows = ni.report_rows(report)
    w = max(len(r[0]) for r in rows) + 2
    print("{:<{w}}{:>7}{:>8}{:>7}{:>6}{:>10}{:>8}".format(*HEAD, w=w))
    for r in rows:
        if r[0] == "TOTAL":
            print("-" * (w + 50))
        print("{:<{w}}{:>7}{:>8}{:>7}{:>6}{:>10}{:>8}".format(*r, w=w))
    t = report["totals"]
    print()
    print("reconciles: %s (%d units, %d accounted)"
          % ("YES" if report["reconciles"] else "NO", t["total"],
             report["accounted"]))
    print("librarian reads %d unit(s); %d flagged for secret KEYWORDS "
          "(imported normally, not quarantined)"
          % (t[ni.LIBRARIAN], report["flagged_secret_keywords"]))
    if report["quarantined"]:
        print()
        print("QUARANTINED (%d) — no note row is ever created for these:"
              % len(report["quarantined"]))
        for q in report["quarantined"]:
            print("  %-12s %-58s %s" % (q["source"], q["ident"][:58], q["reason"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="dump root (default %s)" % DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true", help="emit the fixture shape as JSON")
    ap.add_argument("--write-fixture", metavar="PATH", nargs="?", const=DEFAULT_FIXTURE,
                    help="record the current counts as the committed baseline")
    ap.add_argument("--check", metavar="PATH", nargs="?", const=DEFAULT_FIXTURE,
                    help="compare against a recorded baseline; non-zero exit on drift")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print("error: no dump root at %s" % args.root, file=sys.stderr)
        return 2

    report = ni.analyze(args.root)
    fixture = ni.fixture_of(report)

    if args.json:
        print(json.dumps(fixture, indent=2, sort_keys=True))
    elif not args.check:
        print_table(report)

    if args.write_fixture:
        with open(args.write_fixture, "w") as fh:
            json.dump(fixture, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("baseline written: %s" % args.write_fixture)

    if args.check:
        if not os.path.exists(args.check):
            print("error: no baseline at %s (run --write-fixture first)" % args.check,
                  file=sys.stderr)
            return 2
        recorded = json.load(open(args.check))
        if recorded == fixture:
            print("baseline OK — %d units, unchanged" % fixture["totals"]["total"])
            return 0
        print("BASELINE DRIFT against %s" % args.check, file=sys.stderr)
        for key in sorted(set(recorded.get("totals", {})) | set(fixture["totals"])):
            was, now = recorded.get("totals", {}).get(key), fixture["totals"].get(key)
            if was != now:
                print("  totals.%-14s %s -> %s" % (key, was, now), file=sys.stderr)
        for src in sorted(set(recorded.get("per_source", {})) | set(fixture["per_source"])):
            was = recorded.get("per_source", {}).get(src, {})
            now = fixture["per_source"].get(src, {})
            for key in sorted(set(was) | set(now)):
                if was.get(key) != now.get(key):
                    print("  %s.%-14s %s -> %s" % (src, key, was.get(key), now.get(key)),
                          file=sys.stderr)
        return 1

    if not report["reconciles"]:
        print("error: units did not reconcile", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
