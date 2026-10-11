# Slide anatomy and the house look

The visual grammar below is what reads as "BCG style" to people who have seen those decks:
quiet, grey-and-green, one message, everything in the same place on every slide. The
numbers are in inches on a 13.33 x 7.5 in (16:9) canvas, matching the templates in
`~/Documents/personal/slide_templates`.

## Zones on a content slide

| Zone | Position | Content |
|---|---|---|
| Action title | x 0.5, y 0.35, w 11.5, h 0.9 | The claim. 20 to 24 pt, green `03522D`, Trebuchet MS, left aligned, at most 2 lines. Same position on every slide. |
| Lead-in / subtitle (optional) | under the title, 12 to 14 pt, grey `6E6F73` | Scope, unit or period of the exhibit: "Savings including carry-over effect, 2018 (M EUR)". |
| Tracker / sticker (optional) | top right, small grey or green tab | "Preliminary", "Draft", "For discussion", or the chapter name. Only when it earns its place. |
| Body | x 0.5 to 12.83, y 1.4 to 6.6 | ONE exhibit: a chart, a table, a diagram, or a structured text block. |
| Takeaway box (optional) | bottom band or right column, green tint `E8F5EE` or dark green with white text | One sentence: the so-what if the title alone is not enough. |
| Footer left | x 0.5, y 6.85 | "Note:" lines, then "Source:" line. 8 to 9 pt grey. |
| Footer right | x 12.3, y 6.85 | Page number. 9 pt grey. Every slide. |

Everything repeats in the same place. The title never moves; the footer never moves. When
the reader flips through, only the exhibit changes.

## Typography

- One family: Trebuchet MS (the BCG template face; Henderson Sans is BCG's proprietary
  equivalent and is not available). Calibri or Arial if Trebuchet is unavailable; never
  Aptos; at most two families in a deck.
- Sizes, live deck: title 22 to 24, subtitle 14, body 14 to 16, labels 11 to 12, footnotes
  9. Dense pre-read: title 20, body 11 to 12, labels 9 to 10, footnotes 8. Never below 8.
- Weight and colour carry emphasis, not size. Bold the key figure; grey the rest. Titles
  are regular weight in the template, not bold.
- Left-align text. Centre only inside circles, tiles and chevrons.
- Bullets: one level, parallel grammar, at most 6, no full stops on fragments. Prefer
  a table or a labelled column to a bullet list.

## Colour

BCG-style palette as used in the templates (hex):

| Role | Hex | Use |
|---|---|---|
| Dark green | `03522D` | Titles, key figures, primary bars |
| Green | `197A56` | Secondary emphasis, icons, connectors |
| Bright green | `29BA74` | Highlights, "today" markers, the one number to see |
| Mint | `3EAD92` | Tertiary series |
| Lime | `D4DF33` | Sparingly, one accent on a dark panel |
| Slate | `295E7E` | A contrasting second series when green runs out |
| Dark grey | `575757` | Body text |
| Mid grey | `6E6F73` | Subtitles, labels, footnotes |
| Light grey | `D9D9D9` / `F2F2F2` | Rules, comparison bars, slide background tint |
| White | `FFFFFF` | Panels and slide background |

Rules: one dominant colour (dark green) plus greys; the bright green goes on the single
thing the eye must land on; grey everything that is context. Status reds and ambers are
allowed but each status also says its word ("Open", "At risk"). Keep colour meaning stable
across the deck. No gradients on content (a gradient is reserved for divider panels), no
shadows, no 3D, no clip art, no stock photo behind text unless it is a divider.

## Signature shapes

- Divider: full-bleed green gradient (`03522D` to `197A56`) with a white title at lower
  left and a thin white outline rectangle; or a green wedge on the left third with the
  section name in white and the content on the right. Use dividers only in decks of 20+
  slides.
- Left panel: a green chevron or wedge occupying the left 30 to 35% with the title in
  white, and the exhibit on the right. Good for "three things" lists, process steps and
  framework slides.
- Icon in a circle: thin green ring, line icon inside, label beneath. Rows of 3 to 5.
- Big number: 40 to 60 pt figure in dark green with a 12 pt label under it, in a ring or a
  tile. Three or four across.
- Chevron process: 4 to 6 chevrons in grey with the active step in green, text beneath each.
- Timeline: a horizontal rail with circle milestones and labels alternating above and
  below; or a Gantt with grey bars and green diamonds for milestones, months as columns.
- Table: header row in light grey, hairline rules, numbers right-aligned, units in the
  header, green check marks for yes, totals in bold.
- Callout: a circled number or a short tag in bright green pointing at the key value.

## Text slides that still look like exhibits

When a slide must be text, give it structure: 2 to 4 columns with a bold label on top of
each; a numbered list with the numbers in green circles; a two-column "today / with the
change" comparison; a "what / why it matters / what we do" table. Never a wall of prose.
Target under 50 words on a visual slide and under 120 on a text slide.

## Glyphs and emoji

Thin-line icons are the house style. Emoji are acceptable in Nitin's own decks when they
serve as continuity markers for an idea across slides (the same glyph marks the same idea
every time it appears), kept to one small set and listed in a legend or footer. Never as
decoration.

## Numbers and sources

- Units once, in the subtitle or header, never repeated on every number.
- Round to what the decision needs (2.3x, 36 data books, May '27). Keep one decimal at most.
- Footnotes numbered consecutively, "Note:" before "Source:", both bottom left.
- A source names the system, the date and who produced it: "Source: #data-and-finance,
  30 Jul to 24 Sep 2026, 66 messages tagged; DE analysis". Vague sources ("internal data")
  read as no source.
- Mark anything unverified or estimated as such on the slide ("to confirm", "strawman").

## Consistency checklist (run on the rendered pages)

- Title at the same height on every content slide; same size; at most 2 lines.
- Page number on every slide, same corner.
- Margins 0.5 in all round; nothing touches the edge except a full-bleed divider.
- No text overflow or mid-word breaks; no font below 8 pt.
- Repeated elements (chevrons, rings, tiles) sized identically across slides.
- The key number on each slide is the only bright-green element.
