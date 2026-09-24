// Independent spatial check: PyMuPDF words intersecting the browser's rects.
// Bigram overlap tolerates token splits; precision penalizes unrelated text.
const normalize = s => s.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
const grams = s => {
  const out = new Map();
  for (let i = 0; i < s.length - 1; i++) out.set(s.slice(i, i + 2), (out.get(s.slice(i, i + 2)) || 0) + 1);
  return out;
};
export function overlapText(quote, covered) {
  const a = grams(normalize(quote)), b = grams(normalize(covered));
  const size = x => [...x.values()].reduce((s, n) => s + n, 0);
  const overlap = [...a].reduce((s, [k, n]) => s + Math.min(n, b.get(k) || 0), 0);
  const recall = overlap / Math.max(1, size(a)), precision = overlap / Math.max(1, size(b));
  return { recall, precision, f1: 2 * recall * precision / (recall + precision || 1) };
}
export function coveredText(anchor, pages) {
  if (!anchor || !pages[anchor.page - 1]) return '';
  const p = pages[anchor.page - 1];
  return p.words.filter(([x1, y1, x2, y2]) => anchor.rects.some(r => {
    const left = r.left * p.width / 100, top = r.top * p.height / 100;
    const right = left + r.width * p.width / 100, bottom = top + r.height * p.height / 100;
    const dx = Math.max(0, Math.min(right, x2) - Math.max(left, x1));
    const dy = Math.max(0, Math.min(bottom, y2) - Math.max(top, y1));
    return dx / Math.max(1, x2 - x1) > .45 && dy / Math.max(1, y2 - y1) > .3;
  })).map(w => w[4]).join(' ');
}
export function scoreAnchor(row, highlight, pages) {
  const textUnderRects = coveredText(row.anchor, pages);
  const score = overlapText(highlight.raw_text, textUnderRects);
  const storedTextAgreement = row.stored && row.text ? {
    samePage: row.stored.page === row.text.page,
    firstRectDistancePercent: Math.hypot(row.stored.rects[0].top - row.text.rects[0].top, row.stored.rects[0].left - row.text.rects[0].left),
    ...overlapText(coveredText(row.stored, pages), coveredText(row.text, pages)),
  } : null;
  return { ...row, quote: highlight.raw_text, textUnderRects, ...score,
    correct: Boolean(row.anchor) && score.recall >= .85 && score.precision >= .70, storedTextAgreement };
}
export const percentile = (values, p) => values.length ? Math.round([...values].sort((a,b) => a-b)[Math.ceil(values.length * p) - 1]) : null;
