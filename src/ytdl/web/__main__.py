"""Run the web interface: ``python -m ytdl.web``."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import create_app

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytdl-web",
        description="Serve the YouTube Downloader Suite web interface on localhost.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: 127.0.0.1, localhost only)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window on start")
    parser.add_argument("--debug", action="store_true", help="enable Flask debug mode")
    args = parser.parse_args(argv)

    if args.host not in LOCAL_HOSTS:
        # This interface has no authentication and writes files as the user
        # running it. Binding publicly hands both to anyone who can reach it.
        print(
            f"WARNING: binding to {args.host} exposes this tool beyond your machine.\n"
            "         It has no authentication and can write files as you.\n"
            "         Use 127.0.0.1 unless you specifically intend otherwise.",
            file=sys.stderr,
        )

    app = create_app()
    url = f"http://{'localhost' if args.host in LOCAL_HOSTS else args.host}:{args.port}/"
    print(f"YouTube Downloader Suite  ->  {url}", file=sys.stderr)

    if not args.no_browser and not args.debug:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # threaded=True is required, not optional: downloads run on worker threads
    # and each open Server-Sent Events stream occupies a request thread.
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
