import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { out, bench, root } from './modules.mjs';

const exists=async file=>fs.access(file).then(()=>true,()=>false);
await fs.mkdir(out,{recursive:true});
execFileSync('git',['check-ignore',path.join(out,'pdfs','fixture.pdf')],{cwd:root,stdio:'pipe'});
// Next's emitted server files and generated types resolve installed client packages here.
if(!await exists(path.join(out,'node_modules')))await fs.symlink(path.relative(out,path.join(root,'client/node_modules')),path.join(out,'node_modules'),'dir');
async function run(command,args,cwd=root) {
 await new Promise((resolve,reject)=>{const child=spawn(command,args,{cwd,stdio:'inherit'});child.on('exit',code=>code===0?resolve():reject(new Error(`${command} exited ${code}`)));child.on('error',reject);});
}
const node=file=>run(process.execPath,[path.join(bench,file)]);
if(!await exists(path.join(out,'papers.json')))await run('python3',[path.join(bench,'export.py')]);
if(!await exists(path.join(out,'inventory.json')) || JSON.parse(await fs.readFile(path.join(out,'inventory.json'))).some(p=>Object.values(p.destinations).some(d=>!d?.args)))await node('inventory.mjs');
await fs.mkdir(path.join(out,'baseline-source'),{recursive:true});
for(const file of ['helpers.ts','bibliography.ts','resolve.ts','useCitationLinks.ts','CitationPreviewCard.tsx']) {
 if(!await exists(path.join(out,'baseline-source',file)))await fs.writeFile(path.join(out,'baseline-source',file),execFileSync('git',['show',`8187e1cae736c745d775d4cd5519690932f8b474:client/src/components/reader/citations/${file}`],{cwd:root}));
}
await run('python3',[path.join(bench,'ground_truth.py')]);
await node('labels.mjs');
for(const variant of ['baseline','current']) {
 await run(process.execPath,[path.join(bench,'extraction.mjs'),variant]);
 await run(process.execPath,[path.join(bench,'resolution.mjs'),variant,'--offline']);
}
await node('contracts.mjs');
await node('review-contracts.mjs');
let server;
try {
 const available=await fetch('http://127.0.0.1:3107/citation-benchmark').then(r=>r.ok,()=>false);
 if(!available) {
  await run(process.execPath,['scripts/sync-pdfjs-assets.mjs'],path.join(root,'client'));
  await run(process.execPath,['scripts/patch-pdfjs-webpack-collision.mjs'],path.join(root,'client'));
  const log=await fs.open(path.join(out,'next.log'),'a');
  server=spawn(process.execPath,['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port','3107'],{cwd:path.join(root,'client'),env:{...process.env,CITATION_BENCHMARK:'1',NEXT_DIST_DIR:path.relative(path.join(root,'client'),path.join(out,'next')),NEXT_PUBLIC_API_URL:''},stdio:['ignore',log.fd,log.fd]});
  let ready=false;
  for(let i=0;i<60;i++) {
   ready=await fetch('http://127.0.0.1:3107/citation-benchmark').then(r=>r.ok,()=>false);
   if(ready)break;await new Promise(r=>setTimeout(r,1000));
  }
  if(!ready)throw new Error(`Citation harness did not start; see ${path.join(out,'next.log')}`);
 }
 await node('browser.mjs');
 await node('transitions.mjs');
 await node('publisher-browser.mjs');
 await node('review-browser.mjs');
 if(process.argv.includes('--live-library')||!await exists(path.join(out,'current-live-browser.json')))await node('live-browser.mjs');
 await node('report.mjs');
} finally {server?.kill('SIGTERM');}
