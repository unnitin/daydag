// Pattern deck with placeholder text: the slide shapes the BCG libraries lack, in the house look.
// Run: NODE_PATH="$(npm root -g)" node patterns_build.js "<output.pptx>"
'use strict';
const path = require('path');
const { createDeck } = require(path.join(__dirname, '..', 'scripts', 'bcg_style.js'));
const out = process.argv[2] || path.join(process.cwd(), 'Consulting patterns - data and AI.pptx');

(async () => {
  const deck = createDeck({ title: 'Consulting patterns', author: 'consulting-slides skill' });
  const C = deck.C; const B = deck.box; let s;
  const src = 'Source: system, period, who produced it. Note: estimates marked on the slide.';
  const lorem = 'One or two lines of support. Specific, quantified, no hedging.';

  deck.cover({ meta: 'Eyebrow · month year', title: 'Deck title as a claim', subtitle: 'One sentence the reader should leave with.', audience: 'For whom', right: 'Author, with whose material' });

  s = deck.content({ title: 'Executive summary: the governing thought in one sentence, two lines at most', tracker: '1 · Executive summary', source: src });
  deck.execSummary(s, [
    { glyph: '🧩', label: 'Situation', claim: 'A fact the audience already accepts', support: lorem },
    { glyph: '💸', label: 'Impact', claim: 'What it costs, with the one number', support: lorem },
    { glyph: '🔑', label: 'Solution', claim: 'What we propose, in one line', support: lorem },
    { glyph: '🟢', label: 'Improvement', claim: 'What gets better, by when', support: lorem },
    { glyph: '🎯', label: 'The call', accent: true, claim: 'The decision we need, and from whom', support: 'What it costs to say yes.' },
  ]);

  s = deck.content({ title: 'Situation: three cards with a stat each, and the four costs beneath', tracker: '🧩 2 · Current situation', source: src });
  deck.cards(s, [
    { glyph: '🗄️', eyebrow: 'Stage one', title: 'Stat', text: lorem }, { glyph: '🧱', eyebrow: 'Stage two', title: '37 files', titleColor: C.amber, text: lorem }, { glyph: '📐', eyebrow: 'Stage three', title: '~40 tables', titleColor: C.amber, text: lorem },
  ], { box: B(s.body.x, s.body.y, s.body.w, 2.35), cols: 3, titleSize: 24, titleH: 0.6 });
  deck.text(s, { box: B(s.body.x, s.body.y + 2.6, s.body.w, 0.3), text: 'WHAT THAT COSTS', fontSize: 10, bold: true, color: C.grey, charSpacing: 1.2 });
  deck.columns(s, [1, 2, 3, 4].map(i => ({ label: 'Cost ' + i + ' as a claim', text: lorem })), { box: B(s.body.x, s.body.y + 2.95, s.body.w, 2.0), topRule: true, labelSize: 13, labelH: 0.75 });

  s = deck.content({ title: 'Evidence: four dated cards, each one thing that happened and what it tells you', tracker: '🧩 2 · Current situation', source: src });
  deck.cards(s, [1, 2, 3, 4].map(i => ({ glyph: '🧪', eyebrow: 'Date · what', eyebrowColor: C.amber, title: 'What happened, in one line', text: lorem })), { cols: 2, rows: 2, titleSize: 15, titleH: 0.5 });

  s = deck.content({ title: 'Two costs: a number on the left, the opportunities on the right, a takeaway below', tracker: '💸 3 · Why improve it', source: src });
  let cols = deck.twoCols(s, 0.5, 0.5);
  deck.text(s, { box: B(cols.left.x, cols.left.y, cols.left.w, 0.3), text: '💸 TIME', fontSize: 10, bold: true, color: C.grey, charSpacing: 1.2 });
  deck.bigNumber(s, { box: B(cols.left.x, cols.left.y + 0.3, cols.left.w, 1.4), value: '2.3 to 1', label: 'What the number compares, population, period.', size: 36, valueH: 0.7 });
  deck.barsH(s, { box: B(cols.left.x, cols.left.y + 1.9, cols.left.w, 1.8), labelW: 2.0, items: [{ label: 'Category A', value: 27, hi: true }, { label: 'Category B', value: 19, hi: true }, { label: 'Category C', value: 20, color: C.rule }] });
  deck.text(s, { box: B(cols.right.x, cols.right.y, cols.right.w, 0.3), text: '🎯 OPPORTUNITIES', fontSize: 10, bold: true, color: C.grey, charSpacing: 1.2 });
  deck.execSummary(s, [1, 2, 3].map(i => ({ glyph: '🎧', label: 'Who', claim: 'What they cannot do today', support: 'What it is worth.' })), { box: B(cols.right.x, cols.right.y + 0.35, cols.right.w, 3.0), labelW: 0.9, claimSize: 13 });
  deck.takeaway(s, '💬 The objection the room will raise, and the one-line answer.', { height: 0.7, size: 12 });

  s = deck.content({ title: 'Three questions: who asks, today, with the change', tracker: '💸 3 · Why improve it', source: src });
  deck.cards(s, [1, 2, 3].map(i => ({ glyph: '🎧', eyebrow: 'Who asks', title: 'The question, verbatim?', text: 'Today: how it is answered now.\n\nPlatform: how it is answered after.' })), { box: B(s.body.x, s.body.y, s.body.w, 3.3), cols: 3, titleSize: 15, titleH: 0.75 });
  deck.takeaway(s, '🔑 The one thing that makes all three possible.', { height: 0.6, size: 12, y: s.body.y + 3.55 });

  s = deck.content({ title: 'Hub: one key in the centre, what it joins around it, the three truths on the right', tracker: '🔑 4 · How we solve it', source: src });
  cols = deck.twoCols(s, 0.6, 0.4);
  deck.hub(s, { box: cols.left, hubDiameter: 1.8, satW: 2.1, satH: 0.75, center: { label: 'KEY', sub: '🔑 one of' }, satellites: ['📜', '🧾', '💰', '📈', '🎵'].map(g => ({ glyph: g, label: 'Thing', sub: 'what it carries' })) });
  deck.cards(s, [1, 2, 3].map(i => ({ glyph: '🎵', eyebrow: 'Truth ' + i, title: 'What this layer makes true' })), { box: B(cols.right.x, cols.right.y, cols.right.w, 3.75), cols: 1, rows: 3, titleSize: 13, titleH: 0.7 });
  deck.boxPanel(s, { box: B(cols.right.x, cols.right.y + 3.95, cols.right.w, 1.0), text: 'Why the key is the key, in one sentence.' });

  s = deck.content({ title: 'Principles: six cards, each with the failure it stops and its status', tracker: '🔑 4 · How we solve it', note: '🟢 live · 🟡 still true off it · ⚠️ open', source: src });
  deck.cards(s, [1, 2, 3, 4, 5, 6].map(i => ({ glyph: '🔑', title: i + ' · Principle as a short rule', text: 'Stops: the failure, named.', foot: '🟢 Live: evidence · 🟡 off platform: evidence', footColor: C.ink })), { cols: 3, rows: 2, titleSize: 13, titleH: 0.6 });

  s = deck.content({ title: 'Stages: three cards with arrows, today and after inside each', subtitle: 'Worked example: the one case the room will remember', tracker: '🔑 4 · How we solve it', source: src });
  deck.stages(s, [
    { glyph: '🥉', eyebrow: 'Stage one', eyebrowColor: C.amber, title: 'Principle of the stage', blocks: [{ label: 'Today', text: lorem }, { label: 'After', color: C.green, text: lorem }] },
    { glyph: '🥈', eyebrow: 'Stage two', title: 'Principle of the stage', blocks: [{ label: 'Today', text: lorem }, { label: 'After', color: C.green, text: lorem }] },
    { glyph: '🥇', eyebrow: 'Stage three', eyebrowColor: C.green, accent: true, title: 'Principle of the stage', blocks: [{ label: 'Today', text: lorem }, { label: 'After', color: C.green, text: lorem }] },
  ]);

  s = deck.content({ title: 'Capabilities: three cards and a proof panel', tracker: '🟢 5 · What improves', source: src });
  deck.cards(s, [1, 2, 3].map(i => ({ glyph: '💬', eyebrow: 'Verb', eyebrowColor: C.green, title: 'What becomes possible', text: lorem })), { box: B(s.body.x, s.body.y, s.body.w, 2.9), cols: 3, titleSize: 15, titleH: 0.7 });
  deck.boxPanel(s, { box: B(s.body.x, s.body.y + 3.15, s.body.w, 1.8), eyebrow: '⭐ Proof, not a promise · date', claim: 'Three numbers · in a row · that prove it', claimH: 0.45, text: 'What happened, who did it (by role), and the next step.' });

  s = deck.content({ title: 'Audiences: six cards with status, and the four doors beneath', tracker: '🟢 5 · What improves', note: '🟢 live · 🟡 in progress · ⚠️ open', source: src });
  deck.cards(s, ['💰', '📊', '🛠️', '🤝', '🤖', '🏷️'].map(g => ({ glyph: g, title: 'Department', text: 'What they get', foot: '🟢 Where it stands', footColor: C.ink })), { box: B(s.body.x, s.body.y, s.body.w, 3.3), cols: 3, rows: 2, titleSize: 13, titleH: 0.4 });
  deck.text(s, { box: B(s.body.x, s.body.y + 3.5, s.body.w, 0.3), text: 'FOUR DOORS', fontSize: 10, bold: true, color: C.grey, charSpacing: 1.2 });
  deck.cards(s, ['💬', '📊', '🔌', '📅'].map(g => ({ glyph: g, title: 'Door', text: 'For whom, how', fill: C.greenTint, border: C.greenTint })), { box: B(s.body.x, s.body.y + 3.85, s.body.w, 1.1), cols: 4, rows: 1, titleSize: 13, titleH: 0.35 });

  s = deck.content({ title: 'Quarter timeline: one lane per deliverable, status pills, today shaded', tracker: '🟢 5 · What improves', note: '🟢 built · 🟡 in progress · ⚪ planned', source: src });
  deck.timelineQuarters(s, { columns: ['Q4 · now', 'Q1', 'Q2', 'Q3 +'], today: 0, laneW: 3.2, lanes: [
    { name: '🔭 Deliverable one', sub: '🟢 Built · for whom', items: [{ col: 0, label: 'Milestone · date', status: 'built' }] },
    { name: '💰 Deliverable two', sub: '🟡 In progress · first slice', items: [{ col: 0, label: 'Slice 1 · date', status: 'progress' }, { col: 0, label: 'Slice 2 · date', status: 'planned' }, { col: 1, label: 'Slice 3 · date', status: 'planned' }] },
    { name: '📜 Deliverable three', sub: '⚪ Planned · what', items: [{ col: 1, label: 'Milestone · date', status: 'planned' }] },
    { name: '🧮 Deliverable four', sub: '⚪ Planned · what', items: [{ col: 2, label: 'Tranche 1 · date', status: 'planned' }, { col: 3, label: 'The rest', status: 'planned' }] },
  ] });

  s = deck.content({ title: 'Next steps: the decision on the left, one action per owner on the right', tracker: '🎯 6 · Next steps', source: src });
  cols = deck.twoCols(s, 0.37, 0.4);
  deck.boxPanel(s, { box: B(cols.left.x, cols.left.y, cols.left.w, 4.4), eyebrow: '🎯 The decision · for whom', claim: 'The call in one sentence.', claimH: 1.3, bullets: ['What it includes', 'What it costs', 'Where the alternatives are'] });
  deck.nextSteps(s, [1, 2, 3, 4, 5].map(i => ({ who: 'Owner ' + i, action: 'The action, concrete enough to check', when: 'Month' })), { box: cols.right, colW: [2.2, cols.right.w - 2.2 - 0.9, 0.9], rowH: 0.62 });

  deck.divider({ eyebrow: '📎 Reference · not walked through', title: 'Appendix', items: ['A1 · Title', 'A2 · Title', 'A3 · Title'] });

  s = deck.content({ title: 'Options: two bars for the duration, a table for the trade-offs', tracker: 'A1 · Appendix', note: 'Note: what the months count from; what is a strawman.', source: src });
  deck.barsH(s, { box: B(s.body.x, s.body.y, s.body.w, 1.2), labelW: 3.0, max: 26, items: [{ label: 'Option A', value: 19, color: C.amber, valueLabel: 'About 19 months · date' }, { label: 'Option B', value: 9, hi: true, valueLabel: '9 months · date' }] });
  deck.table(s, { box: B(s.body.x, s.body.y + 1.45, s.body.w, 3.4), fontSize: 11, colW: [2.1, 3.4, 3.4, 3.433], header: ['', 'Option A', 'Option B', 'Recommended'], rows: [['Criterion', 'How A does', 'How B does', 'How the recommendation does'], ['Criterion', '...', '...', '...'], ['People', '...', '...', '...']] });

  deck.quoteSlide({ eyebrow: 'A2 · The sponsor, in their words', quote: 'The quote, verbatim, never tidied, long enough to carry its own context.', who: 'Who, date, channel', meaning: 'What it means for the decision, in two sentences.' });

  s = deck.content({ title: 'Risks: four cards, each with why it matters and an owner', tracker: '⚠️ A4 · Appendix', source: src });
  deck.cards(s, [1, 2, 3, 4].map(i => ({ glyph: '⚠️', title: 'Risk ' + i + ' as a sentence', text: 'Why it matters and what it blocks.', foot: 'Owner: role (question id)', footColor: C.ink })), { cols: 2, rows: 2, titleSize: 15, titleH: 0.6 });

  s = deck.content({ title: 'Table: header shaded, hairlines, first column bold, status with the word', tracker: 'A5 · Appendix', source: src });
  deck.table(s, { fontSize: 11, colW: [0.6, 7.2, 3.2, 1.333], header: ['#', 'Item', 'Who', 'State'], boldFirst: false, rows: [['1', 'A question or requirement', 'Role', '🟢 Answered'], ['2', 'A question or requirement', 'Role', '🟡 Partly'], ['3', 'A question or requirement', 'Role', '⚠️ Open']] });

  s = deck.content({ title: 'Two lists with icons, and a status panel beneath', tracker: 'A8 · Appendix', source: src });
  cols = deck.twoCols(s, 0.5, 0.4);
  deck.bulletList(s, { box: B(cols.left.x, cols.left.y, cols.left.w, 3.6), fill: C.white, border: C.rule, glyph: '🔑', title: 'List one · heading', items: ['Item', 'Item', 'Item', 'Item'] });
  deck.bulletList(s, { box: B(cols.right.x, cols.right.y, cols.right.w, 3.6), fill: C.white, border: C.rule, glyph: '🔒', title: 'List two · heading', items: ['Item', 'Item', 'Item', 'Item'] });
  deck.boxPanel(s, { box: B(s.body.x, s.body.y + 3.85, s.body.w, 1.1), bullets: ['🟢 Live: what is already true.', '⚠️ Open: what is not yet decided.'] });

  await deck.save(out);
  console.log('wrote', out, deck.pages(), 'slides');
})().catch(e => { console.error(e); process.exit(1); });
