import type { UIMessage, UIMessagePart, UIDataTypes, UITools } from "ai";

import { Citation } from "@/lib/schema";

/**
 * Helpers over the Vercel AI UIMessage shape — the single message format for
 * both live streaming (useChat) and history loaded from the server. The
 * assistant's citations arrive as a `data-citations` part; tool activity as
 * `tool-*` parts; reasoning as `reasoning` parts.
 */

/**
 * Metadata the server persists on an assistant row whose turn failed, so a
 * reloaded conversation still shows what went wrong. `errorText` is camelCase
 * on the wire (it travels through the UIMessage `metadata` field as-is).
 */
export interface ChatMessageMetadata {
    interrupted?: boolean;
    errorText?: string;
}

export type ChatUIMessage = UIMessage<ChatMessageMetadata>;
export type ChatUIMessagePart = UIMessagePart<UIDataTypes, UITools>;

interface CitationsData {
    citations?: Array<{
        key: string | number;
        reference: string;
        page?: number;
        paper_id?: string;
        // Code citations carry `file` instead of `page`.
        file?: string;
        start_line?: number | null;
        end_line?: number | null;
        github_url?: string | null;
        verified?: boolean;
    }>;
}

/** Citations from the message's `data-citations` part, keys normalized to strings. */
export function citationsFromMessage(message: ChatUIMessage): Citation[] {
    for (const part of message.parts) {
        if (part.type === "data-citations") {
            const data = (part as { data?: CitationsData }).data;
            return (data?.citations ?? []).map((c) => ({
                ...c,
                key: String(c.key),
            }));
        }
    }
    return [];
}

/**
 * A citation pointing at the paper's code repo rather than its PDF. These must
 * never reach the PDF scroll-to-highlight path — there's nothing to search for
 * in the document.
 */
export function isCodeCitation(citation: Citation): boolean {
    // Presence, not truthiness: ANY citation carrying `file` is a code
    // citation and must stay off the PDF path, even with an empty path.
    return typeof citation.file === "string";
}

/** `path/to/file.py:12-34`, degrading as line info is missing. */
export function codeCitationLabel(citation: Citation): string {
    const file = citation.file ?? "";
    if (!citation.start_line) return file;
    if (!citation.end_line || citation.end_line === citation.start_line) {
        return `${file}:${citation.start_line}`;
    }
    return `${file}:${citation.start_line}-${citation.end_line}`;
}

/** The 40-hex commit a GitHub blob permalink pins, or null. */
export function commitShaFromGithubUrl(
    url: string | null | undefined
): string | null {
    if (!url) return null;
    const match = /\/blob\/([0-9a-f]{40})\//.exec(url);
    return match ? match[1] : null;
}

/** All text content of a message joined (for copy actions / user rows). */
export function textFromMessage(message: ChatUIMessage): string {
    return message.parts
        .filter((p): p is Extract<ChatUIMessagePart, { type: "text" }> => p.type === "text")
        .map((p) => p.text)
        .join("\n\n")
        .trim();
}

export interface ToolPartView {
    type: string;
    toolCallId: string;
    state: string;
    input?: unknown;
    output?: unknown;
    errorText?: string;
}

export function isToolPart(part: ChatUIMessagePart): boolean {
    return part.type.startsWith("tool-");
}

const asRecord = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/** Human label for a tool invocation, mirroring the old server-side status strings. */
export function toolLabel(part: ToolPartView): string {
    const name = part.type.replace(/^tool-/, "");
    const input = asRecord(part.input);
    switch (name) {
        case "read_section":
            return `Reading section "${input.name ?? "…"}"`;
        case "read_pages":
            return `Reading pages ${input.start ?? "?"}–${input.end ?? "?"}`;
        case "search_paper":
            return `Searching for "${input.query ?? "…"}"`;
        case "get_figure":
            return `Fetching ${input.label ?? "figure"}`;
        case "list_docs":
            return "Listing your docs";
        case "read_doc":
            return input.name ? `Reading doc "${input.name}"` : "Reading a doc";
        case "write_doc":
            return input.name ? `Updating doc "${input.name}"` : "Updating a doc";
        case "run_python":
            return "Inspecting the repository";
        // Quick-question repo lookups (the popover's read-only tools).
        case "tree": {
            const path = typeof input.path === "string" ? input.path : "";
            return path && path !== "/repo"
                ? `Listing ${path}`
                : "Listing repo files";
        }
        case "read_file": {
            const path = typeof input.path === "string" ? input.path : "";
            return path ? `Reading ${path}` : "Reading a repo file";
        }
        case "grep_repo":
            return `Searching repo for "${input.pattern ?? "…"}"`;
        default:
            return `Calling ${name}`;
    }
}

