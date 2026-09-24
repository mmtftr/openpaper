import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { out, clientRequire } from './modules.mjs';
const { getDocument } = await import(pathToFileURL(clientRequire.resolve('pdfjs-dist/legacy/build/pdf.mjs')));

const papers = JSON.parse(await fs.readFile(path.join(out, 'papers.json')));
const inventory = [];
for (const paper of papers) {
  const doc = await getDocument({ data: new Uint8Array(await fs.readFile(path.join(out, 'pdfs', paper.id + '.pdf'))), useSystemFonts: false, verbosity: 0 }).promise;
  const destinations = await doc.getDestinations();
  const dests = {};
  async function point(dest) {
    if (!Array.isArray(dest)) return null;
    const index = typeof dest[0] === 'number' ? dest[0] : await doc.getPageIndex(dest[0]);
    const horizontal = ['FitH', 'FitBH'].includes(dest[1]?.name);
    return { page: index + 1, x: horizontal ? null : dest[2] ?? null, y: horizontal ? dest[2] ?? null : dest[3] ?? null, args: dest.slice(1) };
  }
  for (const [key, dest] of Object.entries(destinations || {})) dests[key] = await point(dest);
  const pages = [];
  for (let n = 1; n <= doc.numPages; n++) {
    const p = await doc.getPage(n);
    const text = await p.getTextContent();
    const annotations = await p.getAnnotations();
    const links = [];
    for (const a of annotations.filter(a => a.subtype === 'Link')) {
      links.push({ id: a.id, dest: a.dest, url: a.url, rect: a.rect, point: a.dest ? typeof a.dest === 'string' ? dests[a.dest] ?? await point(await doc.getDestination(a.dest)) : await point(a.dest) : null });
    }
    pages.push({ number: n, width: p.view[2], height: p.view[3], items: text.items.filter(i => 'str' in i), links });
  }
  inventory.push({ id: paper.id, title: paper.title, destinations: dests, pages });
  console.log(`${inventory.length}/48 ${paper.title.slice(0, 60)}: ${Object.keys(dests).length} destinations, ${pages.reduce((n,p) => n + p.links.length, 0)} links`);
  await doc.destroy();
}
await fs.writeFile(path.join(out, 'inventory.json'), JSON.stringify(inventory));
