"use client";

import { memo, useMemo } from "react";
import { CornerDownRightIcon } from "lucide-react";

import { Message, MessageContent } from "@/components/ai-elements/message";
import {
    Reasoning,
    ReasoningContent,
    ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { ChatMessageActions } from "@/components/ChatMessageActions";
import { Markdown } from "@/components/markdown/Markdown";
import { ToolActivity } from "@/components/chat/ToolActivity";
import { jumpToTextAtom } from "@/components/paper/paperStore";
import { useSetPaperAtom } from "@/components/paper/PaperStoreProvider";
import type { CitationClickHandler } from "@/components/paper/useCitationJump";
import { ChatUIMessage, renderBlocks, textFromMessage } from "@/lib/chatMessages";
import type { Citation } from "@/lib/schema";
import { cn } from "@/lib/utils";

import { citationComponents } from "./citationMarkdown";
import { PaperSources } from "./PaperSources";
import { MessageTurnNotice, sameNotice, type TurnNotice } from "./TurnNotice";
import { useMessageCitations } from "./useMessageCitations";

/**
 * Answer typography for the chat column: one 12px rhythm between blocks
 * (margins only below, so a heading's own top margin decides the air above
 * it), smaller heading steps and list indents than `prose-sm`'s page-sized
 * defaults. The root is Streamdown's block container.
 */
const ANSWER_PROSE = cn(
    "prose dark:prose-invert prose-sm !max-w-none",
    "[&>div>*]:mt-0 [&>div>*]:mb-3 [&>div>hr]:my-4",
    "[&>div>:is(h1,h2)]:mt-5 [&>div>h3]:mt-4 [&>div>:is(h4,h5,h6)]:mt-3 [&>div>:is(h1,h2,h3,h4,h5,h6)]:mb-1.5",
    "[&>div>:first-child]:mt-0 [&>div>:last-child]:mb-0",
    "prose-h1:text-lg prose-h2:text-base prose-h3:text-[0.9375rem] prose-h4:text-sm",
    "[&_:is(ul,ol)]:pl-5 [&_li]:my-0.5 [&_li]:pl-1 [&_li>:is(ul,ol)]:my-1 [&_li>p]:my-1",
    "[&_blockquote]:pl-3 [&_blockquote>p:first-child]:mt-0 [&_blockquote>p:last-child]:mb-0",
    "prose-code:rounded prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:font-medium prose-code:before:content-none prose-code:after:content-none"
);

interface PaperMessageProps {
    message: ChatUIMessage;
    index: number;
    isStreamingMessage: boolean;
    handleCitationClick: CitationClickHandler;
    /** Failure/stopped note for this turn, rendered under the assistant bubble. */
    notice: TurnNotice | null;
    onRetry: () => void;
    retryDisabled: boolean;
}

/**
 * One chat turn. Memoised per message: the transcript re-renders on every
 * streamed chunk, but only the streaming message's props change. The notice
 * is rebuilt on every panel render, so it is compared by value.
 */
export const PaperMessage = memo(function PaperMessage({
    message,
    index,
    isStreamingMessage,
    handleCitationClick,
    notice,
    onRetry,
    retryDisabled,
}: PaperMessageProps) {
    const citationState = useMessageCitations(message, index, handleCitationClick);

    if (message.role === "user") {
        return (
            <UserMessage
                message={message}
                index={index}
                citations={citationState.citations}
            />
        );
    }
    return (
        <AssistantMessage
            message={message}
            index={index}
            isStreamingMessage={isStreamingMessage}
            citationState={citationState}
            turnNotice={
                notice ? (
                    <MessageTurnNotice
                        notice={notice}
                        onRetry={onRetry}
                        retryDisabled={retryDisabled}
                    />
                ) : null
            }
        />
    );
}, (prev, next) => {
    const keys = Object.keys(next) as (keyof PaperMessageProps)[];
    return keys.every((key) =>
        key === "notice"
            ? sameNotice(prev.notice, next.notice)
            : prev[key] === next[key]
    );
});

/** The user's turn: the PDF passages it quoted (click to jump), and the text. */
function UserMessage({
    message,
    index,
    citations,
}: {
    message: ChatUIMessage;
    index: number;
    citations: Citation[];
}) {
    const jumpToText = useSetPaperAtom(jumpToTextAtom);
    return (
        <div data-message-index={index}>
            <Message from="user">
                {citations.length > 0 && (
                    <div className="ml-auto flex flex-col items-end gap-1">
                        {citations.map((c, i) => (
                            <button
                                key={`${c.key}-${i}`}
                                type="button"
                                onClick={() => jumpToText(c.reference)}
                                className="group flex items-start gap-1 max-w-[260px] rounded-md border-l-2 border-primary/60 bg-muted/40 px-2 py-1 text-left hover:bg-muted/70 transition-colors"
                                title="Jump to this section in the PDF"
                            >
                                <CornerDownRightIcon className="size-3 mt-0.5 shrink-0 text-muted-foreground" />
                                <span className="text-xs text-muted-foreground line-clamp-2 leading-snug group-hover:text-foreground">
                                    {c.reference}
                                </span>
                            </button>
                        ))}
                    </div>
                )}
                <MessageContent>
                    <div className="prose dark:prose-invert prose-sm !max-w-none whitespace-pre-wrap">
                        {textFromMessage(message)}
                    </div>
                </MessageContent>
            </Message>
        </div>
    );
}

interface AssistantMessageProps {
    message: ChatUIMessage;
    index: number;
    isStreamingMessage: boolean;
    citationState: ReturnType<typeof useMessageCitations>;
    turnNotice: React.ReactNode;
}

/** An answer: reasoning, tool activity and cited markdown blocks in order, then its sources. */
function AssistantMessage({
    message,
    index,
    isStreamingMessage,
    citationState,
    turnNotice,
}: AssistantMessageProps) {
    const { citations, onCitationClick, sourcesOpen, setSourcesOpen, focusedCodeKey } =
        citationState;
    const blocks = useMemo(() => renderBlocks(message), [message]);
    const markdownComponents = useMemo(
        () => citationComponents(onCitationClick, index, citations),
        [onCitationClick, index, citations]
    );

    // A turn that ended before producing anything renders as the note alone —
    // an empty assistant bubble above it is just noise.
    if (blocks.length === 0 && turnNotice) {
        return <div data-message-index={index}>{turnNotice}</div>;
    }

    const text = blocks
        .map((b) => (b.kind === "text" ? b.text : null))
        .filter((t): t is string => t !== null)
        .join("\n\n");

    return (
        <div data-message-index={index}>
            <Message from="assistant">
                <MessageContent className={cn("gap-1", isStreamingMessage && "!text-foreground")}>
                    {blocks.map((block, blockIndex) => {
                        if (block.kind === "reasoning") {
                            return (
                                <Reasoning
                                    key={`reasoning-${blockIndex}`}
                                    isStreaming={isStreamingMessage && block.streaming}
                                    defaultOpen={false}
                                    className="mb-0"
                                >
                                    {/* Sized like the tool rows it sits among. */}
                                    <ReasoningTrigger className="-mx-1.5 w-fit gap-1.5 rounded px-1.5 py-0.5 text-xs hover:bg-muted/60 [&>svg]:size-3.5" />
                                    <ReasoningContent className="mt-1 mb-1">{block.text}</ReasoningContent>
                                </Reasoning>
                            );
                        }
                        if (block.kind === "tool") {
                            return (
                                <ToolActivity
                                    key={`tool-${block.part.toolCallId ?? blockIndex}`}
                                    part={block.part}
                                />
                            );
                        }
                        // Only the tail block of the live message is still
                        // growing; everything before it renders as final.
                        return (
                            <div
                                key={`text-${blockIndex}`}
                                // 12px under reasoning / tool rows (with the gap).
                                className={cn(ANSWER_PROSE, blockIndex > 0 && "mt-2")}
                            >
                                <Markdown
                                    components={markdownComponents}
                                    streaming={
                                        isStreamingMessage &&
                                        blockIndex === blocks.length - 1
                                    }
                                >
                                    {block.text}
                                </Markdown>
                            </div>
                        );
                    })}
                </MessageContent>
            </Message>
            {turnNotice}
            {citations.length > 0 && (
                <div className="mt-2">
                    <PaperSources
                        citations={citations}
                        messageIndex={index}
                        focusedCodeKey={focusedCodeKey}
                        onCitationClick={onCitationClick}
                        open={sourcesOpen}
                        onOpenChange={setSourcesOpen}
                        rightSlot={
                            <ChatMessageActions message={text} references={{ citations }} />
                        }
                    />
                </div>
            )}
        </div>
    );
}
