// Deterministic races against actual PdfReader controls. The dev harness holds
// getTextContent for one real fixture page; it never substitutes anchor results.
export async function runNavigationChecks({ page, url, papers, anchors, trial }) {
  const checks = [];
  const anchor = anchors.find(a => a.correct && a.anchor.page > 2 && papers.some(p =>
    p.id === a.paperId && p.highlights.some(h => h.id === a.id && !h.position)));
  if (!anchor) throw new Error('Navigation checks need a locatable text-only highlight beyond page 2');
  const paper = papers.find(p => p.id === anchor.paperId);
  const target = paper.highlights.find(h => h.id === anchor.id);
  const reader = page.locator('[data-benchmark-reader]');
  const thread = () => page.locator(`[data-thread-id="${target.id}"]`);
  const clickThread = () => thread().click({ position: { x: 12, y: 10 } });
  const top = () => page.locator('.pdfViewer').evaluate(el => el.parentElement.scrollTop);
  const open = async (hold = false, percent = false) => {
    await page.goto(`${url}/reader-benchmark?paper=${paper.id}${hold ? `&hold-text-page=${anchor.anchor.page}` : ''}${percent ? '&percent-outline=1' : ''}`);
    await page.waitForFunction(() => !!window.annotationBenchmark);
    await page.waitForSelector('.page .textLayer span', { state: 'attached' });
  };
  const gotoPage = async number => {
    await page.getByRole('spinbutton', { name: 'Page number' }).fill(String(number));
    // Submit the real toolbar form without a pointer/key event. This checks
    // goToPage cancellation independently of the reader's input listeners.
    await page.getByRole('spinbutton', { name: 'Page number' }).evaluate(el => el.form.requestSubmit());
  };
  const pending = async () => {
    await clickThread();
    await page.waitForFunction(() => (window.annotationBenchmark.delay?.blocked ?? 0) > 0);
    const unresolved = await page.evaluate(() => window.annotationBenchmark.clicks.at(-1).unresolved);
    if (!unresolved) throw new Error('Race setup failed: anchor was already resolved');
    await page.waitForTimeout(30);
  };
  const release = () => page.evaluate(() => window.annotationBenchmark.delay.release());

  for (const action of ['toolbar-page', 'thumbnail', 'outline', 'outline-percent', 'toolbar-zoom', 'wheel', 'key', 'citation']) {
    await open(true, action === 'outline-percent');
    let outline;
    if (action === 'thumbnail' || action.startsWith('outline')) {
      await page.getByTitle('Thumbnails and outline', { exact: true }).click();
      if (action.startsWith('outline')) {
        await reader.getByRole('button', { name: 'outline', exact: true }).click();
        outline = reader.locator('aside button[title]').first();
        await outline.waitFor();
      } else await reader.getByRole('button', { name: 'Go to page 2', exact: true }).waitFor();
    }
    await pending();
    if (action === 'toolbar-page') await gotoPage(2);
    if (action === 'thumbnail') await reader.getByRole('button', { name: 'Go to page 2', exact: true }).click();
    if (action.startsWith('outline')) await outline.click();
    if (action === 'toolbar-zoom') await page.getByTitle('Zoom in', { exact: true }).click();
    if (action === 'wheel') { await page.mouse.move(400, 500); await page.mouse.wheel(0, 250); }
    if (action === 'key') {
      await page.locator('.pdfViewer').evaluate(el => { el.parentElement.tabIndex = 0; el.parentElement.focus(); });
      await page.keyboard.press('PageDown');
    }
    if (action === 'citation') {
      const quote = anchors.find(a => a.paperId === paper.id && a.anchor?.page === 1)?.textUnderRects;
      if (!quote) throw new Error('Citation race needs a page-one quote');
      await page.evaluate(term => window.annotationBenchmark.cite(term), quote.split(' ').slice(0, 7).join(' '));
      await page.waitForSelector('.textLayer .highlight.selected', { state: 'attached' });
    }
    await page.waitForTimeout(200);
    // goToPagePercent intentionally scrolls smoothly. Measure from its settled
    // destination, not from an arbitrary point during the animation.
    await page.evaluate(async () => {
      const scroller = document.querySelector('.pdfViewer').parentElement;
      const deadline = performance.now() + 1000;
      let previous = scroller.scrollTop, stable = performance.now();
      while (performance.now() < deadline) {
        await new Promise(requestAnimationFrame);
        if (scroller.scrollTop !== previous) stable = performance.now();
        previous = scroller.scrollTop;
        if (performance.now() - stable >= 100) return;
      }
      throw new Error('Navigation did not settle before releasing the lookup');
    });
    const before = await top();
    await release();
    await page.waitForTimeout(1600); // Includes the old lookup deadline/fallback.
    const after = await top();
    checks.push({ scenario: `pending-lookup-${action}`, success: Math.abs(after - before) < 3,
      unresolvedAtClick: true, before, after });
  }

  for (const action of ['remount', 'refreshUrl']) {
    await open();
    await trial(paper, target, 'one-shot-setup');
    await gotoPage(1);
    await page.waitForTimeout(700);
    const oldPage = await page.locator('.pdfViewer .page').first().elementHandle();
    await page.evaluate(action => window.annotationBenchmark[action](), action);
    await page.waitForFunction(el => !el.isConnected, oldPage);
    await page.waitForSelector('.page .textLayer span', { state: 'attached' });
    await page.waitForTimeout(1800);
    const after = await top();
    checks.push({ scenario: `handled-request-${action}`, success: after < 30, after });
    // A fresh click on the exact same thread still works after reconstruction.
    checks.push(await trial(paper, target, `fresh-request-after-${action}`));
  }

  await open(true);
  await pending();
  const oldPage = await page.locator('.pdfViewer .page').first().elementHandle();
  await page.evaluate(() => window.annotationBenchmark.remount());
  await page.waitForFunction(el => !el.isConnected, oldPage);
  await release();
  await page.waitForSelector('.page .textLayer span', { state: 'attached' });
  await page.waitForTimeout(1800);
  const after = await top();
  checks.push({ scenario: 'pending-request-remount', success: after < 30, after });
  return checks;
}