/** Repo files a sandbox tool call reports having touched — at most this many. */
const MAX_TOOL_FILES = 20;

/**
 * Repo paths from a `run_python` result, for the tool row's file chips.
 *
 * The tool returns `{"files": [...], "output": "..."}`; past the wire cap the
 * server replaces it with a `{"truncated": true, "preview": "..."}` marker
 * that still carries `files`, so the chips render either way.
 */
export function toolOutputFiles(part: ToolPartView): string[] {
    const output = asRecord(part.output);
    const files = output.files;
    if (!Array.isArray(files)) return [];
    const unique: string[] = [];
    for (const entry of files) {
        if (typeof entry !== "string") continue;
        const path = entry.trim();
        if (!path || unique.includes(path)) continue;
        unique.push(path);
        if (unique.length >= MAX_TOOL_FILES) break;
    }
    return unique;
}

/** What a tool row shows for one payload: a JSON value or plain text. */
export type ToolPayload =
    | { kind: "json"; value: unknown }
    | { kind: "text"; text: string };

export interface ToolOutputView {
    payload: ToolPayload;
    /** Footer note when the server sent a wire-cap preview instead of the output. */
    note: string | null;
}

/**
 * Decode a tool part's output for display.
 *
 * Past the wire cap the server ships `{truncated: true, preview}` — `preview`
 * is either the output text (`run_python` stdout, bare strings; with
 * `omitted_chars`) or the output trimmed as a JSON value with inline
 * `…[+N …]` markers (see `jsonPreview`). Below the cap the output arrives
 * whole; `run_python`'s stdout is still shown as text rather than as an
 * escaped JSON string, matching how its preview reads.
 */
export function toolOutputView(part: ToolPartView): ToolOutputView | null {
    const output = part.output;
    if (output === undefined || output === null) return null;
    const record = asRecord(output);
    if (record.truncated === true && "preview" in record) {
        const preview = record.preview;
        if (typeof preview === "string") {
            const omitted =
                typeof record.omitted_chars === "number" ? record.omitted_chars : 0;
            return {
                payload: { kind: "text", text: preview },
                note:
                    omitted > 0
                        ? `Output truncated · ${omitted.toLocaleString("en-US")} more characters not shown`
                        : "Output truncated",
            };
        }
        return {
            payload: { kind: "json", value: preview },
            note: "Output trimmed for display",
        };
    }
    if (typeof output === "string") {
        return { payload: { kind: "text", text: output }, note: null };
    }
    if (typeof record.output === "string" && Array.isArray(record.files)) {
        return { payload: { kind: "text", text: record.output }, note: null };
    }
    return { payload: { kind: "json", value: output }, note: null };
}

export function toolIsPending(part: ToolPartView): boolean {
    return part.state === "input-streaming" || part.state === "input-available";
}

/**
 * The label to show in the "working…" indicator: the most recent tool part
 * still awaiting output, if any.
 */
export function pendingToolLabel(message: ChatUIMessage | undefined): string | null {
    if (!message) return null;
    for (let i = message.parts.length - 1; i >= 0; i--) {
        const part = message.parts[i];
        if (isToolPart(part)) {
            const view = part as unknown as ToolPartView;
            if (toolIsPending(view)) return toolLabel(view);
        }
    }
    return null;
}

