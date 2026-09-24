import { overlapText } from './score.mjs';

// Cross-check low PyMuPDF scores against actual DOM word boxes. Some PDFs'
// marked content is readable in pdf.js but absent from PyMuPDF extraction.
// This does not replace or relax the independent primary correctness scores.
export async function checkFlagged({ page, url, papers, anchors }) {
  let currentPaper;
  const results = [];
  for (const row of anchors.filter(a => a.anchor && !a.correct)) {
    const paper = papers.find(p => p.id === row.paperId);
    if (currentPaper !== row.paperId) {
      await page.goto(`${url}/reader-benchmark?paper=${row.paperId}`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !!window.annotationBenchmark);
      currentPaper = row.paperId;
    }
    await page.waitForSelector('.page .textLayer span', { state: 'attached' });
    const pageInput = page.getByRole('spinbutton', { name: 'Page number' });
    await pageInput.fill(String(row.anchor.page));
    await pageInput.press('Enter');
    await page.waitForSelector(`.page[data-page-number="${row.anchor.page}"] .textLayer span`, { state: 'attached', timeout: 15000 });
    const text = await page.evaluate(anchor => {
      const page = document.querySelector(`.page[data-page-number="${anchor.page}"]`);
      const box = page.getBoundingClientRect();
      const rects = anchor.rects.map(r => ({ left: box.left + r.left * box.width / 100, top: box.top + r.top * box.height / 100,
        width: r.width * box.width / 100, height: r.height * box.height / 100 }));
      const walker = document.createTreeWalker(page.querySelector('.textLayer'), NodeFilter.SHOW_TEXT);
      const words = [];
      while (walker.nextNode()) {
        const node = walker.currentNode;
        for (const match of node.textContent.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
          const w = range.getBoundingClientRect();
          if (w.width && w.height && rects.some(r => {
            const dx = Math.max(0, Math.min(r.left + r.width, w.right) - Math.max(r.left, w.left));
            const dy = Math.max(0, Math.min(r.top + r.height, w.bottom) - Math.max(r.top, w.top));
            return dx / w.width > .45 && dy / w.height > .3;
          })) words.push(match[0]);
        }
      }
      return words.join(' ');
    }, row.anchor);
    const quote = paper.highlights.find(h => h.id === row.id).raw_text;
    results.push({ id: row.id, paperId: row.paperId, domTextUnderRects: text, ...overlapText(quote, text) });
  }
  return results;
}
