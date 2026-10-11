"""What his feedback taught is rows he edits, with a scope and a lifetime.

The revenue domain deck took three sticky rounds (Oct 2026), and half the
stickies were rules he had already given once. These tests pin that a rule is
a ROW in the vault rather than a memory: adding a row changes what a build
honours, a bad row is one line rather than a crash, a row past its date or
struck through stops applying, and a row with a checkable shape is enforced
over the build text rather than remembered.
"""

from __future__ import annotations

from datetime import date

import pytest

from daydag import style
from daydag.vault import NotMaterialisedError, VaultNote
from daydag.voice import WARN

TODAY = date(2026, 10, 11)

FILE = """# Style

Intro prose he may leave or delete.

## House

- say "destination", never "target state" · his word · always · sticky 10 Oct · stated
- line icons in green, never coloured-dot emoji · emoji on web decks · always · 3 stickies · stated
- no why on this one · always · stated

## Revenue domain

- company X stays out of the pilot tables · · while company X is in migration wave 1 · r2 · stated
- `[Mon]` cells wait for the numbers · · until 2026-10-12 · thread 9 Oct · inferred
- ~~say "strawman"~~ · he said proposal · always · sticky r1 · stated
- an old deadline · · until 2026-10-01 · · inferred

## Other project

- something else entirely · · always · · stated
"""


def test_rows_parse_by_shape_not_position():
    rules = style.parse_rules(FILE)
    assert not rules.warnings and not rules.seeded
    house = [r for r in rules.rules if r.section == "house"]
    assert [r.rule for r in house] == [
        'say "destination", never "target state"',
        "line icons in green, never coloured-dot emoji",
        "no why on this one",
    ]
    assert house[0].why == "his word" and house[0].source == "sticky 10 Oct"
    assert house[0].standing == "stated" and house[0].holds.kind == "always"
    # a row with no why still parses, and is not read as having a why of "always"
    assert house[2].why == "" and house[2].holds.kind == "always"
    revenue = [r for r in rules.rules if r.section == "revenue domain"]
    assert revenue[0].holds == style.Holds("while", "company X is in migration wave 1")
    assert revenue[1].holds == style.Holds("until", until=date(2026, 10, 12))
    assert revenue[1].standing == "inferred"
    assert revenue[2].struck and revenue[2].rule == 'say "strawman"'


def test_a_bad_row_is_one_warning_never_a_crash():
    text = FILE + "\n- a rule · until next tuesday · stated\n- while nothing before the rule\n"
    rules = style.parse_rules(text)
    bad = [w for w in rules.warnings if "not understood" in w]
    assert len(bad) == 1 and WARN in bad[0] and "next tuesday" in bad[0]
    # the second row IS a rule ("while nothing before the rule" is its text) - only
    # a field after the first `·` is read as a lifetime
    assert any(r.rule == "while nothing before the rule" for r in rules.rules)
    assert len(rules.rules) == len(style.parse_rules(FILE).rules) + 1


def test_the_seed_stands_in_when_the_house_section_is_absent():
    rules = style.parse_rules(
        "# Style\n\n## Revenue domain\n\n- only a project row · · always · · stated\n"
    )
    assert rules.seeded and rules.notes == (style.SEEDED_NOTE,)
    assert any(r.section == "house" for r in rules.rules)
    assert any(r.rule == "only a project row" for r in rules.rules)
    unreadable = style.read_rules("/nonexistent/Style.md")
    assert unreadable.seeded and unreadable.notes == (style.UNREADABLE_NOTE,)


def test_the_seed_itself_parses_clean():
    rules = style.parse_rules(style.SEED_SECTION)
    assert not rules.warnings and not rules.seeded
    assert all(r.standing == "stated" and r.holds.kind == "always" for r in rules.rules)
    whole = style.parse_rules(style.SEED_FILE)
    assert len(whole.rules) == len(rules.rules) and not whole.warnings