/**
 * Group a message's parts for rendering: adjacent reasoning parts merge into
 * one block; text and tool parts render in order; data/step parts are
 * handled separately by the caller.
 */
export type RenderBlock =
    | { kind: "reasoning"; text: string; streaming: boolean }
    | { kind: "text"; text: string }
    | { kind: "tool"; part: ToolPartView };

export function renderBlocks(message: ChatUIMessage): RenderBlock[] {
    const blocks: RenderBlock[] = [];
    for (const part of message.parts) {
        if (part.type === "reasoning") {
            const text = part.text;
            const streaming = part.state === "streaming";
            // Providers sometimes return an empty reasoning summary; a
            // "Thought for…" disclosure that expands to nothing is noise.
            if (!streaming && !text.trim()) continue;
            const last = blocks[blocks.length - 1];
            if (last && last.kind === "reasoning") {
                blocks[blocks.length - 1] = {
                    kind: "reasoning",
                    text: `${last.text}\n\n${text}`,
                    streaming,
                };
            } else {
                blocks.push({ kind: "reasoning", text, streaming });
            }
        } else if (part.type === "text") {
            if (part.text) blocks.push({ kind: "text", text: part.text });
        } else if (isToolPart(part)) {
            blocks.push({ kind: "tool", part: part as unknown as ToolPartView });
        }
        // step-start, data-citations, files: not rendered inline
    }
    return blocks;
}

// ---------------------------------------------------------------------------
// Failed turns
// ---------------------------------------------------------------------------

/** Copy of last resort — used only when a failure carries no text at all. */
const GENERIC_CHAT_ERROR = "Something went wrong while answering.";

/** Longest headline shown inline; the rest lives in the details disclosure. */
const HEADLINE_MAX_CHARS = 240;

export interface NormalizedChatError {
    /** One readable line, safe to render inline. Never empty. */
    headline: string;
    /** The complete original text, for the "details" disclosure. May be empty. */
    detail: string;
}

/** The message string carried by whatever the failure was thrown/serialized as. */
function rawErrorText(error: unknown): string {
    if (typeof error === "string") return error.trim();
    if (error instanceof Error) return error.message.trim();
    if (error && typeof error === "object") {
        const message = (error as { message?: unknown }).message;
        if (typeof message === "string") return message.trim();
    }
    return "";
}

/**
 * The human-facing string buried in an error body: `{"detail": "..."}`,
 * `{"error": {"message": "..."}}`, FastAPI's `{"detail": [{"msg": "..."}]}`.
 */
function messageFromBody(value: unknown, depth = 0): string | null {
    if (typeof value === "string") return value.trim() || null;
    if (depth > 3 || !value || typeof value !== "object") return null;
    if (Array.isArray(value)) {
        for (const entry of value) {
            const found = messageFromBody(entry, depth + 1);
            if (found) return found;
        }
        return null;
    }
    const record = value as Record<string, unknown>;
    for (const key of ["detail", "message", "error", "error_message", "msg"]) {
        if (!(key in record)) continue;
        const found = messageFromBody(record[key], depth + 1);
        if (found) return found;
    }
    return null;
}

/** A JSON error body reduced to its message; anything else passes through. */
function unwrapErrorBody(raw: string): string {
    const trimmed = raw.trim();
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return trimmed;
    let parsed: unknown;
    try {
        parsed = JSON.parse(trimmed);
    } catch {
        return trimmed;
    }
    return messageFromBody(parsed) ?? trimmed;
}

/**
 * Split a chat failure into an inline headline and the full text.
 *
 * The server hands back the provider's own error verbatim (mid-stream) or a
 * raw JSON body (pre-stream), either of which can be hundreds of lines. The
 * headline is the first meaningful line, capped; nothing is thrown away —
 * `detail` keeps the original for the disclosure.
 */
