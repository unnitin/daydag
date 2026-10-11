# The feedback loop: his deck, his stickies, dated versions

How Nitin actually iterates on slides, learned on the revenue domain deck (Oct 2026). The
loop is cheap for him and exact for you; follow it rather than inventing a review process.

## Where the work lives

- He keeps a working deck open in PowerPoint (a lock file `~$<deck>.pptx` sits beside it).
  Never write into that file. Build beside it, as `<deck>-draft.pptx`, and let him paste
  slides across or ask you to insert them once the file is closed.
- His seed slides are the brief. A text box with the title, a second text box with
  directions ("show broad steps from inputs to target", "decision needed to focus build
  effort"). "Look at the deck and have a draft" means build those slides, not a deck. Read
  the seed slides and their stickies before anything else: `scripts/stickies.py --all`
  prints every text box with its fill so you can see what he wrote.
- One build script per deck, beside the output. The script is the source; the .pptx is a
  product. Revisions edit the script and rebuild.

## One round

1. `python3 scripts/stickies.py <deck>.pptx` lists the yellow (`FFFF00`) text boxes with
   slide number, position and text. Position tells you what the sticky points at (a sticky
   at the status strip is about the status strip).
2. Copy the sticky version to your scratch folder before touching anything, so his words
   survive the rebuild.
3. Apply every sticky, in his words where he gave them. Where a sticky asks for options
   ("create an option of a slide with X and another with Y"), build both as separate slides
   with "option A" and "option B" in the tracker; he deletes the loser and you remove it
   from the script next round.
4. Rebuild, render with `scripts/render.sh`, and look at every page. Three defects recur:
   bullets spilling below a panel, value labels running off a bar, a table or panel sliding
   under the band below it. Fix, re-render, look again. Only then reply.
5. Reply with what changed, sticky by sticky, in two or three lines each, plus anything you
   could not settle from the sources. Do not restate the slides.
6. "Promote the draft to dated version": `bash scripts/promote.sh <deck>-draft.pptx`
   renames the deck and its build script to `<deck>-<YYYY-MM-DD>` and moves the render
   folder aside (nothing is deleted; the script prints where it went). The next round's
   stickies land on the dated file; promote again and the date is replaced.

## What his stickies have taught, generalised

- Say the main thing plainly and keep ideas separate. A status column about "serve" must
  talk about revenue when the slide is about revenue, not about the performance tables.
  One idea per cell, one idea per title; if a title carries two, split into heading and
  subheading and let the subheading carry the "why".
- Status is written in your own words as a judgement with its implication ("no revenue from
  the platform reaches anyone yet; the first report is due this sprint"), not pasted from
  standups.
- His vocabulary wins: "destination" not "target state", "proposal" not "strawman", "end to
  end" said explicitly. Keep a list per deck in memory and reuse it.
- Exclusions carry their reason where the reader looks for the thing excluded (in the table
  header: "X excluded, it migrates first"), not in a footnote.
- Things that are not in play yet still get a line saying when they join ("the new platform
  joins the plan when its schemas hold actual data"), so nobody asks where they went.
- Worked example of all of the above: the revenue domain slides, built from
  `RevenueDomain-<date>.build.js` under `${DECKS_DIR}` (two slides, three sticky rounds).
- Pending numbers are bracketed placeholders with the date they arrive (`[Mon]`), explained
  once in the note line. Never fill them with a guess.
- In the house look, small icons ("bugs") are thin-line glyphs in green, not coloured-dot
  emoji; he asked three times. Emoji stay for his web decks, as continuity glyphs.
- Callouts are allowed and welcome when they carry a claim the shape cannot ("a draft design
  exists; hydrate it now, the design evolves with the inputs"); anchor them to the element
  with a pointer.

## Layout traps that cost a round each

- Adding a subtitle moves the body down a quarter inch; every box placed from `body.y`
  shifts, and the lowest band falls off the slide. Re-check the bottom after any title change.
- A tinted panel with an eyebrow needs about 0.9 in before its text has room; a strip under
  0.8 in switches to thin padding and fits one line of `text` or `claim`, nothing more.
- Value labels on bars need the full remaining width, and long bars leave none: shorten the
  label or set `max` above the largest value so the bar stops short.
- LibreOffice previews substitute Trebuchet with a wider face: a line that just fits in the
  preview may wrap in PowerPoint. Leave 10% slack; shorten rather than shrink.
- Chevron labels wrap at 12 pt once past ten characters in a four-across row; the helper
  now gives the label most of the chevron, but keep step names short.
