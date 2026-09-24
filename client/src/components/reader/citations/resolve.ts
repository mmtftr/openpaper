"use client";

import { api, unwrap } from "@/lib/api/client";
import { extractArxivId } from "./helpers";

/**
 * Turn a bibliography entry into something we can show the reader a card for.
 *
 * paper-reader resolved references against arXiv's Atom API through a Vite dev
 * proxy, which covers only arXiv and has no production story. openpaper already
 * has two better sources on its own backend, so this asks them in the order
 * that is most useful to the person reading:
 *
 *   1. **their own library** (`/api/search/local`) — "you already have this
 *      paper" is the most actionable answer, and it links straight to it;
 *   2. **OpenAlex** (`/api/search/global/search`) — all of the literature, not
 *      just preprints, with DOIs, abstracts and open-access PDF links.
 *
 * Precision comes from `titleMatches`, not from the query: a search engine will
 * always return *something*, and showing the wrong paper on a citation hover is
 * worse than showing none. That safety net is what lets the queries below be
 * generous.
 */

export interface PaperInfo {
	/** openpaper paper id when the reference is in the user's library. */
	paperId?: string;
	doi?: string | null;
	title: string;
	abstract: string;
	authors: string[];
	publicationDate: string | null;
	topics: string[];
	/** Rendered first page, for papers already in the library. */
	previewUrl?: string | null;
	/** Landing page for an external match. */
	externalUrl?: string | null;
	/** Open-access PDF, when one exists — this is what "Add to library" imports. */
	pdfUrl?: string | null;
	/** Journal / conference, when OpenAlex knows it. */
	venue?: string | null;
}

export type ResolveOutcome =
	| { paper: PaperInfo; matchedBy: "library" | "openalex" }
	| "unavailable"
	| null;

function normalizeTitle(value: string): string {
	return value
		.normalize("NFKD")
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "");
}

/**
 * Accept a candidate only when its title actually appears in the reference
 * text. Normalisation strips punctuation, so extraction noise glued onto a word
 * ("Strategies.AI)") doesn't defeat the containment check.
 */
function titleMatches(candidateTitle: string, referenceText: string): boolean {
	const title = normalizeTitle(candidateTitle);
	if (title.length < 20) return false;
	return normalizeTitle(referenceText).includes(title);
}

function authorsMatch(authors: string[], referenceText: string): boolean {
	const surnames = authors.map(name => normalizeTitle(name.split(/\s+/).at(-1) ?? "")).filter(name => name.length >= 3);
	return surnames.length === 0 || surnames.some(name => normalizeTitle(referenceText).includes(name));
}

