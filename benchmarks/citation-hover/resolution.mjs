import fs from 'node:fs/promises';
import path from 'node:path';
import { modules, out } from './modules.mjs';
import { setupRecording, replayMisses } from './record.mjs';

const variant=process.argv[2]||'current';
const api=await modules(variant);
const recordings=await setupRecording({offline:process.argv.includes('--offline')});
const labels=JSON.parse(await fs.readFile(path.join(out,'resolution-labels.json')));
const extraction=JSON.parse(await fs.readFile(path.join(out,variant+'-extraction.json')));
const norm=s=>(s||'').normalize('NFKD').toLowerCase().replace(/[^a-z0-9]/g,'');
function matches(a,b){const x=norm(a),y=norm(b);return x.length>18&&y.length>18&&(x===y||x.includes(y)||y.includes(x));}
const rows=[];
let next=0;
await Promise.all(Array.from({length:3},async()=>{
  while(next<labels.length){
    const label=labels[next++];
    const oracleUrl='/api/search/global/search?query='+encodeURIComponent(label.title)+'&page=1';
    const oracle=await fetch(oracleUrl,{method:'POST'}).then(r=>r.json());
    const available=!label.expectedNoPaper && Boolean(label.libraryPaperId || (oracle.results??[]).some(w=>matches(w.title,label.title)));
    const extracted=extraction.rows.find(r=>r.paperId===label.paperId&&r.destination===label.destination)?.extracted;
    const supported=Boolean(api.parseCiteHref('#'+label.destination));
    const outcome=supported&&extracted?await api.resolvePaper(extracted):null;
    const predicted=outcome && outcome!=='unavailable'?outcome.paper.title:null;
    const correct=predicted&&!label.expectedNoPaper?matches(predicted,label.title):false;
    rows.push({...label,available,supported,extracted,predicted,correct,wrong:Boolean(predicted&&!correct),matchedBy:outcome?.matchedBy??null});
    console.log(`${rows.length}/${labels.length} ${correct?'OK':predicted?'WRONG':'MISS'}: ${label.title.slice(0,65)} -> ${predicted??outcome}`);
  }
}));
const returned=rows.filter(r=>r.predicted),eligible=rows.filter(r=>r.available);
const live=Object.values(recordings).filter(r=>r.status===200).map(r=>r.ms).sort((a,b)=>a-b);
const summary={labelled:rows.length,available:eligible.length,matched:returned.length,correct:rows.filter(r=>r.correct).length,wrong:rows.filter(r=>r.wrong).length,precision:returned.length?rows.filter(r=>r.correct).length/returned.length:null,recall:eligible.filter(r=>r.correct).length/eligible.length,unrecordedQueries:replayMisses.size,recordedRequests:Object.keys(recordings).length,liveRequestP50:live[Math.floor(live.length*.5)],liveRequestP95:live[Math.floor(live.length*.95)]};
await fs.writeFile(path.join(out,variant+'-resolution.json'),JSON.stringify({summary,rows,unrecordedQueries:[...replayMisses],method:'Fixed response replay; case, punctuation and whitespace equivalent queries share successful recordings. Missing queries are unavailable, not invented empty results. Recall is a lower bound on known indexed/library positives.'},null,2));
console.log(summary);
