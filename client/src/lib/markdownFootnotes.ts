/**
 * Footnotes for the read-only markdown view.
 *
 * Older OCR markdown is the pages' text concatenated, so each page's
 * footnotes — numbered notes, affiliation lines, "*Equal contribution",
 * "Proceedings of …" — sit in the middle of the text, often between the two
 * halves of a paragraph that ran over a column or page. `liftFootnotes` moves
 * them into GFM footnote definitions at the end of the document, links a
 * numbered note to its in-text marker (`text[^n]`, renumbered in document
 * order so repeated per-page numbering can't collide), and re-joins a
 * paragraph the notes had split. It is deliberately conservative: a block it
 * isn't sure about stays where it is.
 *
 * Markdown that already has GFM footnote definitions (newer ingests emit
 * them) is returned untouched.
 *
 * Notes that have no in-text marker (affiliations, venue lines) get a
 * `note-<n>` label; the reader's CSS hides those labels.
 */

const UNLABELED_NOTE_PREFIX = "note-";

const SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹";

// One footnote marker: `\( ^{1} \)`, `$^{1}$`, `${}^1$`, `^{1}`, `¹`, `<sup>1</sup>`.
// Labels are numbers, single letters or note symbols.
const LABEL = String.raw`(\d{1,3}|[a-z]|\*|†|‡|§|¶|\\dagger|\\ddagger|\\ast|\\star)`;
const MARKER_SOURCE = [
    String.raw`\\\(\s*(?:\{\s*\})?\s*\^\s*(?:\{\s*${LABEL}\s*\}|${LABEL})\s*\\\)`,
    String.raw`\$\s*(?:\{\s*\})?\s*\^\s*(?:\{\s*${LABEL}\s*\}|${LABEL})\s*\$`,
    String.raw`\^\{${LABEL}\}`,
    String.raw`<sup>\s*${LABEL}\s*</sup>`,
    `([${SUPERSCRIPT_DIGITS}]{1,3})(?![${SUPERSCRIPT_DIGITS}])`,
].join("|");

interface Marker {
    start: number;
    end: number;
    label: string;
}

function markerLabel(match: RegExpMatchArray): string {
    const raw = match.slice(1).find((group) => group !== undefined) ?? "";
    if (/^[⁰¹²³⁴⁵⁶⁷⁸⁹]+$/.test(raw)) {
        return [...raw].map((char) => SUPERSCRIPT_DIGITS.indexOf(char)).join("");
    }
    return raw.replace(/^0+(?=\d)/, "");
}

function findMarkers(text: string): Marker[] {
    const math = [...text.matchAll(/\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$[^$\n]*\$/g)].map((m) => [m.index, m.index + m[0].length]);
    const markers: Marker[] = [];
    for (const match of text.matchAll(new RegExp(MARKER_SOURCE, "g"))) {
        // A unicode superscript right after a digit is an exponent (10²), not a note.
        if (/^[⁰¹²³⁴⁵⁶⁷⁸⁹]/.test(match[0]) && /[\d⁰¹²³⁴⁵⁶⁷⁸⁹]/.test(text[match.index - 1] ?? "")) continue;
        // `^{17}` inside a formula (`\( 3^{17} \)`) is an exponent.
        if (math.some(([start, end]) => match.index > start && match.index < end)) continue;
        markers.push({ start: match.index, end: match.index + match[0].length, label: markerLabel(match) });
    }
    return markers;
}

function leadingMarker(text: string): Marker | null {
    const match = new RegExp(`^(?:${MARKER_SOURCE})`).exec(text);
    return match ? { start: 0, end: match[0].length, label: markerLabel(match) } : null;
}

type BlockKind = "paragraph" | "heading" | "image" | "table" | "code" | "math" | "list" | "rule" | "other";

interface Block {
    text: string;
    kind: BlockKind;
}

