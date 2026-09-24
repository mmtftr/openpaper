import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { out, clientRequire } from './modules.mjs';
const { chromium } = clientRequire('playwright');

const variant=process.argv[2]||'current';
const data=JSON.parse(await fs.readFile(path.join(out,variant+'-extraction.json')));
const inventory=JSON.parse(await fs.readFile(path.join(out,'inventory.json')));
const modernChrome=path.join(os.homedir(),'Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser=await chromium.launch({headless:true,...(await fs.access(modernChrome).then(()=>true,()=>false)?{executablePath:modernChrome}:{})});
const trials=[];
try {
  const baseline=JSON.parse(await fs.readFile(path.join(out,'baseline-extraction.json')));
  for (const paper of data.perPaper.filter(p=>baseline.perPaper.find(b=>b.id===p.id)?.supportedLinks>0).slice(0,6)) {
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    let requests=0;
    await page.route('**/api/search/**',async route=>{
      requests++;
      await new Promise(r=>setTimeout(r,150));
      await route.fulfill({json:route.request().url().includes('/local')?{papers:[]}:{results:[]}});
    });
    await page.goto(`http://127.0.0.1:3107/citation-benchmark?paper=${paper.id}`);
    const selector='.annotationLayer a[href^="#cite."]';
    const firstPage=inventory.find(p=>p.id===paper.id).pages.find(p=>p.links.some(a=>typeof a.dest==='string' && a.dest.startsWith('cite.'))).number;
    await page.waitForSelector('.pdfViewer .page',{timeout:90000});
    if(firstPage>1) {
      await page.locator('.pdfViewer').evaluate((el,n)=>{const p=el.querySelector(`[data-page-number="${n}"]`);el.parentElement.scrollTop=p.offsetTop;},firstPage);
    }
    await page.waitForSelector(selector,{timeout:90000});
    const link=page.locator(selector).first();
    await link.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    const card=page.locator('[data-citation-preview]');
    const href=await link.getAttribute('href');
    const row={paperId:paper.id,href,errors,cold:[],warm:[],requests:0};
    for(let i=0;i<5;i++) {
      await page.mouse.move(5,5);
      await page.waitForTimeout(400);
      const t=performance.now();
      await link.hover();
      await card.waitFor({timeout:15000});
      row.appears=true;
      await page.waitForFunction(()=>{const c=document.querySelector('[data-citation-preview]');return c && c.textContent.trim().length>0 && Number(getComputedStyle(c).opacity)>=.5;},undefined,{timeout:15000});
      (i===0?row.cold:row.warm).push(performance.now()-t);
      if(i===0) {
        const box=await card.boundingBox();
        await page.mouse.move(box.x+box.width/2,box.y+Math.min(35,box.height/2),{steps:15});
        await page.waitForTimeout(350);
        row.staysOnEnter=await card.isVisible();
        await page.mouse.move(5,5,{steps:15});
        await page.waitForTimeout(450);
        row.dismissesOnCardLeave=!(await card.isVisible());
        // Reset through the actual reader scroller, even if leave failed.
        await page.locator('.pdfViewer').evaluate(el=>el.parentElement.dispatchEvent(new Event('scroll')));
      }
    }
    await page.locator('.pdfViewer').evaluate(el=>el.parentElement.dispatchEvent(new Event('scroll')));
    await page.waitForTimeout(100);
    row.dismissesOnScroll=!(await card.isVisible());
    const before=await page.locator('.pdfViewer').evaluate(el=>el.parentElement.scrollTop);
    await link.click();
    await page.waitForTimeout(900);
    const after=await page.locator('.pdfViewer').evaluate(el=>el.parentElement.scrollTop);
    row.clickKeepsReadingPosition=Math.abs(before-after)<4;
    row.clickScrollBefore=before;
    row.clickScrollAfter=after;
    row.clickShowsPreview=await card.isVisible();
    row.requests=requests;
    trials.push(row);
    console.log(JSON.stringify(row));
    await page.close();
  }
} finally {await browser.close();}
const percentile=(x,p)=>x.sort((a,b)=>a-b)[Math.min(x.length-1,Math.floor(x.length*p))]??null;
const cold=trials.flatMap(t=>t.cold),warm=trials.flatMap(t=>t.warm);
const summary={trials:trials.length,coldP50:percentile(cold,.5),coldP95:percentile(cold,.95),warmP50:percentile(warm,.5),warmP95:percentile(warm,.95)};
for(const name of ['appears','staysOnEnter','dismissesOnCardLeave','dismissesOnScroll','clickKeepsReadingPosition','clickShowsPreview'])summary[name]=trials.filter(t=>t[name]).length;
await fs.writeFile(path.join(out,variant+'-browser.json'),JSON.stringify({transport:'Controlled replay: empty search responses, 150ms per request',summary,trials},null,2));
console.log(summary);
