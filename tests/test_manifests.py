"""Skill manifests and the checks over them (#4, #33).

The first block proves the checks work on dictionaries typed into a test, and
nothing more: a check nothing calls is a check that passes. The middle blocks
take the same checks through a `SKILL.md` on disk.

The last block is the seam test. It loads `.claude/skills/` itself, so a future
skill that quietly claims someone else's artifact - or drops `sensitivity:
private` off the feedback scan - fails here rather than on a Friday.
`tests/test_guardrails.py` drives the entry point over a planted second writer.
"""

from pathlib import Path

import pytest

from daydag.manifests import (
    PRIVATE_SURFACES,
    ManifestError,
    Registry,
    RegistryError,
    load_registry,
    read_manifests,
    repo_skills_dir,
)

REPO_SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"

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

DAYDAG_BLOCK = """daydag:
  writes: [vault:DayDAG/State.md]
  reads: [slack]
  emits: [evidence.pulse]
  schedule: "weekdays 06:45"
"""


def write_skill(root: Path, name: str, block: str = "") -> Path:
    """A SKILL.md on disk, shaped exactly like the ones the repo ships."""
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    lines = ["---", f"name: {name}", 'description: "a test skill"']
    lines += block.splitlines()
    lines += ["---", "", f"# {name}", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_raw(root: Path, name: str, text: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def writers_of(manifests: dict[str, dict], artifact: str) -> list[str]:
    """Every skill whose `writes:` names ``artifact`` exactly."""
    return [name for name, block in manifests.items() if artifact in (block.get("writes") or [])]


# -- the checks, on dictionaries typed into a test --------------------------


@pytest.mark.guardrail
def test_two_writers_to_one_artifact_is_a_startup_error():
    """Invariant 1. The whole reason this component exists."""
    other = {"name": "daydag", "daydag": {"writes": ["vault:Fact Base/Workstreams.md"]}}
    with pytest.raises(RegistryError, match="Workstreams"):
        Registry.load([PLANNING, other])


def test_the_same_skill_declaring_an_artifact_twice_is_fine():
    """A skill may repeat itself; only two different skills are two writers."""
    dup = {"name": "x", "daydag": {"writes": ["a:b", "a:b"]}}
    assert isinstance(Registry.load([dup]), Registry)


def test_schedule_is_projected_from_manifests():
    """What `python -m daydag.manifests` prints as its schedule section."""
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


def test_consumers_are_matched_to_producers():
    """A consumed topic loads when any loaded skill emits it, in either order."""
    producer = {"name": "pulse", "daydag": {"emits": ["evidence.pulse"]}}
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.pulse"]}}
    assert isinstance(Registry.load([producer, consumer]), Registry)
    assert isinstance(Registry.load([consumer, producer]), Registry)


def test_consuming_something_nobody_emits_is_an_error():
    consumer = {"name": "pod-update", "daydag": {"consumes": ["evidence.nope"]}}
    with pytest.raises(RegistryError, match=r"evidence\.nope"):
        Registry.load([consumer])


# -- reading the block ----------------------------------------------------


def test_the_daydag_block_is_read_off_disk(tmp_path):
    write_skill(tmp_path, "daydag", DAYDAG_BLOCK)
    (manifest,) = read_manifests(tmp_path)
    assert manifest["daydag"]["writes"] == ["vault:DayDAG/State.md"]
    assert load_registry(tmp_path).schedule() == {"daydag": "weekdays 06:45"}


def test_a_skill_with_no_daydag_block_declares_nothing(tmp_path):
    """The Skill loader ignores the block, so its absence must be tolerated."""
    write_skill(tmp_path, "plain")
    assert read_manifests(tmp_path) == [{"name": "plain", "daydag": {}}]
    assert load_registry(tmp_path).schedule() == {}


def test_directories_without_a_skill_file_are_skipped(tmp_path):
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "notes.md").write_text("hi", encoding="utf-8")
    write_skill(tmp_path, "real", DAYDAG_BLOCK)
    assert [manifest["name"] for manifest in read_manifests(tmp_path)] == ["real"]


def test_a_missing_skills_directory_says_so(tmp_path):
    with pytest.raises(ManifestError, match="no skills directory"):
        read_manifests(tmp_path / "nope")


# -- refusing to load something ambiguous ---------------------------------


def test_frontmatter_that_is_not_yaml_names_the_file(tmp_path):
    write_raw(tmp_path, "broken", "---\nname: broken\ndescription: a: b: c\n---\nbody\n")
    with pytest.raises(ManifestError, match=r"broken.SKILL\.md"):
        read_manifests(tmp_path)


def test_a_file_without_frontmatter_is_an_error(tmp_path):
    write_raw(tmp_path, "bare", "# bare\n\nno frontmatter here\n")
    with pytest.raises(ManifestError, match="no yaml frontmatter"):
        read_manifests(tmp_path)


def test_a_name_that_disagrees_with_its_directory_is_an_error(tmp_path):
    write_raw(tmp_path, "on-disk", "---\nname: in-frontmatter\n---\nbody\n")
    with pytest.raises(ManifestError, match="in-frontmatter"):
        read_manifests(tmp_path)


def test_a_misspelled_manifest_key_is_rejected(tmp_path):
    """`write:` for `writes:` would silently leave an artifact unowned."""
    write_skill(tmp_path, "typo", "daydag:\n  write: [vault:DayDAG/State.md]\n")
    with pytest.raises(ManifestError, match="write"):
        read_manifests(tmp_path)


def test_an_unknown_sensitivity_is_rejected(tmp_path):
    """`privat` reads as "not private" and would route personnel content out."""
    write_skill(tmp_path, "typo", "daydag:\n  sensitivity: privat\n")
    with pytest.raises(ManifestError, match="privat"):
        read_manifests(tmp_path)


def test_a_scalar_where_a_list_belongs_is_rejected(tmp_path):
    """`writes: vault:x` would iterate as characters, owning nothing real."""
    write_skill(tmp_path, "scalar", "daydag:\n  writes: vault:DayDAG/State.md\n")
    with pytest.raises(ManifestError, match="writes"):
        read_manifests(tmp_path)


# -- the two checks the loader exists to make real -------------------------


@pytest.mark.guardrail
def test_two_skills_on_disk_claiming_one_artifact_fail_loudly(tmp_path):
    """Invariant 1, from files rather than fixtures."""
    shared = "daydag:\n  writes: [vault:Fact Base/Workstreams.md]\n"
    write_skill(tmp_path, "weekly-planning", shared)
    write_skill(tmp_path, "daydag", shared)
    with pytest.raises(RegistryError, match="Workstreams"):
        load_registry(tmp_path)


@pytest.mark.guardrail
def test_a_private_skill_loaded_from_disk_cannot_reach_a_shared_surface(tmp_path):
    """SPEC 10.1. The refusal has to survive the trip through the file."""
    write_skill(
        tmp_path,
        "weekly-feedback-scan",
        "daydag:\n  writes: [drive:log]\n  sensitivity: private\n",
    )
    registry = load_registry(tmp_path)
    with pytest.raises(RegistryError, match="sensitivity"):
        registry.route("weekly-feedback-scan", to="slack:channel")
    assert registry.route("weekly-feedback-scan", to="slack:dm-nitin") is True


def test_a_folder_id_does_not_claim_the_files_inside_it(tmp_path):
    """Honest about a limit rather than assuming past it.

    Artifact ids are matched exactly, so `vault:DayDAG/Proposals/` records
    custody of the folder and nothing more: a second skill declaring a file
    inside it is not a second writer, and the set loads.
    """
    write_skill(tmp_path, "owner", "daydag:\n  writes: [vault:DayDAG/Proposals/]\n")
    write_skill(tmp_path, "sibling", "daydag:\n  writes: [vault:DayDAG/Proposals/0906-x.md]\n")
    assert isinstance(load_registry(tmp_path), Registry)


# -- against the skills this repo actually ships ---------------------------


@pytest.fixture(scope="module")
def shipped():
    return load_registry(REPO_SKILLS)


@pytest.fixture(scope="module")
def shipped_manifests():
    return {manifest["name"]: manifest["daydag"] for manifest in read_manifests(REPO_SKILLS)}


def test_the_default_skills_directory_is_the_one_in_this_repo():
    assert repo_skills_dir() == REPO_SKILLS


@pytest.mark.guardrail
def test_the_shipped_manifests_load(shipped, shipped_manifests):
    """The one-writer check, run over the real set. This is the point of #4.

    `shipped` loading at all is the check: `Registry.load` raises on a second
    writer. The asserts name who the one declared writer is.
    """
    assert writers_of(shipped_manifests, "vault:DayDAG/State.md") == ["daily-loops"]
    assert writers_of(shipped_manifests, "vault:Fact Base/Workstreams.md") == ["weekly-planning"]


@pytest.mark.guardrail
def test_the_shipped_feedback_scan_is_private(shipped):
    """SPEC 10.1 is the strictest rule in the repo; here it is mechanical."""
    for surface in ("slack:channel", "vault:DayDAG/State.md", "gmail:draft"):
        with pytest.raises(RegistryError, match="sensitivity"):
            shipped.route("weekly-feedback-scan", to=surface)


@pytest.mark.guardrail
def test_daydag_writes_nothing_it_does_not_own(shipped_manifests):
    """Ownership table: DayDAG owns the `DayDAG/` folder and the event log.

    `Fact Base/Workstreams.md` is `weekly-planning`'s until the custody cut
    (#37). A manifest that claims it early is the exact regression this catches.
    """
    owned = ("vault:DayDAG/", "local:")
    declared = shipped_manifests["daily-loops"]["writes"]
    assert declared, "daily-loops declares at least one artifact"
    for artifact in declared:
        assert artifact.startswith(owned), artifact


def test_the_dm_is_a_surface_and_not_declared_as_an_artifact(shipped_manifests):
    """`writes:` holds artifact ids; `route()` takes surfaces. Keep them apart.

    `slack:dm-nitin` is the one place every skill's output may land, which is a
    `route()` question. Put it in `writes:` and the two vocabularies
    contradict: `route` lets the feedback scan reach the DM while the
    one-writer check gives it to a single skill and refuses every other.
    (`local:event-log` sits in both vocabularies on purpose - it is DayDAG's
    artifact *and* where a private skill's items are stored, per SPEC 10.1.)
    """
    assert "slack:dm-nitin" in PRIVATE_SURFACES
    for name, block in shipped_manifests.items():
        assert "slack:dm-nitin" not in (block.get("writes") or []), name


def test_the_private_skills_artifact_sits_on_a_private_surface(shipped, shipped_manifests):
    """The pairing finding #1 of the review found nothing pinning.

    `drive:${GDRIVE_FEEDBACK_LOG_FOLDER}/` is the *id*; `drive:private` is the
    *surface* it lives on, and that surface is routable for a private skill.
    """
    (artifact,) = shipped_manifests["weekly-feedback-scan"]["writes"]
    assert artifact.startswith("drive:")
    assert shipped.route("weekly-feedback-scan", to="drive:private") is True


def test_the_scheduling_table_is_a_projection_of_the_manifests(shipped):
    """ARCHITECTURE's Scheduling table, derived rather than hand-kept."""
    assert set(shipped.schedule()) == {
        "daily-loops",
        "progress-deck",
        "team-practices",
        "weekly-feedback-scan",
        "weekly-planning-and-progress",
    }


def test_a_sub_id_is_its_own_artifact_with_its_own_writer(shipped, shipped_manifests):
    """`team-practices` stores rows in `daily-loops`' SQLite file and owns none of it.

    The same exact-match rule as the folder test above, over the shipped set:
    `local:event-log/team-practices` is a distinct id, so declaring it is not a
    second claim on `local:event-log`. That is the pattern the plan
    (`docs/how-we-work-skills.md` §4) leans on, pinned here so it cannot become
    prefix matching by accident - if ids ever match by prefix, `shipped` fails
    to load and the ownership question gets asked again rather than silently
    answered.
    """
    assert writers_of(shipped_manifests, "local:event-log") == ["daily-loops"]
    assert writers_of(shipped_manifests, "local:event-log/team-practices") == ["team-practices"]
    assert writers_of(shipped_manifests, "vault:DayDAG/Proposals/") == ["daily-loops"]


def test_the_pulse_feeds_the_weekly_routines(shipped_manifests):
    """Each weekly routine `consumes:` a topic that `daily-loops` `emits:`."""
    emitted = set(shipped_manifests["daily-loops"]["emits"])
    for consumer in ("weekly-progress-reporting", "weekly-feedback-scan"):
        assert emitted & set(shipped_manifests[consumer]["consumes"]), consumer
