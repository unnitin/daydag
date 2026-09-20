"""Shared test doubles and record builders - the plain callables.

Fixtures live in `conftest.py`; this holds what a test imports by name, the
way `evalset` already is: `from support import FakeSources, calendar_event`.
Seven files each carried an `_event`, five an `identities`, three a
`FakeSources` and three a `Clock` before this existed.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from daydag.push import local

PT = ZoneInfo("America/Los_Angeles")
PRINCIPAL = "UPRINCIPAL1"

_MISSING = object()


def calendar_event(
    event_id: str,
    summary: str,
    start: datetime,
    *,
    minutes: int = 60,
    attendees=("nitin", "vp-data"),
    kind: str = "meeting",
    response: str | None = "needsAction",
    link: str | bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One calendar record, shaped the way the skill hands them to `render`.

    ``response=None`` omits `response_status` (the ledger's default applies);
    ``link=None`` cites a permalink built from the id, ``link=False`` omits
    the permalink so the line renders as unsourced, a string is used as given.
    """
    event: dict[str, Any] = {
        "id": event_id,
        "summary": summary,
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "attendees": list(attendees),
        "kind": kind,
    }
    if response is not None:
        event["response_status"] = response
    if link is None:
        event["permalink"] = f"https://calendar.example.com/e/{event_id}"
    elif link is not False:
        event["permalink"] = link
    event.update(extra)
    return event


class FakeSources:
    """Every read `push.Sources` declares, recorded rather than performed.

    ``notes`` maps a vault path to its text, ``None`` for a file that does not
    exist; ``note`` answers any path not in it, so a brief test can hand over
    one weekly note without naming its path. ``broken`` names reads that raise,
    and each records what it was asked: ``calendar_windows``, ``slack_queries``,
    ``gmail_queries``, ``note_paths``.

    ``calendar`` answers a window with the events on its day (a naive start is
    the principal's wall clock, as `push.local` reads it) plus any event whose
    start is not a datetime, because the loops ask one day at a time and the
    week-ahead asks seven. ``filter_by_day=False`` returns every event for
    every window, for a test about the reader rather than the calendar.
    """

    def __init__(
        self,
        *,
        events=(),
        notes=None,
        note=_MISSING,
        messages=(),
        mail=(),
        broken=(),
        filter_by_day: bool = True,
    ) -> None:
        self._events = list(events)
        self._notes = dict(notes or {})
        self._note = note
        self._messages = list(messages)
        self._mail = list(mail)
        self._broken = set(broken)
        self._filter = filter_by_day
        self.calendar_windows: list[Any] = []
        self.slack_queries: list[str] = []
        self.gmail_queries: list[str] = []
        self.note_paths: list[str] = []

    def _check(self, name: str) -> None:
        if name in self._broken:
            raise RuntimeError(f"{name} is down")

    def calendar(self, window):
        self._check("calendar")
        self.calendar_windows.append(window)
        if not self._filter:
            return list(self._events)
        return [
            event
            for event in self._events
            if local(event.get("start")) is None or local(event["start"]).date() == window.day
        ]

    def vault_note(self, path: str) -> str:
        self._check("vault_note")
        self.note_paths.append(path)
        text = self._notes[path] if path in self._notes else self._note
        if text is _MISSING or text is None:
            raise FileNotFoundError(path)
        return text

    def slack(self, query: str):
        self._check("slack")
        self.slack_queries.append(query)
        return list(self._messages)

    def gmail(self, query: str):
        self._check("gmail")
        self.gmail_queries.append(query)
        return list(self._mail)


class Clock:
    """A hand-wound clock: `set` moves it, calling it reads it. Time a test
    cannot control is a test that flakes."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, now: datetime) -> None:
        self.now = now


def rev(repo, git_env, *args: str) -> str:
    """`git <args>` in ``repo``, stdout stripped. Takes the hermetic env from
    `conftest.git_env`, for the reason written on that fixture."""
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=git_env
    )
    return out.stdout.strip()
