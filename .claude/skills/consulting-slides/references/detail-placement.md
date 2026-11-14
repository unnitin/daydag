# Detail placement: what the slide carries, and where the rest goes

A build knows more than the slide should say: how a number was reached, who said what in
which thread, what is still owed and by whom, what you checked for him. All of it is true
and most of it is useful; little of it belongs on the slide. Nitin cuts it by hand when it
lands there ("make this a summary, don't need play by play"), and his decks go to readers
like a CFO, who take the claim and the number and stop. Put each sentence where its
reader looks for it.

Learned from his edits to builds, 11 Oct to 10 Nov 2026: about sixty cuts and moves across
six decks, counted at the end. In the same rounds he never cut a source.

## The tiers

| Tier | Carries | Who reads it |
|---|---|---|
| Title | The claim, with its number | Everyone, in five seconds |
| Body and exhibit | The proof: one number per row, the result rather than the steps, status as a word, and the qualifier that changes how a number reads ("in a back-test", "simulated", "estimate", "October to date") | The audience, in the room or on the page |
| Labels and headers | Unit, period, series; a word more specific when the short one is vague ("the pricing models", not "the models") | The audience, reading the exhibit |
| Note and source line | Where each number came from (system, period, who produced it), the method in a clause, the base of a percentage, how two figures relate ("includes the 7% on page 2"), the date of an old figure, ids | The sceptic who checks |
| Speaker notes | The mechanism step by step, the worked arithmetic, the thread or meeting a point came from with its permalink, how an idea was formed, the answer to the question you expect | The presenter, and whoever asks |
| Appendix | The detail slide behind a claim: the full table, the method, every case | The reader who asks "how do you know?" |
| Reply and chase list | Asks with an owner and a date, questions for him, option labels, what changed this round, what you checked and how | Him, now |

The appendix tier is already a house row in `Style.md` ("main slides carry the elevated
concept; specifics, counts and the dated record go to the appendix"), and so is "a caveat
the audience does not need stays in the speaker notes". This file is the sentence-level
call inside a slide that stays in the main flow.

## The test, sentence by sentence

Before the render, read every sentence, label and cell the build writes and ask these in
order. The first yes decides where it goes.

1. **Is it an ask?** An owner and a date, "ask Finance", "to name". Chase list and reply.
   The slide keeps the status word: "Not yet sized", and "TBD" for an owner or date not
   yet set (he retyped `[to name]` that way). A placeholder holds the place of a number
   still due (`[Mon]`, the house row); it never carries the chase (`[ask Finance, by 4 Dec]`).
2. **Is it for him rather than the audience?** A question to confirm, an option label, a
   change mark, the note that a claim was pressure-tested, the answer to a question he put
   on a sticky. Reply. A sticky asking "what does X mean?" is answered in the reply and the
   notes; the slide's fix is a clearer word, not an added explanation. Blue 'to confirm'
   boxes are a drafting aid behind a flag (house row); drop each one once he has answered it.
3. **Does something else on the slide already say it?** The impact column, the corner
   marker, the title, a column header. Cut it. A label that announces what the box plainly
   is ("Drivers:", "Option 2 ·", "DECISION FOR THE SPONSOR ·") is the same case.
4. **Would the audience misread the number without it?** A back-test, not live results; a
   simulated baseline; a partial month; an estimate. Keep it beside the number, in two to
   four words. The method in full goes in the note line.
5. **Does it say where something came from?** A system, a person, a date, a thread, an
   id, how an idea was formed. Source line. The exchange itself, verbatim with its
   permalink, goes in the notes; the slide states where it landed. An id becomes what it
   is ("not in the tracker yet") or its dollar figure. A quote stays on the slide only when
   it is the proof (a quote wall), never as the trail.
6. **Does it explain how?** The steps from cause to number, the arithmetic, an assumption
   on the way ("a third at first"). Speaker notes; the slide keeps the result. If the
   audience needs the mechanism to believe the claim, it is a detail slide in the appendix.
7. **Is it a date the claim does not rest on?** Make it coarse ("soon") or drop it. Dated
   commitments belong on the next-steps page and the chase list.
8. **Would the audience miss it?** If not, cut it.

Shorter is not the rule; the right tier is. He lengthened labels three times when the
short one was vague or wrong.

## Before and after

Invented examples, each the shape of an edit he made. Struck text is what he cut.

**A summary row that tells the play by play.** He kept the claim and the number, which
the impact column already carried, and cut the mechanism.

> Every pricing review will go through the model. In a back-test on past contracts its
> forecasts miss by 10%, against 40% for a simulated rule of thumb, so a contract can carry
> up to 20% less capital. ~~At first reviewers may take a third of that gap out of their
> offers: about 7% less capital, $2.8M on $40M. The feedback loop takes it toward 20%.~~

Impact column: "7-20% less capital". The arithmetic goes in the notes; "7% at first is my
assumption, agree it with VP-AI" goes in the reply.

**Said twice.** "The intake service stages each acquired unit's records for the core
systems. ~~Twelve so far.~~" beside an impact figure of "12 units".

**An ask in a status line.** "Next: ~~baseline cycle time before the service [ops lead,
4 Dec];~~ accuracy targets; security review~~, late Jan~~". The reply carries "Baseline
cycle time: ops lead, by 4 Dec". Likewise "Revenue: not yet sized ~~[ask Finance, by
4 Dec]~~".

