import type { PDFDocumentProxy } from "../pdfjs";
import { destinationToPoint, isCitationDestination, parseCiteHref, type DestinationPoint } from "./helpers";

interface Item { str: string; transform: number[]; width: number; height: number }
interface Line { text: string; x: number; y: number; height: number; column: number }
interface Layout { lines: Line[]; width: number; height: number; columns: number }
const pageCache = new WeakMap<PDFDocumentProxy, Map<number, Promise<Layout>>>();
interface BibliographyPoint extends DestinationPoint { name: string }
const destinationCache = new WeakMap<PDFDocumentProxy, Promise<BibliographyPoint[]>>();
const marginCache = new WeakMap<PDFDocumentProxy, Map<number, Promise<string>>>();
const compact = (text: string) => text.normalize("NFKC").replace(/\s+/g, "");

function marginText(doc: PDFDocumentProxy, number: number): Promise<string> {
  let pages = marginCache.get(doc);
  if (!pages) { pages = new Map(); marginCache.set(doc, pages); }
  let text = pages.get(number);
  if (!text) {
    text = (async () => {
      const page = await doc.getPage(number);
      const height = page.getViewport({ scale: 1 }).height;
      const items = (await page.getTextContent()).items as unknown as Item[];
      return compact(items.filter(i => i.str && (i.transform[5] > height * .85 || i.transform[5] < height * .1)).map(i => i.str).join(" "));
    })().catch(() => "");
    pages.set(number, text);
  }
  return text;
}

/** The PDF's bibliography anchors are more reliable boundaries than punctuation. */
function bibliographyPoints(doc: PDFDocumentProxy): Promise<BibliographyPoint[]> {
  let cached = destinationCache.get(doc);
  if (!cached) {
    cached = (async () => {
      const named = await doc.getDestinations();
      const pages = new Map<string, Promise<number>>();
      const indexed: Pick<PDFDocumentProxy, "getPageIndex"> = { getPageIndex: ref => {
        const key = JSON.stringify(ref);
        let page = pages.get(key);
        if (!page) { page = doc.getPageIndex(ref); pages.set(key, page); }
        return page;
      } };
      const points: BibliographyPoint[] = [];
      for (const [name, dest] of Object.entries(named ?? {})) {
        if (!isCitationDestination(name) || !Array.isArray(dest)) continue;
        const point = await destinationToPoint(indexed, dest);
        if (point?.y != null) points.push({ ...point, name });
      }
      return points;
    })().catch(() => []);
    destinationCache.set(doc, cached);
  }
  return cached;
}

function pageLayout(doc: PDFDocumentProxy, pageNumber: number): Promise<Layout> {
  let pages = pageCache.get(doc);
  if (!pages) { pages = new Map(); pageCache.set(doc, pages); }
  let cached = pages.get(pageNumber);
  if (!cached) {
    cached = (async () => {
      const page = await doc.getPage(pageNumber);
      const { width, height } = page.getViewport({ scale: 1 });
      const content = await page.getTextContent();
      const items = (content.items as unknown as Item[]).filter(i => i.str?.trim() && i.transform?.length >= 6);
      const points = (await bibliographyPoints(doc)).filter(p => p.page === pageNumber);
      // Look for anchors on both halves. Also handle FitH destinations (no x)
      // using the empty gutter in the actual glyphs, before any line merging.
      const left = points.some(p => p.x! > 0 && p.x! < width * .4);
      const right = points.some(p => p.x! > width * .45);
      const crossing = items.filter(i => i.transform[4] < width / 2 - 7 && i.transform[4] + i.width > width / 2 + 7).length;
      const two = (left && right) || (crossing < 3 && items.filter(i => i.transform[4] > width / 2 && i.width > 30).length > 12 && items.filter(i => i.transform[4] < width / 2 && i.width > 30).length > 12);
      const columns = two ? 2 : 1;
      const lines: Line[] = [];
      for (let column = 0; column < columns; column++) {
        const own = items.filter(i => !two || (i.transform[4] >= width / 2 ? 1 : 0) === column);
        own.sort((a,b) => b.transform[5] - a.transform[5] || a.transform[4] - b.transform[4]);
        const groups: Item[][] = [];
        for (const item of own) {
          const group = groups[groups.length - 1];
          if (group && Math.abs(group[0].transform[5] - item.transform[5]) <= Math.min(3, Math.max(1.5, item.height * .25))) group.push(item);
          else groups.push([item]);
        }
        for (const group of groups) {
          group.sort((a,b) => a.transform[4] - b.transform[4]);
          let text = "", end = -Infinity;
          for (const item of group) {
            if (text && item.transform[4] - end > Math.max(.5, item.height * .12)) text += " ";
            text += item.str;
            end = item.transform[4] + item.width;
          }
          lines.push({ text: text.replace(/\s+/g," ").trim(), x: group[0].transform[4], y: group[0].transform[5], height: Math.max(...group.map(i=>i.height)), column });
        }
      }
      const neighbor = pageNumber > 1 ? pageNumber - 1 : Math.min(doc.numPages, 2);
      const repeated = neighbor !== pageNumber ? await marginText(doc, neighbor) : "";
      return { lines: lines.filter(line => !(line.text.length > 10 && (line.y > height * .85 || line.y < height * .1) && repeated.includes(compact(line.text)))), width, height, columns };
    })();
    pages.set(pageNumber, cached);
    cached.catch(() => pages!.delete(pageNumber));
  }
  return cached;
}