def test_select_scopes_by_project_and_sorts_by_lifetime():
    picked = style.select(style.parse_rules(FILE), project="Revenue domain", today=TODAY)
    applied = {r.rule for r in picked.applied}
    assert 'say "destination", never "target state"' in applied
    assert "company X stays out of the pilot tables" in applied
    assert "something else entirely" not in applied, "another project's rows stay out"
    assert [r.rule for r in picked.contingent] == [
        "company X stays out of the pilot tables",
        "[Mon] cells wait for the numbers",
    ]
    lapsed = {r.rule for r in picked.lapsed}
    assert lapsed == {'say "strawman"', "an old deadline"}
    assert not (lapsed & applied)
    # the day after the placeholder date, that row lapses too
    later = style.select(
        style.parse_rules(FILE), project="revenue domain", today=date(2026, 10, 13)
    )
    assert "[Mon] cells wait for the numbers" in {r.rule for r in later.lapsed}
    # with no project, only house rows
    house_only = style.select(style.parse_rules(FILE), today=TODAY)
    assert {r.section for r in house_only.applied} == {"house"}


def test_check_text_enforces_the_checkable_rows():
    picked = style.select(style.parse_rules(FILE), project="revenue domain", today=TODAY)
    clean = "title: 'the destination for revenue', icons: ['FiFlag']"
    assert style.check_text(clean, picked.applied) == []
    dirty = "title: 'Target State of the platform 🟢'"
    findings = style.check_text(dirty, picked.applied)
    assert len(findings) == 2
    assert any('"target state" appears' in f for f in findings), "case-insensitive"
    assert any("emoji 🟢 appears" in f for f in findings)
    # a lapsed rule is not enforced
    assert style.check_text("a strawman", picked.applied) == []


def test_append_rows_adds_under_the_section_and_keeps_every_other_byte(tmp_path):
    path = tmp_path / "Style.md"
    path.write_text(FILE, encoding="utf-8")
    row = 'say "end to end" explicitly · · always · sticky r3 · inferred'
    after = style.append_rows(path, "Revenue domain", [row])
    assert path.read_text(encoding="utf-8") == after
    assert after.count(row) == 1
    # it lands inside the section, before the next heading
    section = after.split("## Revenue domain", 1)[1].split("## Other project", 1)[0]
    assert row in section and section.rstrip().endswith(row)
    # everything else is byte-identical
    assert after.replace(f"- {row}\n", "") == FILE
    rules = style.parse_rules(after)
    assert any(
        r.rule == 'say "end to end" explicitly' and r.standing == "inferred" for r in rules.rules
    )


def test_append_rows_creates_the_section_and_the_file_when_absent(tmp_path):
    path = tmp_path / "Style.md"
    after = style.append_rows(path, "new deck", ["a first row · · always · · inferred"])
    assert after.startswith(style.SEED_FILE.rstrip("\n"))
    assert "## New deck\n" in after
    rules = style.parse_rules(after)
    assert not rules.seeded, "the seed house section was written, so it is no longer a fallback"
    assert any(r.section == "new deck" for r in rules.rules)
    # a second append to the new section goes under it, not into a second heading
    style.append_rows(path, "new deck", ["a second row · · always · · inferred"])
    text = path.read_text(encoding="utf-8")
    assert text.count("## New deck") == 1
    assert text.index("a first row") < text.index("a second row")


def test_render_echoes_conditions_and_lists_lapsed():
    picked = style.select(style.parse_rules(FILE), project="revenue domain", today=TODAY)
    text = style.render(picked)
    assert "house rules (3)" in text and "revenue domain rules (2)" in text
    assert (
        "- company X stays out of the pilot tables - while company X is in migration wave 1" in text
    )
    assert "- [Mon] cells wait for the numbers - until 2026-10-12 (inferred)" in text
    assert "lapsed (2) - not applied" in text
    assert '- say "strawman" (struck)' in text
    assert "an old deadline (until 2026-10-01)" in text


