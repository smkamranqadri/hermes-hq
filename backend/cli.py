"""`hermes-hq` command: serve the control plane, or pass through to the engine `wm` CLI."""
import argparse
import os
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "service":
        from backend import service
        return service.cli(argv[1:])
    if argv and argv[0] == "wm":
        from core import wm_cli
        return wm_cli.main(argv[1:])
    p = argparse.ArgumentParser(prog="hermes-hq")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the hermes-hq server (UI + API + dispatcher)")
    s.add_argument("--host", default=os.environ.get("HERMES_HQ_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("HERMES_HQ_PORT", "9010")))
    s.add_argument("--no-dispatcher", action="store_true", help="serve without the background dispatcher")
    s.add_argument("--interval", type=float, default=30.0, help="dispatcher tick seconds")
    sub.add_parser("wm", help="engine CLI passthrough: hermes-hq wm <args>")
    sub.add_parser("service", help="supervisor integration: hermes-hq service install|uninstall|status|restart|update|auto-update")
    im = sub.add_parser("import", help="import a legacy Work Manager dir (wm.db + runs/) into the hq home")
    im.add_argument("src_dir")
    im.add_argument("--force", action="store_true", help="replace an existing non-empty hq.db (a backup is kept)")
    im.add_argument("--no-runs", action="store_true", help="skip copying runs/ artifacts")
    a = p.parse_args(argv)
    if a.cmd == "import":
        import json
        from backend.importer import import_wm, ImportError_
        try:
            print(json.dumps(import_wm(a.src_dir, force=a.force, copy_runs=not a.no_runs), indent=2))
        except ImportError_ as e:
            print("import refused: %s" % e, file=sys.stderr)
            return 2
        return 0
    if a.cmd == "serve":
        import uvicorn
        from backend.app import create_app
        from backend import auth as A
        pw, src = A.resolve_password()
        if src == "generated":
            print("hermes-hq: generated login password: %s  (saved to %s)" % (pw, A.password_path()))
        elif src == "file":
            print("hermes-hq: login password from %s" % A.password_path())
        uvicorn.run(create_app(dispatcher_enabled=not a.no_dispatcher, interval=a.interval),
                    host=a.host, port=a.port, log_level="info")
        return 0


if __name__ == "__main__":
    sys.exit(main())
