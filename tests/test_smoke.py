"""Smoke test: does the package stand up at all?

Deliberately shallow and fast. Every other suite tests behaviour; this one tests
that there IS behaviour to test - that every module imports, the public surface
exists, and nothing in the package explodes on load.

It earns its place because the failures it catches are the ones that make every
other test misleading rather than red: a module that only imports because a
stale editable install is pointing somewhere else, a circular import that
resolves in one order and not another, a `__init__` that silently exports
nothing. Those show up here as a plain failure instead of as forty confusing
ones.

Kept free of fixtures, temp dirs and network on purpose: if this fails, the
problem is the package, not the environment.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import daydag

#: Every module that must import cleanly. Enumerated rather than discovered, so
#: a module deleted by accident fails here rather than quietly leaving the set.
EXPECTED_MODULES = (
    "daydag.config",
    "daydag.ledger",
    "daydag.pulse",
    "daydag.recipes",
    "daydag.registry",
    "daydag.state",
    "daydag.vault",
    "daydag.voice",
)


def test_the_package_imports():
    assert daydag.__name__ == "daydag"


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_every_module_imports(name):
    """A module that cannot be imported makes its whole suite an error, not a
    failure - which reads as 'something is broken' rather than naming what."""
    assert importlib.import_module(name) is not None


def test_no_module_was_added_without_being_listed_here():
    """The enumeration above must not drift from what actually ships.

    A new module absent from this list is a module nobody smoke-tests.
    """
    found = {
        f"daydag.{info.name}"
        for info in pkgutil.iter_modules(daydag.__path__)
        if not info.name.startswith("_")
    }
    assert found == set(EXPECTED_MODULES), (
        f"module list is stale: only in package {sorted(found - set(EXPECTED_MODULES))}, "
        f"only in list {sorted(set(EXPECTED_MODULES) - found)}"
    )


def test_the_package_is_imported_from_this_checkout():
    """Guards the trap that wasted an afternoon.

    An editable install points at ONE source tree. Work done in a git worktree
    imports the primary checkout's modules instead of its own, so a brand-new
    module is not importable and a changed one tests the wrong code - silently,
    and identically to the module simply not existing.
    """
    from pathlib import Path

    package = Path(daydag.__file__).resolve().parent
    expected = (Path(__file__).resolve().parent.parent / "src" / "daydag").resolve()
    assert package == expected, (
        f"daydag imported from {package}, not this checkout's {expected} - "
        "an editable install is pointing at another tree"
    )


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("daydag.recipes", "gmail_gemini_notes"),
        ("daydag.registry", "Registry"),
        ("daydag.state", "StateFolder"),
        ("daydag.state", "EventLog"),
        ("daydag.ledger", "Ledger"),
        ("daydag.pulse", "Pulse"),
        ("daydag.voice", "render"),
        ("daydag.vault", "Vault"),
    ],
)
def test_the_public_surface_exists(module, symbol):
    """One load-bearing name per module. Not a behaviour test - a wiring test."""
    assert hasattr(importlib.import_module(module), symbol), (
        f"{module}.{symbol} is gone; something that imports it will fail far from here"
    )
