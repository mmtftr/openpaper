// Ligatures that expand to multiple characters
export const ligatureMap: Record<string, string> = {
	'\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl',
};

// Greek letters and common math symbols - maps Unicode to ASCII representation
// This allows matching between PDF-rendered symbols and LaTeX input
export const greekLetterMap: Record<string, string> = {
	// Lowercase Greek
	'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta', 'ε': 'epsilon',
	'ζ': 'zeta', 'η': 'eta', 'θ': 'theta', 'ι': 'iota', 'κ': 'kappa',
	'λ': 'lambda', 'μ': 'mu', 'ν': 'nu', 'ξ': 'xi', 'ο': 'omicron',
	'π': 'pi', 'ρ': 'rho', 'σ': 'sigma', 'ς': 'sigma', 'τ': 'tau',
	'υ': 'upsilon', 'φ': 'phi', 'χ': 'chi', 'ψ': 'psi', 'ω': 'omega',
	// Uppercase Greek
	'Α': 'Alpha', 'Β': 'Beta', 'Γ': 'Gamma', 'Δ': 'Delta', 'Ε': 'Epsilon',
	'Ζ': 'Zeta', 'Η': 'Eta', 'Θ': 'Theta', 'Ι': 'Iota', 'Κ': 'Kappa',
	'Λ': 'Lambda', 'Μ': 'Mu', 'Ν': 'Nu', 'Ξ': 'Xi', 'Ο': 'Omicron',
	'Π': 'Pi', 'Ρ': 'Rho', 'Σ': 'Sigma', 'Τ': 'Tau', 'Υ': 'Upsilon',
	'Φ': 'Phi', 'Χ': 'Chi', 'Ψ': 'Psi', 'Ω': 'Omega',
	// Common math symbols
	'∞': 'infinity', '∂': 'partial', '∇': 'nabla', '∑': 'sum',
	'∏': 'prod', '∫': 'int', '√': 'sqrt', '≈': 'approx',
	'≠': 'neq', '≤': 'leq', '≥': 'geq', '±': 'pm',
	'×': 'times', '÷': 'div', '∈': 'in', '∉': 'notin',
	'⊂': 'subset', '⊃': 'supset', '∪': 'cup', '∩': 'cap',
	'∧': 'land', '∨': 'lor', '¬': 'neg', '→': 'to',
	'←': 'leftarrow', '↔': 'leftrightarrow', '⇒': 'Rightarrow',
	'⇐': 'Leftarrow', '⇔': 'Leftrightarrow',
};

// LaTeX commands to their Unicode equivalents (for input normalization)
export const latexCommandMap: Record<string, string> = {
	'\\alpha': 'alpha', '\\beta': 'beta', '\\gamma': 'gamma', '\\delta': 'delta',
	'\\epsilon': 'epsilon', '\\varepsilon': 'epsilon', '\\zeta': 'zeta',
	'\\eta': 'eta', '\\theta': 'theta', '\\vartheta': 'theta', '\\iota': 'iota',
	'\\kappa': 'kappa', '\\lambda': 'lambda', '\\mu': 'mu', '\\nu': 'nu',
	'\\xi': 'xi', '\\pi': 'pi', '\\varpi': 'pi', '\\rho': 'rho',
	'\\varrho': 'rho', '\\sigma': 'sigma', '\\varsigma': 'sigma', '\\tau': 'tau',
	'\\upsilon': 'upsilon', '\\phi': 'phi', '\\varphi': 'phi', '\\chi': 'chi',
	'\\psi': 'psi', '\\omega': 'omega',
	'\\Alpha': 'Alpha', '\\Beta': 'Beta', '\\Gamma': 'Gamma', '\\Delta': 'Delta',
	'\\Epsilon': 'Epsilon', '\\Zeta': 'Zeta', '\\Eta': 'Eta', '\\Theta': 'Theta',
	'\\Iota': 'Iota', '\\Kappa': 'Kappa', '\\Lambda': 'Lambda', '\\Mu': 'Mu',
	'\\Nu': 'Nu', '\\Xi': 'Xi', '\\Pi': 'Pi', '\\Rho': 'Rho', '\\Sigma': 'Sigma',
	'\\Tau': 'Tau', '\\Upsilon': 'Upsilon', '\\Phi': 'Phi', '\\Chi': 'Chi',
	'\\Psi': 'Psi', '\\Omega': 'Omega',
	'\\infty': 'infinity', '\\partial': 'partial', '\\nabla': 'nabla',
	'\\sum': 'sum', '\\prod': 'prod', '\\int': 'int', '\\sqrt': 'sqrt',
	'\\approx': 'approx', '\\neq': 'neq', '\\leq': 'leq', '\\geq': 'geq',
	'\\pm': 'pm', '\\times': 'times', '\\div': 'div', '\\in': 'in',
	'\\notin': 'notin', '\\subset': 'subset', '\\supset': 'supset',
	'\\cup': 'cup', '\\cap': 'cap', '\\land': 'land', '\\lor': 'lor',
	'\\neg': 'neg', '\\to': 'to', '\\rightarrow': 'to',
	'\\leftarrow': 'leftarrow', '\\leftrightarrow': 'leftrightarrow',
	'\\Rightarrow': 'Rightarrow', '\\Leftarrow': 'Leftarrow',
	'\\Leftrightarrow': 'Leftrightarrow',
};

