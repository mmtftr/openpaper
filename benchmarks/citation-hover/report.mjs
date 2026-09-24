import fs from 'node:fs/promises';
import path from 'node:path';
import { out } from './modules.mjs';
const read=async name=>JSON.parse(await fs.readFile(path.join(out,name+'.json')));
const [be,ce,br,cr,bb,cb,bt,ct,publishers,truth,recordings,live]=await Promise.all(['baseline-extraction','current-extraction','baseline-resolution','current-resolution','baseline-browser','current-browser','baseline-transitions','current-transitions','current-publishers','ground-truth','search-recordings','current-live-browser'].map(read));
const [reviewContracts,reviewBrowser]=await Promise.all(['current-review-contracts','current-review-browser'].map(read));
const percent=x=>(100*x).toFixed(1)+'%';
const rows=[
 ['Papers with reference-link previews',`${be.summary.papersWithPreview}/48`,`${ce.summary.papersWithPreview}/48`],
 ['Supported reference links',`${be.summary.supportedLinks}/${be.summary.referenceLinks}`,`${ce.summary.supportedLinks}/${ce.summary.referenceLinks}`],
 ['Correct bibliography destinations',`${be.summary.correctDestinations}/${be.summary.labelledDestinations} (${percent(be.summary.extractionAccuracy)})`,`${ce.summary.correctDestinations}/${ce.summary.labelledDestinations} (${percent(ce.summary.extractionAccuracy)})`],
 ['Correct bibliography links',`${be.summary.correctLinks}/${be.summary.labelledLinks}`,`${ce.summary.correctLinks}/${ce.summary.labelledLinks}`],
 ['Resolution precision (recorded responses)',`${br.summary.correct}/${br.summary.matched} (${percent(br.summary.precision)})`,`${cr.summary.correct}/${cr.summary.matched} (${percent(cr.summary.precision)})`],
 ['Recall on known positives (lower bound)',percent(br.summary.recall),percent(cr.summary.recall)],
 ['Wrong paper matches',br.summary.wrong,cr.summary.wrong],
 ['Cold hover content p50 / p95',`${Math.round(bb.summary.coldP50)} / ${Math.round(bb.summary.coldP95)} ms`,`${Math.round(cb.summary.coldP50)} / ${Math.round(cb.summary.coldP95)} ms`],
 ['Warm hover content p50 / p95',`${Math.round(bb.summary.warmP50)} / ${Math.round(bb.summary.warmP95)} ms`,`${Math.round(cb.summary.warmP50)} / ${Math.round(cb.summary.warmP95)} ms`],
 ['Card leave dismisses',`${bb.summary.dismissesOnCardLeave}/6`,`${cb.summary.dismissesOnCardLeave}/6`],
 ['Click opens preview without jumping',`${bb.summary.clickShowsPreview}/6`,`${cb.summary.clickShowsPreview}/6`],
 ['Adjacent / quick-pass / Escape checks',Object.values(bt).filter(Boolean).length+'/3',Object.values(ct).filter(Boolean).length+'/3'],
];
const table='| Metric | Baseline | Final |\n|---|---:|---:|\n'+rows.map(row=>'| '+row.join(' | ')+' |').join('\n');
const noLinks=ce.perPaper.filter(p=>!p.referenceLinks).map(p=>({id:p.id,title:p.title,ocrCitationTargets:p.ocrCitationTargets}));
const latencies={};
for(const kind of ['local','global']) {
 const values=Object.entries(recordings).filter(([k,r])=>k.startsWith('/api/search/'+kind)&&r.status===200).map(([,r])=>r.ms).sort((a,b)=>a-b);
 latencies[kind]={n:values.length,p50:values[Math.floor(values.length*.5)],p95:values[Math.floor(values.length*.95)]};
}
const summary={recordedAt:new Date().toISOString(),baselineRevision:'8187e1cae736c745d775d4cd5519690932f8b474',baseline:{extraction:be.summary,resolution:br.summary,browser:bb.summary,transitions:bt},final:{extraction:ce.summary,resolution:cr.summary,browser:cb.summary,transitions:ct,publishers,live},liveRequestLatency:latencies,serviceFailures:Object.values(recordings).filter(r=>r.status!==200).length,noLinks,unlabelledDestinations:truth.reduce((n,p)=>n+Object.values(p.labels).filter(l=>l.reference===null).length,0),perPaper:ce.perPaper.map(p=>({...p,baseline:be.perPaper.find(b=>b.id===p.id)})),caveats:['OCR citation targets are an estimate and a different unit from annotation boxes. Range citations and author/year split links prevent a simple combined coverage percentage.','No-annotation papers remain unsupported.','Resolution replay shares successful responses across case/whitespace/punctuation equivalent queries. Unrecorded queries remain unavailable. Recall is a lower bound, not full OpenAlex retrieval recall.','Browser latency comparison uses six real readers, one cold and four warm hovers each, with controlled 150ms empty backend responses. Live library hover and live request latencies are reported separately.','Baseline browser traces were captured before edits; source extraction and resolution replay are recomputed from the preserved original sources.']};
await fs.writeFile(path.join(out,'summary.md'),table+'\n');
console.log(table);
summary.final.reviewContracts=reviewContracts;
summary.final.reviewBrowser=reviewBrowser;
const before=await read('review-before/summary').catch(()=>null);
if(before) {
 const [oldContracts,oldBrowser,oldExtraction]=await Promise.all(['review-before-review-contracts','review-before/current-review-browser','review-before/current-extraction'].map(read));
 const oldRows=new Map(oldExtraction.rows.map(r=>[r.paperId+' '+r.destination,r]));
 const changes=ce.rows.filter(r=>r.correct!==oldRows.get(r.paperId+' '+r.destination)?.correct).map(r=>({paperId:r.paperId,destination:r.destination,links:r.links,before:oldRows.get(r.paperId+' '+r.destination)?.correct,after:r.correct}));
 const passed=cases=>`${cases.filter(c=>c.passed).length}/${cases.length}`;
 const latency=s=>`${Math.round(s.coldP50)} / ${Math.round(s.coldP95)} ms`;
 const warm=s=>`${Math.round(s.warmP50)} / ${Math.round(s.warmP95)} ms`;
 const comparison={recordedAt:summary.recordedAt,before:{...before.final,reviewContracts:oldContracts,reviewBrowser:oldBrowser},after:summary.final,changes};
 const reviewRows=[
  ['Supported links / papers',`${before.final.extraction.supportedLinks} / ${before.final.extraction.papersWithPreview}`,`${ce.summary.supportedLinks} / ${ce.summary.papersWithPreview}`],
  ['Correct destinations',`${before.final.extraction.correctDestinations}/${ce.summary.labelledDestinations}`,`${ce.summary.correctDestinations}/${ce.summary.labelledDestinations}`],
  ['Correct links',`${before.final.extraction.correctLinks}/${ce.summary.labelledLinks}`,`${ce.summary.correctLinks}/${ce.summary.labelledLinks}`],
  ['Correct / wrong paper matches',`${before.final.resolution.correct} / ${before.final.resolution.wrong}`,`${cr.summary.correct} / ${cr.summary.wrong}`],
  ['Resolution recall (recorded positives)',percent(before.final.resolution.recall),percent(cr.summary.recall)],
  ['Cold hover p50 / p95',latency(before.final.browser),latency(cb.summary)],
  ['Warm hover p50 / p95',warm(before.final.browser),warm(cb.summary)],
  ['Review extraction checks',passed(oldContracts),passed(reviewContracts)],
  ['Review browser checks',passed(oldBrowser),passed(reviewBrowser)],
 ];
 const reviewTable='| Metric | Before review fixes | After |\n|---|---:|---:|\n'+reviewRows.map(r=>'| '+r.join(' | ')+' |').join('\n')+'\n';
 await fs.writeFile(path.join(out,'review-summary.json'),JSON.stringify(comparison,null,2));
 await fs.writeFile(path.join(out,'review-summary.md'),reviewTable);
 console.log(reviewTable);
 if(changes.some(r=>r.before&&!r.after))throw new Error('A previously correct bibliography destination regressed');
}
await fs.writeFile(path.join(out,'summary.json'),JSON.stringify(summary,null,2));
const browserChecks=['appears','staysOnEnter','dismissesOnCardLeave','dismissesOnScroll','clickKeepsReadingPosition','clickShowsPreview'];
if(ce.summary.extractionAccuracy<.95 || cr.summary.wrong>0 || Object.values(ct).some(v=>!v) || publishers.some(p=>!p.entryMatches||!p.jumpDismisses) || cb.summary.trials!==6 || browserChecks.some(k=>cb.summary[k]!==6) || cb.trials.some(t=>t.errors.length) || [...reviewContracts,...reviewBrowser].some(c=>!c.passed))throw new Error('Citation benchmark regression');
