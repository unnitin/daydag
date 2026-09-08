"""Identity configuration: every real identifier, resolved from a local .env.

USING IT
    ids = Identities.from_file(Path(".env"))
    ids["SLACK_USER_PRINCIPAL"]                 # KeyError if unset
    resolve_reference("${VAR_NAME}", ids)       # -> the value, or KeyError
    resolve_reference("C0ALREADY", ids)         # -> unchanged, not a reference
    timezone_for(ids)                           # ZoneInfo, TIMEZONE or the default

CONTRACTS
    1. This repo is PUBLIC. No real identifier is committed - not in code, not
       in docs, not in a test fixture. `.env` is gitignored; `.env.example` is
       the committed schema and carries placeholders only.
    2. Code and docs name a value as `${VAR_NAME}` and resolve it here. A raw
       id in the tree is blocked by `scripts/scan_secrets.py` on every commit.
    3. A missing reference RAISES rather than resolving to empty. An empty
       Slack id does not fail loudly, it silently matches nothing - which is
       the failure this module exists to convert into a stack trace.

WHY IT EXISTS
    The key NAMES are part of the secret too: `SLACK_USER_JANE_DOE` leaks a
    colleague even when the id beside it is a placeholder. So keys are named
    for the role or function they serve, never the person or codename behind
    them. `.env.example`'s header carries the full rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REFERENCE = re.compile(r"\$\{([A-Z0-9_]+)\}")

#: A value that is *entirely* one reference, e.g. "${SLACK_USER_VP_DATA}".
_WHOLE_REFERENCE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")

#: The principal's timezone. Configuration, not a constant: every wall-clock
#: boundary in the system is his local one, and hardcoding a zone in a module
#: means the agent works for exactly one person. This is the FALLBACK; the
#: configured value is read by `timezone_for()` below.
DEFAULT_TIMEZONE = "America/Los_Angeles"


def timezone_for(identities: Mapping[str, str] | None = None) -> ZoneInfo:
    """The principal's timezone, from ``TIMEZONE`` in .env, else the fallback.

    An earlier version of this module claimed the value was "read from TIMEZONE
    in .env" while nothing read it - `TIMEZONE=Europe/London` silently produced
    Pacific day boundaries. The comment was the whole feature. This is it made
    true; an unknown zone name is refused rather than silently falling back,
    because a typo would otherwise present as correct-looking wrong times.
    """
    if identities is None:
        return ZoneInfo(DEFAULT_TIMEZONE)
    name = identities.get("TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"TIMEZONE={name.strip()!r} is not a known IANA zone; see .env.example."
        ) from exc


def resolve_reference(
    value: str, identities: Mapping[str, str] | None, *, what: str, error: type[Exception]
) -> str:
    """Expand a lone ``${VAR}``, or refuse it.

    Docs and code carry identifiers as ``${VAR}`` because the repo is public, so
    a reference arriving here is normal. Passing one *through* is not: as literal
    text it is a syntactically valid query that matches nothing, which is
    indistinguishable from a genuinely empty result.

    Lives here rather than beside its caller because this is where identifiers
    are resolved - a second copy elsewhere was already carrying its own regex.
    """
    reference = _WHOLE_REFERENCE.match(value.strip())
    if not reference:
        return value.strip()
    key = reference.group(1)
    if identities is None:
        raise error(
            f"{what} is the unresolved reference ${{{key}}}. "
            "Pass identities= so it can be expanded; a literal ${...} matches nothing."
        )
    try:
        found = identities[key]
    except (KeyError, ConfigError) as exc:
        raise error(f"{key} is not set. Add it to .env; see .env.example.") from exc
    if not isinstance(found, str):
        # `Mapping[str, str]` is an annotation, not a runtime guard, and this is
        # the one function whose job is turning a bad identity into the CALLER's
        # error. A mapping built from `os.environ.get(...)` - which returns None
        # when unset, and is the obvious way to build one without
        # `Identities.from_file` - reached `.strip()` and raised AttributeError:
        # exactly the bare lookup error three frames up that `error=` exists to
        # prevent.
        raise error(f"{key} is set to {type(found).__name__}, not a string. Check .env.")
    return found.strip()


class ConfigError(RuntimeError):
    """Raised when a required identifier is missing or unresolvable.

    Never carries a resolved value in its message - an error that echoes the ID
    defeats the point of holding it outside the repo.
    """


class Identities(Mapping[str, str]):
    """Read-only view over the identifier map."""

    def __init__(self, values: dict[str, str], source: Path) -> None:
        self._values = values
        self._source = source

    @classmethod
    def from_file(cls, path: str | Path) -> Identities:
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"no identity file at {path}. Copy .env.example to .env and fill it in."
            )
        values: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        return cls(values, path)

    def __getitem__(self, key: str) -> str:
        try:
            return self._values[key]
        except KeyError:
            raise ConfigError(
                f"{key} is not set in {self._source.name}. Add it there; see .env.example."
            ) from None

    def __contains__(self, key: object) -> bool:
        # Mapping's default routes through __getitem__ and would surface a
        # ConfigError instead of False, breaking `key in ids`.
        return key in self._values

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def expand(self, text: str) -> str:
        """Replace every ``${VAR}`` with its value, raising on an unknown name."""
        return _REFERENCE.sub(lambda m: self[m.group(1)], text)
