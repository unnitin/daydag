# Pattern library: which slide shape for which message

Pick the pattern from the message, then build it (see `build.md`) or copy it from one of
Nitin's three template decks in `~/Documents/personal/slide_templates/`:

- **K** = `Slide examples_Killer slides_Final.pptx` (101 slides, filled examples with real
  action titles; the best reference for what a finished slide looks like)
- **S** = `Sample killer slides 2020 selection.pptx` (159 slides, layout patterns with
  placeholder text; a renamed copy also sits at `~/Documents/dataplatform/RevenueDomain.pptx`)
- **G** = `German PP Templates.pptx` (151 slides, data-heavy tables, charts, comparisons,
  lists; 2016)

All three share the same theme: Trebuchet MS, 13.33 x 7.5 in, green on grey. Slide numbers
below are positions in the file (`catalog_template.py` prints them with their titles).

## Opening and structure

| Message | Pattern | Templates |
|---|---|---|
| Cover | Green panel with title, subtitle, date; photo optional | K1, S6, G1 |
| Agenda / table of contents | Green wedge left, numbered list right | K2, S23, S24 |
| Executive summary | SCQA rows, or numbered key lines with one line of support each | build: `execSummary` (no template equivalent; K21/K24 "at the end you will have" is the closest) |
| Section divider | Full-bleed green gradient, white title lower left | S5, S9, S22, G3 |
| Next steps | Numbered steps with owner and date; or a photo divider with 3 to 5 steps | S76, S86, K53 (numbered list), build: `nextSteps` |
| Decision required | Left panel "the decision", right table of asks by owner | build: `boxPanel` (dark) in the left column of `twoCols`, `nextSteps` or `table` in the right |

## One idea, structured text

| Message | Pattern | Templates |
|---|---|---|
| Three to four parallel points | Columns with a bold label and 2 to 3 lines each | S15, S19, G64, G73, G74 |
| Three points with a takeaway | Three numbered columns, green takeaway band at the bottom | S140, S141 |
| List with icons | Icon in a ring, label, one line; rows of 3 to 5 | K46, K49, K51, S89, G66, G113 |
| Numbered priorities | Green circled numbers, bars of decreasing length | K41, K53, S88 |
| Two-sided comparison (today vs with the change, A vs B) | Two columns with the same row labels; green wedge on one side | G69, G78, G79, S90, S117 |
| Checklist / recap | Check marks beside short claims | K57, G66 |
| Text intro with a point | Green left panel with the question, points on the right | G111, G112, G115, G116, G118 |

## Numbers

| Message | Pattern | Templates |
|---|---|---|
| Growth or trend | Column chart with a bracket "+x% p.a." and the future bars in green | K4, G14, G15 |
| We beat the alternative | Line chart, our line green, others grey, end labels | K5 |
| Build-up or bridge | Waterfall, steps grey, result green | K6, K10, K72 |
| Share of a whole | 100% stacked bar or donut with the one slice highlighted | K7, K69, K70 |
| Levers and their value | Horizontal bars by lever with a value column | K9, K89, G19 |
| Three or four KPIs | Big numbers in rings or tiles | K20, K23, K27, S72, S144 |
| Impact claims | Numbers in bubbles around a centre | K23, S61 |
| Data table | Header shaded, hairlines, check marks | K87 to K92, G7 to G12, S137, S138 |
| Scatter / positioning | Dot chart with the quadrant that matters shaded | S39, G20 |
| Heatmap of options vs criteria | Grid with filled cells, scale legend in words | S40, G18 |

## Time

| Message | Pattern | Templates |
|---|---|---|
| Milestones so far | Rail with circles and years, text beneath | K38, K81, S103, S127 |
| Roadmap by workstream | Gantt: lanes, months, grey bars, green diamonds, "today" shaded | S41, S129, S131, G13, G16 |
| Phases of work | Chevrons with durations and bullets beneath | K37, K80, S121 |
| Quarter plan with status | Lanes by deliverable, quarter columns, status pills (built, in progress, planned) | build: `timelineQuarters` |
| Speed to launch | Curved rail with dated events | K40, S124 |

## Frameworks and systems

| Message | Pattern | Templates |
|---|---|---|
| Operating model / house | Roof, pillars, foundation | S47 |
| Four questions / dimensions | Four circles on a rail, or a diamond | S44, S102, K15 |
| Flow across a system | Boxes left to right with arrows; lanes for actors | S53, S118, S120, G72 |
| Hub: one key, many joins | Centre circle with satellites | build: `hub`; S102 is the closest template |
| Layers / stages | Three stage cards with arrows (bronze, silver, gold; input, process, output) | S11, S150 to S153, build: `stages` |
| Options to choose from | Three option cards with a verdict row | S154 to S157, K48 |
| Risks with owners | Cards or a table: risk, why it matters, owner, mitigation | build: `cards` (eyebrow = owner, foot = mitigation) or `table`; K94 for the dense variant |

## Patterns built for Nitin's decks (helpers, no template equivalent)

Worked source for all three: `~/Documents/dataplatform/RevenueDomain-<date>.build.js`.

| Message | Pattern | Build |
|---|---|---|
| Work runs in two directions and meets in the middle | Inputs box, chevron pipeline, destination box; two opposing arrow bands beneath; a pointer callout on the step that carries the claim; a status strip under it in his own words | `bulletList` with `iconData`, `chevrons`, `ST.rightArrow` and `ST.leftArrow` with text over them, `ST.triangle` plus `ST.roundRect` for the callout; slide 1 of the example |
| Pick the pilot: one of each kind, end to end | Heading plus subheading (the "why"), a "why one of each" panel, two candidate tables with a `[date]` column for pending numbers and the exclusion in the header, a grey line for what joins later, a dark decision band with the proposal | `boxPanel`, two `table` calls, `ST.rect` strip, `boxPanel` dark; slide 2 of the example |
| Where the domain stands today | Three columns, line icon beside a bold label and a two-word status, then two or three sentences of judgement | custom loop with `rule`, `addImage`, rich `text`; the strip on slide 1 |

## Voices

| Message | Pattern | Templates |
|---|---|---|
| Quote as evidence | Large quote, name, role, date; photo optional | K60, K61, S134, S135 |
| Proof point / case | Situation, actions, impact in three blocks | K95, K63 |

## Rules for using the templates directly

- Copy a slide out of a template only when its layout is the pattern and its text is
  placeholder (S, G). The K deck's slides carry BCG client content and BCG branding: use
  them as references for look and density, never as copies.
- The template masters carry a BCG copyright footer rotated on the right edge and BCG
  logos on cover and back. Strip both from anything that leaves the house; the build path
  in `build.md` avoids them by reproducing the look in a clean file.
