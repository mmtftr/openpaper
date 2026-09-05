/**
 * Pending chat references — the snippets a user attaches before sending a
 * message. They travel as plain strings in the request's `user_references`
 * field, the same channel PDF text selections use.
 */

/** Server-side ceiling on how many references one message may carry. */
export const MAX_USER_REFERENCES = 20;
/** Server-side ceiling on a single reference. */
export const MAX_REFERENCE_CHARS = 5000;

const TRUNCATION_SUFFIX = "\n[truncated]";

/** Clamp a reference to the wire limit, marking that it was cut. */
export function truncateReference(text: string): string {
    if (text.length <= MAX_REFERENCE_CHARS) return text;
    return (
        text.slice(0, MAX_REFERENCE_CHARS - TRUNCATION_SUFFIX.length) +
        TRUNCATION_SUFFIX
    );
}

/**
 * A code selection as a reference the model can read: the file and line range
 * it came from, then the code itself.
 */
export function formatCodeReference(
    path: string,
    startLine: number,
    endLine: number,
    code: string
): string {
    const range =
        startLine === endLine ? `line ${startLine}` : `lines ${startLine}-${endLine}`;
    return `${path} ${range}:\n${code}`;
}