/** Blank-line separated blocks; fenced code and display math stay whole. */
function splitBlocks(markdown: string): Block[] {
    const blocks: Block[] = [];
    let lines: string[] = [];
    let fence: string | null = null;
    let inMath = false;
    const flush = () => {
        if (lines.length) blocks.push({ text: lines.join("\n"), kind: "other" });
        lines = [];
    };
    for (const line of markdown.split("\n")) {
        const trimmed = line.trim();
        if (fence) {
            lines.push(line);
            if (trimmed.startsWith(fence)) fence = null;
            continue;
        }
        if (inMath) {
            lines.push(line);
            if (/(\$\$|\\\])\s*$/.test(trimmed)) inMath = false;
            continue;
        }
        const fenceOpen = /^(`{3,}|~{3,})/.exec(trimmed);
        if (fenceOpen) {
            fence = fenceOpen[1];
            lines.push(line);
            continue;
        }
        if ((trimmed === "$$" || trimmed === "\\[" || (trimmed.startsWith("$$") && !/\$\$.*\$\$/.test(trimmed)))) {
            inMath = true;
            lines.push(line);
            continue;
        }
        if (!trimmed) flush();
        else lines.push(line);
    }
    flush();
    for (const block of blocks) block.kind = blockKind(block.text);
    return blocks;
}

function blockKind(text: string): BlockKind {
    const t = text.trimStart();
    if (/^(`{3,}|~{3,})/.test(t)) return "code";
    if (/^(\$\$|\\\[)/.test(t)) return "math";
    if (/^#{1,6}\s/.test(t)) return "heading";
    if (/^!\[[^\]]*\]\([^)]*\)\s*$/.test(t)) return "image";
    if (/^\|/.test(t)) return "table";
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) return "rule";
    if (/^([-+]|\d{1,3}[.)])\s/.test(t) || /^\*\s/.test(t)) return "list";
    if (/^( {4}|\t|>|<)/.test(text)) return "other";
    return "paragraph";
}

const AFFILIATION_WORDS =
    /\b(Universit|Institut|College|Laborator|Department|Dept\.|School|Cent(?:er|re)|Hospital|Academy|Inc\.|LLC|Ltd|GmbH|Foundation|Research|Labs?\b|Google|DeepMind|Meta\b|FAIR\b|Anthropic|OpenAI|Microsoft|NVIDIA|Apple|Amazon|IBM|Independent|MATS|Program|e-?mail)/i;

/** "¹Univ. of X ²Univ. of Y …": affiliations, not a note with an in-text marker. */
function isAffiliationBlock(text: string, markers: Marker[]): boolean {
    if (markers.length < 2) return false;
    const segments = markers.map((marker, i) => text.slice(marker.end, markers[i + 1]?.start ?? text.length).trim());
    const affiliationLike = segments.filter((s) => s.length < 260 && AFFILIATION_WORDS.test(s)).length;
    return affiliationLike * 2 >= segments.length;
}

const BOILERPLATE =
    /^(?:Proceedings of the\b|Preprint\b|Under review\b|Published (?:as a conference paper|in)\b|Accepted (?:at|to|for|by)\b|\d+(?:st|nd|rd|th) (?:Annual )?(?:Conference|Workshop) on\b|Workshop on\b|Copyright\b|©|Archival Preprint\b|Equal contribution\b|Corresponding authors?\b|Work (?:done|performed) (?:while|during|as)\b|Correspondence(?: to)?\b[^\n]*@)/i;

