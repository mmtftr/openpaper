const latexUnicodeMap: Record<string, string> = {
	alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε",
	zeta: "ζ", eta: "η", theta: "θ", iota: "ι", kappa: "κ",
	lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π",
	rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ",
	chi: "χ", psi: "ψ", omega: "ω",
	Alpha: "Α", Beta: "Β", Gamma: "Γ", Delta: "Δ", Theta: "Θ",
	Lambda: "Λ", Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
	in: "∈", notin: "∉", subset: "⊂", supset: "⊃", cup: "∪",
	cap: "∩", leq: "≤", geq: "≥", neq: "≠", approx: "≈",
	sim: "∼", to: "→", rightarrow: "→", leftarrow: "←",
	infty: "∞", partial: "∂", nabla: "∇", forall: "∀", exists: "∃",
	times: "×", cdot: "·", pm: "±", mp: "∓", sum: "∑", prod: "∏",
	int: "∫", sqrt: "√",
	dots: "…", ldots: "…", cdots: "…", vdots: "…", ddots: "…",
	ll: "≪", gg: "≫", equiv: "≡", propto: "∝", perp: "⊥",
	circ: "∘", star: "⋆", bullet: "•", oplus: "⊕", otimes: "⊗",
	Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔",
	leftrightarrow: "↔", mapsto: "↦", land: "∧", lor: "∨", neg: "¬",
};

const boldOrItalicRe = /\*{1,3}([^*]+?)\*{1,3}/g;
const inlineMathRe = /\${1,2}([^$]+?)\${1,2}/g;
const latexFracRe = /\\frac\{([^}]*)\}\{([^}]*)\}/g;
const latexTypefaceWrapperRe =
	/\\(?:mathbb|mathcal|mathfrak|mathbf|mathit|mathsf|mathtt|mathrm|operatorname|text|boldsymbol|pmb|bm|widehat|widetilde|overline|underline|bar|hat|tilde|vec|dot|ddot|check|breve|acute|grave)\{([^}]*)\}/g;
