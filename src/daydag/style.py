"""What his feedback taught, as rows he edits in the vault - scoped, dated, checkable.

USING IT
    rules = read_rules(path)                              # DayDAG/Style.md, or the seed
    picked = select(rules, project="revenue domain", today=date.today())
    picked.applied                                        # rows to honour now
    picked.contingent                                     # the ones with a condition - echo them
    picked.lapsed                                         # past their date, or struck through
    findings = check_text(build_js_text, picked.applied)  # forbidden phrases, emoji
    append_rows(path, "revenue domain", ['say "end to end" · · always · sticky r3 · inferred'])

    From the shell:
        python -m daydag.style show --project "revenue domain"
        python -m daydag.style check deck.build.js --project "revenue domain"
        python -m daydag.style add "revenue domain" "<rule> · <why> · <holds> · <source> · inferred"
        python -m daydag.style init                       # the seed, only if the file is absent

CONTRACTS
    1. The rows are HIS. `DayDAG/Style.md` is read on every build and a row he
       edited wins over anything a run inferred. The seed stands in only when
       the file cannot be read or has no `## House` section, and says so.
    2. A row that does not parse is ONE warning line, never a crash and never
       a silent drop (the prep-rules contract, for the same reason).
    3. Scope is the section: `## House` holds rules for all his work, any other
       heading is a project. Lifetime is the `holds` field: `always`, `while
       <fact>`, `until <YYYY-MM-DD>`. The two are independent.
    4. A contingent row is applied WITH its condition echoed, a row past its
       date or struck through (`~~row~~`) is listed as lapsed and not applied.
       Nothing here decides a fact has changed; it says which assumptions have
       an expiry.
    5. Writes are additive. `append_rows` adds under the named section and
       leaves every other byte alone; it refuses when the file changed between
       read and write, rather than clobber an edit of his.
    6. Nothing is fetched and nothing is sent (run.py contract 1).

WHY IT EXISTS
    The revenue domain deck took three sticky rounds. Half the stickies were
    one-offs; the other half were rules - "destination" not "target state",
    line icons not emoji (three times), status in his own words. Those lived in
    memory, which is recalled by relevance, not loaded with the work. A rule
    that is forgotten is left as a sticky twice. Rows in a file every producing
    skill reads at build time cannot be forgotten, and rows with a checkable
    form (`"A", never "B"`) are enforced at render QA rather than remembered.

    Two fields keep the file honest. Some lessons are not evergreen: "company X
    stays out of the pilot tables" is true while it is still being migrated, and
    "numbers due Monday" was never a rule at all but a chase-list item. The
    `holds` field records the condition, so the agent can say before a build
    which of its assumptions can expire, and the file never fills with stale
    facts dressed as standards.

KNOWN LIMIT
    `check_text` sees only two shapes - a quoted phrase after `never`, and the
    word emoji beside never. Every other row is read as prose and honoured by
    judgement. Facts named in a `while` condition are echoed, not verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from daydag.voice import WARN

HOUSE = "house"

#: The house section as shipped. Only the rules that hold for any of his work
#: and name nobody; project rows are added in the vault, never here.
SEED_SECTION = """## House

