"""The ownership table, made executable.

ARCHITECTURE states one writer per artifact as invariant 1, and notes that an
invariant enforced by documentation has a half-life: the next skill gets
written by someone who never read the table. This module turns the table into
startup checks, so a second writer is a loud failure rather than a corrupted
file discovered on a Friday.

A manifest is the ``daydag:`` block of a skill's ``SKILL.md`` frontmatter:

    daydag:
      writes:       [vault:Fact Base/Workstreams.md]
      consumes:     [evidence.pulse]
      emits:        [evidence.planning]
      schedule:     "fri 13:00"
      sensitivity:  private
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Surfaces a `sensitivity: private` skill may reach. Everything else has an
# audience above one, which SPEC section 10.1 forbids for personnel content.
PRIVATE_SURFACES = frozenset({"slack:dm-nitin", "local:event-log", "drive:private"})


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
