import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { out } from './modules.mjs';

// Reuse an existing session in memory; never export credentials or create users.
let token;
let recordings;
const pending=new Map();
let writing=Promise.resolve();
let globalQueue=Promise.resolve();
let quotaStopped=false;
const file=path.join(out,'search-recordings.json');
const nativeFetch=globalThis.fetch;
export const replayMisses=new Set();
function canonical(key) {
  const url=new URL(key,'http://benchmark');
  const query=url.searchParams.get('q')??url.searchParams.get('query')??'';
  return url.pathname+':'+query.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]/g,'');
}
export async function setupRecording({offline=false}={}) {
  recordings=JSON.parse(await fs.readFile(file,'utf8').catch(()=>'{}'));
  if(!offline) token=execFileSync('docker',['exec','openpaper-postgres-1','psql','-U','postgres','-d','openpaper','-At','-c',"SELECT s.token FROM sessions s WHERE s.expires_at > now() AND s.user_id IN (SELECT user_id FROM papers) ORDER BY s.created_at DESC LIMIT 1"],{encoding:'utf8'}).trim();
  globalThis.fetch=async (url,options={})=>{
    const key=String(url).replace(/^https?:\/\/[^/]+/,'');
    if(offline) {
      const equivalent=Object.entries(recordings).find(([k,r])=>r.status===200 && canonical(k)===canonical(key))?.[1];
      const recorded=equivalent??recordings[key];
      if(!recorded) replayMisses.add(key);
      return new Response(JSON.stringify(recorded?.body??{detail:'No recording for query'}),{status:recorded?.status===599?503:recorded?.status??503});
    }
    if(!recordings[key]) {
      if(!pending.has(key)) {
       const request=async()=>{
        if(key.includes('/global/') && quotaStopped) {
          recordings[key]={status:503,body:{detail:'Recording stopped after OpenAlex rate limit'},ms:0,recordedAt:new Date().toISOString()};
          return;
        }
        const start=performance.now();
        try {
          const response=await nativeFetch('http://127.0.0.1:12001'+key,{...options,signal:AbortSignal.timeout(20000),headers:{...options.headers,...(token?{Authorization:'Bearer '+token}:{})}});
          const body=await response.json();
          recordings[key]={status:response.status,body,ms:performance.now()-start,recordedAt:new Date().toISOString()};
          if(response.status!==200 && JSON.stringify(body).includes('429'))quotaStopped=true;
        } catch(e) { recordings[key]={status:599,body:{error:String(e)},ms:performance.now()-start,recordedAt:new Date().toISOString()}; }
       };
       if(key.includes('/global/')) {
        globalQueue=globalQueue.then(async()=>{await new Promise(r=>setTimeout(r,1100));await request();});
        pending.set(key,globalQueue);
       } else pending.set(key,request());
      }
      await pending.get(key);
      writing=writing.then(()=>fs.writeFile(file,JSON.stringify(recordings)));
      await writing;
    }
    const r=recordings[key];
    return new Response(JSON.stringify(r.body),{status:r.status===599?503:r.status,headers:{'Content-Type':'application/json'}});
  };
  return recordings;
}
