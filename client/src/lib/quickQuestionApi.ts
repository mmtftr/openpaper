import { readUIMessageStream, type UIMessage, type UIMessageChunk } from "ai";

import { API_BASE_URL } from "@/lib/api";

/**
 * Client for the ephemeral code quick-question endpoint
 * (`POST /api/message/quick-question/code`).
 *
 * The answer streams back in the same Vercel AI UIMessage SSE format the chat
 * endpoint speaks — but this exchange is never persisted: no conversation, no
 * history, nothing to reload. Anything that fails before the stream opens is a
 * plain HTTP error, so the status code is carried on the error for the UI to
 * turn into a friendly line.
 */

/** Server-side ceiling on one question. */
export const QUICK_QUESTION_MAX_CHARS = 2000;

/** Provider / model / effort as the chat model picker currently has them. */
export interface CodeQuestionModel {
    llmProvider?: string | null;
    model?: string | null;
    reasoningEffort?: string | null;
}

export interface QuickQuestionParams {
    paperId: string;
    question: string;
    /** Repo-root-relative path of the file the selection came from. */
    filePath: string;
    startLine: number;
    endLine: number;
    model?: CodeQuestionModel | null;
}

/**
 * Failure before the stream opened, carrying the HTTP status. The UI branches
 * on `status` (429 = quota, 409 = repo not ready, …), which a bare message
 * string can't support.
 */
export class QuickQuestionError extends Error {
    readonly status: number;

    constructor(message: string, status: number) {
        super(message);
        this.name = "QuickQuestionError";
        this.status = status;
    }
}

/** True for the exception a fetch/stream raises when we abort it ourselves. */
export function isAbortError(error: unknown): boolean {
    return (
        (error instanceof DOMException && error.name === "AbortError") ||
        (error instanceof Error && error.name === "AbortError")
    );
}

/**
 * Short, non-technical line for a failure, mapped from the status code.
 *
 * The endpoint's own `detail` strings are already written for humans ("No code
 * repository is connected to this paper yet.", "Chat limit reached."), so they
 * are preferred where they exist and the mapping only supplies a fallback.
 *
 * Note 403: the endpoint answers quota exhaustion with 403, not the 429 the
 * wire contract describes — both are treated as "out of quota" here.
 */
export function quickQuestionErrorMessage(error: unknown): string {
    if (error instanceof QuickQuestionError) {
        const detail = error.message.trim();
        // A bare "API error: 500" carries nothing the reader wants.
        const usable = detail && !detail.startsWith("API error:") ? detail : "";
        switch (error.status) {
            case 401:
                return "You're not signed in anymore — reload the page and try again.";
            case 403:
            case 429:
                return (
                    usable ||
                    "You've used up your chat quota for now, so this question can't be answered."
                );
            case 404:
                return usable || "That file isn't in this repo snapshot anymore.";
            case 409:
                return usable || "The repo is still ingesting — try again in a moment.";
            case 422:
                return (
                    usable ||
                    "That question couldn't be sent — try rephrasing it."
                );
            default:
                if (error.status >= 500) {
                    return "The server couldn't answer that just now. Try again.";
                }
                return usable || "Couldn't ask that question.";
        }
    }
    if (error instanceof Error && error.message) {
        // Network-level failures (offline, server restarting) land here.
        return `Couldn't reach the server: ${error.message}`;
    }
    return "Something went wrong while answering.";
}

/**
 * Line for a failure raised *after* the stream opened — a server-side
 * generation error or a broken connection mid-answer. Worded so it doesn't
 * masquerade as a request that never left the browser.
 */
export function quickQuestionStreamErrorMessage(error: unknown): string {
    const detail = error instanceof Error ? error.message.trim() : "";
    return detail
        ? `The answer stopped early: ${detail}`
        : "The answer stopped early.";
}

/** The server's `detail` string, falling back to the bare status. */
async function errorDetail(response: Response): Promise<string> {
    try {
        const body = await response.json();
        const detail = body?.detail ?? body?.message ?? body?.error;
        if (detail) {
            return typeof detail === "string" ? detail : JSON.stringify(detail);
        }
    } catch {
        // Non-JSON error body — the status alone has to do.
    }
    return `API error: ${response.status} ${response.statusText}`.trim();
}

/** One SSE frame's `data:` payload, decoded into a chunk if it is usable. */
function enqueueFrame(
    frame: string,
    controller: TransformStreamDefaultController<UIMessageChunk>
): void {
    const data = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        // Per the SSE spec exactly one leading space is part of the framing;
        // any further whitespace belongs to the payload.
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
    if (!data.trim() || data.trim() === "[DONE]") return;
    try {
        const parsed: unknown = JSON.parse(data);
        // Anything without a `type` isn't a UIMessage chunk. Chunk types we
        // don't know are still forwarded — the SDK's stream processor ignores
        // the ones it can't use, which is exactly the behaviour we want.
        if (
            parsed &&
            typeof parsed === "object" &&
            typeof (parsed as { type?: unknown }).type === "string"
        ) {
            controller.enqueue(parsed as UIMessageChunk);
        }
    } catch {
        // A malformed frame is dropped rather than killing the stream: the
        // parts that already arrived stay on screen.
    }
}

/** SSE byte stream → UIMessage chunks. */
function toChunkStream(
    body: ReadableStream<Uint8Array>
): ReadableStream<UIMessageChunk> {
    // `stream: true` keeps a multi-byte character split across two network
    // reads from decoding into garbage.
    const decoder = new TextDecoder();
    let buffer = "";
    return body.pipeThrough(
        new TransformStream<Uint8Array, UIMessageChunk>({
            transform(bytes, controller) {
                buffer += decoder.decode(bytes, { stream: true });
                // Frames are separated by a blank line; whatever follows the
                // last separator is a partial frame and stays buffered.
                const frames = buffer.split(/\r?\n\r?\n/);
                buffer = frames.pop() ?? "";
                for (const frame of frames) enqueueFrame(frame, controller);
            },
            flush(controller) {
                buffer += decoder.decode();
                if (buffer.trim()) enqueueFrame(buffer, controller);
            },
        })
    );
}

/**
 * Ask the question and return an async iterable of message snapshots — one per
 * chunk, each a complete `UIMessage` with the text/reasoning parts so far.
 *
 * Throws `QuickQuestionError` when the request fails before streaming starts.
 * Errors raised mid-stream go to `onError`; the iterable then ends normally,
 * leaving the partial answer intact.
 */
export async function openQuickQuestionStream(
    params: QuickQuestionParams,
    options: { signal: AbortSignal; onError?: (error: unknown) => void }
): Promise<AsyncIterable<UIMessage>> {
    const response = await fetch(
        `${API_BASE_URL}/api/message/quick-question/code`,
        {
            method: "POST",
            credentials: "include",
            headers: {
                "Content-Type": "application/json",
                Accept: "text/event-stream",
            },
            signal: options.signal,
            body: JSON.stringify({
                paper_id: params.paperId,
                question: params.question,
                file_path: params.filePath,
                start_line: params.startLine,
                end_line: params.endLine,
                llm_provider: params.model?.llmProvider ?? null,
                model: params.model?.model ?? null,
                reasoning_effort: params.model?.reasoningEffort ?? null,
            }),
        }
    );

    if (!response.ok) {
        throw new QuickQuestionError(
            await errorDetail(response),
            response.status
        );
    }
    if (!response.body) {
        throw new QuickQuestionError(
            "The server sent an empty response.",
            response.status
        );
    }

    return readUIMessageStream<UIMessage>({
        stream: toChunkStream(response.body),
        onError: options.onError,
    });
}
