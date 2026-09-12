"""Reading skill manifests off disk - the step that makes the registry real.

USING IT
    registry = load_registry()          # reads .claude/skills/*/SKILL.md
    read_manifests(skills_dir)          # -> [{"name": ..., "daydag": {...}}]
    read_manifest(path)
    parse_frontmatter(text, source)

    python -m daydag.manifests          # the ownership table and schedule

CONTRACTS
    1. Validation is STRICT: an unknown key or unknown sensitivity is an error,
       not a shrug. Both failure modes are silent otherwise - `write:` for
       `writes:` leaves an artifact with no declared owner so a second writer
       sails through, and `sensitivity: privat` reads as "not private", where
       SPEC 10.1's blast radius is a person's career record.
    2. Artifact ids stay LITERAL. Nothing here resolves a `${VAR}`, which is
       what keeps a public repo free of the real ones.
    3. Two vocabularies, deliberately not merged. `writes:` holds ARTIFACT ids
       (one owner each); `Registry.route` takes a SURFACE (a destination with
       an audience). `weekly-feedback-scan` writes the artifact
       `drive:${GDRIVE_FEEDBACK_LOG_FOLDER}/` onto the surface `drive:private`.
       Passing an artifact id where a surface belongs gets a refusal that looks
       like a guardrail firing and is really a category error. Keep them apart
       until the evidence bus (#34) gives the mapping a home.
    4. Ids match EXACTLY. A trailing `/` marks a folder a skill owns, which is
       honest about custody but does not authorise the files inside it -
       `check_write("daily-loops", "vault:DayDAG/Proposals/x.md")` is still an
       undeclared write. Prefix matching belongs in the registry;
       `test_manifests.py` pins the current behaviour so nobody assumes.
    5. Not a runtime. Nothing here executes a skill. ARCHITECTURE is explicit
       that DayDAG stays a registry, a bus and a scheduler, and that every
       skill must still be useful invoked by hand.

WHY IT EXISTS
    `registry` turns ARCHITECTURE's ownership table into checks - but a check
    nothing calls is a check that passes. Until this module existed,
    `Registry.load` had only ever been handed dictionaries typed into a test,
    so the one-writer invariant held over fixtures and said nothing about the
    skills this repo actually ships.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from daydag.registry import Registry, RegistryError

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
    """A ``SKILL.md`` whose manifest cannot be trusted to mean what it says.

    A subclass of :class:`~daydag.registry.RegistryError` so a caller that only
    wants to know "did the ownership table load" catches one exception type.
    """


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


def render(registry: Registry, manifests: list[dict[str, Any]]) -> str:
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
    """``python -m daydag.manifests [skills_dir]`` - load, check, print.

    Wired into ``scripts/preflight.sh`` so the one-writer and sensitivity checks
    run against the shipped skills on every push, not only in a unit test.
    """
    argv = sys.argv[1:] if argv is None else argv
    # There are no flags. Without this, `--check` - the reflex from the docs
    # step next to it in preflight - is read as a directory name and reported
    # as a missing skills tree, which sends you looking in the wrong place.
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("usage: python -m daydag.manifests [skills_dir]", file=sys.stderr)
        return 2
    try:
        manifests = read_manifests(argv[0] if argv else None)
        registry = Registry.load(manifests)
    except RegistryError as exc:
        print(f"manifests: {exc}", file=sys.stderr)
        return 1
    print(render(registry, manifests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
