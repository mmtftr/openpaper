"use client";

import { useCallback, useEffect, useRef } from "react";
import type { RefObject } from "react";
import { retryTargetFromMessages } from "@/lib/chatMessages";
import { userMessageReferencesAtom } from "@/components/paper/paperStore";
import { usePaperAtom } from "@/components/paper/PaperStoreProvider";
import type { ChatComposerHandle } from "./ChatComposer";
import type { ChatModelOptions } from "./useChatModelOptions";
import type { PaperChat } from "./usePaperChat";

/** Non-default send behaviour — only the retry path uses it. */
interface SubmitMessageOptions {
    /** Attach these references instead of the ones staged in the composer. */
    references?: string[];
    /** Leave the composer text and staged references untouched. */
    keepComposer?: boolean;
}

interface UseChatSubmitArgs {
    paperId: string;
    conversationId: string | null;
    chat: PaperChat;
    options: ChatModelOptions;
    composerRef: RefObject<ChatComposerHandle | null>;
}

/**
 * Sending a turn and re-sending a failed one. The staged references (PDF
 * selections, code snippets) travel as `user_references` and as a
 * `data-citations` part mirroring what the server stores on the user row, so
 * the live message and the reloaded one look alike.
 */
export function useChatSubmit({
    paperId,
    conversationId,
    chat,
    options,
    composerRef,
}: UseChatSubmitArgs) {
    const { session, sendMessage, setMessages, stop, clearError, isStreaming, messages, status } =
        chat;
    const { selectedModelOption, supportsReasoningEffort, reasoningEffort, contextMode } = options;
    const [userMessageReferences, setUserMessageReferences] = usePaperAtom(
        userMessageReferencesAtom
    );

    const submitMessage = useCallback(
        (rawText: string, submitOptions?: SubmitMessageOptions) => {
            const text = rawText.trim();
            if (!text || isStreaming || !conversationId) return false;

            clearError();
            session.clearTurnState();

            // A retry carries the ORIGINAL turn's references; the staged list
            // belongs to whatever the user is composing right now.
            const references = submitOptions?.references
                ? [...submitOptions.references]
                : [...userMessageReferences];
            session.requestExtras = {
                paper_id: paperId,
                conversation_id: conversationId,
                context_mode: contextMode,
                ...(selectedModelOption
                    ? {
                          model: selectedModelOption.id,
                          // Disambiguates model ids shared across providers —
                          // the picker knows which provider this id came from.
                          llm_provider: selectedModelOption.provider,
                      }
                    : {}),
                ...(supportsReasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
                ...(references.length ? { user_references: references } : {}),
            };

            sendMessage({
                parts: [
                    { type: "text", text },
                    ...(references.length
                        ? [
                              {
                                  type: "data-citations" as const,
                                  data: {
                                      citations: references.map((ref, index) => ({
                                          key: index + 1,
                                          reference: ref,
                                      })),
                                  },
                              },
                          ]
                        : []),
                ],
            });

            // A retry re-sends a past turn: the composer and the staged
            // references are the user's current draft and must survive it.
            if (!submitOptions?.keepComposer) {
                composerRef.current?.setText("");
                setUserMessageReferences([]);
            }
            return true;
        },
        [
            isStreaming,
            conversationId,
            paperId,
            session,
            userMessageReferences,
            selectedModelOption,
            supportsReasoningEffort,
            reasoningEffort,
            contextMode,
            clearError,
            sendMessage,
            setUserMessageReferences,
            composerRef,
        ]
    );

    // Stable entry points for memoised children; they call the latest
    // closures, which change on every streamed chunk.
    const submitMessageRef = useRef(submitMessage);
    submitMessageRef.current = submitMessage;
    const onComposerSubmit = useCallback((text: string) => {
        submitMessageRef.current(text);
    }, []);
    const onStop = useCallback(() => {
        void stop();
    }, [stop]);

    // `isStreaming` comes from the last render, so two clicks landing in one
    // frame would both pass that check. This flag flips synchronously.
    const retryInFlightRef = useRef(false);
    useEffect(() => {
        retryInFlightRef.current = false;
    }, [status]);

    /**
     * Re-ask the last question after a failed turn.
     *
     * The failed assistant row and the user row are dropped locally, then the
     * same text AND the same references are sent as a brand new message: the
     * server reuses its dangling user row and clears the partial assistant
     * row, while reusing the old message id here would be rejected as a
     * duplicate. Never `regenerate()` — the endpoint only accepts the
     * `submit-message` trigger.
     */
    const retryLastTurn = useCallback(() => {
        if (retryInFlightRef.current || isStreaming || !conversationId) return;
        const target = retryTargetFromMessages(messages);
        if (!target) return;
        retryInFlightRef.current = true;
        session.clearTurnState();
        setMessages((prev) => prev.slice(0, target.keep));
        const sent = submitMessage(target.text, {
            references: target.references,
            keepComposer: true,
        });
        // Nothing was sent (the guards inside disagreed) — don't leave the
        // button locked until the next status change.
        if (!sent) retryInFlightRef.current = false;
    }, [isStreaming, conversationId, messages, session, setMessages, submitMessage]);
    const retryLastTurnRef = useRef(retryLastTurn);
    retryLastTurnRef.current = retryLastTurn;
    /** Stable `retryLastTurn` for memoised messages. */
    const onRetryLastTurn = useCallback(() => retryLastTurnRef.current(), []);

    /** Drop a failed user turn and put its text and references back in the composer. */
    const editFailedTurn = useCallback(
        (text: string, references: string[]) => {
            setMessages((prev) => prev.slice(0, -1));
            composerRef.current?.setText(text);
            setUserMessageReferences(references);
            clearError();
        },
        [setMessages, composerRef, setUserMessageReferences, clearError]
    );

    return {
        submitMessage,
        onComposerSubmit,
        onStop,
        retryLastTurn,
        onRetryLastTurn,
        editFailedTurn,
    };
}
