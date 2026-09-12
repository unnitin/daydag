"""The USING IT examples have to be callable, not merely plausible.

CONTRIBUTING says a claim in a docstring is a claim under test. The first pass
at this convention checked that every name in a USING IT block *existed* -
`hasattr(module, name)` - which proves nothing about whether the line would
run. Three examples passed that check and were wrong:

* `resolve_reference("${VAR_NAME}", ids)` - `what` and `error` are required
  keyword-only arguments, so the line raises `TypeError` as written.
* `Sources(calendar=..., ...)` - `Sources` is a `Protocol`, and protocols
  cannot be instantiated.
* `ids["MISSING"]  # KeyError if unset` - it raises `ConfigError`, which is
  not a `KeyError` subclass, so `except KeyError:` around it catches nothing.

So this binds each example against the real signature instead. It does not
execute anything: an example names ids and paths that do not exist here, and a
docstring is not a fixture. Binding catches the arity and keyword errors,
which is the half that was actually wrong.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from typing import Protocol, get_origin

import pytest

import daydag

#: Names used as stand-ins inside an example rather than as real arguments.
_PLACEHOLDERS = {"...", "Ellipsis"}


def _modules() -> list[str]:
    return sorted(
        name for _, name, _ in pkgutil.iter_modules(daydag.__path__) if not name.startswith("_")
    )


def _using_it(doc: str) -> str:
    """The USING IT block, or ``""`` for a module that has no convention yet."""
    if "USING IT" not in doc:
        return ""
    after = doc.split("USING IT", 1)[1]
    # The block ends at the next all-caps section heading in column zero.
    for heading in ("CONTRACTS", "WHY IT EXISTS", "KNOWN LIMIT"):
        after = after.split(f"\n{heading}", 1)[0]
    return after


def _calls(block: str) -> list[ast.Call]:
    """Every call expression in an example block, parsed one line at a time.

    Line by line because a block is prose plus code, and one unparseable line
    (a comment continuation, a bare attribute) must not hide the rest.
    """
    found: list[ast.Call] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("  #", 1)[0].strip()
        # Strip a leading `name = ` binding, but ONLY when the `=` comes before
        # the first `(`. Splitting on the first `=` anywhere butchers every
        # line that merely passes a keyword argument - which silently skipped
        # three wrong examples on this check's first run.
        head, sep, tail = line.partition("=")
        if sep and "(" not in head and "=" not in tail[:1]:
            line = tail.strip()
        try:
            tree = ast.parse(line, mode="eval")
        except SyntaxError:
            continue
        found.extend(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    return found


def _root_name(call: ast.Call) -> str | None:
    node = call.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return None  # a method on a local, whose type we cannot resolve here
    return None


@pytest.mark.parametrize("module_name", _modules())
def test_every_using_it_example_binds_to_the_real_signature(module_name: str):
    """A USING IT line must at least be callable as written."""
    module = importlib.import_module(f"daydag.{module_name}")
    block = _using_it(inspect.getdoc(module) or "")
    if not block:
        pytest.skip(f"daydag.{module_name} has no USING IT block")

    problems: list[str] = []
    for call in _calls(block):
        name = _root_name(call)
        if name is None:
            continue
        target = getattr(module, name, None)
        if target is None or not callable(target):
            continue

        if isinstance(target, type) and Protocol in getattr(target, "__mro__", ()):
            problems.append(f"{name}() - Protocols cannot be instantiated")
            continue
        if get_origin(target) is not None:
            continue

        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            continue

        args = [inspect.Parameter.empty] * len(call.args)
        kwargs = {kw.arg: inspect.Parameter.empty for kw in call.keywords if kw.arg is not None}
        try:
            signature.bind(*args, **kwargs)
        except TypeError as bad:
            problems.append(f"{name}(): {bad}")

    assert not problems, (
        f"daydag.{module_name} USING IT does not run as written:\n  " + "\n  ".join(problems)
    )
