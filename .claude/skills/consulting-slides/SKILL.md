---
name: consulting-slides
description: "Build or rewrite a slide deck the way BCG, Bain and McKinsey do it: answer first, action titles that read as the argument, one exhibit per slide, sources and page numbers everywhere, green-on-grey Trebuchet look matched to Nitin's slide templates, delivered as a .pptx. Use this whenever Nitin asks for a deck, slides, a presentation, a one-pager for the sponsor or department leadership, a storyline, an executive summary, a ghost deck, a roadmap or timeline slide, or asks to make existing slides 'more BCG', 'more consulting', 'answer first' or 'less verbose', even if he does not say PowerPoint. Also use it to check a deck's look against the house standards; an adversarial review of the argument and content is slide-review."
daydag:
  # Not orchestrated by DayDAG: invoked by hand. Decks go where the user points it
  # (`${DECKS_DIR}/<project>/`, both from `.env`). The one vault write is
  # the style rules his feedback teaches, appended as rows he can edit (#196).
  writes:
    - vault:DayDAG/Style.md
  reads: [obsidian]
  sensitivity: shared
---

# Consulting-style slides

A deck is an argument. The titles carry it, the bodies prove it, and everything sits in the
same place on every page. This skill turns a brief plus source material into that kind of
deck, in the house look (green on grey, Trebuchet MS, matching the three template decks in
`${SLIDE_TEMPLATES_DIR}` from `.env`), as a `.pptx` the reader can edit.

Read the reference that matches the step you are on; they are short:

| Step | Read |
|---|---|
| Story, titles, executive summary, next steps | `references/storyline.md` |
| Where things go on a slide, type, colour, numbers | `references/slide-anatomy.md` |
| Which chart or table proves which message | `references/charts.md` |
| Which layout for which message, and the template slide to copy | `references/pattern-library.md` |
| Generating, rendering, checking and delivering the file | `references/build.md` |
| Working from his deck, reading his stickies, promoting dated versions, what each round keeps | `references/feedback-loop.md` |

## The process

0. **Read what he already made.** If a working deck exists, its seed slides and yellow
   stickies are the brief: `python3 scripts/stickies.py <deck>.pptx --all`. "Look at the
   deck and have a draft" means build the slides those seeds describe, in a file beside
   his, never a whole deck and never into his open file. `references/feedback-loop.md`.

1. **Pin the brief.** Audience, occasion (live meeting or pre-read), the decision or
   outcome the deck exists for, length in minutes, and the source material. If the
   source is a page or document Nitin already wrote, it is the content: condense it, do
   not re-research it. Ask only what the brief cannot be inferred from. Check the live
   sources (Slack, Gmail notes, Notion) for what moved since the page was written, and
   bracket anything still pending as a placeholder with its arrival date (`[Mon]`).
   Then read what his feedback has already taught, before deciding anything about words
   or look: `.venv/bin/python -m daydag.style show --project "<project>"` prints the house rules and
   the project's, with the ones that carry a condition marked. Those rules outrank this
   file's defaults. Name the project the way its section in `DayDAG/Style.md` is named.

2. **Write the storyline before any slide.** Governing thought in one sentence, SCQA
   opening, 3 to 5 MECE key lines, then the dot-dash outline. Share the title sequence
   in chat and read it top to bottom: it must make sense alone. Nitin iterates on the
   skeleton first, so stop here and get a reaction before building unless he has already
   approved the structure or asked you to run straight through.

3. **Pick a pattern per slide from the message, not the data.** One message, one exhibit.
   A title with "and" is two slides. Text slides still get structure (columns, cards, a
   numbered list); never prose.

4. **Build a look sample first for a new deck** (cover, executive summary, one dense
   slide), render it, show the pages, then fan out. Changing the look after 25 slides
   exist is the expensive mistake.

5. **Build with `scripts/bcg_style.js`** (see `build.md`): one `build.js` next to the
   output, one call per slide in deck order, then render with `scripts/render.sh` and look
   at every page. Fix overflow, collisions and drifting titles before showing anything.
   Revisions change only the slides named; the build script is the source, so edit it and
   rebuild rather than patching the file. A flow that needs explaining gets one worked
   example built click by click: name each step's shapes in the script and run
   `scripts/animate.py` after every rebuild (see `build.md`). Before any handover run
   `.venv/bin/python -m daydag.style check <build.js> --project "<project>"`: it flags the phrases
   and emoji his rules forbid, and echoes every rule applied on a condition, so the reply
   can say which assumptions have an expiry.

