import { createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { exportFixtures } from './export.mjs';
import { checkFlagged } from './check-flagged.mjs';
import { runResilience } from './resilience.mjs';
import { runNavigationChecks } from './navigation.mjs';
import { summarize } from './summary.mjs';
import { scoreAnchor } from './score.mjs';

const bench = fileURLToPath(new URL('.', import.meta.url));
const root = resolve(bench, '../..');
const client = resolve(root, 'client');
const out = resolve(bench, '.data');
const clientRequire = createRequire(resolve(client, 'package.json'));
const { chromium } = clientRequire('playwright');
const arg = name => process.argv.find(x => x.startsWith(`--${name}=`))?.split('=').slice(1).join('=');
const label = arg('label') || 'final';
if (!/^[\w-]+$/.test(label)) throw new Error('Invalid label');
const fixtureDir = `${out}/fixtures`;
execFileSync('git', ['check-ignore', `${fixtureDir}/fixture.pdf`], { cwd: root, stdio: 'pipe' });
const manifest = await exportFixtures(fixtureDir, process.argv.includes('--refresh-fixtures'));
const papers = manifest.papers.filter(p => p.highlights.length && (!arg('paper') || p.id === arg('paper'))).slice(0, Number(arg('limit')) || Infinity);
await mkdir(out, { recursive: true });
const reservation = createServer();
await new Promise(r => reservation.listen(0, '127.0.0.1', r));
const port = reservation.address().port;
await new Promise(r => reservation.close(r));
for (const script of ['sync-pdfjs-assets.mjs', 'patch-pdfjs-webpack-collision.mjs']) {
  execFileSync(process.execPath, [`scripts/${script}`], { cwd: client, stdio: 'inherit' });
}
// Freeze source files: other agents' edits and HMR must not alter a run.
const appDir = await mkdtemp(`${out}/app-`);
await cp(`${client}/src`, `${appDir}/src`, { recursive: true });
const readerSources = ['anchoring.ts', 'useAnchoredHighlights.ts', 'useViewer.ts', 'PdfReader.tsx'];
if (arg('baseline-source')) {
  if (!process.argv.includes('--legacy')) throw new Error('--baseline-source requires --legacy');
  for (const file of readerSources) await cp(resolve(root, arg('baseline-source'), file), `${appDir}/src/components/reader/${file}`);
}
const sourceHashes = Object.fromEntries(await Promise.all([...readerSources, 'jumpToAnchor.ts', 'useHighlightJump.ts', 'useReaderNavigation.ts', 'textNormalization.ts', 'HighlightLayer.tsx'].map(async file => [file,
  createHash('sha256').update(await readFile(`${appDir}/src/components/reader/${file}`)).digest('hex')])));
for (const file of ['package.json', 'yarn.lock', 'tsconfig.json', 'next.config.ts', 'postcss.config.mjs']) {
  await cp(`${client}/${file}`, `${appDir}/${file}`);
}
await symlink(`${client}/node_modules`, `${appDir}/node_modules`, 'dir');
await symlink(`${client}/public`, `${appDir}/public`, 'dir');
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '--hostname', '127.0.0.1', '--port', String(port)], {
  cwd: appDir, env: { ...process.env, ANNOTATION_BENCHMARK: '1', ANNOTATION_BENCHMARK_FIXTURES: fixtureDir, NEXT_DIST_DIR: '.next' }, stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = '';
server.stdout.on('data', x => { logs += x; });
server.stderr.on('data', x => { logs += x; });
const url = `http://127.0.0.1:${port}`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
let browser;
const report = { label, startedAt: new Date().toISOString(), complete: false, sourceHashes, sourceSnapshot: true, exportedAt: manifest.exportedAt, viewport: { width: 1440, height: 1000 }, timeoutMs: 1500,
  papers: manifest.papers.length, pdfHashes: Object.fromEntries(manifest.papers.map(p => [p.id, p.sha256])), anchors: [], jumps: [], browserErrors: [] };
try {
  for (let i = 0; i < 120; i++) {
    if (server.exitCode !== null) throw new Error(logs);
    try { if ((await fetch(`${url}/reader-benchmark`)).ok) break; } catch { /* starting */ }
    await sleep(500);
    if (i === 119) throw new Error(`Next dev did not start: ${logs}`);
  }
  browser = await chromium.launch({ headless: true });
  report.browser = browser.version();
  const page = await browser.newPage({ viewport: report.viewport });
  page.on('pageerror', error => { report.browserErrors.push(error.message); console.error(error.message); });
  page.on('console', msg => { if (msg.type() === 'error') console.error(msg.text()); });
  async function trial(paper, h, scenario, reset = true, duringJump) {
    if (reset) {
      await page.evaluate(hint => {
        const scroller = document.querySelector('.pdfViewer')?.parentElement;
        if (scroller) scroller.dispatchEvent(new WheelEvent('wheel', { bubbles: true }));
        if (scroller) scroller.scrollTo({ top: hint <= 2 ? scroller.scrollHeight : 0, left: 0, behavior: 'instant' });
      }, h.page_number || h.position?.boundingRect?.pageNumber || 1);
      await page.waitForTimeout(80);
    }
    const before = await page.evaluate(id => {
      const rect = document.querySelector(`[data-highlight-id="${id}"][data-rect-index="0"]`);
      const box = rect?.parentElement;
      const r = rect?.getBoundingClientRect(), c = document.querySelector('.pdfViewer')?.parentElement?.getBoundingClientRect();
      const initiallyVisible = !!(r && c && r.top >= c.top && r.bottom <= c.bottom);
      return { initiallyVisible, ... { overlayExists: !!rect, targetRendered: !!box && [...document.querySelectorAll('.page')].some(p => Math.abs(p.getBoundingClientRect().top - box.getBoundingClientRect().top) < 5 && p.querySelector('.textLayer span')) } };
    }, h.id);
    await page.locator(`[data-thread-id="${h.id}"]`).click({ position: { x: 12, y: 10 }, timeout: 10000 });
    if (duringJump) await duringJump();
    const result = await page.evaluate(async ({ id, timeout }) => {
      const click = window.annotationBenchmark.clicks.at(-1);
      let stableSince = 0, previous = '', visibleAt = null, lastState = null;
      let geometrySince = 0, geometryLatencyMs = null;
      while (performance.now() - click.time < timeout) {
        await new Promise(requestAnimationFrame);
        const container = document.querySelector('.pdfViewer')?.parentElement;
        const el = document.querySelector(`[data-highlight-id="${id}"][data-rect-index="0"]`);
        const r = el?.getBoundingClientRect(), c = container?.getBoundingClientRect();
        const visible = r && c && r.width > 0 && r.top >= c.top + 8 && r.bottom <= c.bottom - 8 && r.left >= c.left && r.right <= c.right + 1;
        const rendered = visible && [...document.querySelectorAll('.page')].some(p => p.querySelector('.textLayer span') && r.top >= p.getBoundingClientRect().top && r.top <= p.getBoundingClientRect().bottom);
        lastState = { visible: !!visible, rendered: !!rendered, rect: r?.toJSON(), viewport: c?.toJSON(), scrollTop: container?.scrollTop, scrollLeft: container?.scrollLeft };
        const key = r && container ? [container.scrollTop, container.scrollLeft, r.top, r.left, r.width].map(Math.round).join(':') : '';
        if (visible) {
          if (key !== previous || !geometrySince) geometrySince = performance.now();
          if (performance.now() - geometrySince >= 100) geometryLatencyMs ??= performance.now() - click.time;
        } else geometrySince = 0;
        if (visible && rendered) {
          visibleAt ??= performance.now() - click.time;
          if (key !== previous || !stableSince) stableSince = performance.now();
          if (performance.now() - stableSince >= 100) {
            return { success: true, latencyMs: performance.now() - click.time, visibleMs: visibleAt, geometryLatencyMs, unresolvedAtClick: click.unresolved,
              rect: { top: r.top, left: r.left, width: r.width, height: r.height }, viewport: { top: c.top, bottom: c.bottom } };
          }
        } else stableSince = 0;
        previous = key;
      }
      return { success: false, latencyMs: null, unresolvedAtClick: click.unresolved, geometryLatencyMs, lastState };
    }, { id: h.id, timeout: report.timeoutMs });
    const row = { paperId: paper.id, id: h.id, scenario, ...before, ...result };
    report.jumps.push(row);
    return row;
  }
  for (const [i, paper] of papers.entries()) {
    await page.goto(`${url}/reader-benchmark?paper=${paper.id}${process.argv.includes('--legacy') ? '&legacy=1' : ''}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !!window.annotationBenchmark);
    const early = paper.highlights.findLast(h => !h.position) || paper.highlights.at(-1);
    await trial(paper, early, 'cold', false);
    await page.waitForSelector('.page .textLayer span', { timeout: 30000, state: 'attached' });
    for (const h of paper.highlights) await trial(paper, h, 'far');
    const repeat = paper.highlights.at(-1);
    await trial(paper, repeat, 'repeat');
    await page.getByTitle('Zoom in', { exact: true }).click();
    await page.getByTitle('Zoom in', { exact: true }).click();
    await trial(paper, repeat, 'zoom-in');
    for (let z = 0; z < 4; z++) await page.getByTitle('Zoom out', { exact: true }).click();
    await trial(paper, repeat, 'zoom-out');
    const audit = await page.evaluate(() => window.annotationBenchmark.audit());
    const words = JSON.parse(await readFile(`${fixtureDir}/${paper.id}.words.json`, 'utf8'));
    for (const row of audit) report.anchors.push({ paperId: paper.id, ...scoreAnchor(row, paper.highlights.find(h => h.id === row.id), words) });
    console.log(`[${i + 1}/${papers.length}] ${paper.title.slice(0, 50)}: ${report.jumps.filter(j => j.paperId === paper.id && j.success).length}/${report.jumps.filter(j => j.paperId === paper.id).length}`);
    await writeFile(`${out}/${label}.json`, JSON.stringify(report, null, 2));
  }
  if (!process.argv.includes('--legacy')) {
    report.resilience = await runResilience({ page, url, papers, anchors: report.anchors, viewport: report.viewport,
      trial: async (...args) => { const row = await trial(...args); report.jumps.pop(); return row; } });
    console.table(report.resilience.map(({ scenario, success, latencyMs }) => ({ scenario, success, latencyMs })));
    report.navigation = await runNavigationChecks({ page, url, papers, anchors: report.anchors,
      trial: async (...args) => { const row = await trial(...args); report.jumps.pop(); return row; } });
    console.table(report.navigation.map(({ scenario, success, latencyMs }) => ({ scenario, success, latencyMs })));
  }
  report.flaggedGeometryChecks = await checkFlagged({ page, url, papers, anchors: report.anchors });
  report.summary = summarize(report);
  report.complete = true;
  report.completedAt = new Date().toISOString();
  await writeFile(`${out}/${label}.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ...report.summary, scenarios: undefined }, null, 2));
  console.table(report.summary.scenarios);
  console.log(`Report: ${out}/${label}.json`);
  if ([...(report.resilience ?? []), ...(report.navigation ?? [])].some(check => !check.success)) process.exitCode = 1;
} finally {
  await writeFile(`${out}/${label}.json`, JSON.stringify(report, null, 2));
  await browser?.close();
  server.kill('SIGTERM');
  await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit', resolve));
  await rm(appDir, { recursive: true, force: true });
  await writeFile(`${out}/${label}-next.log`, logs);
}
