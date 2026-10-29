/*
 * bcg_style.js - pptxgenjs helpers for consulting-style decks (green on grey, Trebuchet,
 * action titles, one exhibit per slide, source and page number on every slide).
 *
 * Usage:
 *   const { createDeck } = require('.../scripts/bcg_style.js');
 *   const deck = createDeck({ title, author });
 *   deck.cover({ title, subtitle, meta, audience });
 *   const s = deck.content({ title, subtitle, source, tracker });   // -> { slide, body }
 *   deck.columns(s, [{ label, text }]);                          // one column per item
 *   await deck.save('out.pptx');
 *
 * Geometry is in inches on a 13.333 x 7.5 canvas; type sizes in points. Components take
 * `s` (from content() or panel()) and an options object; pass `box: { x, y, w, h }` to
 * place one somewhere other than the whole body. Exceptions: `takeaway` is placed by
 * `y` and `height` at the foot of the body; `callout`, `rule` and `icon` take `x, y, w`;
 * `twoCols(s, leftFrac, gap)` is positional and returns the two boxes. Text heights are
 * clamped at 0.1 in, so a box too small for its content clips rather than writing an
 * invalid file. Helpers that draw icons need
 * icon data from `await deck.icons(['FiDatabase', ...])` (react-icons Feather set, rendered
 * to PNG through sharp); everything else is synchronous.
 *
 * Components: cover, divider, content, panel, quoteSlide, execSummary, columns, cards,
 * kpis, table, chevrons, timelineQuarters, nextSteps, boxPanel, twoCols, hub, stages, barsH,
 * chart, takeaway, callout, light, lightLegend, bigNumber, bulletList, text, rule, icon, icons.
 * Traffic lights: `light('good' | 'watch' | 'bad' | 'none')` is a table cell (soft fill, no
 * word); `lightLegend(s, [[level, label], ...], { y })` draws the one legend under the table.
 *
 * Shapes the helpers do not cover (an arrow band, a pointer triangle, an odd rectangle) are
 * one line of pptxgenjs through `deck.pres.ShapeType`; see the revenue domain build script
 * for the two-directions slide as an example.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const pptxgen = require('pptxgenjs');

const T = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'assets', 'theme.json'), 'utf8'));
const C = T.colors;
const S = T.sizes;
const Z = T.zones;
const FONT = T.font;

// ---------- small utilities ----------
function box(x, y, w, h) { return { x, y, w, h }; }
function sub(b, dx, dy, dw, dh) { return box(b.x + dx, b.y + dy, b.w + (dw || 0), b.h + (dh || 0)); }
function textOpts(o) {
  // one fresh object per call: pptxgenjs mutates option objects in place
  const r = Object.assign({
    fontFace: FONT, fontSize: S.body, color: C.ink, margin: 0, isTextBox: true,
    valign: 'top', align: 'left', lineSpacingMultiple: 1.1,
  }, o || {});
  // a negative extent is schema-invalid and makes PowerPoint repair the file
  if (typeof r.h === 'number' && !(r.h >= 0.1)) r.h = 0.1;
  if (typeof r.w === 'number' && !(r.w >= 0.1)) r.w = 0.1;
  return r;
}
function noLine(color) { return { color: color, width: 0 }; }

// ---------- deck ----------
function createDeck(meta) {
  meta = meta || {};
  const pres = new pptxgen();
  pres.layout = 'LAYOUT_WIDE';
  pres.theme = { headFontFace: FONT, bodyFontFace: FONT };
  if (meta.title) pres.title = meta.title;
  if (meta.author) pres.author = meta.author;
  if (meta.company) pres.company = meta.company;

  const pageNo = { x: 12.4, y: 6.95, w: 0.45, h: 0.3, fontSize: S.footnote, color: C.grey, fontFace: FONT, align: 'right' };

  pres.defineSlideMaster({ title: 'CONTENT', background: { color: C.white }, objects: [], slideNumber: pageNo });
  pres.defineSlideMaster({ title: 'COVER', background: { color: C.white }, objects: [] });
  pres.defineSlideMaster({ title: 'DIVIDER', background: { color: C.green }, objects: [], slideNumber: Object.assign({}, pageNo, { color: C.white }) });
  pres.defineSlideMaster({ title: 'DARK', background: { color: C.green }, objects: [], slideNumber: Object.assign({}, pageNo, { color: C.white }) });

  let pageCount = 0;
  const iconCache = {};

  // ----- icons (react-icons Feather -> PNG via sharp) -----
  async function iconData(name, color, strokeWidth) {
    const key = name + '|' + (color || C.green2) + '|' + (strokeWidth || 1.6);
    if (iconCache[key]) return iconCache[key];
    const React = require('react');
    const ReactDOMServer = require('react-dom/server');
    const sharp = require('sharp');
    const set = name.slice(0, 2).toLowerCase();           // Fi -> fi
    const lib = require('react-icons/' + set);
    const Icon = lib[name];
    if (!Icon) throw new Error('unknown icon ' + name);
    const svg = ReactDOMServer.renderToStaticMarkup(
      React.createElement(Icon, { size: 256, color: '#' + (color || C.green2), strokeWidth: strokeWidth || 1.6 }));
    const png = await sharp(Buffer.from(svg)).png().toBuffer();
    iconCache[key] = 'image/png;base64,' + png.toString('base64');
    return iconCache[key];
  }
  async function icons(names, color) {
    const out = {};
    for (const n of names) out[n] = await iconData(n, color);
    return out;
  }

  // ----- slide frames -----
  function cover(o) {
    const slide = pres.addSlide({ masterName: 'COVER' });
    pageCount++;
    slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 8.6, h: 7.5, fill: { color: C.green }, line: noLine(C.green) });
    slide.addShape(pres.ShapeType.rect, { x: 8.6, y: 0, w: 4.733, h: 7.5, fill: { color: C.tint }, line: noLine(C.tint) });
    slide.addShape(pres.ShapeType.rect, { x: 0.6, y: 0.6, w: 7.4, h: 6.3, fill: { type: 'none' }, line: { color: C.white, width: 0.75 } });
    if (o.meta) slide.addText(o.meta, textOpts({ x: 0.9, y: 0.9, w: 6.8, h: 0.4, fontSize: S.label, color: C.lime, charSpacing: 2 }));
    slide.addText(o.title, textOpts({ x: 0.9, y: 2.3, w: 6.8, h: 1.8, fontSize: 40, color: C.white, valign: 'bottom', lineSpacingMultiple: 1.0 }));
    if (o.subtitle) slide.addText(o.subtitle, textOpts({ x: 0.9, y: 4.25, w: 6.6, h: 1.2, fontSize: 16, color: C.white }));
    if (o.audience) slide.addText(o.audience, textOpts({ x: 0.9, y: 6.1, w: 6.8, h: 0.5, fontSize: S.label, color: C.white }));
    if (o.right) slide.addText(o.right, textOpts({ x: 9.0, y: 6.1, w: 4.0, h: 0.8, fontSize: S.small, color: C.grey }));
    if (o.notes) slide.addNotes(o.notes);
    return { slide, body: box(9.0, 0.9, 3.9, 5.0) };
  }

  function divider(o) {
    const slide = pres.addSlide({ masterName: 'DIVIDER' });
    pageCount++;
    slide.addShape(pres.ShapeType.rect, { x: 0.6, y: 0.6, w: 12.13, h: 6.3, fill: { type: 'none' }, line: { color: C.white, width: 0.75 } });
    if (o.eyebrow) slide.addText(o.eyebrow, textOpts({ x: 0.9, y: 4.3, w: 6, h: 0.4, fontSize: S.label, color: C.lime, charSpacing: 2 }));
    slide.addText(o.title, textOpts({ x: 0.9, y: 4.7, w: 7.5, h: 1.6, fontSize: 32, color: C.white, valign: 'top' }));
    if (o.items && o.items.length) {
      const runs = o.items.map((t, i) => ({ text: t, options: { breakLine: i < o.items.length - 1, bullet: false } }));
      slide.addText(runs, textOpts({ x: 8.3, y: 1.0, w: 4.3, h: 5.6, fontSize: S.body, color: C.white, paraSpaceAfter: 8 }));
    }
    if (o.notes) slide.addNotes(o.notes);
    return { slide, body: box(0.9, 1.0, 11.5, 3.0) };
  }

  function footer(slide, o) {
    if (o.note || o.source) {
      const runs = [];
      if (o.note) runs.push({ text: o.note, options: { breakLine: !!o.source } });
      if (o.source) runs.push({ text: o.source });
      slide.addText(runs, textOpts({ x: Z.margin, y: Z.footer.y - 0.15, w: 11.3, h: 0.5, fontSize: S.footnote, color: C.grey, valign: 'bottom' }));
    }
  }

  function content(o) {
    const slide = pres.addSlide({ masterName: 'CONTENT' });
    pageCount++;
    slide.addText(o.title, textOpts({ x: Z.title.x, y: Z.title.y, w: Z.title.w, h: Z.title.h, fontSize: o.titleSize || S.title, color: C.green, valign: 'middle', lineSpacingMultiple: 1.05 }));
    let bodyTop = Z.body.y;
    if (o.subtitle) {
      slide.addText(o.subtitle, textOpts({ x: Z.subtitle.x, y: Z.subtitle.y, w: Z.subtitle.w, h: Z.subtitle.h, fontSize: S.subtitle, color: C.grey }));
    } else {
      bodyTop = Z.body.y - 0.25;
    }
    if (o.tracker) slide.addText(o.tracker, textOpts({ x: 9.3, y: 0.15, w: o.sticker ? 1.85 : 3.55, h: 0.3, fontSize: S.small, color: C.grey, align: 'right' }));
    if (o.sticker) {
      slide.addShape(pres.ShapeType.rect, { x: 11.3, y: 0.0, w: 1.55, h: 0.32, fill: { color: C.green2 }, line: noLine(C.green2) });
      slide.addText(o.sticker, textOpts({ x: 11.3, y: 0.0, w: 1.55, h: 0.32, fontSize: S.small, color: C.white, align: 'center', valign: 'middle' }));
    }
    footer(slide, o);
    if (o.notes) slide.addNotes(o.notes);
    return { slide, body: box(Z.body.x, bodyTop, Z.body.w, Z.body.h + (Z.body.y - bodyTop)) };
  }

  // left green wedge with the message, exhibit on the right
  function panel(o) {
    const slide = pres.addSlide({ masterName: 'CONTENT' });
    pageCount++;
    const pw = o.panelWidth || 4.4;
    slide.addShape(pres.ShapeType.homePlate, { x: 0, y: 0, w: pw, h: 7.5, fill: { color: C.green }, line: noLine(C.green) });
    if (o.eyebrow) slide.addText(o.eyebrow, textOpts({ x: 0.5, y: 0.5, w: pw - 1.2, h: 0.4, fontSize: S.label, color: C.lime, charSpacing: 2 }));
    slide.addText(o.panelTitle, textOpts({ x: 0.5, y: 1.0, w: pw - 1.3, h: 2.2, fontSize: 24, color: C.white, lineSpacingMultiple: 1.05 }));
    if (o.panelText) slide.addText(o.panelText, textOpts({ x: 0.5, y: 3.3, w: pw - 1.3, h: 1.8, fontSize: S.body, color: C.white }));   // the wedge narrows below y 5.1
    if (o.title) slide.addText(o.title, textOpts({ x: pw + 0.3, y: Z.title.y, w: 12.83 - pw - 0.3, h: Z.title.h, fontSize: S.title, color: C.green, valign: 'middle' }));
    if (o.source || o.note) {
      // the footer starts right of the wedge, not at the margin
      const runs = [];
      if (o.note) runs.push({ text: o.note, options: { breakLine: !!o.source } });
      if (o.source) runs.push({ text: o.source });
      slide.addText(runs, textOpts({ x: pw + 0.3, y: Z.footer.y - 0.15, w: 12.83 - pw - 0.3, h: 0.5, fontSize: S.footnote, color: C.grey, valign: 'bottom' }));
    }
    if (o.notes) slide.addNotes(o.notes);
    const top = o.title ? Z.body.y - 0.25 : 0.7;
    return { slide, body: box(pw + 0.3, top, 12.83 - pw - 0.3, 6.6 - top) };
  }

  function quoteSlide(o) {
    const slide = pres.addSlide({ masterName: 'DARK' });
    pageCount++;
    if (o.eyebrow) slide.addText(o.eyebrow, textOpts({ x: 0.9, y: 0.6, w: 8, h: 0.4, fontSize: S.label, color: C.lime, charSpacing: 2 }));
    slide.addText('“', textOpts({ x: 0.8, y: 1.0, w: 1, h: 1, fontSize: 60, color: C.bright }));
    slide.addText(o.quote, textOpts({ x: 0.9, y: 1.7, w: 11.4, h: 2.6, fontSize: o.size || 22, color: C.white, lineSpacingMultiple: 1.2 }));
    if (o.who) slide.addText(o.who, textOpts({ x: 0.9, y: 4.4, w: 11.4, h: 0.4, fontSize: S.label, color: C.lime }));
    if (o.meaning) {
      slide.addShape(pres.ShapeType.line, { x: 0.9, y: 5.0, w: 11.4, h: 0, line: { color: C.white, width: 0.5 } });
      slide.addText(o.meaning, textOpts({ x: 0.9, y: 5.15, w: 11.4, h: 1.4, fontSize: S.body, color: C.white }));
    }
    if (o.notes) slide.addNotes(o.notes);
    return { slide, body: box(0.9, 1.7, 11.4, 4.0) };
  }

  // ----- components -----
  function text(s, o) {
    const b = o.box || s.body;
    s.slide.addText(o.text, textOpts(Object.assign({ x: b.x, y: b.y, w: b.w, h: b.h }, o)));
  }
  function rule(s, o) {
    s.slide.addShape(pres.ShapeType.line, { x: o.x, y: o.y, w: o.w, h: 0, line: { color: o.color || C.rule, width: o.width || 0.75 } });
  }
  function twoCols(s, leftFrac, gap) {
    const b = s.body; gap = gap == null ? 0.4 : gap;
    const lw = (b.w - gap) * (leftFrac == null ? 0.5 : leftFrac);
    return { left: box(b.x, b.y, lw, b.h), right: box(b.x + lw + gap, b.y, b.w - lw - gap, b.h) };
  }

  function execSummary(s, rows, o) {
    o = o || {}; const b = o.box || s.body;
    const n = rows.length; const rh = b.h / n;
    const labelW = o.labelW || 1.7; const glyphW = rows.some(r => r.glyph) ? 0.55 : 0;
    rows.forEach((r, i) => {
      const y = b.y + i * rh;
      rule(s, { x: b.x, y: y, w: b.w, color: C.rule });
      if (r.glyph) s.slide.addText(r.glyph, textOpts({ x: b.x, y: y + 0.12, w: glyphW, h: rh - 0.2, fontSize: 20, valign: 'top' }));
      s.slide.addText((r.label || '').toUpperCase(), textOpts({ x: b.x + glyphW, y: y + 0.14, w: labelW, h: 0.4, fontSize: S.small, color: r.accent ? C.green : C.grey, charSpacing: 1.5, bold: true }));
      const tx = b.x + glyphW + labelW + 0.2; const tw = b.w - glyphW - labelW - 0.2;
      s.slide.addText(r.claim, textOpts({ x: tx, y: y + 0.1, w: tw, h: 0.5, fontSize: o.claimSize || 15, bold: true, color: r.accent ? C.green : C.ink }));
      if (r.support) s.slide.addText(r.support, textOpts({ x: tx, y: y + 0.58, w: tw, h: Math.max(0.1, rh - 0.62), fontSize: S.body - 1, color: C.grey }));
    });
    rule(s, { x: b.x, y: b.y + b.h, w: b.w, color: C.rule });
  }

  function columns(s, items, o) {
    o = o || {}; const b = o.box || s.body; const n = items.length; const gap = o.gap == null ? 0.35 : o.gap;
    const cw = (b.w - gap * (n - 1)) / n;
    items.forEach((it, i) => {
      const x = b.x + i * (cw + gap); let y = b.y;
      if (o.numbered) {
        s.slide.addShape(pres.ShapeType.ellipse, { x: x, y: y, w: 0.5, h: 0.5, fill: { color: C.green }, line: noLine(C.green) });
        s.slide.addText(String(i + 1), textOpts({ x: x, y: y, w: 0.5, h: 0.5, fontSize: 14, bold: true, color: C.white, align: 'center', valign: 'middle' }));
        y += 0.7;
      } else if (it.iconData || it.glyph) {
        const d = o.iconSize || 0.8;
        s.slide.addShape(pres.ShapeType.ellipse, { x: x, y: y, w: d, h: d, fill: { color: C.white }, line: { color: C.green2, width: 1.25 } });
        if (it.iconData) s.slide.addImage({ data: it.iconData, x: x + d * 0.22, y: y + d * 0.22, w: d * 0.56, h: d * 0.56 });
        else s.slide.addText(it.glyph, textOpts({ x: x, y: y, w: d, h: d, fontSize: 20, align: 'center', valign: 'middle' }));
        y += d + 0.2;
      }
      if (o.topRule) rule(s, { x: x, y: y, w: cw, color: C.green, width: 1.5 });
      s.slide.addText(it.label, textOpts({ x: x, y: y + (o.topRule ? 0.1 : 0), w: cw, h: o.labelH || 0.6, fontSize: o.labelSize || 14, bold: true, color: C.green }));
      s.slide.addText(it.text, textOpts({ x: x, y: y + (o.labelH || 0.6) + (o.topRule ? 0.1 : 0), w: cw, h: b.h - (y - b.y) - (o.labelH || 0.6), fontSize: o.textSize || S.body - 1, color: C.ink }));
      if (it.foot) s.slide.addText(it.foot, textOpts({ x: x, y: b.y + b.h - 0.5, w: cw, h: 0.5, fontSize: S.label, color: C.grey, valign: 'bottom' }));
    });
  }

  function cards(s, items, o) {
    o = o || {}; const b = o.box || s.body; const cols = o.cols || Math.min(items.length, 3);
    const rows = o.rows || Math.ceil(items.length / cols); const gap = o.gap == null ? 0.3 : o.gap;
    const cw = (b.w - gap * (cols - 1)) / cols; const ch = (b.h - gap * (rows - 1)) / rows;
    items.forEach((it, i) => {
      const x = b.x + (i % cols) * (cw + gap); const y = b.y + Math.floor(i / cols) * (ch + gap);
      const fill = it.fill || o.fill || C.white;
      s.slide.addShape(pres.ShapeType.rect, { x, y, w: cw, h: ch, fill: { color: fill }, line: { color: it.border || C.rule, width: 0.75 } });
      let ty = y + 0.18; const pad = 0.22;
      if (it.glyph || it.iconData) {
        if (it.iconData) s.slide.addImage({ data: it.iconData, x: x + pad, y: ty, w: 0.42, h: 0.42 });
        else s.slide.addText(it.glyph, textOpts({ x: x + pad, y: ty - 0.04, w: 0.5, h: 0.5, fontSize: 20 }));
      }
      const tx = (it.glyph || it.iconData) ? x + pad + 0.55 : x + pad;
      if (it.eyebrow) { s.slide.addText(it.eyebrow.toUpperCase(), textOpts({ x: tx, y: ty, w: cw - (tx - x) - pad, h: 0.3, fontSize: S.small, color: it.eyebrowColor || C.grey, charSpacing: 1.2, bold: true })); ty += 0.3; }
      s.slide.addText(it.title, textOpts({ x: tx, y: ty, w: cw - (tx - x) - pad, h: o.titleH || 0.65, fontSize: o.titleSize || 14, bold: true, color: it.titleColor || C.green, lineSpacingMultiple: 1.05 }));
      ty += (o.titleH || 0.65) + 0.05;
      if (it.text) s.slide.addText(it.text, textOpts({ x: x + pad, y: ty, w: cw - 2 * pad, h: y + ch - ty - (it.foot ? 0.5 : 0.15), fontSize: o.textSize || S.body - 1, color: C.ink }));
      if (it.foot) s.slide.addText(it.foot, textOpts({ x: x + pad, y: y + ch - 0.5, w: cw - 2 * pad, h: 0.4, fontSize: S.label, color: it.footColor || C.grey, valign: 'bottom' }));
    });
  }

  function kpis(s, items, o) {
    o = o || {}; const b = o.box || s.body; const n = items.length; const cw = b.w / n;
    const d = o.ring === false ? 0 : (o.diameter || 1.7);
    items.forEach((it, i) => {
      const cx = b.x + i * cw + cw / 2;
      if (d) {
        s.slide.addShape(pres.ShapeType.ellipse, { x: cx - d / 2, y: b.y, w: d, h: d, fill: { color: it.fill || C.white }, line: { color: it.color || C.green2, width: 1.5 } });
        s.slide.addText(it.value, textOpts({ x: cx - d / 2, y: b.y, w: d, h: d, fontSize: it.size || 26, bold: true, color: it.color || C.green, align: 'center', valign: 'middle' }));
        s.slide.addText(it.label, textOpts({ x: cx - cw / 2 + 0.1, y: b.y + d + 0.12, w: cw - 0.2, h: 0.8, fontSize: S.label, color: C.ink, align: 'center' }));
      } else {
        s.slide.addText(it.value, textOpts({ x: cx - cw / 2, y: b.y, w: cw, h: 1.0, fontSize: it.size || S.kpi, bold: true, color: it.color || C.green, align: o.align || 'left', valign: 'bottom' }));
        s.slide.addText(it.label, textOpts({ x: cx - cw / 2, y: b.y + 1.05, w: cw - 0.2, h: 0.8, fontSize: S.label, color: C.ink, align: o.align || 'left' }));
      }
    });
  }

  function table(s, o) {
    const b = o.box || s.body; const fs = o.fontSize || S.body - 1;
    const hair = { type: 'solid', pt: 0.5, color: C.rule };
    const rows = [];
    if (o.header) rows.push(o.header.map((h, j) => ({ text: h, options: { bold: true, color: C.green, fill: { color: C.tint }, align: (o.align && o.align[j]) || 'left', valign: 'middle' } })));
    o.rows.forEach(r => rows.push(r.map((cell, j) => {
      const c = cell && typeof cell === 'object' ? cell : { text: cell };
      return { text: String(c.text == null ? '' : c.text), options: Object.assign({ color: C.ink, align: (o.align && o.align[j]) || 'left', valign: 'top', bold: j === 0 && o.boldFirst !== false }, c.options || {}) };
    })));
    s.slide.addTable(rows, { x: b.x, y: b.y, w: b.w, colW: o.colW, fontFace: FONT, fontSize: fs, color: C.ink, border: hair, margin: o.cellMargin == null ? 0.06 : o.cellMargin, rowH: o.rowH, autoPage: false, valign: 'top' });
  }

  function chevrons(s, steps, o) {
    o = o || {}; const b = o.box || s.body; const n = steps.length; const gap = 0.08;
    const cw = (b.w - gap * (n - 1)) / n; const h = o.height || 0.75;
    steps.forEach((st, i) => {
      const x = b.x + i * (cw + gap); const active = o.active == null ? true : (Array.isArray(o.active) ? o.active.includes(i) : i <= o.active);
      const shape = i === 0 ? pres.ShapeType.homePlate : pres.ShapeType.chevron;
      s.slide.addShape(shape, { x, y: b.y, w: cw, h, fill: { color: active ? C.green : C.rule }, line: noLine(active ? C.green : C.rule) });
      s.slide.addText(st.label, textOpts({ x: x + (i === 0 ? 0.15 : 0.35), y: b.y, w: cw - (i === 0 ? 0.35 : 0.5), h, fontSize: o.labelSize || 13, bold: true, color: active ? C.white : C.ink, valign: 'middle' }));
      if (st.text) s.slide.addText(st.text, textOpts({ x: x + 0.1, y: b.y + h + 0.15, w: cw - 0.3, h: b.h - h - 0.15, fontSize: o.textSize || S.body - 1, color: C.ink }));
    });
  }

  // lanes: [{ name, sub, items: [{ col, label, status: 'built'|'progress'|'planned', span }] }]
  function timelineQuarters(s, o) {
    const b = o.box || s.body; const cols = o.columns; const laneW = o.laneW || 3.0;
    const cw = (b.w - laneW) / cols.length; const hh = 0.42;
    cols.forEach((c, j) => {
      const x = b.x + laneW + j * cw; const today = o.today === j;
      if (today) s.slide.addShape(pres.ShapeType.rect, { x: x + 0.04, y: b.y, w: cw - 0.08, h: hh, fill: { color: C.greenTint }, line: noLine(C.greenTint) });
      s.slide.addText(c, textOpts({ x: x, y: b.y, w: cw, h: hh, fontSize: S.label, bold: true, color: today ? C.green : C.grey, align: 'center', valign: 'middle', charSpacing: 1 }));
    });
    s.slide.addText((o.laneHeader || 'What lands').toUpperCase(), textOpts({ x: b.x, y: b.y, w: laneW, h: hh, fontSize: S.small, bold: true, color: C.grey, valign: 'middle', charSpacing: 1.2 }));
    rule(s, { x: b.x, y: b.y + hh, w: b.w, color: C.ink, width: 0.75 });
    const n = o.lanes.length; const lh = (b.h - hh) / n;
    const style = { built: { fill: C.green, color: C.white, line: C.green, dash: 'solid' }, progress: { fill: C.amberTint, color: C.ink, line: C.amber, dash: 'dash' }, planned: { fill: C.white, color: C.ink, line: C.grey, dash: 'solid' } };
    o.lanes.forEach((ln, i) => {
      const y = b.y + hh + i * lh;
      s.slide.addText(ln.name, textOpts({ x: b.x, y: y + 0.08, w: laneW - 0.1, h: 0.3, fontSize: S.body - 1, bold: true, color: C.ink }));
      if (ln.sub) s.slide.addText(ln.sub, textOpts({ x: b.x, y: y + 0.36, w: laneW - 0.1, h: 0.3, fontSize: S.small, color: C.grey }));
      const groups = {};
      (ln.items || []).forEach(it => { (groups[it.col] = groups[it.col] || []).push(it); });
      Object.keys(groups).forEach(col => {
        const its = groups[col]; const ph = 0.3; const stack = its.length;
        const total = stack * ph + (stack - 1) * 0.06; let py = y + (lh - total) / 2;
        its.forEach(it => {
          const st = style[it.status] || style.planned; const span = it.span || 1;
          const x = b.x + laneW + Number(col) * cw + 0.08; const w = cw * span - 0.16;
          s.slide.addShape(pres.ShapeType.roundRect, { x, y: py, w, h: ph, rectRadius: 0.08, fill: { color: st.fill }, line: { color: st.line, width: 1, dashType: st.dash } });
          s.slide.addText(it.label, textOpts({ x, y: py, w, h: ph, fontSize: S.small, bold: true, color: st.color, align: 'center', valign: 'middle' }));
          py += ph + 0.06;
        });
      });
      rule(s, { x: b.x, y: y + lh, w: b.w, color: C.rule });
    });
  }

  function nextSteps(s, rows, o) {
    o = o || {}; const b = o.box || s.body;
    const colW = o.colW || [b.w * 0.26, b.w * 0.6, b.w * 0.14];
    table(s, { box: b, header: o.header || ['Who', 'Action', 'When'], rows: rows.map(r => [r.who, r.action, { text: r.when, options: { bold: true } }]), colW, align: ['left', 'left', 'right'], fontSize: o.fontSize || S.body - 1, rowH: o.rowH });
  }

  // a tinted box with a title, a claim and bullets (the decision panel, a takeaway panel)
  function boxPanel(s, o) {
    const b = o.box || s.body; const dark = o.dark;
    s.slide.addShape(pres.ShapeType.rect, { x: b.x, y: b.y, w: b.w, h: b.h, fill: { color: dark ? C.green : (o.fill || C.greenTint) }, line: noLine(dark ? C.green : (o.fill || C.greenTint)) });
    const pad = b.h < 0.8 ? 0.1 : 0.25; let y = b.y + pad;   // a short strip keeps room for its one line of text
    if (o.eyebrow) { s.slide.addText(o.eyebrow.toUpperCase(), textOpts({ x: b.x + pad, y, w: b.w - 2 * pad, h: 0.3, fontSize: S.small, bold: true, color: dark ? C.lime : C.green, charSpacing: 1.2 })); y += 0.35; }
    if (o.claim) { s.slide.addText(o.claim, textOpts({ x: b.x + pad, y, w: b.w - 2 * pad, h: o.claimH || 1.1, fontSize: o.claimSize || 15, bold: true, color: dark ? C.white : C.ink })); y += (o.claimH || 1.1) + 0.05; }
    if (o.bullets && o.bullets.length) {
      const runs = o.bullets.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < o.bullets.length - 1 } }));
      s.slide.addText(runs, textOpts({ x: b.x + pad, y, w: b.w - 2 * pad, h: b.y + b.h - y - pad, fontSize: S.body - 1, color: dark ? C.white : C.ink, paraSpaceAfter: 6 }));
    } else if (o.text) {
      s.slide.addText(o.text, textOpts({ x: b.x + pad, y, w: b.w - 2 * pad, h: b.y + b.h - y - pad, fontSize: S.body - 1, color: dark ? C.white : C.ink }));
    }
  }

  function takeaway(s, textStr, o) {
    o = o || {}; const b = s.body; const h = o.height || 0.6;
    const y = o.y == null ? b.y + b.h - h : o.y;
    s.slide.addShape(pres.ShapeType.rect, { x: b.x, y, w: b.w, h, fill: { color: o.dark ? C.green : C.greenTint }, line: noLine(o.dark ? C.green : C.greenTint) });
    s.slide.addText(textStr, textOpts({ x: b.x + 0.25, y, w: b.w - 0.5, h, fontSize: o.size || S.body, bold: true, color: o.dark ? C.white : C.ink, valign: 'middle' }));
  }

  // Traffic lights: a status cell is a soft fill with no word in it, and one legend under the exhibit names
  // each colour; the reason goes in its own column. Levels: good, watch, bad, none.
  const LIGHTS = { good: C.lightGood, watch: C.lightWatch, bad: C.lightBad, none: C.rule };
  function light(level) {
    return { text: '', options: { fill: { color: LIGHTS[level] || LIGHTS.none } } };
  }

  function lightLegend(s, items, o) {
    o = o || {}; const b = s.body; const sw = 0.2; const size = o.size || S.small;
    let x = o.x == null ? b.x : o.x; const y = o.y == null ? b.y + b.h - 0.3 : o.y;
    items.forEach(([level, label]) => {
      const col = LIGHTS[level] || LIGHTS.none; const w = label.length * size * 0.006 + 0.1;
      s.slide.addShape(pres.ShapeType.rect, { x, y: y + 0.05, w: sw, h: sw, fill: { color: col }, line: noLine(col) });
      s.slide.addText(label, textOpts({ x: x + sw + 0.08, y, w, h: 0.3, fontSize: size, color: C.ink, valign: 'middle' }));
      x += sw + 0.08 + w + 0.25;
    });
  }

  function callout(s, o) {
    s.slide.addShape(pres.ShapeType.roundRect, { x: o.x, y: o.y, w: o.w, h: o.h || 0.36, rectRadius: 0.1, fill: { color: o.fill || C.bright }, line: noLine(o.fill || C.bright) });
    s.slide.addText(o.text, textOpts({ x: o.x, y: o.y, w: o.w, h: o.h || 0.36, fontSize: o.size || S.small, bold: true, color: o.color || C.white, align: 'center', valign: 'middle' }));
  }

  function bigNumber(s, o) {
    const b = o.box || s.body;
    s.slide.addText(o.value, textOpts({ x: b.x, y: b.y, w: b.w, h: o.valueH || 0.9, fontSize: o.size || S.kpi, bold: true, color: o.color || C.green, valign: 'bottom' }));
    if (o.label) s.slide.addText(o.label, textOpts({ x: b.x, y: b.y + (o.valueH || 0.9) + 0.05, w: b.w, h: b.h - (o.valueH || 0.9) - 0.05, fontSize: S.body - 1, color: C.grey }));
  }

  // horizontal bars drawn as shapes: direct labels, one highlight colour, no chart chrome
  function barsH(s, o) {
    const b = o.box || s.body; const items = o.items; const labelW = o.labelW || 2.4; const valW = 0.7;
    const max = Math.max(o.max || 0, ...items.map(i => i.value), 1e-9);   // all zeros draw the minimum bar, never NaN
    let bh = o.barH || 0.38; let gap = o.gap == null ? 0.22 : o.gap;
    const need = items.length * (bh + gap) - gap;
    if (need > b.h) { const k = b.h / need; bh *= k; gap *= k; }            // more rows than the box holds: shrink the pitch
    const trackW = b.w - labelW - valW - 0.3;
    items.forEach((it, i) => {
      const y = b.y + i * (bh + gap);
      s.slide.addText(it.label, textOpts({ x: b.x, y, w: labelW - 0.15, h: bh, fontSize: S.body - 1, color: C.ink, valign: 'middle', align: o.labelAlign || 'left' }));
      const w = Math.max(0.05, trackW * Math.min(1, it.value / max));     // a value past an explicit max stops at the track end
      const col = it.color || (it.hi ? C.green : C.rule);
      s.slide.addShape(pres.ShapeType.rect, { x: b.x + labelW, y, w, h: bh, fill: { color: col }, line: noLine(col) });
      const vx = b.x + labelW + w + 0.1; const vw = Math.max(1.0, b.x + b.w - vx);
      s.slide.addText(it.valueLabel || String(it.value), textOpts({ x: vx, y, w: vw, h: bh, fontSize: S.body, bold: true, color: it.hi ? C.green : C.ink, valign: 'middle' }));
    });
  }

  // native chart with quiet defaults; type: 'line' | 'col' | 'bar'
  function chart(s, o) {
    const b = o.box || s.body; const type = o.type || 'col';
    const ct = type === 'line' ? pres.ChartType.line : pres.ChartType.bar;
    const data = o.series.map(se => ({ name: se.name, labels: o.labels, values: se.values }));
    const colors = o.colors || [C.green, C.rule, C.mint, C.slate];
    s.slide.addChart(ct, data, Object.assign({
      x: b.x, y: b.y, w: b.w, h: b.h, barDir: type === 'bar' ? 'bar' : 'col', chartColors: colors,
      showLegend: o.series.length > 1, legendPos: 'b', legendFontSize: S.small, legendFontFace: FONT,
      showValue: o.showValue !== false, dataLabelPosition: type === 'line' ? 't' : 'outEnd', dataLabelFontSize: S.small, dataLabelFontFace: FONT, dataLabelColor: C.ink,
      catAxisLabelFontSize: S.small, catAxisLabelFontFace: FONT, catAxisLabelColor: C.grey, catGridLine: { style: 'none' },
      valAxisHidden: true, valGridLine: { style: 'none' }, barGapWidthPct: 60, lineSize: 2, lineDataSymbol: 'none',
      showTitle: false,
    }, o.options || {}));
  }

  // centre hub with satellites joined by thin lines
  function hub(s, o) {
    const b = o.box || s.body; const cx = b.x + b.w / 2; const cy = b.y + b.h / 2;
    const hd = o.hubDiameter || 1.9; const sw = o.satW || 2.2; const sh = o.satH || 0.8;
    const n = o.satellites.length; const rx = b.w / 2 - sw / 2 - 0.05; const ry = b.h / 2 - sh / 2 - 0.05;
    o.satellites.forEach((sat, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      const sx = cx + rx * Math.cos(a); const sy = cy + ry * Math.sin(a);
      // line from satellite centre to hub centre, drawn behind both
      const dx = cx - sx; const dy = cy - sy;
      s.slide.addShape(pres.ShapeType.line, { x: Math.min(sx, cx), y: Math.min(sy, cy), w: Math.abs(dx), h: Math.abs(dy), flipH: (dx < 0) !== (dy < 0), line: { color: C.rule, width: 1.25 } });
    });
    o.satellites.forEach((sat, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      const sx = cx + rx * Math.cos(a) - sw / 2; const sy = cy + ry * Math.sin(a) - sh / 2;
      s.slide.addShape(pres.ShapeType.roundRect, { x: sx, y: sy, w: sw, h: sh, rectRadius: 0.08, fill: { color: C.white }, line: { color: C.rule, width: 0.75 } });
      const gx = sat.glyph ? 0.5 : 0;
      if (sat.glyph) s.slide.addText(sat.glyph, textOpts({ x: sx + 0.12, y: sy, w: 0.5, h: sh, fontSize: 18, valign: 'middle' }));
      s.slide.addText([{ text: sat.label, options: { bold: true, breakLine: !!sat.sub } }].concat(sat.sub ? [{ text: sat.sub, options: { color: C.grey, fontSize: S.small } }] : []), textOpts({ x: sx + 0.15 + gx, y: sy, w: sw - 0.25 - gx, h: sh, fontSize: S.body - 1, color: C.ink, valign: 'middle' }));
    });
    s.slide.addShape(pres.ShapeType.ellipse, { x: cx - hd / 2, y: cy - hd / 2, w: hd, h: hd, fill: { color: C.green }, line: noLine(C.green) });
    s.slide.addText([{ text: o.center.label, options: { bold: true, fontSize: o.center.size || 24, breakLine: !!o.center.sub } }].concat(o.center.sub ? [{ text: o.center.sub, options: { fontSize: S.small } }] : []), textOpts({ x: cx - hd / 2, y: cy - hd / 2, w: hd, h: hd, color: C.white, align: 'center', valign: 'middle' }));
  }

  // stage cards left to right with arrows (bronze -> silver -> gold, input -> process -> output)
  function stages(s, items, o) {
    o = o || {}; const b = o.box || s.body; const n = items.length; const arrow = 0.45; const gap = 0.15;
    const cw = (b.w - (arrow + 2 * gap) * (n - 1)) / n;
    items.forEach((it, i) => {
      const x = b.x + i * (cw + arrow + 2 * gap);
      s.slide.addShape(pres.ShapeType.rect, { x, y: b.y, w: cw, h: b.h, fill: { color: C.white }, line: { color: it.accent ? C.green : C.rule, width: it.accent ? 1.5 : 0.75 } });
      const pad = 0.22; let y = b.y + pad;
      if (it.glyph) { s.slide.addText(it.glyph, textOpts({ x: x + pad, y: y - 0.05, w: 0.6, h: 0.5, fontSize: 22 })); }
      if (it.eyebrow) s.slide.addText(it.eyebrow.toUpperCase(), textOpts({ x: x + pad + (it.glyph ? 0.6 : 0), y: y + 0.08, w: cw - 2 * pad, h: 0.3, fontSize: S.small, bold: true, color: it.eyebrowColor || C.grey, charSpacing: 1.2 }));
      y += 0.6;
      s.slide.addText(it.title, textOpts({ x: x + pad, y, w: cw - 2 * pad, h: 0.75, fontSize: 14, bold: true, color: C.green }));
      y += 0.85;
      const blocks = it.blocks || [];
      const bh = (b.y + b.h - y - pad) / Math.max(1, blocks.length);
      blocks.forEach(bl => {
        s.slide.addText([{ text: bl.label + ': ', options: { bold: true, color: bl.color || C.grey } }, { text: bl.text }], textOpts({ x: x + pad, y, w: cw - 2 * pad, h: bh, fontSize: S.body - 1, color: C.ink }));
        y += bh;
      });
      if (i < n - 1) s.slide.addShape(pres.ShapeType.rightArrow, { x: x + cw + gap, y: b.y + b.h / 2 - 0.18, w: arrow, h: 0.36, fill: { color: C.grey }, line: noLine(C.grey) });
    });
  }

  // a titled bullet or numbered list in a box (one level, parallel items)
  function bulletList(s, o) {
    const b = o.box || s.body; let y = b.y;
    if (o.fill) s.slide.addShape(pres.ShapeType.rect, { x: b.x, y: b.y, w: b.w, h: b.h, fill: { color: o.fill }, line: { color: o.border || o.fill, width: 0.75 } });
    const pad = o.fill ? 0.22 : 0;
    const lead = o.iconData ? 0.5 : (o.glyph ? 0.6 : 0);   // room left of the title for an icon or glyph
    if (o.iconData) s.slide.addImage({ data: o.iconData, x: b.x + pad, y: y + pad + 0.04, w: 0.36, h: 0.36 });
    else if (o.glyph) s.slide.addText(o.glyph, textOpts({ x: b.x + pad, y: y + pad - 0.05, w: 0.6, h: 0.5, fontSize: 22 }));
    if (o.title) { s.slide.addText(o.title, textOpts({ x: b.x + pad + lead, y: y + pad, w: b.w - 2 * pad - lead, h: 0.45, fontSize: o.titleSize || 14, bold: true, color: o.titleColor || C.green, valign: 'middle' })); y += 0.55; }
    const runs = o.items.map((t, i) => ({ text: t, options: { bullet: o.numbered ? { type: 'number' } : (o.bullets === false ? false : true), breakLine: i < o.items.length - 1 } }));
    s.slide.addText(runs, textOpts({ x: b.x + pad, y: y + pad, w: b.w - 2 * pad, h: b.y + b.h - y - 2 * pad, fontSize: o.fontSize || S.body - 1, color: o.color || C.ink, paraSpaceAfter: o.space == null ? 6 : o.space }));
  }

  async function icon(s, o) {
    const data = await iconData(o.name, o.color);
    s.slide.addImage({ data, x: o.x, y: o.y, w: o.size || 0.5, h: o.size || 0.5 });
  }

  async function save(file) {
    await pres.writeFile({ fileName: file });
    return file;
  }

  return { pres, T, C, S, box, sub, cover, divider, content, panel, quoteSlide, execSummary, columns, cards, kpis, table, chevrons, timelineQuarters, nextSteps, boxPanel, twoCols, takeaway, callout, light, lightLegend, bigNumber, barsH, chart, hub, stages, bulletList, text, rule, icon, icons, save, pages: () => pageCount };
}

module.exports = { createDeck, THEME: T };
