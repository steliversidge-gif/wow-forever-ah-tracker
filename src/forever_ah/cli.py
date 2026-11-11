"""Command line entry point: `forever-ah <command>` or `python -m forever_ah <command>`."""

from __future__ import annotations

import argparse
import functools
import http.server
import logging
import os
import sys

from forever_ah import analyse, collector, db, export
from forever_ah.blizzard import BlizzardClient
from forever_ah.config import load_settings

log = logging.getLogger("forever_ah")


def cmd_run(args) -> int:
    """Collect new snapshots, refresh recommendations, write the dashboard."""
    settings = load_settings()
    conn = db.connect(settings.db_path)
    client = BlizzardClient(settings.client_id, settings.client_secret, settings.region, settings.locale)

    updated, failures = collector.collect(conn, client, settings)
    collector.resolve_items(conn, client)
    collector.prune(conn, settings)

    houses = [h["house_id"] for h in collector.tracked_houses(conn, settings)] if args.reanalyse else updated
    for house_id in houses:
        analyse.analyse_house(conn, house_id, settings)
    if houses:
        analyse.analyse_exports(conn, settings)

    dist_missing = not (settings.dist_path / "data" / "meta.json").exists()
    if updated or args.reanalyse or dist_missing:
        export.export_site(conn, settings)
    db.checkpoint(conn)
    conn.close()

    changed = bool(updated or args.reanalyse)
    log.info("Done: %d house(s) updated, %d failure(s)", len(updated), failures)
    if os.environ.get("GITHUB_OUTPUT"):  # lets the workflow skip upload/deploy when nothing changed
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"updated={'true' if changed else 'false'}\n")
    return 1 if failures else 0


def cmd_export(args) -> int:
    settings = load_settings()
    conn = db.connect(settings.db_path)
    export.export_site(conn, settings)
    conn.close()
    return 0


def cmd_status(args) -> int:
    settings = load_settings()
    conn = db.connect(settings.db_path)
    print(f"Database: {settings.db_path} ({settings.db_path.stat().st_size / 1e6:.1f} MB)")
    print(f"{'house':>6} {'faction':<11} {'snaps':>5} {'latest (UTC)':<20} {'listings':>9} {'signals':>8}")
    for h in collector.tracked_houses(conn, settings):
        hid = h["house_id"]
        snaps, latest = conn.execute(
            "SELECT COUNT(*), MAX(published_at) FROM snapshots WHERE house_id = ?", (hid,)).fetchone()
        listings = conn.execute("SELECT COUNT(*) FROM listings WHERE house_id = ?", (hid,)).fetchone()[0]
        signals = conn.execute("SELECT COUNT(*) FROM signals WHERE house_id = ?", (hid,)).fetchone()[0]
        mark = "*" if settings.houses.get(h["faction"]) == hid else " "
        print(f"{hid:>5}{mark} {h['faction']:<11} {snaps:>5} {latest or '-':<20} {listings:>9} {signals:>8}")
    named, total = conn.execute("SELECT COUNT(name), COUNT(*) FROM items").fetchone()
    print(f"Items resolved: {named}/{total}   (* = pinned in config.toml)")
    conn.close()
    return 0


def cmd_serve(args) -> int:
    settings = load_settings()
    if not (settings.dist_path / "index.html").exists():
        print("No dashboard yet - run `forever-ah run` first.", file=sys.stderr)
        return 1
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(settings.dist_path))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Dashboard: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forever-ah", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="collect, analyse and export the dashboard")
    run.add_argument("--reanalyse", action="store_true",
                     help="rebuild recommendations for every house even if no new data arrived")
    run.set_defaults(func=cmd_run)
    sub.add_parser("export", help="rewrite the dashboard from the database").set_defaults(func=cmd_export)
    sub.add_parser("status", help="show what has been collected").set_defaults(func=cmd_status)
    serve = sub.add_parser("serve", help="view the dashboard locally")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    return args.func(args)
