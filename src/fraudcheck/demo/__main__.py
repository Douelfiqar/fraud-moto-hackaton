"""Start the control desk and open it in the browser: ``python -m fraudcheck.demo``.

Without ``--azure`` nothing is ever sent to the network: a document whose Azure answers
are saved (the team's set) is replayed from them, any other document gets the local
checks only, and the page says so.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

DEFAULT_PORT = 8765
_PORT_TRIES = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fraudcheck.demo",
        description="The control desk: check a document in the browser, see why it is flagged, and decide.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port of the page (default {DEFAULT_PORT}; "
                        "the next free one is taken when it is busy)")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--azure", action="store_true", help="also call Azure for a document that has no saved answer "
                        "(needs .env and APRIL's network; costs tokens)")
    return parser


def free_port(host: str, wanted: int) -> int:
    """``wanted`` when it is free, else the next free port after it."""
    for port in range(wanted, wanted + _PORT_TRIES):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"no free port between {wanted} and {wanted + _PORT_TRIES - 1}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        import uvicorn

        from fraudcheck.demo.app import LOCAL_HOSTS, create_app
        from fraudcheck.demo.desk import DeskSettings
    except ImportError as exc:
        print(f"Il manque un paquet ({exc.name}) : pip install -r requirements.txt", file=sys.stderr)
        return 2
    try:
        port = free_port(args.host, args.port)
        settings = DeskSettings.from_repository(live=args.azure)
    except (OSError, ValueError) as exc:
        print(f"L'interface ne peut pas démarrer : {exc}", file=sys.stderr)
        return 2
    local = args.host in LOCAL_HOSTS
    app = create_app(settings, allowed_hosts=LOCAL_HOSTS if local else ("*",))
    url = f"http://{args.host if local else '127.0.0.1'}:{port}/"
    print(f"Contrôle des justificatifs : {url}   (Ctrl+C pour arrêter)", flush=True)
    print("Azure : appels en direct autorisés (--azure)." if args.azure
          else "Azure : aucun appel ; les réponses enregistrées sont rejouées.", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