/** Missing left coordinates do not imply the left column. Decline tied fits. */
function columnAt(layout: Layout, x: number | null, y: number | null, author?: string | null, year?: number | null): number | null {
  if (layout.columns === 1) return 0;
  if (x != null) return x >= layout.width * .45 ? 1 : 0;
  if (y == null) return null;
  const candidates = [0, 1].flatMap(column => {
    const first = layout.lines.find(line => line.column === column && line.y <= y - 3 && !pageFurniture(line, layout));
    if (!first || y - first.y > 40) return [];
    const authorMatch = author && compact(first.text).toLowerCase().includes(compact(author).toLowerCase());
    const yearMatch = year && first.text.includes(String(year));
    return [{ column, score: y - first.y - (authorMatch ? 40 : 0) - (yearMatch ? 10 : 0) }];
  }).sort((a, b) => a.score - b.score);
  if (!candidates.length || (candidates[1] && Math.abs(candidates[0].score - candidates[1].score) <= 3)) return null;
  return candidates[0].column;
}

function pageFurniture(line: Line, layout: Layout): boolean {
  return line.y < 30 || line.y > layout.height - 30 || /^\d+$/.test(line.text) || /^(?:Published (?:as|in)|Under review|References$)/i.test(line.text);
}

function sectionHeading(line: Line, previous: Line | null): boolean {
  if (/^(?:Appendix\b|Acknowledg(?:e?ments?)\b|Supplementary (?:Materials?|Information)\b)/i.test(line.text)) return true;
  // An appendix letter alone is insufficient: author initials and titles such
  // as "A Survey" have the same text shape. Require heading-sized typography.
  return Boolean(previous && line.height > previous.height * 1.15 && /^[A-Z](?:\.\d+)*[.)]?\s+[A-Z][a-z]{2,}\b/.test(line.text));
}

