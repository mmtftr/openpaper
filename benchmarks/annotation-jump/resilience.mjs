// Extra integration checks use real fixture rows, outside the baseline matrix.
export async function runResilience({ page, url, papers, anchors, trial, viewport }) {
  const checks = [];
  const usable = anchors.filter(a => a.correct);
  const paper = papers.find(p => p.highlights.filter(h => usable.some(a => a.id === h.id)).length >= 2);
  if (!paper) return checks;
  const targets = paper.highlights.filter(h => usable.some(a => a.id === h.id));
  const open = async p => {
    await page.goto(`${url}/reader-benchmark?paper=${p.id}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.annotationBenchmark);
    await page.waitForSelector('.page .textLayer span', { state: 'attached' });
  };
  const visible = id => page.evaluate(id => {
    const r = document.querySelector(`[data-highlight-id="${id}"][data-rect-index="0"]`)?.getBoundingClientRect();
    const c = document.querySelector('.pdfViewer')?.parentElement?.getBoundingClientRect();
    return !!(r && c && r.top >= c.top + 8 && r.bottom <= c.bottom - 8 && r.left >= c.left && r.right <= c.right + 1);
  }, id);
  await open(paper);
  await page.locator(`[data-thread-id="${targets[0].id}"]`).click({ position: { x: 12, y: 10 } });
  const rapid = await trial(paper, targets[1], 'rapid-replacement', false);
  await page.waitForTimeout(700);
  checks.push({ ...rapid, success: rapid.success && await visible(targets[1].id), checkedAgainAfterMs: 700 });
  checks.push(await trial(paper, targets[0], 'zoom-during-jump', true, async () => {
    await page.getByTitle('Zoom in', { exact: true }).click();
    await page.getByTitle('Zoom in', { exact: true }).click();
  }));
  await page.getByTitle('Fit width', { exact: true }).click();
  const resize = await trial(paper, targets[1], 'resize-during-jump', true, () => page.setViewportSize({ width: 1100, height: 800 }));
  await page.waitForTimeout(700);
  checks.push({ ...resize, success: resize.success && await visible(targets[1].id), checkedAgainAfterMs: 700 });
  await page.setViewportSize(viewport);

  // A manual wheel scroll must stop the correction, including during its tail.
  await trial(paper, targets[1], 'manual-scroll-setup');
  await page.mouse.move(400, 500);
  await page.mouse.wheel(0, 450);
  await page.waitForTimeout(150);
  const manualTop = await page.evaluate(() => document.querySelector('.pdfViewer').parentElement.scrollTop);
  await page.waitForTimeout(700);
  const afterTop = await page.evaluate(() => document.querySelector('.pdfViewer').parentElement.scrollTop);
  checks.push({ scenario: 'manual-scroll-cancels-correction', success: Math.abs(afterTop - manualTop) < 2 });

  // Exercise the still-separate citation prop path with real PDF text.
  const text = usable.find(a => a.paperId === paper.id).textUnderRects.split(' ').slice(0, 7).join(' ');
  await page.evaluate(text => window.annotationBenchmark.cite(text), text);
  await page.waitForSelector('.textLayer .highlight.selected', { timeout: 5000, state: 'attached' });
  checks.push({ scenario: 'citation-find-after-jump', success: await page.locator('.textLayer .highlight.selected').first().isVisible() });

  // An actually unlocatable fixture must fall back to its supplied page hint.
  const missing = anchors.find(a => !a.anchor && papers.some(p => p.id === a.paperId && p.highlights.some(h => h.id === a.id && h.page_number)));
  if (missing) {
    const p = papers.find(p => p.id === missing.paperId), h = p.highlights.find(h => h.id === missing.id);
    await open(p);
    await page.evaluate(hint => {
      const scroller = document.querySelector('.pdfViewer').parentElement;
      scroller.scrollTo({ top: hint <= 2 ? scroller.scrollHeight : 0, behavior: 'instant' });
    }, h.page_number);
    await page.locator(`[data-thread-id="${h.id}"]`).click({ position: { x: 12, y: 10 } });
    const started = Date.now();
    let success = false;
    while (Date.now() - started < 2500) {
      success = await page.evaluate(hint => {
        const c = document.querySelector('.pdfViewer')?.parentElement?.getBoundingClientRect();
        const p = document.querySelector(`.page[data-page-number="${hint}"]`)?.getBoundingClientRect();
        return !!(c && p && p.top >= c.top - 2 && p.top < c.bottom - 8);
      }, h.page_number);
      if (success) break;
      await page.waitForTimeout(25);
    }
    checks.push({ scenario: 'unlocatable-page-fallback', id: h.id, page: h.page_number, success, latencyMs: Date.now() - started });
  }
  return checks;
}
