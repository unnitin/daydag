# Attack the evidence: the sceptic in the room

Pass 4. The pyramid can be sound and the deck still lose the room, because one number
does not survive a question or one option was never considered. This file is the
question bank. Run it against the key lines first (they carry the argument), then
against any slide whose title states a figure.

## Checklist

1. Write the audience's first three questions; find the slide that answers each (§1).
2. Run the so-what ladder on every key line: so what, why, how do you know, compared to
   what, big enough to matter, what would change your mind (§2).
3. `--numbers`: summary figures against the body; one figure written two ways; ranges
   that disagree between slides; base, period, denominator, units; one hand check of the
   arithmetic; gross against net (§3).
4. Where does the plan land, against the budget or goal, by when? Does it close the gap
   the deck opened (§4)?
5. Is anything stale: dates already past, status as of the build date, an owner who left
   (§5)?
6. Does any title reassure while its fine print undoes it? Any anecdote, correlation or
   cherry-picked window doing the work of evidence (§6)?
7. What is missing: the alternative, the cost, the risk, the owner, the ask (§7)?
8. Pre-mortem: three reasons it failed six months on; does the deck answer each (§8)?
9. Reading tests: glance, stop, removal, forwarded slide (§9). Wording (§10).
10. Status claims verified against the live source that settles them (§11).

## 1. The audience's first three questions

Before anything else, write down the three questions the named audience asks in the
first five minutes. Then find the slide that answers each. A question with no slide is a
blocker if it is about the decision, a major finding otherwise. Starting points by role:

| Audience | Asks first |
|---|---|
| Executive sponsor | What do you need from me, by when? What happens if I say no? How does this land with the people I answer to? |
| Finance | What does it cost, against which budget? What is the payback and when? Which number is the actuals and which the forecast? |
| CTO / engineering | What breaks, and who runs it after? What did you assume about the system that might not hold? Why not the simpler option? |
| Peer leaders | What does my team have to do, and what do we get? Who decided this, and was I asked? |
| Board / outside | Why now? Compared to whom? What is the risk you are not showing me? |

## 2. The so-what ladder

Ask each key line, and each slide title, these in order. Stop at the first that fails;
that is the finding.

1. **So what?** Does the title state what the fact means for the audience, or only the
   fact? ("cloud spend tripled" is a fact; "at this rate we overrun the budget by
   October" is the so-what.)
2. **Why?** Is the cause on the page, or only the effect?
3. **How do you know?** Is the evidence on this slide, sourced, with its period?
4. **Compared to what?** Is there a baseline, a target, a benchmark or a prior period? A
   number without a comparison is decoration.
5. **Is it big enough to matter?** Is the magnitude stated against something the
   audience cares about (budget, revenue, headcount, a deadline)?
6. **What would change your mind?** If nothing could, it is an article of faith, not an
   argument. Name the condition under which the recommendation flips.

## 3. Numbers forensics

Run `outline.py --numbers` and work through it.

- **Summary against body.** Every figure on the executive summary appears on a body slide,
  written the same way (the same rounding, the same unit, the same period). A summary
  figure no body slide carries is unproven. Two versions of one figure (7x and 6.9x;
  $1.2M and $1.19M) are a finding even when both are right. BCG's own NYCHA deck shows how
  it drifts: "~$70M" on the summary, "over $70M" in the title, $71M on the chart, "~$71M"
  on the next page. All agree; the reader still has to stop and check.
- **Base and period.** "Up 40%" from what, over what window? A growth figure needs its
  start, end and period. A partial period (month to date) is marked as one beside the
  figure, not only in the footer.
- **Denominator.** A share or a rate names what it is a share of. "60% adoption" of whom?
- **Units and scale.** Units once, and consistent across slides ($k on one slide, $M on the
  next, for the same series, is a finding). Monthly against annual, list price against net.
- **Precision that the source cannot carry.** Three significant figures from a survey of
  twelve, or a forecast to the dollar, reads as false confidence. Round to what the
  decision needs.
- **Arithmetic.** Components sum to the total; percentages of a whole sum to 100; a
  waterfall's steps reach its end bar; a CAGR matches its endpoints. Check one by hand.
- **One source per figure.** If two systems give two numbers, the slide says which one it
  uses and why, or shows both and the gap.
- **Estimates and placeholders.** Anything estimated, extrapolated or assumed says so on
  the slide ("~", "estimate", "at September's rate"). A figure still due is a bracketed
  placeholder with its date, never a guess (house rule).
- **Ranges that disagree.** The same saving or share given as two ranges on two slides
  (20-35% in a title, 10-20% in the build behind the total) means one of them is not the
  number the deck used. `--numbers` keeps ranges whole so they can be compared.
- **A rate applied to the wrong base.** A discount that applies to compute only, applied
  to a bill that includes storage; a growth rate for one segment applied to the whole.
- **Gross against net.** A saving from retiring or replacing something is gross until the
  cost of what replaces it is subtracted (the jobs that move, the new licence, the
  migration). Say which, and show the net or a dated placeholder for it.
- **Forecasts that leave out the deck's own driver.** A projection at last month's rate,
  when another slide says usage grows tenfold next quarter, is not the forecast.

## 4. Where does it land?

A plan or a recommendation is judged by its end state, not its activity. For any deck
that proposes action:

- State the end point the plan reaches, in the audience's measure (budget, run rate,
  revenue, headcount, a date), and the date it gets there. If the deck has none, that is
  a blocker for a decision deck: "back under control" undefined is not an answer.
