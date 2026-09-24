import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { out, clientRequire } from './modules.mjs';
const { chromium } = clientRequire('playwright');
const chrome = path.join(os.homedir(), 'Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await chromium.launch({ headless: true, ...(await fs.access(chrome).then(() => true, () => false) ? { executablePath: chrome } : {}) });
const results = [];
const paper = '0031984c-e539-4668-a3bd-5a158203f244';
const url = `http://127.0.0.1:3107/citation-benchmark?paper=${paper}`;
const selector = '.annotationLayer a[href^="#cite."]';
try {
  for (const fault of ['missing', 'empty', 'throw']) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    let lookups = 0;
    await page.route('**/api/**', async route => {
      if (route.request().url().includes('/api/search/')) lookups++;
      await route.fulfill({ json: { papers: [], results: [] } });
    });
    await page.goto(url + (fault === 'missing' ? '' : '&reviewFault=' + fault));
    if (fault !== 'missing') await page.waitForSelector('[data-citation-fault-ready]', { state: 'attached', timeout: 90000 });
    const link = page.locator(selector).first();
    await link.waitFor({ timeout: 90000 });
    if (fault === 'missing') await link.evaluate(a => a.setAttribute('href', '#cite.missing.reference'));
    await link.hover();
    const card = page.locator('[data-citation-preview]');
    await card.waitFor();
    await page.waitForTimeout(1000);
    const text = await card.innerText();
    const state = await card.getAttribute('data-citation-state');
    const row = { name: fault + ' extraction shows unavailable without lookup', state, text, lookups,
      passed: state === 'unavailable' && /reference text (?:is )?unavailable/i.test(text) && !/cite\.|missing\.reference|lookup service/i.test(text) && lookups === 0 };
    results.push(row);
    await page.close();
  }

  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pdf = await fs.readFile(path.join(out, 'pdfs', paper + '.pdf'));
  let uploads = 0;
  let releaseUpload;
  const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
  // All imports are intercepted: this checks the real button without any writes.
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = request.url();
    if (url.includes('/api/search/local')) return route.fulfill({ json: { papers: [] } });
    if (url.includes('/api/search/global')) return route.fulfill({ json: { results: [{ id: 'review-fixture', title: 'Alignment faking in large language models', authorships: [{ author: { display_name: 'Ryan Greenblatt' } }], primary_location: { pdf_url: 'https://citation-fixture.invalid/paper.pdf' } }] } });
    if (url.includes('/api/paper/upload')) {
      uploads++;
      await uploadGate;
      return route.fulfill({ json: { job_id: 'review-intercepted-only', file_name: 'paper.pdf' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route('https://citation-fixture.invalid/paper.pdf', route => route.fulfill({ body: pdf, contentType: 'application/pdf', headers: { 'access-control-allow-origin': '*' } }));
  await page.goto(url);
  const link = page.locator(selector).first();
  await link.waitFor({ timeout: 90000 });
  await link.hover();
  const card = page.locator('[data-citation-preview]');
  const add = card.getByRole('button', { name: 'Add to library' });
  await add.waitFor({ timeout: 15000 });
  await page.evaluate(() => {
    document.addEventListener('click', event => {
      if (!event.composedPath().some(node => node instanceof Element && node.matches('[data-citation-preview]'))) return;
      window.__citationClickTarget = event.target;
    }, { capture: true, once: true });
  });
  await add.locator('svg').click();
  await page.waitForTimeout(400);
  const targetDetached = await page.evaluate(() => window.__citationClickTarget?.isConnected === false);
  const staysDuringImport = await card.isVisible() && await card.getByRole('button', { name: 'Adding…' }).count() === 1;
  releaseUpload();
  await page.waitForTimeout(600);
  const staysAfterImport = await card.isVisible() && await card.getByText('Added', { exact: true }).count() === 1;
  results.push({ name: 'detached Add icon does not dismiss the card', targetDetached, uploads, staysDuringImport, staysAfterImport,
    passed: targetDetached && uploads === 1 && staysDuringImport && staysAfterImport });
  await page.mouse.click(5, 5);
  await page.waitForTimeout(350);
  results.push({ name: 'outside click still dismisses', passed: !await card.isVisible() });
  await page.close();
} finally { await browser.close(); }
await fs.writeFile(path.join(out, (process.argv[2] || 'current') + '-review-browser.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
if (!process.argv.includes('--record')) assert.ok(results.every(r => r.passed), 'Review browser regressions failed');
