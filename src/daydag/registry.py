"""The ownership table, made executable: one writer per artifact.

USING IT
    registry = load_registry()              # from .claude/skills/*/SKILL.md; raises on a 2nd writer
    registry = Registry.load(manifests)     # or from manifests already in hand
    registry.writer_of("vault:DayDAG/State.md")     # -> skill name
    registry.check_write(skill, artifact)   # raises if undeclared
    registry.route(skill, surface)          # raises if sensitivity forbids it
    registry.schedule()                     # -> {skill: cron-ish string}
    registry.producers_for(skill)           # who emits what it consumes

    python -m daydag.registry [skills_dir]   # the ownership table and schedule

    A manifest is the `daydag:` block of a skill's SKILL.md frontmatter:

        daydag:
          writes:       [vault:Fact Base/Workstreams.md]
          consumes:     [evidence.pulse]
          emits:        [evidence.planning]
          schedule:     "fri 13:00"
          sensitivity:  private

CONTRACTS
    1. Two writers for one artifact is a `RegistryError` AT LOAD, regardless of
       declaration order. Not a warning, and not discovered on write.
    2. `writes:` holds ARTIFACT ids; `route()` takes SURFACES. Different
       namespaces - passing an artifact id to `route()` is a category error
       that looks like a guardrail firing.
    3. A `sensitivity: private` skill cannot be routed to any surface with an
       audience above one. `State.md` is plaintext on every synced device, so
       it counts as an audience.

WHY IT EXISTS
    ARCHITECTURE states one-writer-per-artifact as invariant 1, and an invariant
    enforced by documentation has a half-life: the next skill gets written by
    someone who never read the table. This turns the table into startup checks,
    so a second writer is a loud failure rather than a corrupted file discovered
    on a Friday. Reading the manifests off disk is the second half, below, and
    was its own module until the two halves were only ever used together.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

#: The one autonomous Slack destination this system may ever address (SPEC
#: guardrail 1). Named rather than left implicit inside `PRIVATE_SURFACES` so
#: `daydag.delivery` - the module that actually sends there - imports the
#: identifier instead of carrying a second hand-typed copy of the string.
DM_SURFACE = "slack:dm-nitin"

# Surfaces a `sensitivity: private` skill may reach. Everything else has an
# audience above one, which SPEC section 10.1 forbids for personnel content.
PRIVATE_SURFACES = frozenset({DM_SURFACE, "local:event-log", "drive:private"})


class RegistryError(RuntimeError):
    """A manifest set that cannot be loaded, or a routing rule that was broken."""


class Registry:
    """Loaded manifests, plus the checks that make the ownership table real."""

    def __init__(
        self,
        writers: dict[str, str],
        manifests: dict[str, dict[str, Any]],
    ) -> None:
        self._writers = writers
        self._manifests = manifests

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, manifests: Iterable[dict[str, Any]]) -> Registry:
        """Build a registry, refusing any set that breaks an invariant.

        Two skills claiming the same artifact is the failure this whole module
        exists to prevent, so it is raised here rather than surfaced later.
        """
        by_name: dict[str, dict[str, Any]] = {}
        writers: dict[str, str] = {}
        emitted: set[str] = set()

        for manifest in manifests:
            name = manifest["name"]
            block = manifest.get("daydag") or {}
            by_name[name] = block
            emitted.update(block.get("emits") or [])

            # A skill may repeat itself; two *different* skills may not.
            for artifact in dict.fromkeys(block.get("writes") or []):
                owner = writers.get(artifact)
                if owner is not None and owner != name:
                    raise RegistryError(
                        f"two writers declared for {artifact!r}: {owner!r} and {name!r}. "
                        "One writer per artifact - see the ownership table in ARCHITECTURE.md."
                    )
                writers[artifact] = name

        for name, block in by_name.items():
            for topic in block.get("consumes") or []:
                if topic not in emitted:
                    raise RegistryError(
                        f"{name!r} consumes {topic!r}, which no loaded skill emits."
                    )

        return cls(writers, by_name)

    # -- the checks ------------------------------------------------------

    def writer_of(self, artifact: str) -> str | None:
        """Which skill owns this artifact, if any."""
        return self._writers.get(artifact)

    def check_write(self, skill: str, artifact: str) -> None:
        """Raise unless ``skill`` declared ``artifact`` in its manifest.

        An undeclared write is how the ownership table silently stops being
        true, so it fails rather than warns.
        """
        owner = self._writers.get(artifact)
        if owner != skill:
            raise RegistryError(
                f"undeclared write: {skill!r} wrote {artifact!r}, which it does not "
                f"declare in `writes:` (owner: {owner or 'nobody'})."
            )

    def route(self, skill: str, to: str) -> bool:
        """Raise if a private skill's output would reach a shared surface.

        SPEC section 10.1 as a check instead of a paragraph. Its failure mode is
        a person's career record, which is not a thing to leave to convention.
        """
        block = self._manifests.get(skill)
        if block is None:
            raise RegistryError(f"unknown skill {skill!r}")
        if block.get("sensitivity") == "private" and to not in PRIVATE_SURFACES:
            raise RegistryError(
                f"{skill!r} declares sensitivity: private and cannot route to {to!r}, "
                "which has an audience above one."
            )
        return True

    # -- projections -----------------------------------------------------

    def schedule(self) -> dict[str, str]:
        """The scheduling table, derived from manifests rather than hand-kept."""
        return {
            name: block["schedule"]
            for name, block in sorted(self._manifests.items())
            if block.get("schedule")
        }

    def producers_for(self, skill: str) -> list[str]:
        """Which skills emit what ``skill`` consumes - the evidence-bus wiring."""
        wanted = set((self._manifests.get(skill) or {}).get("consumes") or [])
        return sorted(
            name
            for name, block in self._manifests.items()
            if wanted & set(block.get("emits") or [])
        )


# ---------------------------------------------------------------------------
# reading manifests off disk - the step that makes the checks real. Validation
# is STRICT: an unknown key or an unknown sensitivity is an error, because both
# failures are silent otherwise. Artifact ids stay literal and match exactly.
# ---------------------------------------------------------------------------

#: The file a skill directory must contain to be one.
SKILL_FILE = "SKILL.md"

#: Keys the ``daydag:`` block may carry, per ARCHITECTURE "Extensibility".
MANIFEST_KEYS = frozenset({"writes", "reads", "consumes", "emits", "schedule", "sensitivity"})

#: Keys whose value is a list of artifact ids or topic names.
LIST_KEYS = ("writes", "reads", "consumes", "emits")

#: The two sensitivities. ``private`` is the one with teeth - see SPEC 10.1.
SENSITIVITIES = frozenset({"private", "shared"})

#: Leading ``---`` ... ``---`` block. Non-greedy, so a horizontal rule further
#: down the body (``weekly-progress-reporting`` opens with one) cannot swallow
#: the whole file into the frontmatter.
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class ManifestError(RegistryError):
    """A ``SKILL.md`` whose manifest cannot be trusted to mean what it says."""


def repo_skills_dir() -> Path:
    """``.claude/skills`` for the checkout this package was installed from.

    Found by walking up from this file rather than from the working directory,
    because the loader is called from a test, a hook and a script with three
    different cwds.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".claude" / "skills"
        if candidate.is_dir():
            return candidate
    raise ManifestError(
        "no .claude/skills directory above "
        f"{Path(__file__).resolve()} - pass an explicit path instead."
    )