- Compare it with the gap the deck opened. If the problem is "2.5x over budget" and the
  levers, added up, still leave it 2x over, say so: either the target moves (and the
  deck asks for that), or more levers are needed.
- Check the scope of the comparison: does the budget or goal include the things the
  plan cuts (a separate commitment, a cost booked elsewhere)?
- When the end state cannot be known yet, the slide carries a bracketed placeholder with
  the date it will be known and what decides it.

## 5. Is it still true?

A deck is built on a date and read on another. Run the outline with `--today` and check:

- Dates in due columns already past (the outline flags them). Each is either done (say
  so) or late (say what happens now).
- Status written as of the build date: re-verify before it goes out (§11).
- "This week", "next sprint", "now" in titles: still this week?
- Owners named on the slide who have since changed role or left.

## 6. Evidence that does not prove what it claims

| Pattern | What it looks like | Ask |
|---|---|---|
| Anecdote as trend | One customer quote, one incident, under a title about "customers" or "always" | How many, out of how many? |
| Correlation as cause | Two lines that move together, under a title that says one drives the other | What else changed at the same time? |
| Cherry-picked window | A start date at a low point, or a period that ends just before a reversal | What does the longer series show? |
| Survivorship | Only the successes, the pilots that shipped, the teams that responded | What happened to the rest? |
| Axis that misleads | A bar chart not starting at zero; two y-axes; a log scale unlabelled | Would the shape survive a zero baseline? |
| Moving target | The comparison changes between slides (budget on one, last year on the next) | Against what, consistently? |
| Activity as outcome | Counts of meetings held, tickets closed, pipelines built under a title about value | What changed for the business? |
| Plan as fact | A roadmap date or an owner stated as settled when no one has agreed it | Who committed, where, when? (permalink) |
| Authority as evidence | "Industry best practice", "consultancies say", a vendor's own claim | Which report, which figure, independent of the seller? |
| Title contradicted by the fine print | A reassuring title; the fact that undoes it in a bottom bullet or a footnote | Would a reader who stops at the title come away believing something false? |

The last row is the one that matters most, and the canonical case is public. The slide
Boeing engineers showed NASA during the Columbia mission was titled "Review Of Test Data
Indicates Conservatism for Tile Penetration". Only at the bottom, at the lowest of six
levels of bullets, did it say the debris was about 640 times larger than the data the
model was calibrated on. The accident board concluded that "the priority assigned to
information can be easily misrepresented by its placement on a chart and the language
that is used", and that a senior manager could read the slide "and not realize that it
addresses a life-threatening situation". Tufte's rewrite of the title: "Review of Test
Data Indicates Irrelevance of Two Models". Review check: read the lowest bullet and the
notes of every slide whose title reassures, and ask whether they undo it.

## 7. What is missing

MECE is about gaps as much as overlaps. Look for:

- **The alternative not considered.** A recommendation with no options slide invites
  "why not X?". The simplest option, doing nothing, and the obvious competitor's approach
  should each be ruled out with a reason, at least in the appendix.
- **The cost not counted.** People time, migration, run cost after launch, the cost of
  the delay itself.
- **The risk not named.** What has to be true for this to work? Each such assumption is
  either evidenced, or listed as a risk with an owner and a mitigation.
- **The owner not named.** Every next step has one person and a date. "The team" owns
  nothing.
- **The ask not made.** If the deck exists for a decision, the decision is written as a
  question the audience can answer yes or no, on one slide, near the end.

## 8. The pre-mortem

Gary Klein's method (HBR, September 2007): the team assumes the project "has just failed"
and generates the reasons why. Assume it is six months later and the recommendation failed. Write the three most
plausible reasons. For each, does the deck address it (evidence, mitigation, a check-in
date), or leave it for the audience to raise? An unaddressed failure mode the audience
can see is a blocker for a decision deck and a major finding for an update.

