/**
 * Finding a PDF highlight's text in the rendered markdown (the Text view).
 *
 * The markdown is OCR output, so the stored PDF text rarely matches it
 * character for character: line-break hyphenation, ligatures, spacing,
 * `70%` against the math `\(70\%\)`, `11 × 11` against `11 \times 11`.
 * Both sides are reduced to lowercase letters and digits (LaTeX command
 * names dropped, Greek commands turned into letters) and matched exactly,
 * then by windows of the needle when that fails.
 */

const GREEK: Record<string, string> = {
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε",
    zeta: "ζ", eta: "η", theta: "θ", vartheta: "θ", iota: "ι", kappa: "κ",
    lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ",
    tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
    Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

/** The letters and digits of `text`, lowercased, ligatures and accents decomposed. */
export function canonicalize(text: string): string {
    return text
        .normalize("NFKD")
        .replace(/[^\p{L}\p{N}]/gu, "")
        .toLowerCase()
        .replace(/ς/g, "σ");
}

/** A LaTeX formula as the plain text a PDF would carry for it, canonicalized. */
export function canonicalizeLatex(latex: string): string {
    return canonicalize(
        latex.replace(/\\([a-zA-Z]+)/g, (_, name: string) => GREEK[name] ?? " ")
    );
}

const WINDOW = 32;
const MIN_WINDOW = 16;

function occurrences(haystack: string, needle: string): number[] {
    const found: number[] = [];
    for (let i = haystack.indexOf(needle); i !== -1; i = haystack.indexOf(needle, i + 1)) {
        found.push(i);
    }
    return found;
}

/** The occurrence nearest the expected position (a fraction of the document). */
function nearest(candidates: number[], expected: number | null): number {
    if (expected === null || candidates.length === 1) return candidates[0];
    return candidates.reduce((best, c) =>
        Math.abs(c - expected) < Math.abs(best - expected) ? c : best
    );
}

/**
 * The span `[start, end)` of `haystack` (canonical) that best matches the
 * canonical `needle`, or null. `expectedFraction` (where in the document the
 * passage should be, e.g. from its page) breaks ties between repeats.
 */
export function findPassage(
    haystack: string,
    needle: string,
    expectedFraction: number | null = null
): { start: number; end: number; exact: boolean } | null {
    if (needle.length < 4 || !haystack) return null;
    const expected = expectedFraction === null ? null : expectedFraction * haystack.length;

    const exact = occurrences(haystack, needle);
    if (exact.length) {
        const start = nearest(exact, expected);
        return { start, end: start + needle.length, exact: true };
    }
    // Too short to match partially without landing on a stray repeat.
    if (needle.length < MIN_WINDOW * 2) return null;

    const size = Math.min(WINDOW, Math.floor(needle.length / 2));
    const clamp = (start: number, end: number) => ({
        start: Math.max(0, start),
        end: Math.min(haystack.length, end),
        exact: false,
    });

    // Both ends found, in order and about the right distance apart.
    const heads = occurrences(haystack, needle.slice(0, size));
    const tails = occurrences(haystack, needle.slice(-size));
    for (const head of heads) {
        const tail = tails.find((t) => t > head);
        if (tail === undefined) continue;
        const length = tail + size - head;
        if (length > needle.length * 0.6 && length < needle.length * 1.6) return clamp(head, tail + size);
    }

    // Otherwise the first window of the needle that is found anchors it.
    for (let offset = 0; offset + size <= needle.length; offset += size) {
        const hits = occurrences(haystack, needle.slice(offset, offset + size));
        if (!hits.length) continue;
        const hit = nearest(hits, expected === null ? null : expected + offset);
        return clamp(hit - offset, hit - offset + needle.length);
    }
    const lastHits = occurrences(haystack, needle.slice(-size));
    if (lastHits.length) {
        const hit = nearest(lastHits, expected === null ? null : expected + needle.length - size);
        return clamp(hit + size - needle.length, hit + size);
    }
    return null;
}

// ---- Rendered DOM ----------------------------------------------------------

type Location = { node: Node; offset: number; length: number } | { element: Element };

/** Canonical text of the editor's DOM, with where each character came from. */
interface TextIndex {
    text: string;
    locations: Location[];
}

const MATH = '[data-type="math_inline"]';
/** Rendered formulas (read from their LaTeX instead) and editor chrome. */
const SKIP =
    ".katex, .cm-gutters, .tools, .preview-label, button, input, textarea, .ProseMirror-widget, " +
    ".milkdown-code-block:has(.katex) .codemirror-host";

function indexDom(root: Element): TextIndex {
    const chars: string[] = [];
    const locations: Location[] = [];
    const visit = (parent: Node) => {
        for (let node = parent.firstChild; node; node = node.nextSibling) {
            if (node.nodeType === Node.TEXT_NODE) {
                const value = node.nodeValue ?? "";
                let offset = 0;
                for (const codePoint of value) {
                    for (const c of canonicalize(codePoint)) {
                        chars.push(c);
                        locations.push({ node, offset, length: codePoint.length });
                    }
                    offset += codePoint.length;
                }
            } else if (node.nodeType === Node.ELEMENT_NODE) {
                const element = node as Element;
                if (element.matches(MATH)) {
                    for (const c of canonicalizeLatex(element.getAttribute("data-value") ?? "")) {
                        chars.push(c);
                        locations.push({ element });
                    }
                } else if (!element.matches(SKIP)) {
                    visit(element);
                }
            }
        }
    };
    visit(root);
    return { text: chars.join(""), locations };
}

/**
 * A DOM Range over `needleText` in the rendered markdown under `root`, or
 * null when it can't be found (e.g. it's in a figure or a table image).
 */
export function findPassageRange(
    root: Element,
    needleText: string,
    expectedFraction: number | null
): Range | null {
    const index = indexDom(root);
    const span = findPassage(index.text, canonicalize(needleText), expectedFraction);
    if (!span || span.end <= span.start) return null;
    const first = index.locations[span.start];
    const last = index.locations[span.end - 1];
    const range = document.createRange();
    if ("element" in first) range.setStartBefore(first.element);
    else range.setStart(first.node, first.offset);
    if ("element" in last) range.setEndAfter(last.element);
    else range.setEnd(last.node, last.offset + last.length);
    return range;
}
