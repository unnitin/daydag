# Building the deck as a .pptx

Two routes. Default to the first; it produces a clean file in the house look with no
third-party branding and every slide editable in PowerPoint or Keynote.

## Route A: generate with `scripts/bcg_style.js` (default)

A pptxgenjs helper library that encodes the anatomy in `slide-anatomy.md`: masters for
cover, divider, content and left-panel slides; components for columns, cards, KPI rings,
tables, chevrons, quarter timelines, hub diagrams, stage flows, bar charts, quotes, next
steps and the executive summary; a footer with source and page number on every slide.

Prerequisites (once per machine): `npm install -g pptxgenjs sharp react-icons react react-dom`
and `export NODE_PATH="$(npm root -g)"` in the shell that runs the build; LibreOffice and
poppler (`pdftoppm`) for the render step.

Workflow:

1. Write the storyline and ghost deck first (`storyline.md`). Keep the titles in a text
   file and read them top to bottom before building.
2. Write `build.js` next to the output, one call per slide, in deck order:

```js
const { createDeck } = require('<skill>/scripts/bcg_style.js');
const deck = createDeck({ title: 'Why the Data Platform', author: 'Nitin Srivastava' });

deck.cover({ title: 'Why the Data Platform', subtitle: 'One trusted set of tables the whole company reads from', meta: 'Data & AI · October 2026' });

let s = deck.content({ title: 'Analysts chase numbers more than twice as often as they analyse them',
                       subtitle: '#data-and-finance, 66 messages tagged, 30 Jul to 24 Sep 2026',
                       source: 'Source: #data-and-finance; tagging is one reader\'s judgement' });
deck.barsH(s, { items: [{ label: 'Wrong number', value: 27, hi: true }, { label: 'Pull request', value: 19, hi: true }, { label: 'Analysis', value: 20 }] });
deck.takeaway(s, 'By replies the ratio is 3.1 to 1.');

deck.nextSteps(deck.content({ title: 'One decision from the sponsor, one action from each department' }),
               [{ who: 'Finance', action: 'Confirm the data books as the revenue spec', when: 'Oct' }]);

deck.save('deck.pptx');
```

   Components take the slide returned by `content()` or `panel()` and lay out inside
   the body zone; pass `{ box: { x, y, w, h } }` to override. Read the top of
   `bcg_style.js` for each helper's options and the few that take `x, y, w` instead.
3. Build: `NODE_PATH="$(npm root -g)" node build.js`.
4. Render and look: `bash <skill>/scripts/render.sh deck.pptx` writes `deck-pages/` next
   to the deck, holding `deck.pdf` and `page-NN.jpg` (LibreOffice then pdftoppm) and a
   marker file naming the script that made it. Open every page. Fix overflow,
   collisions, drifting titles, fonts under 8 pt, missing sources and page numbers; then
   rebuild and re-render only what changed.
5. Validate the package with the pptx skill's `scripts/office/validate.py deck.pptx` so
   PowerPoint opens it without repair prompts.
6. Catalogue the template decks when you need a pattern you have not built before:
   `python3 <skill>/scripts/catalog_template.py <deck.pptx>` prints slide number, text
   and picture count; `pattern-library.md` maps messages to slide numbers.
7. Feedback rounds: `python3 <skill>/scripts/stickies.py <deck>.pptx` lists his yellow
   stickies with slide and position; `python3 <skill>/scripts/drift.py <his>.pptx <last-build>.pptx`
   lists what he retyped in place; `bash <skill>/scripts/promote.sh <deck>-draft.pptx`
   renames deck and script to the dated version and moves the render folder aside. Neither
   script deletes: a render folder is only moved, and only when it carries the marker, so
   a same-named folder of his is never touched. Details in `feedback-loop.md`.

Click-to-build: give every shape of step k an `objectName` starting `<prefix><k>` (pptxgenjs
passes it through `deck.text` and any raw `addShape`), then run
`python3 <skill>/scripts/animate.py deck.pptx --slide N --prefix P --steps K`. Each step
appears on one click and earlier steps stay. It rewrites the slide's timing in place and
keeps `<deck>.pre-anim.pptx` the first time, so run it again after every rebuild; the
build script cannot carry animation itself.

Helper options added from use: `chevrons(..., { textSize })` for the text under each step;
`bulletList(..., { iconData })` puts a line icon before the box title; a `boxPanel` shorter
than 0.8 in switches to thin padding so a one-line strip still fits. Anything the helpers
lack is one `deck.pres.ShapeType` call (arrow bands, pointer triangles): with
`const ST = deck.pres.ShapeType`, `ST.rightArrow`, `ST.leftArrow`, `ST.downArrow`,
`ST.triangle`, `ST.roundRect`.

Gotchas that bite in layout: a `subtitle` on `content()` moves `body.y` down 0.25 in, so
every box placed from it shifts and the lowest band can fall off the slide; re-check the
bottom after any title change. Keep panel bullets short enough to sit inside the panel at
12 pt (the render shows the spill; the build does not). Long value labels on `barsH` need
`max` set above the largest value so the bar leaves room.

Gotchas that bite in pptxgenjs: hex colours without `#`; one options object per call
(the library mutates them); `rectRadius` only on rounded rectangles; `bullet: true` per
item, never a literal bullet character; `breakLine: true` on every run except the last;
charts need `chartColors`, direct labels and quiet axes or they render in Office's
default palette with a legend; LibreOffice substitutes Trebuchet MS with a metric-similar
face, so a line that fits in the preview may wrap by a word in PowerPoint. Leave 10%
slack in any text box that is nearly full.

## Route B: copy and fill a template slide

When a template in `${SLIDE_TEMPLATES_DIR}` already has the exact layout
(a Gantt, a heatmap, a map), copy that deck, keep only the slides you need, and replace
the text. Use the pptx skill: `thumbnail.py` to pick slides (needs Python 3.10+; make a
venv with `defusedxml` and `pillow`), unpack, edit `<p:sldIdLst>` to keep the slides,
`clean.py`, edit the slide XML, zip, `validate.py --original`. Strip the BCG copyright
footer from the master and the BCG logo from cover and back before the file leaves the
house; the K deck's slides also carry client content that must never be reused.

Route B keeps the template's exact geometry but every edit is XML surgery; it pays off for
one or two complex exhibits, not for a 25-slide deck.

## Delivering

- Save under `${DECKS_DIR}/<project>/` (both from `.env`), or the folder the user names.
  Never into the templates folder.
- Review first: every build goes through `slide-review` in a fresh agent before it is
  handed over (SKILL.md step 6). `render.sh` prints a reminder after every render of a
  deck outside a temp folder.
- Hand back the `.pptx` path and the rendered page images for the slides changed in this
  pass, the review's verdict with what was fixed and what was left, and the one or two
  things to check named by page number.
- Fonts: the deck names Trebuchet MS, which ships with Office. If it is ever opened on a
  machine without it, Calibri substitutes cleanly.
