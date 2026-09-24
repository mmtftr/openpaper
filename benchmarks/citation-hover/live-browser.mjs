import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { out, clientRequire } from './modules.mjs';
const { chromium } = clientRequire('playwright');
const token=execFileSync('docker',['exec','openpaper-postgres-1','psql','-U','postgres','-d','openpaper','-At','-c',"SELECT token FROM sessions WHERE expires_at>now() AND user_id IN (SELECT user_id FROM papers) ORDER BY created_at DESC LIMIT 1"],{encoding:'utf8'}).trim();
const browser=await chromium.launch({executablePath:path.join(os.homedir(),'Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing')});
const results=[],requests=[];
try {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  await page.route('**/api/search/**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.includes('/global/')){await route.fulfill({status:503,json:{detail:'OpenAlex live benchmark circuit open after observed 429'}});return;}
    const start=performance.now();
    const response=await fetch('http://127.0.0.1:12001'+url.pathname+url.search,{headers:{Authorization:'Bearer '+token}});
    const body=await response.json();requests.push({status:response.status,ms:performance.now()-start});
    await route.fulfill({status:response.status,json:body});
  });
  await page.goto('http://localhost:3107/citation-benchmark?paper=0031984c-e539-4668-a3bd-5a158203f244');
  const link=page.locator('.annotationLayer a[href="#cite.chen2025reasoning"]').first();
  await page.waitForSelector('.pdfViewer .page',{state:'attached'});
  await page.locator('.pdfViewer').evaluate(el=>{el.parentElement.scrollTop=el.querySelector('[data-page-number="2"]').offsetTop;});
  await link.waitFor({timeout:30000});await link.scrollIntoViewIfNeeded();await page.waitForTimeout(500);
  for(let i=0;i<8;i++) {
    await page.mouse.move(5,5);await page.waitForTimeout(350);
    const start=performance.now();await link.hover();
    await page.waitForFunction(()=>{const c=document.querySelector('[data-citation-preview]');return c&&c.getAttribute('data-citation-state')!=='skeleton';});
    const contentMs=performance.now()-start;
    await page.waitForFunction(()=>document.querySelector('[data-citation-preview]')?.getAttribute('data-citation-state')==='paper',{timeout:10000});
    results.push({temperature:i?'warm':'cold',contentMs,paperMs:performance.now()-start});
  }
} finally {await browser.close();}
await fs.writeFile(path.join(out,'current-live-browser.json'),JSON.stringify({scope:'Live authenticated library lookup; warm repeats use reader cache; no OpenAlex requests after quota failure.',requests,results},null,2));
console.log({requests,results});
