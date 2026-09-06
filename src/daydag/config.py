"""Identity configuration.

Real Slack/Google/Notion/Databricks identifiers live in a gitignored `.env`
and nowhere else - this repo is public. `.env.example` is the committed schema.
Docs and code refer to values as ``${VAR_NAME}``; this module resolves them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path

_REFERENCE = re.compile(r"\$\{([A-Z0-9_]+)\}")


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
