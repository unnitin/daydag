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


@pytest.mark.guardrail
def test_two_writers_to_one_artifact_is_a_startup_error():
    """Invariant 1. The whole reason this component exists."""
    other = {"name": "daydag", "daydag": {"writes": ["vault:Fact Base/Workstreams.md"]}}
    with pytest.raises(RegistryError, match="Workstreams"):
        Registry.load([PLANNING, other])


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


@pytest.mark.guardrail
def test_private_skills_cannot_route_to_a_shared_surface():
    """SPEC 10.1 as a check rather than a paragraph."""
    reg = Registry.load([FEEDBACK])
    with pytest.raises(RegistryError, match="sensitivity"):
        reg.route("weekly-feedback-scan", to="slack:channel")


def test_private_skills_may_route_to_the_dm():
    reg = Registry.load([FEEDBACK])
    assert reg.route("weekly-feedback-scan", to="slack:dm-nitin") is True


def test_undeclared_write_is_rejected():
    """An undeclared write is the failure the registry exists to prevent."""
    reg = Registry.load([PLANNING])
    with pytest.raises(RegistryError, match="undeclared"):
        reg.check_write("weekly-planning", "vault:Weekly Notes/0907-0911.md")


def test_consumers_are_matched_to_producers():
    producer = {"name": "pulse", "daydag": {"emits": ["evidence.pulse"]}}
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.pulse"]}}
    reg = Registry.load([producer, consumer])
    assert reg.producers_for("pod-update") == ["pulse"]


def test_consuming_something_nobody_emits_is_an_error():
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.nope"]}}
    with pytest.raises(RegistryError, match="evidence.nope"):
        Registry.load([consumer])