**A label and a hedge in a so-what box.** "~~Drivers:~~ each cohort's results prove the
forecasts, so reviewers take more of the gap out of offers ~~(a third at first)~~;
retraining keeps forecasts tight."

**The trail in a lead-in.** "Growth ideas from outside the industry~~, from the strategy
team's research (how they were formed: A1). We agreed with them to rate the ideas on cost
and benefit and take the top ones to leadership; this is the first pass.~~" The source
line carries "How the ideas were formed: A1. Source: strategy team's idea list, Sep 2026";
the agreement goes in the notes.

**A thread on the slide.** Two people's messages quoted with names and a date, under a
title that already says what they settled. The quotes go to the notes with the permalink.
His sticky: "remove the conversation".

**Ids.** "~$40k + a91f3c2e" becomes "~$40k", the id named once in the source line;
"X-2" becomes "Not in the tracker yet".

**What stays.** "In a back-test on past contracts" and "simulated rule of thumb" stayed in
his own retype of the summary row and in the chart labels; the title kept "in a back-test".
A CFO who reads "10% against 40%" without "simulated" has been misled; one who reads it
without the arithmetic has not.

## Where the moved detail goes in the build

- Speaker notes: `notes` on `content()` and the other slide makers in `bcg_style.js`.
  Write them as the presenter would say them, with the arithmetic and the permalinks.
- Note and source line: `source`, "Note:" before "Source:" (`slide-anatomy.md`).
- Reply: asks go on the chase list with owner and date; questions for him go with the
  things to check, by page number (SKILL.md step 6).
- Appendix: a detail slide (`pattern-library.md`).

`slide-review`'s outline flags a bracket in slide text that names a who and a when, so an
ask that slipped onto a slide surfaces in every review.

## What his edits showed

His returned files diffed against the build before each (`scripts/drift.py`, and a variant
of it that matches moved slides by content and reads table cells), plus his stickies,
across a business case for the sponsor, the revenue domain deck, the cloud cost deck, the
data platform walkthrough, a decommission ticket table and a how-I-work page.

| What he cut or moved | Edits | Decks |
|---|---|---|
| Notes to him left on the slide: to-confirm boxes and tabs, option labels, a pressure-test note | 10 | 5 |
| Said twice, including label prefixes and eyebrows | 9 | 2 |
| Asks and owner or date placeholders in slide text | 9 | 2 |
| The trail: where an idea came from, who agreed what, a quoted thread, meeting tags | 8 | 4 |
| Mechanism and play by play, including the answer to his own question | 7 | 3 |
| Internal ids and codes in cells | 7 | 2 |
| Dates the claim does not rest on | 3 | 1 |
| Specifics moved to the appendix (already a house row) | 8 | 3 |

What he left alone: every "Sources:" list, however long; the method qualifier beside a
number, in every round; and page pointers in summary rows (he cut one from a number's
label and kept seven).
