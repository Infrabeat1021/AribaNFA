"""Entry point: `python -m ariabnfa`."""

from __future__ import annotations

import argparse
import logging
import sys

from . import APP_NAME, __version__
from .config import load_config
from .errors import NFAError
from .logging_setup import setup_logging
from .secrets_store import SecretsStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ariabnfa",
        description="Generate NFA documents from SAP Ariba sourcing events.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log debug detail to the console")
    parser.add_argument("--offline", action="store_true",
                        help="start in offline mode, using the sample fixtures")
    parser.add_argument("--dump", metavar="EVENT_ID",
                        help="dump an event's raw payload and field-path CSV, then exit")
    parser.add_argument("--web", action="store_true",
                        help="serve the browser interface instead of the desktop window")
    parser.add_argument("--port", type=int, default=5000,
                        help="port for --web (default 5000)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for --web; loopback only by default")
    parser.add_argument("--no-browser", action="store_true",
                        help="with --web, do not open a browser automatically")
    return parser.parse_args(argv)


def run_dump(event_id: str, config, *, offline: bool) -> int:
    """Headless field discovery - the first step when wiring up a new realm."""
    from .ariba.discovery import dump_event
    from .pipeline import make_source

    source = make_source(config, offline=offline)
    print(f"Source: {source.describe()}")
    result = dump_event(source, event_id)
    print(f"Raw payload : {result.json_path}")
    print(f"Field paths : {result.csv_path}  ({result.row_count} distinct paths)")
    if result.errors:
        print("\nSome data was not available:")
        for name, error in result.errors.items():
            print(f"  - {name}: {error}")
    print("\nOpen the CSV in Excel and match paths to values, then put the real "
          "paths into mapping/nfa_mapping.json.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log_file = setup_logging(verbose=args.verbose)

    config = load_config()
    secrets = SecretsStore()
    secrets.register_all_for_redaction()

    log = logging.getLogger(__name__)
    log.info("%s %s starting (log: %s)", APP_NAME, __version__, log_file)

    offline = args.offline or not secrets.has_all()

    if args.dump:
        try:
            return run_dump(args.dump, config, offline=offline)
        except NFAError as exc:
            print(f"\nError: {exc.user_message}", file=sys.stderr)
            return 1

    if args.web:
        from .web import run as run_web

        if args.host != "127.0.0.1":
            # Serving beyond loopback puts Ariba credentials behind an
            # unmanaged, untrusted-network listener with no TLS.
            log.warning("Binding to %s exposes this beyond the local machine "
                        "with no TLS and no authentication.", args.host)
        run_web(host=args.host, port=args.port,
                open_browser=not args.no_browser, config=config)
        return 0

    from .ui.app import run

    run(config, secrets, offline=offline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
