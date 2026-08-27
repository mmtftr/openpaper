import type { PDFDocumentProxy, PageViewport } from "../pdfjs";

/**
 * Bibliography entry extraction from page text content (notes §3 / bundle `po`).
 * Groups text items into lines by y-coordinate, restricts to the correct
 * column for two-column layouts, then extracts the entry starting near the
 * destination point — either a numbered `[N]` entry or an author-year block.
 */

const CHAR_CAP = 1200;
const LINE_TOLERANCE = 8; // px; notes say ~6-10px
/** Narrowest gap between line-start clusters that counts as a real gutter. */
const MIN_COLUMN_GAP = 60;

interface TextItemBox {
  str: string;
  transform: number[];
  width: number;
  height: number;
}

interface Line {
  text: string;
  x: number;
  y: number;
  height: number;
}

export interface ExtractedBibEntry {
  text: string | null;
  pageNumber: number;
}

async function getPageLines(
  pdfDoc: PDFDocumentProxy,
  pageNumber: number,
): Promise<{ lines: Line[]; viewportWidth: number } | null> {
  let page;
  try {
    page = await pdfDoc.getPage(pageNumber);
  } catch {
    return null;
  }
  const content = await page.getTextContent();
  const viewport: PageViewport = page.getViewport({ scale: 1 });

  const items = content.items as unknown as TextItemBox[];
  // Group items into lines by y (transform[5]) with a small tolerance.
  const raw: { item: TextItemBox }[] = items
    .filter((it) => it.str && it.transform?.length >= 6)
    .map((item) => ({ item }));

  raw.sort((a, b) => b.item.transform[5] - a.item.transform[5] || a.item.transform[4] - b.item.transform[4]);

  const lines: Line[] = [];
  let current: { y: number; parts: string[]; minX: number; height: number } | null = null;
  const flush = () => {
    if (current && current.parts.some((p) => p.trim())) {
      lines.push({
        text: joinParts(current.parts),
        x: current.minX,
        y: current.y,
        height: current.height,
      });
    }
    current = null;
  };
  for (const { item } of raw) {
    const y = item.transform[5];
    if (!current || Math.abs(current.y - y) > LINE_TOLERANCE) {
      flush();
      current = { y, parts: [item.str], minX: item.transform[4], height: item.height };
    } else {
      current.parts.push(item.str);
      current.minX = Math.min(current.minX, item.transform[4]);
      current.height = Math.max(current.height, item.height);
    }
  }
  flush();
  return { lines: lines.sort(sortByColumnAware), viewportWidth: viewport.width };
}

function joinParts(parts: string[]): string {
  let out = "";
  for (const part of parts) {
    out += part;
  }
  return out.replace(/\s+/g, " ").trim();
}

function sortByColumnAware(a: Line, b: Line): number {
  return b.y - a.y || a.x - b.x;
}

/**
 * Find the column boundary from the page's own layout.
 *
 * The original implementation used a fixed x window (220–250pt) copied from a
 * reverse-engineered bundle. That misclassifies any page whose columns don't
 * happen to sit there, and the symptom is ugly: lines from the neighbouring
 * column get spliced into the middle of the extracted reference, e.g.
 * "Superintelligence: Paths, Dangers, Strategies.**AI) funded by the European
 * Union under grant agreement** Oxford University Press …".
 *
 * Instead, look for the widest empty vertical band in the distribution of line
 * start positions. A two-column page has a real gutter there; a single-column
 * page has no gap wide enough and is left alone.
 */
function columnThreshold(lines: Line[], targetX: number): { min: number; max: number } {
  const xs = [...new Set(lines.map((l) => Math.round(l.x)))].sort((a, b) => a - b);
  if (xs.length < 4) return { min: -Infinity, max: Infinity };

  const spread = xs[xs.length - 1] - xs[0];
  // A gutter has to be a real gap, not the indent of a hanging paragraph.
  const minGap = Math.max(MIN_COLUMN_GAP, spread * 0.2);

  let gapAt = -1;
  let gapSize = 0;
  for (let i = 1; i < xs.length; i++) {
    const gap = xs[i] - xs[i - 1];
    if (gap > gapSize) {
      gapSize = gap;
      gapAt = i;
    }
  }
  if (gapAt < 0 || gapSize < minGap) return { min: -Infinity, max: Infinity };

  const split = (xs[gapAt - 1] + xs[gapAt]) / 2;
  // Keep whichever column the destination actually points into. With no x hint
  // we cannot tell, so don't filter at all rather than guess wrong.
  if (targetX == null || !Number.isFinite(targetX)) {
    return { min: -Infinity, max: Infinity };
  }
  return targetX > split
    ? { min: split, max: Infinity }
    : { min: -Infinity, max: split };
}

const NUMBERED_MARKER_RE = /\[\s*(\d{1,3})\s*\]/;

function nextEntryStart(text: string, from: number): number {
  // A new entry starts at "[N]" where N is at most previous+2 (bundle `Eo`).
  const re = /\[\s*(\d{1,3})\s*\]/g;
  re.lastIndex = from + 1;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const n = Number(m[1]);
    if (n <= 999 && m.index > from) {
      // Heuristic: numbered entries ascend; a jump of >3 suggests a false hit.
      const prev = text.slice(0, m.index).match(/\[\s*(\d{1,3})\s*\]\s*$/);
      if (!prev || n > Number(prev[1])) return m.index;
    }
  }
  return text.length;
}