def test_the_cli_shows_checks_and_adds(tmp_path, capsys):
    path = tmp_path / "Style.md"
    assert style.main(["init", "--file", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == style.SEED_FILE
    assert style.main(["init", "--file", str(path)]) == 0, "a second init leaves the file alone"
    assert (
        style.main(
            [
                "add",
                "revenue domain",
                'say "end to end" · · always · r3 · inferred',
                "--file",
                str(path),
            ]
        )
        == 0
    )
    assert (
        style.main(
            ["add", "revenue domain", "bad · until whenever · inferred", "--file", str(path)]
        )
        == 1
    )
    build = tmp_path / "deck.build.js"
    build.write_text("title: 'the target state'", encoding="utf-8")
    assert (
        style.main(["check", str(build), "--project", "revenue domain", "--file", str(path)]) == 1
    )
    out = capsys.readouterr().out
    assert '"target state" appears' in out
    build.write_text("title: 'the destination'", encoding="utf-8")
    assert (
        style.main(["check", str(build), "--project", "revenue domain", "--file", str(path)]) == 0
    )
    assert (
        style.main(
            ["show", "--project", "revenue domain", "--today", "2026-10-11", "--file", str(path)]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "revenue domain rules (1)" in out and "end to end" in out
    assert style.main([]) == 2


def test_append_rows_refuses_to_clobber_a_concurrent_edit(tmp_path, monkeypatch):
    """The snapshot is the compare-and-swap token: his edit between the read and
    the write wins, and the agent's row is not written over it."""
    path = tmp_path / "Style.md"
    path.write_text(FILE, encoding="utf-8")
    real_read = VaultNote.read

    def read_then_he_edits(self):
        snapshot = real_read(self)
        path.write_text(
            path.read_text(encoding="utf-8") + "- his new row · · always · · stated\n",
            encoding="utf-8",
        )
        return snapshot

    monkeypatch.setattr(VaultNote, "read", read_then_he_edits)
    with pytest.raises(RuntimeError, match="changed while rows were being added"):
        style.append_rows(path, "house", ["mine · · always · · inferred"])
    text = path.read_text(encoding="utf-8")
    assert "his new row" in text and "mine" not in text


def test_append_rows_refuses_an_icloud_placeholder(tmp_path):
    """An evicted file is a stub, not his rules; writing stub plus one row over
    the real file is the loss `vault.NotMaterialisedError` exists to prevent."""
    (tmp_path / ".Style.md.icloud").write_bytes(b"placeholder")
    with pytest.raises(NotMaterialisedError):
        style.append_rows(tmp_path / "Style.md", "house", ["a row · · always · · inferred"])
    assert not (tmp_path / "Style.md").exists()


def test_append_rows_writes_atomically_through_the_vault(tmp_path, monkeypatch):
    from daydag import vault

    path = tmp_path / "Style.md"
    path.write_text(FILE, encoding="utf-8")
    seen: list[str] = []
    real = vault._atomic_write

    def spy(p, data):
        seen.append(str(p))
        return real(p, data)

    monkeypatch.setattr(vault, "_atomic_write", spy)
    style.append_rows(path, "house", ["a row · · always · · inferred"])
    assert seen == [str(path)], "the write did not go through the vault's atomic replace"


def test_a_condition_keeps_his_casing_and_matches_any_casing_of_the_keyword():
    rules = style.parse_rules("## House\n\n- r · · WHILE Company X migrates · · stated\n")
    (rule,) = rules.rules
    assert rule.holds == style.Holds("while", "Company X migrates")
    always = style.parse_rules("## House\n\n- r · · Always · · stated\n").rules[0]
    assert always.holds.kind == "always"


def test_the_house_glyphs_are_not_emoji_but_a_coloured_dot_is():
    rules = style.parse_rules("## House\n\n- never emoji on slides · · always · · stated\n")
    assert style.check_text("status: ✓ live ⚠ watch ⭐", rules.rules) == []
    assert len(style.check_text("status: 🟢 live", rules.rules)) == 1


def test_the_cli_takes_flags_anywhere_and_refuses_a_bad_date(tmp_path, capsys):
    path = tmp_path / "Style.md"
    row = 'say "end to end" · · always · r3 · inferred'
    # the natural habit after `show --project X`: the section repeats the flag's value
    assert (
        style.main(
            ["add", "revenue domain", row, "--project", "revenue domain", "--file", str(path)]
        )
        == 0
    )
    assert row in path.read_text(encoding="utf-8")
    assert style.main(["--file", str(path), "show", "--project", "revenue domain"]) == 0
    assert "end to end" in capsys.readouterr().out
    assert style.main(["show", "--today", "10/11/2026", "--file", str(path)]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().out
