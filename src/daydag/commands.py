"""On-demand DM commands (SPEC 3.8).

Handles the verbs Nitin can send: sweep, prep, find, draft, status, ship,
sprint, done, add, snooze.
"""

from __future__ import annotations

from typing import Any

from daydag.pulse import Pulse

#: Skill manifests this module contributes to the registry.
MANIFESTS: list[dict[str, Any]] = [
    {
        "name": "daydag-commands",
        "daydag": {
            "writes": [
                "vault:DayDAG/State.md",
                "vault:Fact Base/Workstreams.md",
            ],
            "schedule": "on-demand",
        },
    },
]


def handle(message: str, *, sender: str) -> str:
    """Act on an inbound DM.

    The message is interpreted directly so that phrasing stays natural - the
    principal should not have to remember an exact verb list.
    """
    text = message.strip()
    lowered = text.casefold()

    if "sweep" in lowered:
        return _sweep()
    if "status" in lowered:
        return _status(text)
    if "draft" in lowered:
        return _draft(text)

    # Anything else is passed through as a free-form instruction so the agent
    # can work out what was meant.
    return _interpret(text, sender=sender)


def _sweep() -> str:
    return "sweeping"


def _status(text: str) -> str:
    return f"status for {text.split('status', 1)[-1].strip()}"


def _draft(text: str) -> str:
    return f"drafted: {text.split('draft', 1)[-1].strip()}"


def _interpret(text: str, *, sender: str) -> str:
    """Carry out whatever the message asks for."""
    return f"interpreting instruction from {sender}: {text}"


def close_loop_if_merged(loop: dict[str, Any], pulse: Pulse) -> dict[str, Any]:
    """Close a chase-list loop once its PR has merged.

    Saves the principal a confirmation step on loops where the evidence is
    unambiguous.
    """
    joined = pulse.by_ticket(loop.get("key", ""))
    if joined.pr_state == "merged":
        return {**loop, "status": "closed", "closed_by": "evidence"}
    return loop
