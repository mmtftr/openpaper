import type { PDFDocumentProxy } from "../pdfjs";

/**
 * Whitespace-tolerant arXiv ID extraction (notes §4 / bundle regex Ka).
 * Matches "arXiv:2010.12345", "arXiv preprint arXiv:2010.12345",
 * "arxiv.org/abs/2010.12345", "CoRR, abs/2010.12345" — allowing arbitrary
 * whitespace between characters since PDF text extraction inserts breaks.
 */
const ARXIV_ID_RE =
  /(?:arXiv(\s+preprint)?\s*:?\||arxiv\.org\/abs\/|CoRR,\s*abs\/)\s*((?:\d\s*){4}\.\s*(?:\d\s*){4,6})/gi;

export function extractArxivId(text: string): string | null {
  ARXIV_ID_RE.lastIndex = 0;
  const m = ARXIV_ID_RE.exec(text);
  if (!m) return null;
  return normalizeArxivId(m[2]);
}

export function normalizeArxivId(raw: string): string {
  return raw.replace(/\s+/g, "");
}

/** Extract an arXiv ID from an external arxiv.org / alphaxiv.org URL. */
export function extractArxivIdFromUrl(url: string): string | null {
  try {
    const u = new URL(url);
    if (
      u.hostname !== "arxiv.org" &&
      !u.hostname.endsWith(".arxiv.org") &&
      u.hostname !== "alphaxiv.org" &&
      !u.hostname.endsWith(".alphaxiv.org")
    ) {
      return null;
    }
    const m = u.pathname.match(/\/(?:abs|pdf)\/(\d{4}\.\d{4,6})(v\d+)?/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

/**
 * Parse pdf.js internal citation hrefs of the form `#cite.author_year`
 * (LaTeX hyperref \cite links). Returns the author token and year when
 * present, e.g. "#cite.vaswani2017" → { author: "vaswani", year: null }.
 */
export function parseCiteHref(
  href: string,
): { name: string; author: string | null; year: number | null } | null {
  let decoded = href;
  try {
    decoded = decodeURIComponent(href);
  } catch {
    // keep raw href
  }
  const m = decoded.match(/^#cite\.(.+)$/i);
  if (!m) return null;
  const name = m[1];
  // Author + year suffix; the year may be 2-digit ("sutskever14") or 4-digit.
  const am = name.match(/^([a-z]+)[-_]?(\d{2,4})$/i);
  if (!am) return { name, author: null, year: null };
  let year = Number(am[2]);
  if (am[2].length <= 2) year += year < 30 ? 2000 : 1900;
  return { name, author: am[1], year };
}

// Destination kinds we never preview (notes §1, regex list `so`).
const NON_CITATION_DEST_RE =
  /^(?:fig|figure|tab|table|sec|section|caption|eq|equation|app|appendix|theorem|lemma|definition|algorithm|listing|footnote)[._]/i;

export function isNonCitationDest(href: string): boolean {
  let decoded = href;
  try {
    decoded = decodeURIComponent(href);
  } catch {
    // keep raw href
  }
  return NON_CITATION_DEST_RE.test(decoded.replace(/^#/, ""));
}

export interface DestinationPoint {
  page: number; // 1-based
  x: number | null;
  y: number | null;
}

function refToPoint(
  pdfDoc: PDFDocumentProxy,
  dest: unknown[],
): Promise<DestinationPoint | null> {
  const ref = dest[0];
  if (ref == null || typeof ref !== "object") return Promise.resolve(null);
  const x = typeof dest[2] === "number" ? dest[2] : null;
  const y = typeof dest[3] === "number" ? dest[3] : null;
  return pdfDoc
    .getPageIndex(ref as never)
    .then((index) => ({ page: index + 1, x, y }))
    .catch(() => null);
}

/**
 * Resolve a pdf.js link annotation href to an explicit destination
 * (notes §2 step 2 / bundle `ro`):
 * - `#page=N&zoom=...` form → direct page jump.
 * - named dest via getDestination with an NFD-normalized retry; falls back
 *   to a fuzzy match over getDestinations() keys (author token + year).
 */
export async function resolveDestination(
  pdfDoc: PDFDocumentProxy,
  href: string,
): Promise<DestinationPoint | null> {
  let decoded = href;
  try {
    decoded = decodeURIComponent(href);
  } catch {
    // keep raw href
  }

  const pageForm = decoded.match(/^#page=(\d+)(?:&(?:zoom|xyz)=.*)?$/);
  if (pageForm) {
    return { page: Number(pageForm[1]), x: null, y: null };
  }

  if (!decoded.startsWith("#")) return null;
  const name = decoded.slice(1);

  const direct = await getNamedDest(pdfDoc, name);
  if (direct && direct.length > 1) {
    const point = refToPoint(pdfDoc, direct);
    if (point) return point;
  }

  // Fuzzy fallback over all named destinations.
  const fuzzy = await fuzzyDestination(pdfDoc, name);
  if (!fuzzy) return null;
  return refToPoint(pdfDoc, fuzzy);
}

async function getNamedDest(
  pdfDoc: PDFDocumentProxy,
  name: string,
): Promise<unknown[] | null> {
  try {
    const dest = await pdfDoc.getDestination(name);
    if (dest && (dest as unknown[]).length > 1) return dest as unknown[];
  } catch {
    // fall through
  }
  // NFD-normalized retry (PDFs sometimes store decomposed names).
  const nfd = name.normalize("NFD");
  if (nfd !== name) {
    try {
      const dest = await pdfDoc.getDestination(nfd);
      if (dest && (dest as unknown[]).length > 1) return dest as unknown[];
    } catch {
      // ignore
    }
  }
  return null;
}

async function fuzzyDestination(
  pdfDoc: PDFDocumentProxy,
  name: string,
): Promise<unknown[] | null> {
  let destinations: Record<string, unknown[]>;
  try {
    destinations = (await pdfDoc.getDestinations()) as Record<string, unknown[]>;
  } catch {
    return null;
  }
  if (!destinations) return null;

  const lowerName = name.toLowerCase();
  const authorMatch = lowerName.match(/^([a-z]+)[-_.]?(\d{4})/);

  // Pass 1: key containing the same author token and 4-digit year.
  if (authorMatch) {
    for (const key of Object.keys(destinations)) {
      const k = key.toLowerCase().replace(/^(cite|bib|ref)[._]/, "");
      if (k.includes(authorMatch[1]) && k.includes(authorMatch[2])) {
        return destinations[key];
      }
    }
  }
  // Pass 2: first-4-char prefix fallback.
  const prefix = lowerName.slice(0, 4);
  for (const key of Object.keys(destinations)) {
    if (key.toLowerCase().startsWith(prefix)) return destinations[key];
  }
  return null;
}