6. **Review, then deliver. Every build gets a review**: the first build, every sticky
   round, every revision however small. After the render and the style check, hand the
   deck to `slide-review` in a fresh agent (Agent tool, general-purpose) with this brief
   and nothing else. Never pass the storyline or your reasoning: a reviewer who knows what
   a slide was meant to say reads that meaning into it.

   ```
   Read .claude/skills/slide-review/SKILL.md and follow it to review <deck>.pptx.
   Renders: <deck>-pages/ (use these, do not re-render).
   Brief: audience <role>, <live meeting | pre-read>, decision <what it is for>,
   Style.md section <name>.
   Scope: <whole deck | slides N, M changed this round, plus the titles-only pass>.
   You did not build this deck. Return the step 8 report.
   ```

   The scope is the whole deck for a new deck or a round that changed titles or the
   order. Otherwise it is the slides this round touched, plus the titles-only pass, so a
   one-slide fix is still checked against the argument around it. Act on the report:
   - **Blockers**: fix before delivering. If a fix needs his call (a target, an owner, a
     decision), it becomes a bracketed placeholder and a question in the reply.
   - **Majors**: fix the ones in your hands; list the rest.
   - **Minors**: fix the cheap ones.
   - **Conflicts with him**: a finding that contradicts one of his stickies, his retyped
     wording or a `Style.md` row loses. His word wins; say so in the reply.

   Before rebuilding with the fixes, run `scripts/drift.py` against his current file again:
   the review takes minutes, and anything he retyped meanwhile goes into the script first
   (his heading edits were lost this way on 25 Oct). Then rebuild and re-render. Send the
   changed slides back to the same reviewer (SendMessage) for one recheck, not a new full
   review; at most two review passes per round.

   **Deliver** five things:
   - the `.pptx` path;
   - the rendered pages for what changed;
   - the review's verdict in one line, with what was fixed and what was left and why;
   - two or three things to check, by page number;
   - what is unverified or estimated.

   A delivery without the review line means this step was skipped.

7. **Iterate through stickies.** He answers with yellow text boxes on the slides, and by
   retyping headings in place. Each round: back up his version, list the stickies, run
   `scripts/drift.py` against your last build to catch what he retyped, carry both into
   the script, rebuild, render, look at every page, review (step 6), reply sticky by
   sticky. When he says
   "promote", `scripts/promote.sh` turns the draft into a dated file. Neither script
   deletes anything: render output lives in one marked folder, and an old one is moved
   aside with its path printed. Where a sticky asks for alternatives, build each as its
   own slide marked option A and B.

8. **Write through what the round taught.** Sort each sticky into a one-off, which dies
   with the round, or a rule. A rule is appended as a row he can edit:
   `.venv/bin/python -m daydag.style add "<section>" "<rule> · <why> · <holds> · <source> · inferred"`,
   where the section is `house` for anything that holds across his work and the project's
   name for the rest, and `holds` is `always`, `while <fact>` or `until <date>`. A sticky
   that repeats a rule already in the file is a sign the build skipped step 1, not a new
   row. Never store a one-off or a due date as a rule; due dates are chase-list items.

## House rules that override generic advice

- Answer first, everywhere: the executive summary states the call; the last body slide
  is decisions and next steps with owners and dates. Nitin's default shape for a change
  proposal is situation, impact, solution, improvement, next steps, appendix.
- Action titles only, in one grammar, at most two lines. Shorten rather than shrink.
- Quote verbatim and cite: a source line on every slide with data, naming the system,
  period and who produced it. Mark estimates and strawmen on the slide.
- Individual contributors appear by role; leaders by name. Personnel detail never goes
  on a slide for a wider audience.
- Hyphens, never em dashes. Plain words; no "leverage", no "synergy", no hedging.
- His vocabulary wins and persists: "destination" over "target state", "proposal" over
  "strawman", "end to end" said outright. Keep each deck's word list in memory.
- One idea per title and per cell; a title that carries two becomes heading plus
  subheading. Status is a judgement in your own words with its implication, never a
  pasted standup line, and it stays on the slide's subject (revenue on a revenue slide).
- Exclusions name their reason where the reader looks for the excluded thing; items not
  in play yet get a line saying when they join.
- In the house look, small icons are thin-line glyphs in green from the library's icon
  helper, never coloured-dot emoji. Emoji are for his web decks only, as a continuity
  glyph set with a legend.
- One highlighted number per slide in bright green; everything else grey. Status colours
  always come with the status word.
- No grey tracker text in the top corner. A section cue is the green tab (`sticker`), used
  only where it earns its place: "Illustrative" on a built example, "A1" on an appendix page.
- Never copy slides from the template decks that carry client content or BCG branding;
  reproduce the layout in a clean file.
- The vault is a lagging indicator: if the deck states status, derive it from live
  sources (Slack, Gmail, Calendar, repos) with permalinks, not from Obsidian.

## Quick checks before handing over

- Titles read as the story; executive summary and body agree on every number.
- Title position, size and page number identical on every content slide; margins 0.5 in.
- No text below 8 pt; no overflow; no mid-word breaks (render and look, do not assume).
- Every data slide has a source; every acronym is expanded once; units appear once.
- The one bright-green element on each slide is the thing the reader must see.
