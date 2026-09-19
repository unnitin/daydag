"""Skill registry: the ownership table, made executable (ARCHITECTURE, issue #33).

An invariant enforced by documentation has a half-life. These describe the
startup checks that replace it.
"""

import pytest

from daydag.registry import Registry, RegistryError

PLANNING = {
    "name": "weekly-planning",
    "daydag": {"writes": ["vault:Fact Base/Workstreams.md"], "schedule": "fri 13:00"},
}
FEEDBACK = {
    "name": "weekly-feedback-scan",
    "daydag": {
        "writes": ["drive:feedback-log"],
        "sensitivity": "private",
        "schedule": "weekly",
    },
}


def test_the_same_skill_declaring_an_artifact_twice_is_fine():
    dup = {"name": "x", "daydag": {"writes": ["a:b", "a:b"]}}
    assert Registry.load([dup]).writer_of("a:b") == "x"


def test_schedule_is_projected_from_manifests():
    """Adding a routine means shipping a skill, not editing a table and a crontab."""
    reg = Registry.load([PLANNING, FEEDBACK])
    assert reg.schedule() == {
        "weekly-feedback-scan": "weekly",
        "weekly-planning": "fri 13:00",
    }


def test_consumers_are_matched_to_producers():
    producer = {"name": "pulse", "daydag": {"emits": ["evidence.pulse"]}}
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.pulse"]}}
    reg = Registry.load([producer, consumer])
    assert reg.producers_for("pod-update") == ["pulse"]


def test_consuming_something_nobody_emits_is_an_error():
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.nope"]}}
    with pytest.raises(RegistryError, match=r"evidence\.nope"):
        Registry.load([consumer])