export function normalizeChatError(error: unknown): NormalizedChatError {
    const raw = rawErrorText(error);
    if (!raw) return { headline: GENERIC_CHAT_ERROR, detail: "" };

    const firstLine =
        unwrapErrorBody(raw)
            .split(/\r?\n/)
            .map((line) => line.trim())
            .find(Boolean) ?? "";
    const collapsed = firstLine.replace(/\s+/g, " ");
    if (!collapsed) return { headline: GENERIC_CHAT_ERROR, detail: raw };

    const headline =
        collapsed.length > HEADLINE_MAX_CHARS
            ? `${collapsed.slice(0, HEADLINE_MAX_CHARS - 1).trimEnd()}…`
            : collapsed;
    return { headline, detail: raw };
}

/**
 * Failure metadata off a reloaded assistant message, validated — the server's
 * `metadata` is untyped JSON on the wire. Null when the turn was fine.
 */
export function interruptedMetadata(
    message: ChatUIMessage
): ChatMessageMetadata | null {
    const meta = message.metadata;
    if (!meta || typeof meta !== "object") return null;
    const record = meta as Record<string, unknown>;
    if (record.interrupted !== true) return null;
    const errorText =
        typeof record.errorText === "string" && record.errorText.trim()
            ? record.errorText
            : undefined;
    return { interrupted: true, ...(errorText ? { errorText } : {}) };
}

/**
 * True when a streamed assistant message ended mid-flight: a text/reasoning
 * part never got its `-end` chunk, a tool call never got output, or nothing
 * renderable arrived at all.
 *
 * This is the only signal for a stream that died on a clean EOF — the SDK
 * reports no error and leaves the status at "ready" with a half-written turn.
 */
export function isTruncatedAssistantMessage(message: ChatUIMessage): boolean {
    if (message.role !== "assistant") return false;
    let renderable = 0;
    for (const part of message.parts) {
        if (part.type === "text" || part.type === "reasoning") {
            if (part.state === "streaming") return true;
            if (part.text.trim()) renderable += 1;
        } else if (isToolPart(part)) {
            if (toolIsPending(part as unknown as ToolPartView)) return true;
            renderable += 1;
        }
    }
    return renderable === 0;
}

/**
 * What a retry needs to re-send the trailing user turn EXACTLY as it was:
 * its text, the references that were attached to it, and how many messages
 * to keep (everything before it) so the failed turn is dropped first.
 *
 * The references matter — they are the evidence block of the original
 * prompt. Re-sending without them would silently ask a different question.
 */
export interface RetryTarget {
    text: string;
    references: string[];
    keep: number;
}

export function retryTargetFromMessages(
    messages: ChatUIMessage[]
): RetryTarget | null {
    for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i];
        if (message.role === "user") {
            const text = textFromMessage(message);
            if (!text) return null;
            const references = citationsFromMessage(message)
                .map((citation) => citation.reference)
                .filter((reference) => typeof reference === "string" && reference);
            return { text, references, keep: i };
        }
        // Only a failed assistant turn may sit between here and the user's
        // question; anything else means there is nothing to re-send.
        if (message.role !== "assistant") return null;
    }
    return null;
}

/**
 * Transient `data-retry-status` part: the server retrying a failed provider
 * call in-place. Never persisted — it only ever reaches `onData`.
 */
export interface RetryStatus {
    state: "retrying" | "recovered";
    attempt?: number;
    maxAttempts?: number;
    delayMs?: number;
    error?: string;
}

const positiveInt = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isFinite(value) && value > 0
        ? Math.floor(value)
        : undefined;

/** Validated read of a `data-retry-status` payload; null when unusable. */
export function parseRetryStatus(data: unknown): RetryStatus | null {
    if (!data || typeof data !== "object") return null;
    const record = data as Record<string, unknown>;
    if (record.state !== "retrying" && record.state !== "recovered") return null;
    const error =
        typeof record.error === "string" && record.error.trim()
            ? record.error.trim()
            : undefined;
    return {
        state: record.state,
        attempt: positiveInt(record.attempt),
        maxAttempts: positiveInt(record.maxAttempts),
        delayMs: positiveInt(record.delayMs),
        ...(error ? { error } : {}),
    };
}
