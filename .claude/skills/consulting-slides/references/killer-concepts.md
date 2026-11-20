# Killer concepts: borrow the story, not only the layout

The template decks (S, G, P in `pattern-library.md`) give abstract layouts. The killer deck
(K, `Slide examples_Killer slides_Final.pptx`) shows specific concepts told well: a growth
target, beating a competitor, a business case, a track record. When a slide tells a story K
already tells, build K's concept: the same exhibit and the same device, with our numbers.
Read this file at step 3, before `pattern-library.md`. Nitin asked for it on 7 Nov 2026,
for "model performance etc." among others.

Look at the K slide before building: `bash scripts/render.sh` on a copy of the deck (the
slide numbers below are pages of that render), or `python3 scripts/catalog_template.py` for
titles. K carries client content and BCG branding: copy the device, never the slide.

## What makes a killer slide, and what our decks lacked

Measured against the GCP cost deck (6 Nov 2026): 10 of its 22 pages were tables at 9 to
10 pt, and the so-what sat in a pale strip at the foot. A K page carries one exhibit, one
so-what device, 30 to 60 words, and text at 13 to 16 pt. Six devices do most of the work.

| Device | What it does | K examples | Build |
|---|---|---|---|
| So-what device | The conclusion gets its own place on the page: a green wedge holding the claim, or a green connector disc on a rule leading to a big number and one line of green text | wedge K7, K8, K10, K43, K47, K55; connector K4, K5, K9, K51, K89, G29 | `panel`; `soWhat` (draws `connector`) |
| Grey context, one bright | History grey and today bright; competitors grey and ours bright; steps grey and the result bright | K4, K5, K10, K72 | `bars`, `lines`, `waterfall` highlight one item |
| Numbers in rings | A number in a grey ring, a line icon on the ring's edge, two-word label beneath | K20, K23, K27, K63 | `kpis` with `iconData`, `ringColor` |
| Big line icons | 0.6 to 0.9 in icons in grey rings as anchors for parallel points, not 0.3 in glyphs inside table cells | K46, K49, K50, K51, K55 | `columns` with `iconData`, `iconSize` 1.2 |
| Brackets and arrows | The change is drawn: an elbow bracket with "-51%", a trend arrow over the bars, a total bracket over a bridge | G5, G6, K4, K10 | `bars` (`brackets`, `trend`), `waterfall` (`bracket`) |
| Air | The exhibit takes most of the body; a table is for lookup and lives in the detail or appendix section (K85 to K92 are K's own "detail" pages) | all | house rule: "less words, bigger type" |

K and S also sit content on light grey (`F2F2F2`), which lifts white cards and rings off the
page: `createDeck({ background: 'tint' })`. Shown to him as the 7 Nov 2026 look sample; white
stays the default until he picks.

## Story to concept

| Our story (examples from past decks) | Killer concept | Exhibit and device | Build |
|---|---|---|---|
| Spend, volume or usage grew and keeps growing (cloud bill, advances by year, users) | K4 "to reach X, the client needs to keep growing" | Columns: history grey, the latest or the plan bright, a dotted split with "Historical / Plan", a thin trend arrow; the % in the so-what column | `bars` (`split`, `splitLabels`, `trend`) + `soWhat` |
| Ours beat the alternative over time (a model against the rule of thumb, one service against the rest, our cohort against the market) | K5 "you have won against your competitors" | One line thick and bright with values at each point, the rest thin grey with names at the line ends; an icon in a ring and the connector on the left | `lines` + `connector` |
| A business case or a savings bridge (cuts by lever, value by case) | K10 "has a strong business case"; K6, K72 savings by unit | The case on the green wedge; the bridge: start grey, steps light grey, result bright, one bracket with the total. K72 adds a row of % above the bars; K6 stacks each step by maturity (locked, high, mid, to be evaluated) | `panel` + `waterfall` (`bracket`) |
| Before and after on one metric (payback, error, cost per query, days to price a deal) | G5, G6, G29 | Pairs of bars, the new one bright, the change on a bracket; the so-what after the connector | `bars` (`brackets`) + `soWhat` |
| A metric against a target across segments (accuracy by segment, coverage by label) | G14, G15 | Sorted columns with a dashed reference line labelled with the target; segments past it bright | `bars` (`refLine`) |
| Share of a whole where one slice matters (spend by service, contracts carrying most revenue) | K7; K70 adds the largest items beside | Donut with the total in the centre, the message on the wedge | `panel` + native doughnut through `deck.pres` (`chart` draws line, column, bar) |
| A few carry most (advances by size band, top decile of a score) | K8 | Staircase or columns by group, each group's share in a ring above it | `bars` + `kpis` (rings) above |
| Mix over time with each part's growth (cost by service by month, revenue by business) | K9; G21 adds multipliers | Stacked columns with connector lines between years; each part's growth in a ring on the right | native stacked `chart` + rings |
| Three or four headline numbers (a value strip, adoption) | K20, K27; S136, S139 tiles | Numbers in grey rings with a line icon on the edge; only one bright | `kpis` (`iconData`, `ringColor`, `ringWidth`) |
| A track record of many results (what the team delivered this year) | K23 | Bubbles of different sizes, each a % or $ with a short label, the areas named left and right | custom circles |
| A track record by case or deal (deals the data carried, past acquisitions) | K43; K63 for one case with "3x" and "5x" | The claim in green on the left, one row per case on a green panel: icon, case, the result in lime, one line under it | custom: a rect plus a narrow `homePlate` |
| What people say (adoption, sponsor support) | K59 for several, K60 and K61 for one | Who on top where the logo sat, a dotted rule with a green quote mark, the words beneath | `quotes`; `quoteSlide` |
| Phased proof of an analytics idea (pilot to production to scale) | K37 "initial 5 week sprint to prove the potential of advanced analytics" | Chevrons, done in green and ahead in grey, a "today" marker, a check and the proof point under each phase | `chevrons` + a marker |
| The journey so far (milestones since the team started) | K38; K81 as a pin rail | Circles rising and growing year by year, an icon per milestone | custom; `timelineQuarters` for plans |
| Speed (months from idea to launch, weeks instead of months) | K40 "speed is of the essence" | A rail of dated events ending in an icon | custom |
| One key joins many things (one recording ID) | K14; S102 | A centre with satellites | `hub` |
| Several levers powered by one engine (cases that run on the data platform) | K51 "three levers powered by the analytical brain"; K50 | Three to six icons in rings on stems, the connector, the result in green | `columns` (`iconData`) + `soWhat` |
| The value parts of a deal (buying a label: stand-alone, synergies, risk, strategic) | K16 | Concentric quarter arcs, a label on each | custom arcs |
| Uplift from combining (two teams, orgs or platforms) | K89; K65 as overlapping circles | Stand-alone and combined columns, the connector, the uplift in rings | `columns` + `soWhat` |
| Roles or options with one emphasised (how I work, option B) | K45, K96 | Three columns, the chosen one on a green panel | `columns` + `boxPanel` dark |
| A selection funnel (artists scanned, scored, priced, signed; labels screened) | K15 | A funnel with the criteria table beside it | custom trapezoids + `table` |
| Value against cost per item (return by initiative) | K92 | Table rows with the ratio in a ring at the end | `table` + rings |

## Model performance

Pick by the question the audience is asking:

- **Is it better than how we decide today?** K5 lines, the model green and the baseline grey
  over cohorts or time; or G5 pairs with the change on a bracket (payback, over-prediction).
- **Where does it work, and where not yet?** G14 sorted bars by segment with the target as a
  reference line; the gap is named in the so-what column.
- **Does it rank well?** K8 staircase of score deciles, with the share of hits in rings
  above the top deciles.
- **What is it worth?** K10 bridge from the book of deals, through the over-advance it
  avoids, to the capital it frees.
- **How far along is it?** K37 phases with a today marker.

Every model number carries its run date and sample (n, period). It wears a "To confirm"
sticker until its owner signs off, and is called a result only after an out-of-sample
backtest.

## Worked example

The 7 Nov 2026 business-case look sample (the `*-look.build.js` script in the business-case
folder under the decks directory) redraws seven storyline pages in the patterns above: K4,
K10, K20, G5, K5, K59 and K43.
