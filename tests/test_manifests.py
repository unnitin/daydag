"""Loading manifests off disk - what makes the registry's checks non-inert (#4).

`test_registry.py` proves the checks work on dictionaries typed into a test.
These prove they run against the skills this repo actually ships, which is a
different claim and the one that matters: a check nothing calls is a check that
passes.

The last block is the seam test. It loads `.claude/skills/` itself, so a future
skill that quietly claims someone else's artifact - or drops `sensitivity:
private` off the feedback scan - fails here rather than on a Friday.
"""

from pathlib import Path

import pytest

from daydag.manifests import (
    ManifestError,
    load_registry,
    read_manifests,
    repo_skills_dir,
)
from daydag.registry import PRIVATE_SURFACES, RegistryError

REPO_SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"

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


# -- reading the block ----------------------------------------------------


def test_the_daydag_block_is_read_off_disk(tmp_path):
    write_skill(tmp_path, "daydag", DAYDAG_BLOCK)
    registry = load_registry(tmp_path)
    assert registry.writer_of("vault:DayDAG/State.md") == "daydag"
    assert registry.schedule() == {"daydag": "weekdays 06:45"}


def test_a_skill_with_no_daydag_block_declares_nothing(tmp_path):
    """The Skill loader ignores the block, so its absence must be tolerated."""
    write_skill(tmp_path, "plain")
    registry = load_registry(tmp_path)
    assert registry.schedule() == {}
    assert registry.writer_of("vault:DayDAG/State.md") is None


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
def test_the_shipped_manifests_load(shipped):
    """The one-writer check, run over the real set. This is the point of #4."""
    assert shipped.writer_of("vault:DayDAG/State.md") == "daydag"
    assert shipped.writer_of("vault:Fact Base/Workstreams.md") == "weekly-planning"


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
    declared = shipped_manifests["daydag"]["writes"]
    assert declared, "daydag declares at least one artifact"
    for artifact in declared:
        assert artifact.startswith(owned), artifact


def test_the_dm_is_a_surface_and_not_declared_as_an_artifact(shipped_manifests):
    """`writes:` holds artifact ids; `route()` takes surfaces. Keep them apart.

    `slack:dm-nitin` is the one place every skill's output may land, which is a
    `route()` question. Put it in `writes:` and the two checks contradict:
    `route` lets the feedback scan reach the DM while `check_write` says it
    belongs to `daydag`. (`local:event-log` sits in both vocabularies on
    purpose - it is DayDAG's artifact *and* where a private skill's items are
    stored, per SPEC 10.1.)
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


def test_a_folder_id_does_not_authorise_the_files_inside_it(shipped):
    """Honest about a limit rather than assuming past it.

    Artifact ids are matched exactly, so `vault:DayDAG/Proposals/` records
    custody of the folder and nothing more. Prefix matching belongs in the
    registry (the skill-registry path); until it lands, this is the behaviour.
    """
    assert shipped.writer_of("vault:DayDAG/Proposals/") == "daydag"
    with pytest.raises(RegistryError, match="undeclared"):
        shipped.check_write("daydag", "vault:DayDAG/Proposals/0906-workstreams.md")


def test_the_scheduling_table_is_a_projection_of_the_manifests(shipped):
    """ARCHITECTURE's Scheduling table, derived rather than hand-kept."""
    assert set(shipped.schedule()) == {
        "daydag",
        "weekly-feedback-scan",
        "weekly-planning-and-progress",
    }


def test_the_pulse_feeds_the_weekly_routines(shipped):
    """`consumes:` is the wiring that replaces five hardcoded relationships."""
    for consumer in ("weekly-progress-reporting", "weekly-feedback-scan"):
        assert "daydag" in shipped.producers_for(consumer)
