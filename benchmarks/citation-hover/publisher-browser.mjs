import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { out, modules, clientRequire } from './modules.mjs';
const { chromium } = clientRequire('playwright');
const api=await modules();
const current=JSON.parse(await fs.readFile(path.join(out,'current-extraction.json')));
const baseline=JSON.parse(await fs.readFile(path.join(out,'baseline-extraction.json')));
const inventory=JSON.parse(await fs.readFile(path.join(out,'inventory.json')));
const executablePath=path.join(os.homedir(),'Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser=await chromium.launch({executablePath});
const trials=[];
try {
  for(const paper of current.perPaper.filter(p=>p.supportedLinks>0 && !baseline.perPaper.find(b=>b.id===p.id).supportedLinks)) {
    const pdf=inventory.find(p=>p.id===paper.id);
    const targetPage=pdf.pages.find(p=>p.links.some(a=>typeof a.dest==='string'&&api.parseCiteHref('#'+a.dest)));
    const target=targetPage.links.find(a=>typeof a.dest==='string'&&api.parseCiteHref('#'+a.dest));
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    await page.route('**/api/search/**',r=>r.fulfill({json:{papers:[],results:[]}}));
    await page.goto(`http://localhost:3107/citation-benchmark?paper=${paper.id}`);
    await page.waitForSelector('.pdfViewer .page',{timeout:60000,state:'attached'});
    if(targetPage.number>1)await page.locator('.pdfViewer').evaluate((el,n)=>{el.parentElement.scrollTop=el.querySelector(`[data-page-number="${n}"]`).offsetTop;},targetPage.number);
    await page.waitForFunction(name=>[...document.querySelectorAll('.annotationLayer a')].some(a=>unescape(a.getAttribute('href')||'')==='#'+name),target.dest,{timeout:30000});
    await page.evaluate(name=>{[...document.querySelectorAll('.annotationLayer a')].find(a=>unescape(a.getAttribute('href')||'')==='#'+name).setAttribute('data-publisher-test','true');},target.dest);
    const link=page.locator('[data-publisher-test]');
    await link.scrollIntoViewIfNeeded();await page.waitForTimeout(400);await link.hover();
    const card=page.locator('[data-citation-preview]');await card.waitFor({timeout:15000});
    await page.waitForFunction(()=>document.querySelector('[data-citation-preview]')?.getAttribute('data-citation-state')==='raw');
    const expected=current.rows.find(r=>r.paperId===paper.id&&r.destination===target.dest)?.extracted;
    const compact=s=>s.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]/g,'');
    const text=await card.innerText();
    const row={paperId:paper.id,destination:target.dest,appears:true,entryMatches:!!expected&&compact(text).includes(compact(expected).slice(0,80))};
    await page.waitForTimeout(200);
    await page.screenshot({path:path.join(out,`publisher-${paper.id.slice(0,8)}.png`)});
    const jump=card.getByRole('button',{name:/Jump to page/});
    if(await jump.count()) {
      await jump.click();await page.waitForTimeout(1000);
      row.jumpDismisses=!(await card.isVisible());
      row.jumpPage=await page.getByLabel('Page number').inputValue();
    }
    trials.push(row);console.log(row);await page.close();
  }
} finally {await browser.close();}
await fs.writeFile(path.join(out,'current-publishers.json'),JSON.stringify(trials,null,2));
