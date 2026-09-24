/**
 * Structure-preserving clipping of JSON values for display.
 *
 * Mirrors `server/app/llm/chat/tool_preview.py` — keep the two in sync. Long
 * strings are cut with an inline `…[+N chars]` tail, long arrays/objects keep
 * their first entries plus a `…[+N more items]` / `"…": "[+N more keys]"`
 * marker, and nesting past the depth limit collapses to `[…N items]` /
 * `{…N keys}`. The result is always a JSON value, so it pretty-prints and
 * highlights exactly like the original would — no dangling quotes or braces
 * from cutting the encoded text.
 */

export interface ClipOptions {
    /** Longest string kept whole. */
    maxStringChars: number;
    /** Entries kept per array / object. */
    maxItems: number;
    /** Nesting depth past which collections collapse to a summary string. */
    maxDepth: number;
}

/** Defaults for a tool row's expandable details. */
export const DISPLAY_CLIP: ClipOptions = {
    maxStringChars: 400,
    maxItems: 30,
    maxDepth: 8,
};

/** Key holding the "more keys" marker inside a clipped object. */
export const MORE_KEY = "…";

export interface ClippedJson {
    value: unknown;
    /** Whether anything was actually dropped. */
    clipped: boolean;
}

const count = (n: number): string => n.toLocaleString("en-US");

export function clipString(text: string, cap: number): string {
    if (text.length <= cap) return text;
    return `${text.slice(0, cap)}…[+${count(text.length - cap)} chars]`;
}

/** A trimmed copy of `value` under `options`; the input is never mutated. */
export function clipJson(
    value: unknown,
    options: ClipOptions = DISPLAY_CLIP
): ClippedJson {
    let clipped = false;
    const markClipped = () => {
        clipped = true;
    };
    const result = clipNode(value, options, 0, markClipped);
    return { value: result, clipped };
}

function clipNode(
    value: unknown,
    options: ClipOptions,
    depth: number,
    markClipped: () => void
): unknown {
    if (typeof value === "string") {
        const cut = clipString(value, options.maxStringChars);
        if (cut !== value) markClipped();
        return cut;
    }
    if (value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) {
        if (depth >= options.maxDepth) {
            markClipped();
            return `[…${count(value.length)} items]`;
        }
        const head = value
            .slice(0, options.maxItems)
            .map((item) => clipNode(item, options, depth + 1, markClipped));
        if (value.length > options.maxItems) {
            markClipped();
            head.push(`…[+${count(value.length - options.maxItems)} more items]`);
        }
        return head;
    }
    const entries = Object.entries(value as Record<string, unknown>);
    if (depth >= options.maxDepth) {
        markClipped();
        return `{…${count(entries.length)} keys}`;
    }
    const out: Record<string, unknown> = {};
    for (const [key, item] of entries.slice(0, options.maxItems)) {
        out[key] = clipNode(item, options, depth + 1, markClipped);
    }
    if (entries.length > options.maxItems) {
        markClipped();
        out[MORE_KEY] = `[+${count(entries.length - options.maxItems)} more keys]`;
    }
    return out;
}

/** Pretty-printed JSON; falls back to `String()` for values JSON can't encode. */
export function formatJson(value: unknown): string {
    try {
        return JSON.stringify(value, null, 2) ?? String(value);
    } catch {
        return String(value);
    }
}