/** Extract in reading order, continuing across a column/page to the next anchor. */
export async function extractBibEntry(
  doc: PDFDocumentProxy, pageNumber: number, targetX: number | null,
  targetY: number | null, authorHint: string | null, yearHint: number | null,
  destinationName?: string,
): Promise<string | null> {
  const numbered = destinationName?.replace(/\uFEFF/g, "").match(/^(?:bib|bm_CR|link_sbref)(\d+)$|^[LR]?pone\.[\d.]+\.ref(\d+)$|\.indd:B(\d+):|\.indd:\s*(\d+)\.\s/i);
  if (numbered) {
    const number = Number(numbered.slice(1).find(Boolean));
    const text = await numberedEntry(doc, pageNumber, number, targetX, targetY);
    if (text) return text;
    // Publisher destinations can be offset by several entries. Do not use
    // unrelated text at that coordinate when the explicit number cannot align.
    return null;
  }
  if (targetY == null) return null; // A page-only destination cannot identify an entry.
  const layout = await pageLayout(doc, pageNumber);
  const points = await bibliographyPoints(doc);
  // Hyperref anchors sit to the left of the glyphs, sometimes just left of
  // the geometric page midpoint even for the right column.
  const column = columnAt(layout, targetX, targetY, authorHint, yearHint);
  if (column == null) return null;
  const start = { page: pageNumber, column, y: targetY };
  let next: (BibliographyPoint & { column: number }) | undefined;
  const candidatePages = [...new Set(points.filter(p => p.page >= pageNumber && p.page <= pageNumber + 2).map(p => p.page))].sort((a, b) => a - b);
  for (const pn of candidatePages) {
    const data = pn === pageNumber ? layout : await pageLayout(doc, pn);
    const ordered = points.filter(p => p.page === pn && p.y != null).flatMap(p => {
      const hint = parseCiteHref("#" + p.name);
      const col = columnAt(data, p.x, p.y, hint?.author, hint?.year);
      return col == null ? [] : [{ ...p, column: col }];
    });
    next = ordered.filter(p => p.page > start.page || p.column > column || (p.column === column && p.y! < targetY - 2))
      .sort((a, b) => a.column - b.column || b.y! - a.y!)[0];
    if (next) break;
  }
  const collected: string[] = [];
  let length = 0;
  let previous: Line | null = null;
  // Bounded even for corrupt destinations. A reference can span at most three
  // pages here; unusually long bibliographies should still never freeze hover.
  const lastPage = Math.min(doc.numPages, next?.page ?? pageNumber, pageNumber + 2);
  for (let pn = pageNumber; pn <= lastPage; pn++) {
    const data = pn === pageNumber ? layout : await pageLayout(doc, pn);
    for (const line of data.lines) {
      if (pn === start.page && (line.column < column || (line.column === column && line.y > targetY - 3))) continue;
      const nextCol = next?.column ?? 0;
      if (next && pn === next.page && (line.column > nextCol || (line.column === nextCol && line.y <= next.y! - 3))) return finish(collected);
      // Running headers/footers and page numbers are never bibliography text.
      if (pageFurniture(line, data)) continue;
      if (collected.length && sectionHeading(line, previous)) return finish(collected);
      // Last entry / incomplete destination sets: paragraph gaps and numbered
      // markers remain useful fallbacks, but a period never ends an entry.
      if (collected.length && !next && previous && (line.column !== previous.column || previous.y - line.y > previous.height * 2.2 || /^\[\d+\]\s|^\d+\.\s/.test(line.text))) return finish(collected);
      collected.push(line.text); length += line.text.length; previous = line;
      if (length > 5000) return finish(collected);
    }
  }
  return finish(collected);
}

async function numberedEntry(doc: PDFDocumentProxy, pageNumber: number, number: number, targetX: number | null, targetY: number | null): Promise<string | null> {
  const marker = /^(?:\[\s*(\d{1,3})\s*\]|\((\d{1,3})\)|(\d{1,3})\.)\s/;
  for (const pn of [pageNumber, pageNumber + 1, pageNumber - 1]) {
    if (pn < 1 || pn > doc.numPages) continue;
    const data = await pageLayout(doc, pn);
    const heading = data.lines.findIndex(line => /^(?:References|Bibliography)$/i.test(line.text));
    const column = pn === pageNumber ? columnAt(data, targetX, targetY) : null;
    const candidates = data.lines.flatMap((line, index) => {
      const match = line.text.match(marker);
      if (!match || Number(match.slice(1).find(Boolean)) !== number || index < heading) return [];
      const distance = pn === pageNumber && targetY != null ? Math.abs(line.y - targetY) : 0;
      return [{ index, score: distance + (column != null && line.column !== column ? data.height : 0) }];
    });
    candidates.sort((a, b) => a.score - b.score);
    const start = candidates[0]?.index;
    if (start == null) continue;
    const collected: string[] = [];
    let previous: Line | null = null;
    for (let current = pn; current <= Math.min(doc.numPages, pn + 1); current++) {
      const layout = current === pn ? data : await pageLayout(doc, current);
      for (const line of layout.lines.slice(current === pn ? start : 0)) {
        if (pageFurniture(line, layout)) continue;
        if (/^(?:PLOS ONE|Published |References$|https?:\/\/doi.org\/10.1371\/journal.pone)/.test(line.text)) continue;
        if (collected.length && (marker.test(line.text) || sectionHeading(line, previous))) return finish(collected);
        collected.push(line.text);
        previous = line;
        if (collected.join(" ").length > 5000) return finish(collected);
      }
      // Only continue at a column/page edge, never collect an unrelated next page
      // after the final reference on a partially filled bibliography page.
      if (previous && previous.y > 100) break;
    }
    return finish(collected);
  }
  return null;
}

function finish(lines: string[]): string | null {
  const text = lines.join(" ").replace(/([a-z])-\s+([a-z])/g,"$1$2").replace(/\s+/g," ").trim();
  return text.length >= 15 ? text : null;
}
