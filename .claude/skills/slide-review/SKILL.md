---
name: slide-review
description: "Adversarial content review of a deck or a single slide before it reaches its audience: reads the titles alone as the argument and tests it against Minto's pyramid (one governing thought, key lines that answer the audience's question, MECE groupings, every level a summary of the one below), checks that each slide carries one main idea that its exhibit proves, checks that the visuals build the point the way BCG decks do, and attacks the numbers and claims the way a sceptical sponsor would in the room. Returns a verdict and ranked findings by page number, each with the question it invites and a rewrite; never edits the deck. Use when Nitin asks to review, critique, red-team, pressure-test, poke holes in or sanity-check a deck, a storyline or a slide, or asks 'is this ready', 'does this flow', 'what will the sponsor ask', 'what is the main message here'. consulting-slides also runs it on every build before handing a deck over. Building or fixing the slides, and visual polish, belong to consulting-slides."
daydag:
  # Invoked by hand, and by consulting-slides on every build before it hands over. Reads the
  # deck, his style rules and the live sources behind any status claim; writes nothing.
  # Findings come back in chat; fixes go through consulting-slides, which owns the build
  # scripts and DayDAG/Style.md.
  reads: [obsidian, slack, gmail, calendar, github, jira]
  sensitivity: shared
---

# Adversarial slide review

The review exists to find what the audience will find, before they do. A deck is an
argument: the titles carry it, the bodies prove it. So the review attacks the argument
first, the slides second and the wording last, and it reports in that order.

Each reference opens with a one-screen checklist; the rest of it is the reasoning and the
examples, for when a check needs judging.

| Pass | Read |
|---|---|
| 1, the argument: governing thought, key lines, groupings, SCQA | `references/pyramid.md` |
| 2 and 3, each slide and the build across slides; which exhibit for which message | `references/bcg-flow.md` |
| 4, the sceptic's questions, numbers forensics, where the plan lands, the pre-mortem | `references/attack.md` |

Run every command from the DayDAG repo root (where `.env` lives). `python` is not on the
path there: use `.venv/bin/python` for the `daydag` package and `python3` for
the skill's scripts, which need only the standard library.

## Stance

- **You did not build this deck.** If this session wrote or edited it, or wrote its
  storyline, hand the review to a fresh agent (Agent tool, general-purpose) with only the
  deck path, the brief and this file's path. A reviewer who knows what the slide was meant
  to say reads that meaning into it.
- **Read as the audience, not as the author.** Name the person (by role) and the question
  they walk in with. Every finding is phrased as what that person would say or ask.
- **Guilty until proven.** A title is a claim until the exhibit proves it; a number is wrong
  until its source, period and denominator are on the page; a grouping has a gap until
  you have looked for one.
- **Rank, do not list.** Ten nits bury the one broken argument. Lead with the verdict and
  the blockers; cap the main list at about a dozen findings and roll the rest up.
- **Every finding carries its fix,** written in his voice and house wording: a rewritten
  title, a split, a move, a cut. A criticism without a fix is half a finding.
- **Content, not polish.** Alignment, fonts, overflow and colours belong to the
  consulting-slides QA. Raise a visual point only when it changes what the reader takes
  away (the highlight is on the wrong bar; the status is carried by colour alone).
- **Surface conflicts between his rules, do not settle them.** Where `Style.md` and the
  consulting-slides skill disagree, or a struck row leaves a rule unclear, quote both and
  ask. A project row outranks a house row for that deck.

## The process

1. **Pin the brief.** Audience, occasion (live meeting or pre-read), the decision or
   outcome the deck exists for, and the question the audience walks in with. Infer them
   from the cover, the summary and the close when he has not said; state the inference
   in one line and ask only if the decision itself cannot be inferred. Then load his
   rules. Find the deck's section name first, since a wrong name silently returns the
   house rules alone:
   `grep '^## ' "$(grep '^VAULT_ROOT=' .env | cut -d= -f2-)/DayDAG/Style.md"`, then
   `.venv/bin/python -m daydag.style show --project "<section>"`. A breach of a row is a
   finding; a project row overrides a house row; a struck row is not applied.

2. **Extract and render.** Each step's output feeds a later pass:
   - `python3 .claude/skills/slide-review/scripts/outline.py <deck>.pptx --today <YYYY-MM-DD>`
     prints every slide's title, body, full table cells, chart data, sources, notes,
     stickies and mechanical flags, and where the appendix starts.
   - `--titles` prints the titles alone, appendix pages marked `a`.
   - `--numbers` lists every figure by slide, with ranges kept whole, then the bare counts,
     and the summary figures nothing else carries.

   Then copy the deck to the scratchpad, run
   `bash .claude/skills/consulting-slides/scripts/render.sh <copy>.pptx` on the copy (never
   beside his file), and look at every page. The visual argument cannot be judged from
   text. The flags are questions, not findings; confirm each by eye. Yellow stickies are
   his notes: read them for intent, do not review them. For a storyline or a ghost deck
   in chat, skip all of this and work from the titles.

