"""The one argparse tree behind `run`, `people`, `soak` and `registry`.

USING IT
    python -m daydag registry               # one entry for all four commands
    python -m daydag.run plan morning
    python -m daydag.run render chase --log ~/.local/state/daydag/events.db < payloads.json
    python -m daydag.run render ship --mirrors --log <db> < /dev/null
    python -m daydag.people show vp-data --log <db>
    python -m daydag.soak note "put the clashes above the meeting list" --log <db>
    python -m daydag.registry

    Each module's `main` prepends its own subcommand name and hands the rest
    here, so those four entry points and `main(["run", "plan", "morning"])` are
    the same parse (`tests/test_cli.py`).

CONTRACTS
    1. One parser. The four modules each hand-rolled `args.index("--log") + 1`,
       and one of them wrote a sqlite file literally named "None" when the
       flag came last (#117, #126). argparse refuses a flag with no value.
    2. Nothing here decides anything. Every handler builds the objects the
       module already exposes and calls them; the rules stay in the modules.
    3. A caller mistake is exit code 2 with the usage; a refusal the module
       raises (`RunError`, `ConfigError`, `RegistryError`) is exit code 1 with
       its one-line message on stderr. Never a traceback for either.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from daydag import run as run_module
from daydag.config import ConfigError, Identities
from daydag.eventlog import EventLog
from daydag.people import STATED, People
from daydag.registry import Registry, RegistryError, read_manifests, render_manifests
from daydag.soak import Soak

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daydag", description="the DayDAG loops and stores")
    commands = parser.add_subparsers(dest="command", required=True)

    run_p = commands.add_parser("run", help="plan a loop's fetches, or render its push")
    run_p.add_argument("phase", choices=("plan", "render"))
    run_p.add_argument("loop", choices=run_module.LOOPS)
    run_p.add_argument("--log", metavar="PATH", help="the event log; without it the run forgets")
    run_p.add_argument(
        "--write-state", action="store_true", help="append what was learned to State.md"
    )
    run_p.add_argument(
        "--mirrors", action="store_true", help="sync the watchlist's repos and build the pulse"
    )
    run_p.add_argument(
        "--for", dest="selector", default="", metavar="MEETING", help="prep this one"
    )

    people_p = commands.add_parser("people", help="the people directory")
    people_p.add_argument("verb", choices=("add", "set", "show", "list"))
    people_p.add_argument("key", nargs="?", default="")
    people_p.add_argument("--log", metavar="PATH", required=True)
    people_p.add_argument("--email")
    people_p.add_argument("--slack-id", dest="slack_id")
    people_p.add_argument("--name", dest="display_name")
    people_p.add_argument("--title")
    people_p.add_argument("--dm")
    people_p.add_argument("--group", dest="groups", action="append", default=[])
    people_p.add_argument("--leadership", action="store_true")

    soak_p = commands.add_parser("soak", help="the five-day gate's journal")
    soak_p.add_argument("verb", choices=("shipped", "note", "folded", "report"))
    soak_p.add_argument("text", nargs="*")
    soak_p.add_argument("--log", metavar="PATH", required=True)
    soak_p.add_argument("--day", type=date.fromisoformat, default=None)

    registry_p = commands.add_parser(
        "registry", help="the ownership table, from the shipped skills"
    )
    registry_p.add_argument("skills_dir", nargs="?", default=None)
    return parser


def _run(args: argparse.Namespace) -> int:
    identities = Identities.from_file(Path(".env"))
    now = datetime.now().astimezone()
    if args.phase == "plan":
        built = run_module.plan(args.loop, now=now, identities=identities, selector=args.selector)
        print(json.dumps(built.to_dict(), indent=2))
        return 0
    payloads = json.load(sys.stdin)
    events = EventLog.open(args.log) if args.log else None
    pulse, report = (None, None)
    if args.mirrors:
        pulse, report = run_module.build_pulse(identities, events)
    print(
        run_module.render(
            args.loop,
            now=now,
            identities=identities,
            payloads=payloads,
            log=args.log,
            pulse=pulse,
            write_state=args.write_state,
            selector=args.selector,
        )
    )
    if events is not None and report is not None:
        run_module.store_cursors(events, report)
    return 0


def _people(args: argparse.Namespace) -> int:
    directory = People(EventLog.open(args.log))
    if args.verb == "list":
        for person in sorted(directory.all(), key=lambda p: p.key):
            met = f"met {person.met}x" if person.met else "not met yet"
            print(
                f"  {person.key:22} {person.primary_email or '-':34} {person.title or '-':28} {met}"
            )
        return 0
    if not args.key:
        print(f"people {args.verb} needs a key", file=sys.stderr)
        return 2
    if args.verb == "show":
        person = directory.resolve(args.key)
        if person is None:
            print(f"not in the directory: {args.key}")
            return 1
        for name in (
            "key",
            "emails",
            "slack_id",
            "display_name",
            "title",
            "dm",
            "groups",
            "leadership",
            "met",
            "first_met",
            "last_met",
        ):
            print(f"  {name:14} {getattr(person, name)}")
        print(f"  {'sources':14} {dict(person.sources)}")
        return 0
    directory.remember(
        args.key,
        source=STATED,
        email=args.email,
        slack_id=args.slack_id,
        display_name=args.display_name,
        title=args.title,
        dm=args.dm,
        groups=args.groups,
        leadership=True if args.leadership else None,
    )
    print(f"  remembered {args.key}")
    return 0


def _soak(args: argparse.Namespace) -> int:
    journal = Soak(EventLog.open(args.log))
    day = args.day or date.today()
    text = " ".join(args.text).strip()
    if args.verb == "report":
        print(journal.report().render())
        return 0
    if args.verb == "shipped":
        journal.shipped(day)
    elif not text:
        print(f"soak {args.verb} needs the text of the edit", file=sys.stderr)
        return 2
    elif args.verb == "note":
        journal.note(text, day=day)
    else:
        journal.folded(text)
    print(journal.report().render())
    return 0


def _registry(args: argparse.Namespace) -> int:
    manifests = read_manifests(args.skills_dir)
    registry = Registry.load(manifests)
    print(render_manifests(registry, manifests))
    return 0


_HANDLERS = {"run": _run, "people": _people, "soak": _soak, "registry": _registry}


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, dispatch, and turn a module's refusal into one line and exit 1."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exit_:  # argparse already printed the usage
        return int(exit_.code or 0)
    try:
        return _HANDLERS[args.command](args)
    except (run_module.RunError, ConfigError, RegistryError) as refused:
        print(f"{refused}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
