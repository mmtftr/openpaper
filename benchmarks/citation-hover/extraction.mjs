import fs from 'node:fs/promises';
import path from 'node:path';
import { modules, out } from './modules.mjs';

const variant = process.argv[2] || 'current';
const api = await modules(variant);
const inventory = JSON.parse(await fs.readFile(path.join(out, 'inventory.json')));
const truth = JSON.parse(await fs.readFile(path.join(out, 'ground-truth.json')));
const normalize = s => (s || '').normalize('NFKD').toLowerCase().replace(/(?<=\w)-\s+(?=\w)/g, '').replace(/[^a-z0-9]/g, '');
function grams(s) { const n = normalize(s); return new Set(Array.from({ length: Math.max(0,n.length-3) }, (_,i)=>n.slice(i,i+4))); }
function accuracy(a,b) {
  const x=grams(a),y=grams(b), hit=[...x].filter(g=>y.has(g)).length;
  const precision=hit/Math.max(1,x.size), recall=hit/Math.max(1,y.size);
  return {precision,recall,correct:precision>=.9 && recall>=.85};
}
const rows=[], perPaper=[], perLink=[];
for(const p of inventory) {
  const gold=truth.find(g=>g.id===p.id);
  const doc={numPages:p.pages.length, getPage:async n=>({getTextContent:async()=>({items:p.pages[n-1].items}),getViewport:()=>({width:p.pages[n-1].width,height:p.pages[n-1].height})}),
    getDestinations:async()=>Object.fromEntries(Object.entries(p.destinations).map(([k,d])=>[k,[{num:d.page},...(d.args??[{name:'XYZ'},d.x,d.y])]])),
    getDestination:async name=>{const d=p.destinations[name];return d?[{num:d.page},...(d.args??[{name:'XYZ'},d.x,d.y])]:null;},getPageIndex:async ref=>ref.num-1};
  const cache=new Map();
  for(const page of p.pages) for(const a of page.links) {
    const name=typeof a.dest==='string'?a.dest:null;
    if(!name || !gold.labels[name]) continue;
    const label=gold.labels[name], ref=label.reference===null?null:gold.references[label.reference];
    let extracted=cache.get(name);
    if(!cache.has(name)) {
      // Exactly the href PDFLinkService produces (legacy escape, not UTF-8).
      const href='#'+escape(name);
      const point=await api.resolveDestination(doc,href);
      const hint=api.parseCiteHref(href);
      const started=performance.now();
      extracted=point?await api.extractBibEntry(doc,point.page,point.x,point.y,hint?.author??null,hint?.year??null,name):null;
      cache.set(name,extracted);
      rows.push({paperId:p.id,destination:name,point,extracted,groundTruth:ref?.text??null,goldIndex:ref?.index??null,alignment:label.score,ms:performance.now()-started,...(ref?accuracy(extracted,ref.text):{})});
    }
    perLink.push({paperId:p.id,page:page.number,annotationId:a.id,destination:name,href:'#'+escape(name),previewSupported:Boolean(api.parseCiteHref('#'+escape(name))),labelled:Boolean(ref),extracted:extracted??null,...(ref?accuracy(extracted,ref.text):{})});
  }
  const links=p.pages.flatMap(pg=>pg.links.map(a=>({...a,page:pg.number}))).filter(a=>typeof a.dest==='string'&&gold.labels[a.dest]);
  const current=rows.filter(r=>r.paperId===p.id);
  for(const row of current) row.links=links.filter(a=>a.dest===row.destination).length;
  const supported=links.filter(a=>api.parseCiteHref('#'+a.dest));
  perPaper.push({id:p.id,title:p.title,pdfPages:p.pages.length,ocrReferences:gold.references.length,referenceLinks:links.length,supportedLinks:supported.length,ocrCitationTargets:gold.ocrCitationOccurrences.length,labelledDestinations:current.filter(r=>r.groundTruth).length,correctDestinations:current.filter(r=>r.correct).length});
}
const labelled=rows.filter(r=>r.groundTruth);
const sum=(r,key)=>r.reduce((n,v)=>n+v[key],0);
const summary={papers:perPaper.length,referenceLinks:sum(perPaper,'referenceLinks'),supportedLinks:sum(perPaper,'supportedLinks'),papersWithPreview:perPaper.filter(p=>p.supportedLinks>0).length,labelledDestinations:labelled.length,correctDestinations:labelled.filter(r=>r.correct).length,extractionAccuracy:labelled.filter(r=>r.correct).length/labelled.length,labelledLinks:sum(labelled,'links'),correctLinks:sum(labelled.filter(r=>r.correct),'links'),meanPrecision:sum(labelled,'precision')/labelled.length,meanRecall:sum(labelled,'recall')/labelled.length};
await fs.writeFile(path.join(out,variant+'-extraction.json'),JSON.stringify({variant,summary,perPaper,rows,perLink},null,2));
console.log(JSON.stringify(summary,null,2));