## 9. The reading tests

- **The glance test.** Duarte's version: "Will your audience understand the main point of
  your slide in 3 seconds or less?" Cover the body; from the title and the one highlighted
  element alone, a reader gets the point. If not, the title or the highlight fails. The
  squint test is the same check on the page: squint, and the thing you still see should
  be the thing the title is about.
- **The title-only read.** The titles in order read as the argument with no gap and no
  detour (Pass 1). A title that only makes sense after the slide before it is fine; one
  that only makes sense with the presenter talking is not, in a pre-read.
- **The forwarded-slide test.** A slide that will be pasted into someone else's deck
  carries its own source, period and unit; it does not lean on a legend three slides back.
- **The 60-second test.** The presenter can explain the slide in a minute. If it takes
  three, it holds two or three ideas.
- **The stop test.** A reader who stops after the executive summary has the answer, the
  reason and the ask. A reader who stops after any section has that section's claim.
- **The removal test.** Take a slide out and read the titles again. If the argument still
  holds, the slide is backup (appendix) or redundant (cut). If it breaks, the slide is
  carrying a key line and must be strong enough to.
- **The elevator test.** The recommendation and its reason can be said in the time of a
  short lift ride. If they cannot, the governing thought is not yet written.

## 10. Things a sceptic will not let pass in the wording

- Hedges stacked on a claim ("we believe this could potentially"). Say it or cut it.
- Superlatives and vague quantifiers without a comparison ("best", "significant",
  "major", "world class"). On the Columbia slide "significant" appeared five times, meaning
  anything from a detectable difference in a calibration test to a 640-fold gap. Ask for
  the number each one stands for.
- Units written two ways for one quantity (the same slide had "3cu. In" and "1920cu in").
- Passive voice that hides the actor ("mistakes were made", "it was decided").
- A title that announces instead of stating ("There are three issues"; Minto's
  "intellectually blank" assertion). Name the issues' common point instead.
- Jargon the audience does not use, and trade terms where a plain word exists (house rule).
- A reframe in place of a finding ("it's not X, it's Y"). State the finding.

## 11. Verifying status: which source settles which claim

The vault is a lagging indicator; never read it as status. Check a claim against the
source that owns it, quote what you find with its permalink, and give a source you cannot
reach one line ("couldn't check Jira: 403"), never a guess.

| Claim on the slide | Settled by | How |
|---|---|---|
| A PR merged, open, or "moves X" | GitHub | `gh pr view <n> -R CreateMusicGroup/<repo>`; a number may be an issue, not a PR (`gh issue view`) |
| A ticket's status or owner | Jira | the Atlassian connector, JQL bounded by key or project and date (CLAUDE.md §3) |
| "Agreed with X", "raised on date", an owner's commitment | Slack, Gmail | search by the person's Slack id (`.venv/bin/python -m daydag.people show <role> --log ~/.local/state/daydag/events.db`), never display name; read the thread, not only the parent |
| A meeting held or scheduled | Calendar | day by day; a scheduled meeting is not a held one |
| A figure from a system (billing, a table) | the system's own export or query | re-run only if cheap; otherwise check the source line names the system, period and date |

A status that disagrees with the live source is a finding at the severity of the claim it
supports. A status you could not verify is listed under Live checks in the report.

## Sources

- Columbia Accident Investigation Board, Report Vol. I, ch. 7, p. 191, quoting and
  extending Edward Tufte's critique:
  https://govinfo.library.unt.edu/caib/news/report/pdf/vol1/chapters/chapter7.pdf ;
  Tufte's notes: https://www.edwardtufte.com/notebook/columbia-accident-investigation-board-the-boeing-powerpoint-slide/
- NYCHA deck for the rounding drift: see `bcg-flow.md`, Sources.
- Duarte, "The Glance Test": https://www.duarte.com/resources/guides-tools/the-glance-test/
- G. Klein, "Performing a Project Premortem", HBR, Sept 2007:
  https://hbr.org/2007/09/performing-a-project-premortem
- Title-only, squint and removal tests, and checking footnotes and sources, from a former
  BCG consultant (secondary):
  https://slideworks.io/resources/bcg-approach-to-great-slides-practical-guide-from-former-consultant
- The elevator test and the initial hypothesis, from E. Rasiel, *The McKinsey Way*, via a
  secondary summary: https://www.antoinebuteau.com/lessons-from-ethan-m-rasiel/
- "The chart's only job is to prove the title"; check the most important number is the
  most visible (StrategyU, secondary): https://strategyu.co/think-before-you-chart
