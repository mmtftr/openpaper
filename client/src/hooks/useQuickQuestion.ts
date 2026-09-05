"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
    isAbortError,
    openQuickQuestionStream,
    quickQuestionErrorMessage,
    quickQuestionStreamErrorMessage,
    QUICK_QUESTION_MAX_CHARS,
    type CodeQuestionModel,
} from "@/lib/quickQuestionApi";
import type { ChatUIMessage } from "@/lib/chatMessages";

/**
 * One ephemeral code question at a time.
 *
 * Nothing here is persisted: the answer lives in component state for as long
 * as the panel is open, and the in-flight request is aborted when the caller
 * stops it, asks something else, or unmounts.
 */

export interface QuickQuestionTarget {
    paperId: string;
    filePath: string;
    startLine: number;
    endLine: number;
    model?: CodeQuestionModel | null;
}

export type QuickQuestionStatus = "idle" | "streaming" | "done" | "error";

export interface QuickQuestionState {
    status: QuickQuestionStatus;
    /** The question being answered, or the one just answered. */
    question: string | null;
    /** Snapshot of the streamed answer so far — null until the first chunk. */
    message: ChatUIMessage | null;
    error: string | null;
}

const INITIAL_STATE: QuickQuestionState = {
    status: "idle",
    question: null,
    message: null,
    error: null,
};

export function useQuickQuestion() {
    const [state, setState] = useState<QuickQuestionState>(INITIAL_STATE);
    const abortRef = useRef<AbortController | null>(null);
    // Identifies the in-flight run so a late chunk from an aborted or
    // superseded request can never paint over the current one.
    const runRef = useRef(0);

    useEffect(
        () => () => {
            runRef.current += 1;
            abortRef.current?.abort();
        },
        []
    );

    const stop = useCallback(() => {
        // Retiring the run first matters: the reader has already buffered
        // whole chunks, and without this they keep painting after the click.
        runRef.current += 1;
        abortRef.current?.abort();
        abortRef.current = null;
        // The partial answer stays on screen — stopping isn't a failure.
        setState((current) =>
            current.status === "streaming"
                ? { ...current, status: "done" }
                : current
        );
    }, []);

    /**
     * Ask about `target`. The target is passed per call rather than captured,
     * so the callback can't go stale when the selection or model changes.
     */
    const ask = useCallback(
        async (rawQuestion: string, target: QuickQuestionTarget) => {
            const question = rawQuestion
                .trim()
                .slice(0, QUICK_QUESTION_MAX_CHARS)
                // Clamping by UTF-16 units can cut an emoji in half; a lone
                // surrogate survives JSON but breaks on the way out of it.
                .replace(/[\uD800-\uDBFF]$/, "");
            if (!question) return;

            abortRef.current?.abort();
            const controller = new AbortController();
            abortRef.current = controller;
            runRef.current += 1;
            const run = runRef.current;
            const isCurrent = () => run === runRef.current;

            setState({
                status: "streaming",
                question,
                message: null,
                error: null,
            });

            try {
                const stream = await openQuickQuestionStream(
                    { ...target, question },
                    {
                        signal: controller.signal,
                        onError: (error: unknown) => {
                            if (!isCurrent() || isAbortError(error)) return;
                            // Raised after the stream opened, so it's a
                            // generation/connection failure, not a bad request.
                            setState((current) => ({
                                ...current,
                                status: "error",
                                error: quickQuestionStreamErrorMessage(error),
                            }));
                        },
                    }
                );

                for await (const message of stream) {
                    if (!isCurrent()) return;
                    setState((current) => ({
                        ...current,
                        message: message as ChatUIMessage,
                    }));
                }

                if (!isCurrent()) return;
                setState((current) => {
                    // An error already reported by `onError` wins.
                    if (current.status !== "streaming") return current;
                    // A 200 that carried no usable chunk (a proxy's HTML, a
                    // stream cut before the first part) would otherwise read
                    // as a successful, empty answer.
                    if (!current.message) {
                        return {
                            ...current,
                            status: "error",
                            error: "The server didn't send an answer — try asking again.",
                        };
                    }
                    return { ...current, status: "done" };
                });
            } catch (error: unknown) {
                if (!isCurrent() || isAbortError(error)) return;
                setState((current) => ({
                    ...current,
                    status: "error",
                    error: quickQuestionErrorMessage(error),
                }));
            } finally {
                if (abortRef.current === controller) abortRef.current = null;
            }
        },
        []
    );

    return { state, ask, stop };
}