const latexCmdRe = /\\(?:mathrm|operatorname|text)\{([^}]*)\}/g;
const latexSubscriptRe = /_\{([^}]*)\}/g;
const latexSuperscriptRe = /\^\{([^}]*)\}/g;
const backslashLetterRe = /\\([a-zA-Z]+)(?![a-zA-Z])/g;
const backslashPunctRe = /\\([%$&#_{}\[\]~^])/g;
const latexBracesRe = /\{([^{}]*)\}/g;
const headingPrefixRe = /^\s*#{1,6}\s+(?:\d+(?:\.\d+)*\s+)?/gm;
const bulletPrefixRe = /^\s*[-*+]\s+/gm;
const tablePipeRe = /\s*\|\s*/g;
const tableSepRowRe = /^\s*[-:|\s]+\s*$/gm;
const htmlCloseTagRe = /<\/[a-zA-Z][^>]*>/g;
const htmlOpenTagRe =
	/<(?:i|b|u|s|em|strong|sub|sup|br|hr|p|div|span|small|big|tt|code|pre|blockquote|kbd|var|cite|mark|ins|del|abbr|acronym|strike|font)\b[^>]*>/gi;
const fenceRe = /```[a-zA-Z]*/g;
const linkRe = /\[([^\]]+)\]\([^\)]+\)/g;
const imageRe = /!\[[^\]]*\]\([^\)]+\)/g;
const footnoteRefRe = /\[\^[^\]]+\]/g;
const multiWhitespaceRe = /\s+/g;
const sectionNumberPrefixRe = /^(\d+(?:\.\d+)*)\s+(?=\S)/;
const punctNoise = new Set(".,:;-+{}■●▲○◇◆□△▪▫");

const punctFoldMap: Record<string, string> = {
	"‘": "'", "’": "'", "‚": "'", "‛": "'",
	"“": '"', "”": '"', "„": '"', "‟": '"',
	"′": "'", "″": '"',
	"–": "-", "—": "-", "−": "-",
	// U+2010 HYPHEN / U+2011 NON-BREAKING HYPHEN: some PDFs set every hyphen
	// (including line-end hyphenation) with these rather than ASCII '-'.
	"\u2010": "-", "\u2011": "-",
	"­": "",
	"​": "", "‌": "", "‍": "",
	"﻿": "",
	"·": "•", "∙": "•", "⋅": "•",
	"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
};

export type PdfTextMatchStrategy =
	| "normalized"
	| "hyphen-keep"
	| "hyphen-drop"
	| "section-period"
	| "compact-whitespace"
	| "compact-punctuation";

export interface PdfTextMatch {
	start: number;
	end: number;
	strategy: PdfTextMatchStrategy;
	matchedText: string;
}

function decodeHtmlEntities(text: string): string {
	return text.replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos);/g, (entity, body: string) => {
		if (body === "amp") return "&";
		if (body === "lt") return "<";
		if (body === "gt") return ">";
		if (body === "quot") return '"';
		if (body === "apos") return "'";
		const isHex = body.toLowerCase().startsWith("#x");
		const value = Number.parseInt(body.slice(isHex ? 2 : 1), isHex ? 16 : 10);
		return Number.isFinite(value) ? String.fromCodePoint(value) : entity;
	});
}

function canonicalizeForPdfMatch(text: string): string {
	let result = decodeHtmlEntities(text).normalize("NFC");
	for (const [from, to] of Object.entries(punctFoldMap)) {
		result = result.split(from).join(to);
	}
	return result;
}

export const SOFT_HYPHEN = "\u00AD";

/**
 * Fold one PDF text character for the page index. Unlike quotes (where the
 * soft hyphen is simply dropped), the page index keeps soft hyphens: at a line
 * end one marks hyphenation, and `findMatchRange` needs to see it to rejoin
 * the word.
 */
export function foldPdfMatchChar(char: string): string {
	if (char === SOFT_HYPHEN) return char;
	return (punctFoldMap[char] ?? char).normalize("NFC");
}

export function normalizeForPdfMatch(text: string): string {
	if (!text) return "";

	let result = text
		.replace(imageRe, "")
		.replace(linkRe, "$1")
		.replace(footnoteRefRe, "")
		.replace(fenceRe, "")
		.replace(htmlCloseTagRe, "")
		.replace(htmlOpenTagRe, "");

	result = result.replace(inlineMathRe, "$1").replace(inlineMathRe, "$1");
	result = result.replace(latexFracRe, "$1/$2");
	result = result.replace(latexTypefaceWrapperRe, " $1 ");
	result = result.replace(latexCmdRe, " $1 ");
	result = result.replace(
		backslashLetterRe,
		(_, command: string) => latexUnicodeMap[command] ?? command
	);
	result = result.replace(backslashPunctRe, "$1");
	result = result.replace(latexSubscriptRe, "$1");
	result = result.replace(latexSuperscriptRe, "$1");
	result = result.replace(latexBracesRe, "$1");

	result = result
		.replace(headingPrefixRe, "")
		.replace(bulletPrefixRe, "")
		.replace(tableSepRowRe, "")
		.replace(tablePipeRe, " ")
		.replace(boldOrItalicRe, "$1")
		.replaceAll("*", "");

	return canonicalizeForPdfMatch(result).replace(multiWhitespaceRe, " ").trim();
}

function isWhitespace(char: string): boolean {
	return /\s/.test(char);
}

function isWhitespaceOrPunctNoise(char: string): boolean {
	return isWhitespace(char) || punctNoise.has(char);
}

function isWordChar(char: string | undefined): boolean {
	return Boolean(char && /[\p{L}\p{N}_]/u.test(char));
}

function exactMatch(
	needle: string,
	haystack: string,
	strategy: PdfTextMatchStrategy
): PdfTextMatch | null {
	const index = haystack.toLowerCase().indexOf(needle.toLowerCase());
	if (index < 0) return null;
	return {
		start: index,
		end: index + needle.length,
		strategy,
		matchedText: haystack.slice(index, index + needle.length),
	};
}

function rejoinHyphenated(
	haystack: string,
	keepHyphen: boolean
): { text: string; map: number[] } {
	const chars: string[] = [];
	const map: number[] = [];
	let i = 0;

	while (i < haystack.length) {
		const next = haystack[i + 1];
		if (isWordChar(haystack[i]) && next === "-") {
			let j = i + 2;
			while (j < haystack.length && isWhitespace(haystack[j])) j += 1;
			if (j > i + 2 && isWordChar(haystack[j])) {
				chars.push(haystack[i]);
				map.push(i);
				if (keepHyphen) {
					chars.push("-");
					map.push(i + 1);
				}
				chars.push(haystack[j]);
				map.push(j);
				i = j + 1;
				continue;
			}
		}

		chars.push(haystack[i]);
		map.push(i);
		i += 1;
	}

	return { text: chars.join(""), map };
}

function mappedMatch(
	needle: string,
	haystack: string,
	mapped: { text: string; map: number[] },
	strategy: PdfTextMatchStrategy
): PdfTextMatch | null {
	const index = mapped.text.toLowerCase().indexOf(needle.toLowerCase());
	if (index < 0) return null;
	const last = index + needle.length - 1;
	if (last >= mapped.map.length) return null;
	const start = mapped.map[index];
	const end = mapped.map[last] + 1;
	return {
		start,
		end,
		strategy,
		matchedText: haystack.slice(start, end),
	};
}

function compactFind(
	needle: string,
	haystack: string,
	dropPredicate: (char: string) => boolean,
	strategy: PdfTextMatchStrategy
): PdfTextMatch | null {
	const needleCompact = Array.from(needle).filter((char) => !dropPredicate(char)).join("");
	if (needleCompact.length < 8) return null;

	const map: number[] = [];
	const haystackCompactChars: string[] = [];
	for (let i = 0; i < haystack.length; i++) {
		const char = haystack[i];
		if (!dropPredicate(char)) {
			map.push(i);
			haystackCompactChars.push(char);
		}
	}

	const haystackCompact = haystackCompactChars.join("");
	const index = haystackCompact.toLowerCase().indexOf(needleCompact.toLowerCase());
	if (index < 0) return null;

	const last = index + needleCompact.length - 1;
	if (last >= map.length) return null;
	const start = map[index];
	const end = map[last] + 1;
	return {
		start,
		end,
		strategy,
		matchedText: haystack.slice(start, end),
	};
}

export function findServerAlignedMatchInNormalizedPdfText(
	quote: string,
	normalizedPageText: string
): PdfTextMatch | null {
	const needle = normalizeForPdfMatch(quote);
	if (needle.length < 4 || !normalizedPageText) return null;

	const exact = exactMatch(needle, normalizedPageText, "normalized");
	if (exact) return exact;

	const keepHyphen = mappedMatch(
		needle,
		normalizedPageText,
		rejoinHyphenated(normalizedPageText, true),
		"hyphen-keep"
	);
	if (keepHyphen) return keepHyphen;

	const dropHyphen = mappedMatch(
		needle,
		normalizedPageText,
		rejoinHyphenated(normalizedPageText, false),
		"hyphen-drop"
	);
	if (dropHyphen) return dropHyphen;

	const sectionNumberMatch = sectionNumberPrefixRe.exec(needle);
	if (sectionNumberMatch && !sectionNumberMatch[1].includes(".")) {
		const withPeriod = `${sectionNumberMatch[1]}. ${needle.slice(sectionNumberMatch[0].length)}`;
		const sectionMatch = exactMatch(withPeriod, normalizedPageText, "section-period");
		if (sectionMatch) return sectionMatch;
	}

	if (needle.length >= 12) {
		const whitespaceMatch = compactFind(
			needle,
			normalizedPageText,
			isWhitespace,
			"compact-whitespace"
		);
		if (whitespaceMatch) return whitespaceMatch;
	}

	if (needle.length >= 30) {
		const punctuationMatch = compactFind(
			needle,
			normalizedPageText,
			isWhitespaceOrPunctNoise,
			"compact-punctuation"
		);
		if (punctuationMatch) return punctuationMatch;
	}

	return null;
}