// Author-year entry heuristics (bundle `Po` / `Fo`).
const BIB_LINE_START_RE =
  /^(\[\s*\d{1,3}\s*\]|[A-Z][A-Za-z.'’\-]*(?:,\s*[A-Z]\.)+\s|von\s|de\s|van\s)/;
const ENTRY_TERMINATOR_RE = /[.")]\s*$|\b(19|20)\d{2}[a-z]?\s*$|https?:\/\/\S+$/;

function looksLikeBibLine(line: string): boolean {
  return BIB_LINE_START_RE.test(line.trim()) || NUMBERED_MARKER_RE.test(line.slice(0, 8));
}

/**
 * Extract the bibliography entry text at/near the destination point.
 */
export async function extractBibEntry(
  pdfDoc: PDFDocumentProxy,
  pageNumber: number,
  targetX: number | null,
  targetY: number | null,
  authorHint: string | null,
  yearHint: number | null,
): Promise<string | null> {
  const data = await getPageLines(pdfDoc, pageNumber);
  if (!data || data.lines.length === 0) return null;
  const { lines, viewportWidth } = data;

  const tx = targetX ?? viewportWidth / 4;
  const ty = targetY ?? 0;

  // Convert destination coords (PDF user space, origin bottom-left) to the
  // same space as transforms: transform[5] is already in that space, so we
  // can compare directly. If no y is given, scan all lines.
  const threshold = columnThreshold(lines, tx);
  const column = lines.filter((l) => l.x >= threshold.min && l.x <= threshold.max);

  const fullText = buildConcatenated(column);

  // Prefer numbered entries when present anywhere in this column/page.
  const firstNumbered = column.findIndex((l) => NUMBERED_MARKER_RE.test(l.text));
  if (firstNumbered >= 0) {
    // Char offset of the destination's nearest line, used as a position hint.
    let nearest = column[0];
    for (const l of column) {
      if (Math.abs(l.y - ty) < Math.abs(nearest.y - ty)) nearest = l;
    }
    const targetOffset = fullText.indexOf(nearest.text);
    return extractNumbered(fullText, authorHint, yearHint, targetOffset);
  }

  // Author-year: start at the line nearest the target y (or best author match).
  let anchorIdx = 0;
  let bestScore = -Infinity;
  for (let i = 0; i < column.length; i++) {
    const l = column[i];
    let score = 0;
    if (authorHint && l.text.toLowerCase().includes(authorHint.toLowerCase().slice(0, 5))) score += 10;
    if (yearHint && l.text.includes(String(yearHint))) score += 5;
    if (ty != null && ty !== 0) score += 3 / (1 + Math.abs(l.y - ty));
    if (score > bestScore) {
      bestScore = score;
      anchorIdx = i;
    }
  }

  // Walk backwards to the entry's first line, then forward until terminator.
  let start = anchorIdx;
  while (start > 0 && !looksLikeBibLine(column[start].text)) start--;
  while (start > 0 && looksLikeBibLine(column[start].text) && looksLikeBibLine(column[start - 1].text)) {
    // Keep walking up while previous line also starts an entry only if the
    // current one doesn't look like a continuation — simple heuristic:
    break;
  }

  const collected: string[] = [];
  for (let i = start; i < column.length; i++) {
    collected.push(column[i].text);
    const joined = collected.join(" ");
    if (ENTRY_TERMINATOR_RE.test(joined) && joined.length > 20) break;
    if (joined.length >= CHAR_CAP) break;
  }
  let text = collected.join(" ");
  if (text.length > CHAR_CAP) text = text.slice(0, CHAR_CAP);
  return text.length > 20 ? text : null;
}

function extractNumbered(
  fullText: string,
  authorHint: string | null,
  yearHint: number | null,
  targetOffset: number,
): string | null {
  // Always clone with the /g flag: exec() on a non-global regex never
  // advances lastIndex and would loop forever.
  const re = new RegExp(NUMBERED_MARKER_RE.source, "g");
  re.lastIndex = 0;
  type Hit = { index: number; num: number };
  const hits: Hit[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(fullText))) hits.push({ index: m.index, num: Number(m[1]) });
  if (hits.length === 0) return null;

  // Pick the entry matching author/year hints if possible, breaking ties by
  // proximity to the destination's char offset in the column text.
  let chosen: Hit | undefined;
  const lower = authorHint ? authorHint.toLowerCase().slice(0, 5) : null;
  const scored = hits.map((hit) => {
    const end = nextEntryStart(fullText, hit.index);
    const own = fullText.slice(hit.index, end).toLowerCase();
    let score = 0;
    if (lower && own.includes(lower)) score += 10;
    if (yearHint != null && own.includes(String(yearHint))) score += 5;
    // Position proximity: entries are ordered, so the closest one to the
    // destination's line is most likely the intended target.
    score += 4 / (1 + Math.abs(hit.index - targetOffset) / 200);
    return { hit, end, score };
  });
  chosen = scored.reduce<{ hit: Hit; end: number; score: number } | null>(
    (best, s) => (!best || s.score > best.score ? s : best),
    null,
  )?.hit;

  const startHit = chosen ?? hits[0];
  const end = nextEntryStart(fullText, startHit.index);
  let text = fullText.slice(startHit.index, end).trim();
  if (text.length > CHAR_CAP) text = text.slice(0, CHAR_CAP);
  return text.length > 20 ? text : null;
}

function buildConcatenated(lines: Line[]): string {
  // Join lines with spaces but keep hyphen-free word boundaries sane.
  return lines.map((l) => l.text).join(" ").replace(/\s+/g, " ").trim();
}