- say "destination", never "target state" · his word · always · stickies, Oct 2026 · stated
- say "proposal", never "strawman" · something to decide on · always · stickies, Oct 2026 · stated
- line icons in green, never coloured-dot emoji · emoji on web decks · always · 3 stickies · stated
- status is a judgement in own words, on the slide's subject · · always · sticky, Oct 2026 · stated
- one idea per title and per cell; two become heading and subheading · · always · sticky · stated
- an exclusion names its reason where the reader looks for it · · always · sticky, Oct 2026 · stated
- a number still due is a [placeholder] with its date, never a guess · · always · sticky · stated
"""

SEED_FILE = (
    "# Style\n\n"
    "What feedback taught, one rule per row: `rule · why · holds · source · stated|inferred`.\n"
    "`holds` is `always`, `while <fact>` or `until <YYYY-MM-DD>`. `## House` is every piece of\n"
    "work; any other heading is one project. Strike a row (`~~...~~`) to retire it; the agent\n"
    "appends rows marked `inferred` and never edits yours.\n\n" + SEED_SECTION
)

SEEDED_NOTE = "no `## House` in Style.md yet - used the seed house rules"
UNREADABLE_NOTE = "couldn't read Style.md - used the seed house rules"

_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<name>.+?)\s*$")
_BULLET = re.compile(r"^[-*]\s+(?P<body>.+?)\s*$")
_FIELDS = re.compile(r"·")
_HOLDS = re.compile(
    r"^(?:(?P<always>always)|while\s+(?P<fact>.+)|until\s+(?P<date>\d{4}-\d{2}-\d{2}))$"
)
_STANDING = frozenset({"stated", "inferred"})
_STRIKE = re.compile(r"~~")
#: `"A", never "B"` or `never "B"`: B is the phrase to flag.
_NEVER_PHRASE = re.compile(r"never\s+\"(?P<phrase>[^\"]+)\"", re.IGNORECASE)
_NEVER_EMOJI = re.compile(r"\bnever\b.*\bemoji\b|\bemoji\b.*\bnever\b", re.IGNORECASE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿]")


@dataclass(frozen=True)
class Holds:
    """How long a row holds: ``always``, ``while`` a fact, or ``until`` a date."""

    kind: str = "always"
    fact: str = ""
    until: date | None = None

    def label(self) -> str:
        if self.kind == "while":
            return f"while {self.fact}"
        if self.kind == "until":
            return f"until {self.until.isoformat()}"
        return "always"


@dataclass(frozen=True)
class Rule:
    """One row of `DayDAG/Style.md`."""

    section: str
    rule: str
    why: str = ""
    holds: Holds = field(default_factory=Holds)
    source: str = ""
    standing: str = "inferred"
    struck: bool = False
    #: The row as he wrote it, for the reply.
    row: str = ""

    def forbidden_phrases(self) -> tuple[str, ...]:
        """The quoted phrases this rule says never to use, if it has that shape."""
        return tuple(m["phrase"] for m in _NEVER_PHRASE.finditer(self.rule))

    def forbids_emoji(self) -> bool:
        return bool(_NEVER_EMOJI.search(self.rule))


@dataclass(frozen=True)
class Rules:
    rules: tuple[Rule, ...] = ()
    #: One line per row that did not parse (contract 2).
    warnings: tuple[str, ...] = ()
    #: True when the house section was absent and the seed stood in.
    seeded: bool = False
    notes: tuple[str, ...] = ()

    def sections(self) -> tuple[str, ...]:
        seen: list[str] = []
        for r in self.rules:
            if r.section not in seen:
                seen.append(r.section)
        return tuple(seen)


@dataclass(frozen=True)
class Selection:
    """The rows for one build: what to honour, what to echo, what lapsed."""

    applied: tuple[Rule, ...] = ()
    contingent: tuple[Rule, ...] = ()
    lapsed: tuple[Rule, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _parse_row(section: str, body: str) -> Rule | str:
    """A rule, or the reason the row is not one.

    Fields are recognised by shape, not position, so a row missing its `why`
    still parses: the first field is the rule; a field matching `always` /
    `while ...` / `until YYYY-MM-DD` is the lifetime; `stated` or `inferred` is
    the standing; of what is left, the first is the why and the next the source.
    """
    struck = bool(_STRIKE.search(body))
    text = _STRIKE.sub("", body).replace("`", "")
    fields = [f.strip() for f in _FIELDS.split(text)]
    rule = fields[0] if fields else ""
    if not rule:
        return "no rule text before the first `·`"
    holds = Holds()
    holds_seen = False
    standing = "inferred"
    rest: list[str] = []
    for entry in fields[1:]:
        lowered = entry.casefold()
        matched = _HOLDS.match(" ".join(lowered.split()))
        if matched:
            if matched["always"]:
                holds = Holds()
            elif matched["fact"]:
                holds = Holds("while", matched["fact"])
            else:
                try:
                    holds = Holds("until", until=date.fromisoformat(matched["date"]))
                except ValueError:
                    return f"until {matched['date']!r} is not a date"
            holds_seen = True
        elif lowered in _STANDING:
            standing = lowered
        elif lowered.startswith(("while ", "until ")) and not holds_seen:
            return f"holds {entry!r} - use `always`, `while <fact>` or `until YYYY-MM-DD`"
        else:
            rest.append(entry)
    why = rest[0] if rest else ""
    source = rest[1] if len(rest) > 1 else ""
    return Rule(section, rule, why, holds, source, standing, struck, body.strip())


def parse_rules(text: str | None) -> Rules:
    """Every section of ``text`` as rules; the seed house rules when it has none.

    ``None`` means the file could not be read, which also falls back to the
    seed - with a note, because silence would read as "his rules".
    """
    if text is None:
        seed = parse_rules(SEED_SECTION)
        return Rules(seed.rules, seeded=True, notes=(UNREADABLE_NOTE,))
    section: str | None = None
    rules: list[Rule] = []
    warnings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        if heading:
            # the H1 is the file's title, not a scope
            section = None if len(heading["hashes"]) == 1 else heading["name"].casefold()
            continue
        if section is None:
            continue
        bullet = _BULLET.match(stripped)
        if bullet is None:
            continue
        parsed = _parse_row(section, bullet["body"])
        if isinstance(parsed, Rule):
            rules.append(parsed)
        else:
            warnings.append(
                f"{WARN} style rule not understood, ignored: {bullet['body']} ({parsed})"
            )
    if not any(r.section == HOUSE for r in rules):
        seed = parse_rules(SEED_SECTION)
        return Rules(seed.rules + tuple(rules), tuple(warnings), seeded=True, notes=(SEEDED_NOTE,))
    return Rules(tuple(rules), tuple(warnings))


def read_rules(path: str | Path | None) -> Rules:
    """`parse_rules` over the file at ``path``; unreadable degrades to the seed."""
    if path is None:
        return parse_rules(None)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return parse_rules(None)
    return parse_rules(text)


def select(rules: Rules, *, project: str | None = None, today: date | None = None) -> Selection:
    """The house rows plus the named project's rows, split by what to do with each.

    A struck row or one whose ``until`` date is before ``today`` is lapsed. A
    ``while`` or future ``until`` row is applied and also listed as contingent,
    so the build can echo the assumption.
    """
    today = today or date.today()
    wanted = {HOUSE}
    if project:
        wanted.add(project.casefold())
    applied: list[Rule] = []
    contingent: list[Rule] = []
    lapsed: list[Rule] = []
    for r in rules.rules:
        if r.section not in wanted:
            continue
        if r.struck or (r.holds.kind == "until" and r.holds.until < today):
            lapsed.append(r)
            continue
        applied.append(r)
        if r.holds.kind != "always":
            contingent.append(r)
    return Selection(tuple(applied), tuple(contingent), tuple(lapsed), rules.warnings, rules.notes)


def check_text(text: str, rules: tuple[Rule, ...]) -> list[str]:
    """Findings for the rows with a checkable shape, one line each; empty is clean."""
    findings: list[str] = []
    lowered = text.casefold()
    for r in rules:
        for phrase in r.forbidden_phrases():
            if phrase.casefold() in lowered:
                findings.append(f'{WARN} style: "{phrase}" appears (rule: {r.rule})')
        if r.forbids_emoji():
            hit = _EMOJI.search(text)
            if hit:
                findings.append(f"{WARN} style: emoji {hit.group(0)} appears (rule: {r.rule})")
    return findings


def append_rows(path: str | Path, section: str, rows: list[str]) -> str:
    """Add ``rows`` as bullets at the end of ``section``, creating what is missing.

    Returns the text written. The file is created from the seed when absent and
    the section is added at the end when absent. Every other byte is kept.

    Raises:
        RuntimeError: the file changed between the read and the write. Rerun;
            his edit is on disk and this one is not.
    """
    path = Path(path)
    try:
        before = path.read_text(encoding="utf-8")
    except OSError:
        before = SEED_FILE
    lines = before.splitlines(keepends=True)
    name = section.strip().casefold()
    start = end = None
    for i, line in enumerate(lines):
        heading = _HEADING.match(line.strip())
        if heading is None or len(heading["hashes"]) == 1:
            continue
        if start is None and heading["name"].casefold() == name:
            start = i
            continue
        if start is not None:
            end = i
            break
    bullets = [f"- {row.strip()}\n" for row in rows if row.strip()]
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        heading_text = section.strip()
        heading_text = heading_text[0].upper() + heading_text[1:] if heading_text else "House"
        lines += [f"## {heading_text}\n", "\n", *bullets]
    else:
        end = len(lines) if end is None else end
        # insert after the last non-blank line of the section, keeping its trailing blanks
        insert = end
        while insert > start + 1 and not lines[insert - 1].strip():
            insert -= 1
        if insert == start + 1:
            bullets = ["\n", *bullets]
        lines[insert:insert] = bullets
    after = "".join(lines)
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = SEED_FILE
    if current != before:
        raise RuntimeError(f"{path} changed while rows were being added; rerun")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
    return after


def render(selection: Selection) -> str:
    """The selection as the lines a build summary shows, in the house voice."""
    out: list[str] = []
    for note in selection.notes:
        out.append(f"{WARN} {note}")
    by_section: dict[str, list[Rule]] = {}
    for r in selection.applied:
        by_section.setdefault(r.section, []).append(r)
    for section, rows in by_section.items():
        out.append(f"{section} rules ({len(rows)})")
        for r in rows:
            tag = "" if r.holds.kind == "always" else f" - {r.holds.label()}"
            mark = "" if r.standing == "stated" else " (inferred)"
            out.append(f"- {r.rule}{tag}{mark}")
    if selection.lapsed:
        out.append(f"lapsed ({len(selection.lapsed)}) - not applied")
        for r in selection.lapsed:
            why = "struck" if r.struck else r.holds.label()
            out.append(f"- {r.rule} ({why})")
    out.extend(selection.warnings)
    return "\n".join(out)


def _default_path() -> Path | None:
    """`DayDAG/Style.md` under `VAULT_ROOT` from `.env`, or None when unset."""
    from daydag.config import Identities
    from daydag.recipes import RecipeError, vault_root

    try:
        return vault_root(Identities.from_file(Path(".env"))) / "DayDAG" / "Style.md"
    except (OSError, RecipeError, KeyError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """`show` and `check` read; `add` appends one inferred-or-stated row; `init` seeds."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: python -m daydag.style show [--project P] [--today YYYY-MM-DD] [--file PATH]\n"
        "       python -m daydag.style check BUILD_FILE [--project P] [--file PATH]\n"
        "       python -m daydag.style add SECTION ROW [--file PATH]\n"
        "       python -m daydag.style init [--file PATH]"
    )
    if not args or args[0] not in {"show", "check", "add", "init"}:
        print(usage)
        return 2

    def opt(name: str) -> str | None:
        if name not in args:
            return None
        after = args[args.index(name) + 1 :]
        if not after or after[0].startswith("--"):
            raise SystemExit(f"{name} needs a value")
        return after[0]

    file_opt = opt("--file")
    path = Path(file_opt) if file_opt else _default_path()
    if path is None:
        print(f"{WARN} no --file and VAULT_ROOT is not set in .env")
        return 2
    project = opt("--project")
    today_opt = opt("--today")
    today = date.fromisoformat(today_opt) if today_opt else date.today()
    command = args[0]

    if command == "init":
        if path.exists():
            print(f"{path} exists; left alone")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SEED_FILE, encoding="utf-8")
        print(f"wrote the seed to {path}")
        return 0

    if command == "add":
        positional = [a for a in args[1:] if not a.startswith("--")]
        flag_values = {v for v in (file_opt, project, today_opt) if v}
        positional = [a for a in positional if a not in flag_values]
        if len(positional) < 2:
            print(usage)
            return 2
        section, row = positional[0], positional[1]
        parsed = _parse_row(section.casefold(), row)
        if not isinstance(parsed, Rule):
            print(f"{WARN} row not understood, nothing written: {row} ({parsed})")
            return 1
        append_rows(path, section, [row])
        print(f"added to {section}: {row}")
        return 0

    selection = select(read_rules(path), project=project, today=today)
    if command == "show":
        print(render(selection))
        return 0

    positional = [
        a for a in args[1:] if not a.startswith("--") and a not in {file_opt, project, today_opt}
    ]
    if not positional:
        print(usage)
        return 2
    try:
        text = Path(positional[0]).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{WARN} couldn't read {positional[0]}: {exc}")
        return 2
    findings = check_text(text, selection.applied)
    for line in findings:
        print(line)
    for r in selection.contingent:
        print(f"applying: {r.rule} - {r.holds.label()}")
    if not findings:
        print(f"style ok - {len(selection.applied)} rules, none broken")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