const SENTENCE_END = /(?:[.!?]|[.!?]["'”’)\]*]+)\s*$/;
const ABBREVIATION_END = /\b(?:al|e\.g|i\.e|etc|vs|cf|Fig|Figs|Eq|Eqs|Sec|Tab|resp|approx|No)\.\s*$/;

// "¹³C NMR", "¹⁸F-FDG": an isotope starting a sentence, not a note marker.
const ISOTOPE =
    /^(?:H|C|N|O|F|P|S|K|U|B|Cl|Br|Na|Ca|Fe|Cu|Zn|Tc|Sr|Cs|Rb|Xe|Li|Mg|Si|Ar|Se|Kr|Co|Ni|Mn|Ga|Tl|Pb|Bi|Po|Ra|Th|Zr|Mo|Ru|Pd|Ag|Cd|Sn|Sb|Te|Ba|Gd|Lu|Ir|Pt|Au|Hg|Rn|Pu|Ge|Al|Cr|Ti)(?=[\s\-–(),]|$)/;

/**
 * A note's text after its leading marker: "strong" when it reads like a
 * note (capitalised, a URL), "weak" when it could still be one (a lowercase
 * fragment) — lifted only if its in-text marker is found.
 */
function noteText(rest: string): "strong" | "weak" | null {
    if (ISOTOPE.test(rest) || rest.trim().length < 8) return null;
    if (/^\s*(?:[A-Z"“‘'(]|https?:|www\.)/.test(rest)) return "strong";
    return /^\s*\p{Ll}/u.test(rest) ? "weak" : null;
}

type Classified =
    | { type: "keep" }
    | { type: "numbered"; label: string; marker: string; body: string; weak: boolean }
    | { type: "unlabeled"; body: string };

function classify(block: Block): Classified[] {
    if (block.kind !== "paragraph") return [{ type: "keep" }];
    const text = block.text.trim();
    const lead = leadingMarker(text);
    if (lead) {
        const markers = findMarkers(text);
        if (isAffiliationBlock(text, markers)) return [{ type: "unlabeled", body: text }];
        // Two notes OCR'd into one block: "¹³ … hazard’.⁴⁴ An amusing …".
        const parts: { label: string; text: string }[] = [];
        let from = 0;
        let label = lead.label;
        for (const marker of markers.slice(1)) {
            if (!/^\d+$/.test(label) || marker.label !== String(Number(label) + 1)) continue;
            if (!SENTENCE_END.test(text.slice(0, marker.start)) || !/^\s*[A-Z]/.test(text.slice(marker.end))) continue;
            parts.push({ label, text: text.slice(from, marker.start) });
            from = marker.start;
            label = marker.label;
        }
        parts.push({ label, text: text.slice(from) });

        return parts.map((part) => {
            const own = leadingMarker(part.text)!;
            const rest = part.text.slice(own.end);
            const kind = noteText(rest);
            if (/^\d+$/.test(part.label) && kind) {
                const marker = part.text.slice(0, own.end);
                return { type: "numbered", label: part.label, marker, body: rest.trim(), weak: kind === "weak" };
            }
            return kind === "strong" ? { type: "unlabeled", body: part.text.trim() } : { type: "keep" };
        }) as Classified[];
    }
    // "*Equal contribution." / "†Work done at …" — a lone leading symbol, not emphasis.
    const symbolNote =
        (/^\*(?=[A-Z])/.test(text) && text.split("*").length === 2) || /^[†‡§¶]\s*(?=[A-Z])/.test(text);
    if (symbolNote && text.length < 600) return [{ type: "unlabeled", body: text }];
    if (BOILERPLATE.test(text.replace(/^[*_]+/, "")) && text.length < 400) {
        return [{ type: "unlabeled", body: text }];
    }
    return [{ type: "keep" }];
}

interface Note {
    // The in-text marker's number (numbered notes) — null for unlabeled ones.
    label: string | null;
    marker: string;
    weak: boolean;
    body: string;
    // Block index where the note sat; its in-text marker is searched around it.
    at: number;
}

const flat = (text: string) => text.replace(/\s*\n\s*/g, " ").trim();

export function liftFootnotes(markdown: string): string {
    if (!markdown || /^\[\^[^\]\s]+\]:/m.test(markdown)) return markdown;

    const blocks = splitBlocks(markdown);
    const classified = blocks.map(classify);

    // Front matter (title, authors, affiliations) ends at the first real prose
    // paragraph after the title; notes above it are where they belong.
    const title = blocks.findIndex((block) => block.kind === "heading");
    const frontEnd = blocks.findIndex(
        (block, i) =>
            i > title &&
            block.kind === "paragraph" &&
            classified[i].every((c) => c.type === "keep") &&
            block.text.length >= 300 &&
            findMarkers(block.text).length <= 2
    );
    if (frontEnd < 0) return markdown;

    // Notes-like blocks right under a heading ("## Addresses") are that section.
    const isCandidate = (i: number) => classified[i].every((part) => part.type !== "keep");
    const underHeading = new Set<number>();
    blocks.forEach((block, i) => {
        if (block.kind !== "heading") return;
        for (let j = i + 1; j < blocks.length && isCandidate(j); j++) underHeading.add(j);
    });

    const lifted = new Set<number>();
    const notes: Note[] = [];
    classified.forEach((parts, i) => {
        if (i < frontEnd || !isCandidate(i) || underHeading.has(i)) return;
        lifted.add(i);
        for (const part of parts) {
            if (part.type === "numbered") notes.push({ ...part, at: i });
            else if (part.type === "unlabeled") notes.push({ label: null, marker: "", body: part.body, weak: false, at: i });
        }
    });

    // In-text markers a numbered note can attach to: in body text, not at a
    // block's start, not one of a run of citation superscripts ("⁹,¹⁰").
    type Ref = Marker & { block: number; used: boolean; note?: number };
    const refs: Ref[] = [];
    blocks.forEach((block, i) => {
        if (i < frontEnd || lifted.has(i) || block.kind === "code" || block.kind === "math" || block.kind === "heading") return;
        for (const marker of findMarkers(block.text)) {
            if (!/^\d+$/.test(marker.label) || marker.start === 0) continue;
            const around = block.text.slice(Math.max(0, marker.start - 1), marker.end + 1);
            if (/^[,–-]|[,–-]$/.test(around)) continue;
            refs.push({ ...marker, block: i, used: false });
        }
    });

    const BACK = 25;
    const AHEAD = 8;
    notes.forEach((note, n) => {
        if (note.label === null) return;
        const candidates = refs.filter((ref) => !ref.used && ref.label === note.label);
        const before = candidates.filter((ref) => ref.block < note.at && ref.block >= note.at - BACK).pop();
        const after = candidates.find((ref) => ref.block > note.at && ref.block <= note.at + AHEAD);
        const ref = before ?? after;
        if (ref) {
            ref.used = true;
            ref.note = n;
        }
    });

    // A block with a weak note stays in place unless that note found its marker.
    const linkedNotes = new Set(refs.flatMap((ref) => (ref.note === undefined ? [] : [ref.note])));
    for (const i of new Set(notes.filter((note, n) => note.weak && !linkedNotes.has(n)).map((note) => note.at))) {
        lifted.delete(i);
        for (const ref of refs) if (ref.note !== undefined && notes[ref.note].at === i) ref.note = undefined;
    }
    const keptNotes = notes.filter((note) => lifted.has(note.at));
    if (!keptNotes.length) return markdown;

    // Numbered notes without a marker keep theirs in the note's text.
    let counter = 0;
    const labels = notes.map((note, n) => {
        if (!lifted.has(note.at)) return null;
        const linked = refs.some((ref) => ref.note === n);
        if (!linked) note.body = `${note.marker}${note.body}`;
        return linked ? String(++counter) : null;
    });
    let unlabeled = 0;
    const seen = new Set<string>();
    const definitions: string[] = [];
    notes.forEach((note, n) => {
        if (!lifted.has(note.at)) return;
        const body = flat(note.body);
        if (labels[n] === null) {
            // Running headers ("Published as a conference paper at …") repeat per page.
            if (seen.has(body)) return;
            seen.add(body);
        }
        definitions.push(`[^${labels[n] ?? `${UNLABELED_NOTE_PREFIX}${++unlabeled}`}]: ${body}`);
    });

    const texts = blocks.map((block) => block.text);
    const refsByBlock = new Map<number, Ref[]>();
    for (const ref of refs) {
        if (ref.note === undefined) continue;
        refsByBlock.set(ref.block, [...(refsByBlock.get(ref.block) ?? []), ref]);
    }
    for (const [i, blockRefs] of refsByBlock) {
        let text = texts[i];
        for (const ref of blockRefs.sort((a, b) => b.start - a.start)) {
            const head = text.slice(0, ref.start).replace(/[ \t]+$/, "");
            text = `${head}[^${labels[ref.note!]}]${text.slice(ref.end)}`;
        }
        texts[i] = text;
    }

    const body = joinSplitParagraphs(blocks, texts, lifted);
    return `${body}\n\n${definitions.join("\n\n")}\n`;
}

const isPageNumber = (text: string) => /^\d{1,4}$/.test(text.trim());
const isCaption = (text: string) =>
    /^(?:\*\*|\*)?(?:Figure|Fig\.|Table|Algorithm|Listing|Transcript)\s*[A-Z]?\d+/.test(text.trim());

/**
 * Drops the lifted blocks, and where they cut a paragraph mid-sentence — the
 * text before doesn't end a sentence and the text after starts in lowercase
 * — joins the halves. Figures between the halves move below the joined
 * paragraph; page numbers and running headers in that gap go. Only a gap
 * with a lifted note joins: the markdown API already joined the paragraphs
 * that page furniture and figures alone had split.
 */
function joinSplitParagraphs(blocks: Block[], texts: string[], lifted: Set<number>): string {
    const counts = new Map<string, number>();
    for (const text of texts) counts.set(text.trim(), (counts.get(text.trim()) ?? 0) + 1);
    const isRunningHeader = (i: number) => {
        const text = texts[i].trim();
        return blocks[i].kind === "paragraph" && text.length <= 150 && !text.includes("\n") && (counts.get(text) ?? 0) >= 3;
    };
    // A rule right before notes is the footnote separator line.
    const dropped = new Set(lifted);
    blocks.forEach((block, i) => {
        if (block.kind === "rule" && lifted.has(i + 1)) dropped.add(i);
    });

    const out: string[] = [];
    let i = 0;
    while (i < blocks.length) {
        if (dropped.has(i)) {
            i++;
            continue;
        }
        let text = texts[i];
        i++;
        // Only prose continues: not a leftover note, a code fragment or a label.
        if (blocks[i - 1].kind !== "paragraph" || text.length < 40 || leadingMarker(text) || !/^[\p{L}*_("“'\\$[]/u.test(text)) {
            out.push(text);
            continue;
        }
        const moved: string[] = [];
        for (;;) {
            if (SENTENCE_END.test(text) && !ABBREVIATION_END.test(text)) break;
            // Scan the gap after this paragraph for its continuation.
            let j = i;
            let broken = false;
            const figures: string[] = [];
            for (; j < blocks.length; j++) {
                if (dropped.has(j)) {
                    broken ||= lifted.has(j);
                } else if (isPageNumber(texts[j])) {
                    // Skipped, but not a reason to join: the server
                    // (`app/ingest/paragraphs.py`) already joined every
                    // paragraph a page break alone had cut.
                } else if (isRunningHeader(j)) {
                    // skipped
                } else if (blocks[j].kind === "image" || (blocks[j].kind === "paragraph" && isCaption(texts[j]))) {
                    figures.push(texts[j]);
                } else break;
            }
            const next = texts[j]?.trimStart() ?? "";
            if (!broken || j >= blocks.length || blocks[j].kind !== "paragraph" || !/^\p{Ll}/u.test(next)) break;
            // A caption cut off in the gap: the lowercase text may be its rest.
            const caption = figures.findLast((figure) => isCaption(figure));
            if (caption && !SENTENCE_END.test(caption)) break;
            const glue = /\p{L}-$/u.test(text) ? "" : " ";
            text = `${text.trimEnd()}${glue}${next}`;
            moved.push(...figures);
            i = j + 1;
        }
        out.push(text, ...moved);
    }
    return out.join("\n\n");
}

const lastReference = new WeakMap<Element, Map<string, Element>>();

// A jump, not a smooth scroll: notes sit at the end, often pages away. The
// target flashes so the eye lands on it.
function reveal(element: Element) {
    element.scrollIntoView({ block: "center" });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // Web Animations, not a class: ProseMirror owns these nodes' attributes.
    element.animate(
        [{ backgroundColor: "color-mix(in oklch, var(--brand) 22%, transparent)" }, { backgroundColor: "transparent" }],
        { duration: 1400, easing: "ease-out", delay: 150 }
    );
}

/**
 * A tap on a footnote reference in the rendered markdown scrolls to its note;
 * a tap on the note's number scrolls back to where it was read. Returns
 * whether the event was handled.
 */
export function followFootnote(target: EventTarget | null): boolean {
    if (!(target instanceof Element)) return false;
    const root = target.closest(".ProseMirror");
    if (!root) return false;

    const reference = target.closest('sup[data-type="footnote_reference"]');
    if (reference) {
        const label = reference.getAttribute("data-label") ?? "";
        const note = root.querySelector(`dl[data-type="footnote_definition"][data-label="${CSS.escape(label)}"]`);
        if (!note) return false;
        const seen = lastReference.get(root) ?? new Map<string, Element>();
        seen.set(label, reference);
        lastReference.set(root, seen);
        reveal(note);
        return true;
    }

    const number = target.closest('dl[data-type="footnote_definition"] > dt');
    if (number) {
        const label = number.parentElement?.getAttribute("data-label") ?? "";
        const remembered = lastReference.get(root)?.get(label);
        const back =
            (remembered?.isConnected ? remembered : null) ??
            root.querySelector(`sup[data-type="footnote_reference"][data-label="${CSS.escape(label)}"]`);
        if (!back) return false;
        reveal(back);
        return true;
    }
    return false;
}
