import fs from 'node:fs/promises';
import path from 'node:path';
import { out } from './modules.mjs';

const gold=JSON.parse(await fs.readFile(path.join(out,'ground-truth.json')));
const papers=JSON.parse(await fs.readFile(path.join(out,'papers.json')));
const norm=s=>(s||'').normalize('NFKD').toLowerCase().replace(/[^a-z0-9]/g,'');
const labels=[];
for(const p of gold) {
  const used=new Set();
  let libraryCount=0,otherCount=0;
  for(const [destination,label] of Object.entries(p.labels)) {
    if(label.reference===null||used.has(label.reference))continue;
    const r=p.references[label.reference];
    const own=papers.find(p=>p.title && norm(p.title).length>20 && norm(r.text).includes(norm(p.title)));
    const quoted=r.text.match(/[“"]([^”"]{20,180})[”"]/);
    const segments=r.text.replace(/^\[\d+\]\s*/,'').split(/(?<=[a-z])\.\s+(?=[A-Z])/);
    // Conservative full-name author list then title sentence; initials stay intact.
    const parsed=segments.length>=2 && segments[0].length>8 && segments[1].split(/\s+/).length>=4 && !/^(?:In |URL |Proceedings|Journal|Advances|arXiv)/.test(segments[1]) ? segments[1] : null;
    let title=own?.title??quoted?.[1]??parsed;
    if(!title || /https?:|\bet al\b|^doi:|^volume\b/i.test(title) || title.length>180)continue;
    if(/^(?:Psychological bulletin|Neural Networks \d|Annals |Nat News|Nature methods|PLoS computational|Nat Rev|Oxford University|Technical report|IEEE Signal)/i.test(title))continue;
    title=title.replace(/\.\s+(?:arxiv|\*?Proceedings|\d{4}|Goodfire|Correspondence).*$/i,'').replace(/,?\s+(?:19|20)\d{2}[a-z]?\.?$/,'').replace(/\.\s*$/,'');
    // OCR says "monoseismicity" in two duplicate PDFs; do not silently label it.
    if(/monoseismicity/i.test(title))continue;
    if(own?libraryCount>=2:otherCount>=2)continue;
    if(own)libraryCount++;else otherCount++;
    used.add(label.reference);
    labels.push({paperId:p.id,destination,title,doi:r.doi,reference:r.text,labelSource:own?'Library title contained in OCR':quoted?'Quoted title in OCR':'Title sentence parsed from OCR',libraryPaperId:own?.id??null});
  }
}
// Manual review rejected these sentence parses: they are venues, not titles.
// Keep the rejected labels in the audit, never count them as resolver misses.
const rejected=labels.filter(l=>/^(?:Science \(|Commun\. on Pure|Nature \d|Nucleic Acids Research|Cold Spring Harbor Protocols|Mol Metab|Curr Opin)/.test(l.title));
const reviewed=labels.filter(l=>!rejected.includes(l)).map(l=>({...l,title:l.title.replace(/\.\s+\*Journal of Cryptology.*$/,''),expectedNoPaper:/Code for Human-GEM.*Github/.test(l.reference)}));
await fs.writeFile(path.join(out,'resolution-labels-rejected.json'),JSON.stringify(rejected,null,2));
await fs.writeFile(path.join(out,'resolution-labels.json'),JSON.stringify(reviewed,null,2));
console.log(reviewed.length,'labels');
for(const l of reviewed) console.log(l.paperId.slice(0,8),l.labelSource,':',l.title);
