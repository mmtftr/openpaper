import type { PDFDocumentProxy } from "../pdfjs";

function decodeLegacyHash(href: string): string {
  // PDFLinkService uses escape(), including %uXXXX and Latin-1 %XX, rather
  // than encodeURIComponent(). Publisher names often contain BOMs/accents.
  return href.replace(/%u([0-9a-f]{4})|%([0-9a-f]{2})/gi, (_match, unicode: string | undefined, byte: string | undefined) => String.fromCharCode(parseInt(unicode ?? byte!, 16)));
}

export function decodeCitationHref(href: string): string {
  try { return decodeURIComponent(href); } catch { return decodeLegacyHash(href); }
}

/**
 * Whitespace-tolerant arXiv ID extraction (notes §4 / bundle regex Ka).
 * Matches "arXiv:2010.12345", "arXiv preprint arXiv:2010.12345",
 * "arxiv.org/abs/2010.12345", "CoRR, abs/2010.12345" — allowing arbitrary
 * whitespace between characters since PDF text extraction inserts breaks.
 */
const ARXIV_ID_RE =
  /(?:arXiv(\s+preprint)?\s*:?\s*|arxiv\.org\/abs\/|CoRR,\s*abs\/)\s*((?:\d\s*){4}\.\s*(?:\d\s*){4,6})/gi;

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
  const decoded = decodeCitationHref(href);
  if (!decoded.startsWith("#") || !isCitationDestination(decoded.slice(1))) return null;
  const name = decoded.slice(1).replace(/^cite\./i, "");
  // Author + year suffix; the year may be 2-digit ("sutskever14") or 4-digit.
  const am = name.match(/^([a-z]+)[-_]?(\d{2,4})$/i);
  if (!am) return { name, author: null, year: null };
  let year = Number(am[2]);
  if (am[2].length <= 2) year += year < 30 ? 2000 : 1900;
  return { name, author: am[1], year };
}

/** Explicit bibliography link formats observed in publisher PDFs. */
export function isCitationDestination(name: string): boolean {
  return /^(?:cite\.|bib\d+(?:$|[._])|bm_CR\d+$|link_sbref\d+$|[LR]?pone\.[\d.]+\.ref\d+$)/i.test(name) ||
    /\.indd:B\d+:\d+$/i.test(name) ||
    /\.indd:\s*\d+\.\s*\S.{20}/u.test(name.replace(/\uFEFF/g, ""));
}

// Destination kinds we never preview (notes §1, regex list `so`).
const NON_CITATION_DEST_RE =
  /^(?:fig|figure|tab|table|sec|section|caption|eq|equation|app|appendix|theorem|lemma|definition|algorithm|listing|footnote)[._]/i;

export function isNonCitationDest(href: string): boolean {
  const decoded = decodeCitationHref(href);
  return NON_CITATION_DEST_RE.test(decoded.replace(/^#/, ""));
}

export interface DestinationPoint {
  page: number; // 1-based
  x: number | null;
  y: number | null;
}

export function destinationToPoint(
  pdfDoc: Pick<PDFDocumentProxy, "getPageIndex">,
  dest: unknown[],
): Promise<DestinationPoint | null> {
  const ref = dest[0];
  if (ref == null || (typeof ref !== "object" && typeof ref !== "number")) return Promise.resolve(null);
  const kind = (dest[1] as { name?: string } | null)?.name;
  const horizontal = kind === "FitH" || kind === "FitBH";
  const x = !horizontal && typeof dest[2] === "number" ? dest[2] : null;
  const y = horizontal ? (typeof dest[2] === "number" ? dest[2] : null) : (typeof dest[3] === "number" ? dest[3] : null);
  if (typeof ref === "number") return Promise.resolve({ page: ref + 1, x, y });
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
  const decoded = decodeCitationHref(href);

  const pageForm = decoded.match(/^#page=(\d+)(?:&(?:zoom|xyz)=.*)?$/);
  if (pageForm) {
    return { page: Number(pageForm[1]), x: null, y: null };
  }

  if (!decoded.startsWith("#")) return null;
  const name = decoded.slice(1);

  const direct = await getNamedDest(pdfDoc, name) ?? (decodeLegacyHash(href) !== decoded ? await getNamedDest(pdfDoc, decodeLegacyHash(href).slice(1)) : null);
  if (direct && direct.length > 1) {
    const point = await destinationToPoint(pdfDoc, direct);
    if (point) return point;
  }

  // Fuzzy fallback over all named destinations.
  const fuzzy = await fuzzyDestination(pdfDoc, name);
  if (!fuzzy) return null;
  return destinationToPoint(pdfDoc, fuzzy);
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

  // A shared "cite" prefix says nothing about identity. Never silently use
  // the first bibliography entry when the requested destination is missing.
  const normalized = name.normalize("NFC").toLowerCase();
  const keys = Object.keys(destinations).filter(key => key.normalize("NFC").toLowerCase() === normalized);
  return keys.length === 1 ? destinations[keys[0]] : null;
}
