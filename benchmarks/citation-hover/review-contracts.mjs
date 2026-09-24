import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { modules, out } from './modules.mjs';

const variant = process.argv[2] || 'current';
const api = await modules(variant);
const line = (str, y, x = 50, height = 10) => ({ str, transform: [1, 0, 0, 1, x, y], width: Math.min(220, str.length * 4), height });
const xyz = (y, x = 50, page = 0) => [page, { name: 'XYZ' }, x, y];
function document(pages, destinations) {
  return {
    numPages: pages.length,
    getDestinations: async () => destinations,
    getDestination: async name => destinations[name] ?? null,
    getPageIndex: async ref => ref.num - 1,
    getPage: async number => ({ getViewport: () => ({ width: 600, height: 800 }), getTextContent: async () => ({ items: pages[number - 1] }) }),
  };
}
const results = [];
async function check(name, pages, destinations, target, expected, hints = []) {
  const doc = document(pages, destinations);
  const point = await api.resolveDestination(doc, '#' + target);
  const actual = await api.extractBibEntry(doc, point.page, point.x, point.y, hints[0] ?? null, hints[1] ?? null, target);
  results.push({ name, passed: actual === expected, expected, actual });
}

for (const target of ['cite.ref12', 'cite.unsrt.ref12', 'cite.alpha.ref12']) {
  await check('hyperref suffix is not a publisher number: ' + target,
    [[line('[1] Smith. Correct paper under a different label.', 700), line('[12] Jones. Unrelated twelfth publication.', 660)]],
    { [target]: xyz(710), 'cite.next': xyz(670) }, target, '[1] Smith. Correct paper under a different label.');
}
for (const target of ['Lpone.0243360.ref2', 'Rpone.0243360.ref2', 'pone.0243360.ref2']) {
  await check('PLOS explicit number: ' + target,
    [[line('1. Other reference with an offset destination.', 700), line('2. Smith. The intended PLOS publication.', 670), line('3. Jones. The next publication.', 640)]],
    { [target]: xyz(710) }, target, '2. Smith. The intended PLOS publication.');
}
await check('numbered section before References',
  [[line('5. Conclusions', 740), line('We show a different result in this section.', 720), line('References', 600, 50, 14), line('5. Smith. The fifth bibliography entry.', 560), line('6. Jones. A following reference.', 520)]],
  { bib5: xyz(570) }, 'bib5', '5. Smith. The fifth bibliography entry.');
await check('numbered statement in the other column',
  [[line('(2) we show an unrelated mathematical result.', 620), line('Left column text continues here.', 600), line('(2) Smith. Right column bibliography entry.', 620, 330), line('(3) Jones. Next reference.', 580, 330)]],
  { 'cite.left': xyz(630), bm_CR2: xyz(630, 320), bm_CR3: xyz(590, 320) }, 'bm_CR2', '(2) Smith. Right column bibliography entry.');
await check('footer does not force continuation onto the next page',
  [[line('2. Smith. Last reference on a partial page.', 450), line('12', 40)], [line('Unrelated experimental results on the following page.', 740), line('This text must not enter the citation preview.', 720)]],
  { bib2: xyz(460) }, 'bib2', '2. Smith. Last reference on a partial page.');
await check('genuine page-edge continuation is preserved',
  [[line('2. Smith. A reference spanning', 65), line('12', 40)], [line('the page boundary with its remaining title.', 740), line('3. Jones. Next reference.', 710), line('13', 40)]],
  { bib2: xyz(75) }, 'bib2', '2. Smith. A reference spanning the page boundary with its remaining title.');
for (const text of ['A Survey of Language Model Interpretability.', 'Y LeCun, L Bottou. Learning useful representations.']) {
  await check('capital initial is not a heading: ' + text,
    [[line('Smith, J. (2024).', 700), line(text, 688), line('Jones, M. (2025). Another publication.', 660)]],
    { 'cite.smith2024': xyz(710), 'cite.jones2025': xyz(670) }, 'cite.smith2024', 'Smith, J. (2024). ' + text);
}
await check('real appendix heading stops extraction',
  [[line('Smith, J. (2024). A complete paper reference.', 700), line('A Supplementary experiments', 680, 50, 14), line('Unrelated appendix prose.', 660)]],
  { 'cite.smith2024': xyz(710) }, 'cite.smith2024', 'Smith, J. (2024). A complete paper reference.');
await check('a superscript before author initials is not a heading',
  [[line('𝑛', 705, 210, 6), line('Z. M. Kim. A scientific reference containing superscripts.', 700)]],
  { 'cite.kim2026': xyz(715) }, 'cite.kim2026', '𝑛 Z. M. Kim. A scientific reference containing superscripts.');
for (const kind of ['FitH', 'FitBH']) {
  await check(kind + ' anchors separate closely spaced author-year entries',
    [[line('Smith, J. (2024). The intended scientific article.', 700), line('Jones, M. (2025). A separate scientific article.', 685)]],
    { 'cite.smith2024': [0, { name: kind }, 710], 'cite.jones2025': [0, { name: kind }, 695] }, 'cite.smith2024', 'Smith, J. (2024). The intended scientific article.');
}
await check('missing x can identify the right column by y',
  [[line('Left, A. (2020). An unrelated reference.', 700), line('Left, B. (2021). More unrelated text.', 640), line('Smith, J. (2024). The right-column reference.', 600, 330), line('Jones, M. (2025). A following reference.', 565, 330)]],
  { 'cite.left': xyz(710), 'cite.right': xyz(650, 320), 'cite.smith2024': xyz(610, null), 'cite.jones2025': xyz(575, 320) }, 'cite.smith2024', 'Smith, J. (2024). The right-column reference.', ['smith', 2024]);
await check('ambiguous missing x declines to guess a column',
  [[line('Left, A. (2020). An unrelated reference.', 600), line('Right, B. (2021). Another unrelated reference.', 600, 330)]],
  { 'cite.left': xyz(610), 'cite.right': xyz(610, 320), 'cite.unknown': xyz(610, null) }, 'cite.unknown', null);

await fs.writeFile(path.join(out, variant + '-review-contracts.json'), JSON.stringify(results, null, 2));
console.log(`${results.filter(r => r.passed).length}/${results.length} review extraction regression checks passed.`);
for (const r of results.filter(r => !r.passed)) console.log(JSON.stringify(r));
if (!process.argv.includes('--record')) assert.ok(results.every(r => r.passed), 'Review extraction regressions failed');