3. **Pass 1, the argument (titles only).** Before reading any slide body, read the
   titles top to bottom and write down, in one sentence, the governing thought they add
   up to. Then run the `pyramid.md` checklist: is there one answer, does it come first,
   does it answer the audience's question, do the key lines support it, are the groupings
   MECE and ordered on purpose, does each title follow from the one before. Read the main
   flow first; then ask what the appendix holds that the argument needs (stakes, targets
   and savings parked behind the divider are the commonest blocker). If you could not
   write the sentence, that is the first finding.

4. **Pass 2, each slide (vertical logic).** For every slide:
   - one main idea, and a title that states it as a claim with its so-what;
   - an exhibit that proves that title and nothing else, with the title's figure marked
     on it so the eye lands on the proof;
   - an exhibit type that matches the comparison the title makes;
   - nothing in the bottom bullets or notes that undoes the title;
   - a source for every figure;
   - its kind (key message or detail slide) matched to where it sits (main flow or
     appendix).

   The checklist is in `bcg-flow.md`.

5. **Pass 3, the build across slides.** Where a point spans several slides, does each
   step add one thing (overview, then deep dives, then the summary in the same layout; the
   same exhibit with the next element lit; a recurring tracker or marker)? Is anything
   shown twice, two versions of one list (the outline flags tables that share rows), or a
   jump the reader has to bridge alone?

6. **Pass 4, attack the evidence.** Run the `attack.md` checklist. It covers:
   - the audience's first three questions, and the so-what ladder on every key line;
   - numbers forensics: the summary against the body, ranges that disagree, gross against
     net;
   - where the plan lands against the budget or goal, and by when;
   - staleness: dates already past, status as of the build date;
   - the alternative not considered, the risk not named, and the pre-mortem.

   Verify status claims against live sources (the vault is a lagging indicator). Use the
   recipe there: which source settles which kind of claim. A source you cannot reach gets
   one line ("couldn't check Jira: 403"), never a guess.

7. **Pass 5, house rules by machine.** Save the outline and check it:
   `python3 .claude/skills/slide-review/scripts/outline.py <deck>.pptx > <scratch>/outline.txt`,
   then `.venv/bin/python -m daydag.style check <scratch>/outline.txt --project "<section>"`.
   The checker reads house rows without the project's exceptions and stops at the first
   emoji. So a hit that a project row allows is not a finding: name the row that allows
   it, and read the outline yourself for the rest.

8. **Report.** In this order, in chat:
   - **Brief**, one line: audience, occasion, decision, their question (inferred or given).
   - **Verdict**, one line: ready; ready after the fixes below; or not ready, because the
     argument does not hold (say where).
   - **The governing thought** as the titles state it today, and as it should read.
   - **Findings**, most severe first, one line each where possible:
     `p.N [deck label] · severity · what fails · what <audience> will ask · fix`.
     `p.N` is the position in the file; add the deck's own label when it has one
     (`p.9 [A1]`). Severity:
     - **Blocker**: the argument breaks. There is no answer, it answers the wrong question,
       a key line is unproven, figures contradict, or the room can disprove a claim.
     - **Major**: a slide fails its job. A topic title, an exhibit that does not prove its
       title, two ideas on one slide, a MECE gap, or a detail slide in the main flow.
     - **Minor**: hedges, a missing source, a word the house rules forbid, density.

     Severity follows the effect on the argument, not the page's location. A
     contradiction in the appendix is major. It is a blocker when the figure is quoted
     in the main flow, or belongs there.
   - **Rolled up**: the minor items, one line each.
   - **Live checks**: what was verified, against what, and what could not be checked.
   - **The title sequence after the fixes**, when any title changed or a slide moved: the
     ghost deck as it should read, so he can judge the argument in ten seconds.
   - **Protect in the rewrite**: one line on what already works, only if a rewrite could
     lose it.

   Quote the slide's own words when criticising them. No praise padding, no summary of
   what each slide says.

9. **Hand off.** This skill never edits the deck or its build script. When he says
   apply, fix, or go, run consulting-slides with the findings as the brief. A rule the
   review surfaced that he confirms becomes a `Style.md` row through that skill's step 8,
   not from here.

## When consulting-slides calls it

Every build in consulting-slides ends with this review (its step 6). The brief arrives
in a fixed shape: deck path, render folder, audience, occasion, decision, `Style.md`
section, and scope. Differences from a review he asks for himself:
- **Renders**: use the render folder you are given instead of rendering again.
- **Brief**: take the one given; do not re-infer it, but say so if the deck plainly
  serves another audience or decision.
- **Scope**:
  - *Whole deck*: run every pass.
  - *Changed slides*: run Pass 1 on all the titles, Passes 2 to 5 on the changed slides
    only, and Pass 3 on the slides either side of each change.
- **Stickies**: findings that touch a slide carrying his stickies or wording are still
  reported. The builder decides; his word outranks yours.
- **Recheck**: a recheck (the builder sends back the slides it fixed) answers only one
  question per finding: is it closed, still open, or is something new broken? Do not
  re-review.

## Reviewing one slide

Same stance, shorter route:
1. Pin the slide's audience and its place in the deck: the claim before it and the claim
   after it.
2. Run Pass 2 and the relevant part of Pass 4.
3. Report the verdict, the title as it should read, and at most five findings.

If the slide needs the deck around it to make sense, say so. For a pre-read, a slide that
only works with the presenter talking over it is a finding.