// Quote normalization - all quote types map to empty (removed)
// Using unicode escapes for special characters to avoid parser issues
export const quoteChars = new Set([
	'"', "'", '`',
	'\u201C', '\u201D',  // " "  left/right double quotation marks
	'\u2018', '\u2019',  // ' '  left/right single quotation marks
	'\u201A', '\u201E',  // ‚ „  low-9 quotation marks
	'\u2039', '\u203A',  // ‹ ›  single angle quotation marks
	'\u00AB', '\u00BB',  // « »  double angle quotation marks
	'\u300C', '\u300D',  // 「 」 CJK corner brackets
	'\u300E', '\u300F',  // 『 』 CJK white corner brackets
	'\u301D', '\u301E', '\u301F',  // 〝 〞 〟 double prime quotation marks
	'\uFF02', '\uFF07',  // ＂ ＇ fullwidth quotation marks
]);

// Expand LaTeX commands in the input text
export function expandLatexCommands(text: string): string {
	let result = text;
	// Sort by length descending to match longer commands first (e.g., \varepsilon before \epsilon)
	const sortedCommands = Object.keys(latexCommandMap).sort((a, b) => b.length - a.length);
	for (const cmd of sortedCommands) {
		// Use regex to match the command followed by a non-letter (or end of string)
		// This prevents matching \alpha inside \alphaXYZ
		const regex = new RegExp(cmd.replace(/\\/g, '\\\\') + '(?![a-zA-Z])', 'g');
		result = result.replace(regex, latexCommandMap[cmd]);
	}
	return result;
}

// Normalize text for search matching:
// - Expand LaTeX commands to ASCII equivalents
// - Expand ligatures
// - Expand Greek letters to ASCII equivalents
// - Remove all quote characters entirely
// - Keep only alphanumeric characters and spaces
export function normalizeForSearch(text: string): string {
	// First expand LaTeX commands
	const expandedText = expandLatexCommands(text);

	let result = '';
	for (const char of expandedText) {
		// Handle ligatures first
		if (ligatureMap[char]) {
			result += ligatureMap[char];
		}
		// Handle Greek letters and math symbols
		else if (greekLetterMap[char]) {
			result += greekLetterMap[char];
		}
		// Remove quote characters entirely (don't convert to space)
		else if (quoteChars.has(char)) {
			// Skip quotes - don't add anything
			continue;
		}
		else if (/[\p{L}\p{N}]/u.test(char)) {
			// Keep letters and numbers (Unicode-aware)
			result += char;
		} else {
			// Replace all other characters (punctuation, symbols, spaces) with space
			result += ' ';
		}
	}
	// Collapse multiple spaces into one
	return result.replace(/\s+/g, ' ').trim();
}

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

export function foldPdfMatchChar(char: string): string {
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

export function normalizePdfTextForMatch(text: string): string {
	return canonicalizeForPdfMatch(text || "")
		.replace(htmlCloseTagRe, "")
		.replaceAll("*", "")
		.replace(multiWhitespaceRe, " ")
		.trim();
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

export function findServerAlignedPdfTextMatch(
	quote: string,
	pageText: string
): PdfTextMatch | null {
	return findServerAlignedMatchInNormalizedPdfText(
		quote,
		normalizePdfTextForMatch(pageText)
	);
}