/** Leading "Bostrom, N." / "Vaswani, A., Shazeer, N., et al." author block. */
const AUTHOR_PREFIX_RE =
	/^(?:\[\s*\d{1,3}\s*\]\s*)?(?:[A-Z][\w'’-]*,?\s+(?:[A-Z]\.\s*)+(?:and\s+|,\s*|&\s*)?){1,12}/;

/**
 * Candidate search queries for a reference, best first.
 *
 * The previous implementation split the entry on commas as well as periods,
 * which shreds any title containing a comma — "Superintelligence: Paths,
 * Dangers, Strategies" became four fragments, none of them usable, and the
 * longest surviving segment was a footnote that had bled in from the adjacent
 * column. It then searched for *that*. Sentences only, plus a whole-entry
 * fallback, and let `titleMatches` reject bad hits.
 */
export function buildQueries(referenceText: string): string[] {
	const queries: string[] = [];
	const cleaned = referenceText
		.replace(/^\s*\[\s*\d{1,3}\s*\]\s*/, "")
		.replace(/\s+/g, " ")
		.trim();

	// Drop the author block; what follows a reference's authors is the title.
	const withoutAuthors = cleaned.replace(AUTHOR_PREFIX_RE, "").trim();
	// Quoted titles are common in author-year bibliographies. Keep their commas.
	const quoted = cleaned.match(/[“"]([^”"]{20,200})[”"]/);
	if (quoted) queries.push(quoted[1]);

	// Sentence split. `.` immediately followed by a capital with no space is an
	// extraction artefact ("Strategies.AI)"), so treat it as a boundary too —
	// but not when the preceding token is an initial ("N. Superintelligence").
	const source = (withoutAuthors || cleaned).replace(/^.*?\((?:19|20)\d{2}[a-z]?\)\.?\s+(?=[A-Z])/, "");
	const segments = source
		.split(/\.(?=\s|[A-Z])|(?<=\w)\?\s+(?=[A-Z])/)
		.map((s) => s.trim())
		.filter(Boolean);

	// Venue/publication tails, which are never the title. Note `in` is only a
	// stop word when a venue word follows it: a bare `^in\b` would also throw
	// away perfectly good titles that happen to start with "In" ("In Defense
	// of ...", "In Search of ...").
	const stopRe =
		/^(?:in\s+(?:proc\b|proceedings|advances|workshop|conf\b|conference|international|the\s+proceedings)|proceedings|advances|journal|vol\b|volume|pages|number|no\b|acm|ieee|arxiv|preprint|technical report|phd thesis|springer|elsevier|oxford|cambridge|mit press|urlhttp|https?:)/i;

	for (const seg of segments) {
		const words = seg.split(/\s+/);
		if (words.length < 4 || words.length > 30) continue;
		if (stopRe.test(seg)) continue;
		if (!/[a-z]{3}/i.test(seg)) continue;
		// Author lists can be longer than titles and used to occupy both search
		// slots. Initials or several comma-separated capitalized names identify
		// those lists without splitting titles at their commas.
		if ((seg.match(/\b[A-Z]\.\s/g) ?? []).length >= 2) continue;
		if ((seg.match(/,/g) ?? []).length >= 2 && words.filter(w => /^[A-Z]/.test(w)).length / words.length > .6) continue;
		queries.push(seg.replace(/["“”]/g, "").replace(/,?\s+(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+)?(?:19|20)\d{2}[a-z]?\.?$/, "").trim());
	}

	// Whole entry, trimmed. OpenAlex tolerates a messy query, and the title
	// containment check keeps a loose match from being shown.
	const whole = withoutAuthors || cleaned;
	if (whole.length > 20) queries.push(whole.slice(0, 200));

	const arxivId = extractArxivId(referenceText);
	// Identifier search is a fallback: general search does not reliably index
	// arXiv IDs, and must never displace a usable title query.
	if (arxivId) queries.push(`arXiv:${arxivId}`);

	return [...new Set(queries)].slice(0, 4);
}

async function searchLibrary(
	query: string,
	referenceText: string,
	signal?: AbortSignal
): Promise<PaperInfo | null> {
	const results = await unwrap(
		api.GET("/api/search/local/", { params: { query: { q: query, limit: 5 } }, signal })
	);
	for (const paper of results?.papers ?? []) {
		if (!paper.title) continue;
		if (!titleMatches(paper.title, referenceText)) continue;
		if (!authorsMatch(paper.authors ?? [], referenceText)) continue;
		return {
			paperId: paper.id,
			title: paper.title,
			abstract: paper.abstract ?? "",
			authors: paper.authors ?? [],
			publicationDate: paper.publish_date,
			topics: [],
			previewUrl: paper.preview_url,
		};
	}
	return null;
}

async function searchOpenAlex(
	query: string,
	referenceText: string,
	signal?: AbortSignal
): Promise<PaperInfo | null> {
	const response = await unwrap(
		api.POST("/api/search/global/search", { params: { query: { query, page: 1 } }, signal })
	);
	for (const work of response?.results ?? []) {
		if (!work.title || !titleMatches(work.title, referenceText)) continue;
		if (!authorsMatch((work.authorships ?? []).map(a => a.author?.display_name ?? ""), referenceText)) continue;
		const doi = work.doi ?? null;
		return {
			doi,
			title: work.title,
			abstract: work.abstract ?? "",
			authors: (work.authorships ?? [])
				.map((a) => a.author?.display_name ?? "")
				.filter(Boolean),
			publicationDate:
				work.publication_date ??
				(work.publication_year ? String(work.publication_year) : null),
			topics: (work.topics ?? [])
				.map((t) => t.display_name ?? "")
				.filter(Boolean),
			venue: work.primary_location?.source?.display_name ?? null,
			pdfUrl: work.primary_location?.pdf_url ?? work.open_access?.oa_url ?? null,
			externalUrl:
				work.primary_location?.landing_page_url ??
				(doi
					? `https://doi.org/${doi.replace(/^https?:\/\/doi\.org\//, "")}`
					: null),
		};
	}
	return null;
}

/**
 * Returns the matched paper, `null` when the lookup worked but nothing matched,
 * and `"unavailable"` when neither source could be reached — the card shows a
 * different state for each, so "no match" never masquerades as "offline".
 */
export async function resolvePaper(
	referenceText: string,
	signal?: AbortSignal
): Promise<ResolveOutcome> {
	// Software/data references sometimes quote the associated paper's entire
	// title. Importing that paper would be a confidently wrong resolution.
	if (/\b(?:code|data|dataset)\s+for\b.*\b(?:github|deposited|zenodo)\b/i.test(referenceText)) return null;
	const controller = new AbortController();
	const abort = () => controller.abort();
	if (signal?.aborted) return null;
	signal?.addEventListener("abort", abort, { once: true });
	const timeout = setTimeout(abort, 8000);
	try {
		const result = await performLookup(referenceText, controller.signal);
		return controller.signal.aborted && !signal?.aborted ? "unavailable" : result;
	} finally {
		clearTimeout(timeout);
		signal?.removeEventListener("abort", abort);
	}
}

async function performLookup(referenceText: string, signal: AbortSignal): Promise<ResolveOutcome> {
	// Two queries per backend, not four. This runs on hover, and each attempt is
	// a sequential round-trip: an unmatchable reference with four candidates
	// would otherwise cost eight requests before the card could say "no match",
	// and moving the pointer along a row of citations would stack that up.
	// The first query is the extracted title, which is the one that matters.
	const queries = buildQueries(referenceText).slice(0, 2);
	if (queries.length === 0) return null;

	let reachedSomething = false;
	const aborted = () => signal?.aborted === true;

	// The library is the cheapest and most useful answer, so ask it first.
	for (const query of queries) {
		if (aborted()) return null;
		try {
			const own = await searchLibrary(query, referenceText, signal);
			reachedSomething = true;
			if (own) return { paper: own, matchedBy: "library" };
		} catch {
			// unauthenticated, aborted or offline — fall through
		}
	}

	for (const query of queries) {
		if (aborted()) return null;
		try {
			const external = await searchOpenAlex(query, referenceText, signal);
			reachedSomething = true;
			if (external) return { paper: external, matchedBy: "openalex" };
		} catch {
			// try the next query
		}
	}

	// An aborted lookup reached nothing, but it isn't evidence the service is
	// down — saying "unavailable" would be a lie the user can see.
	if (aborted()) return null;
	return reachedSomething ? null : "unavailable";
}