def parse_frontmatter(text: str, source: Path) -> dict[str, Any]:
    """The YAML frontmatter of a ``SKILL.md``, as a mapping."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ManifestError(f"{source}: no yaml frontmatter (a SKILL.md opens with `---`).")
    try:
        loaded = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{source}: frontmatter is not valid yaml - {exc}") from exc
    if not isinstance(loaded, dict):
        raise ManifestError(
            f"{source}: frontmatter must be a mapping, got {type(loaded).__name__}."
        )
    return loaded


def _validate_block(block: Any, source: Path) -> dict[str, Any]:
    """Refuse a manifest that would quietly mean less than it appears to."""
    if not isinstance(block, dict):
        raise ManifestError(f"{source}: `daydag:` must be a mapping, got {type(block).__name__}.")

    unknown = sorted(set(block) - MANIFEST_KEYS)
    if unknown:
        raise ManifestError(
            f"{source}: unknown key(s) in `daydag:`: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(MANIFEST_KEYS))}. A misspelled `writes:` "
            "leaves the artifact with no declared owner."
        )

    for key in LIST_KEYS:
        value = block.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ManifestError(
                f"{source}: `{key}:` must be a list of strings, got {value!r}. "
                "A bare string iterates as characters and owns nothing real."
            )

    sensitivity = block.get("sensitivity")
    if sensitivity is not None and sensitivity not in SENSITIVITIES:
        raise ManifestError(
            f"{source}: unknown sensitivity {sensitivity!r}; expected one of "
            f"{', '.join(sorted(SENSITIVITIES))}. Anything but `private` routes freely."
        )

    schedule = block.get("schedule")
    if schedule is not None and not isinstance(schedule, str):
        raise ManifestError(f"{source}: `schedule:` must be a string, got {schedule!r}.")

    return block


def read_manifest(path: Path) -> dict[str, Any]:
    """One skill's manifest: its ``name`` plus a validated ``daydag:`` block.

    The block is optional - the Skill loader ignores it, and a skill that
    declares nothing is simply a skill DayDAG does not orchestrate.
    """
    frontmatter = parse_frontmatter(path.read_text(encoding="utf-8"), path)

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{path}: frontmatter has no `name:`.")
    if name != path.parent.name:
        raise ManifestError(
            f"{path}: declares `name: {name}` but lives in {path.parent.name}/. "
            "The registry keys on the name, so a mismatch owns the wrong artifacts."
        )

    block = frontmatter.get("daydag")
    return {"name": name, "daydag": _validate_block(block, path) if block is not None else {}}


def read_manifests(skills_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Every skill manifest under ``skills_dir``, sorted by name.

    A directory with no ``SKILL.md`` is skipped rather than reported: the skills
    tree also holds a README and, in time, per-skill reference files.
    """
    directory = Path(skills_dir) if skills_dir is not None else repo_skills_dir()
    if not directory.is_dir():
        raise ManifestError(f"no skills directory at {directory}.")
    return [
        read_manifest(path)
        for path in sorted(directory.glob(f"*/{SKILL_FILE}"), key=lambda p: p.parent.name)
    ]


def load_registry(skills_dir: Path | str | None = None) -> Registry:
    """Build the registry from what is on disk. The whole point of the module."""
    return Registry.load(read_manifests(skills_dir))


def render_manifests(registry: Registry, manifests: list[dict[str, Any]]) -> str:
    """The ownership table and schedule, as the manifests actually declare them."""
    lines = ["ownership (one writer per artifact)"]
    for manifest in manifests:
        for artifact in manifest["daydag"].get("writes") or []:
            lines.append(f"  {artifact:<44} {manifest['name']}")

    lines += ["", "schedule (projected from manifests)"]
    lines += [f"  {when:<44} {name}" for name, when in registry.schedule().items()]

    private = [m["name"] for m in manifests if m["daydag"].get("sensitivity") == "private"]
    lines += ["", "private - never routed above an audience of one"]
    lines += [f"  {name}" for name in private] or ["  (none)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``python -m daydag.registry [skills_dir]`` - the one CLI, entered here."""
    from daydag.cli import main as cli_main

    return cli_main(["registry", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
