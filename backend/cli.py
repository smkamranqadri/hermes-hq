"""`hermes-hq` command: serve the control plane, or pass through to the engine `wm` CLI."""
import argparse
import os
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
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
    a = p.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn
        from backend.app import create_app
        uvicorn.run(create_app(dispatcher_enabled=not a.no_dispatcher, interval=a.interval),
                    host=a.host, port=a.port, log_level="info")
        return 0


if __name__ == "__main__":
    sys.exit(main())
