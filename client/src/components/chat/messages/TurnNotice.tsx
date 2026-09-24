"use client";

import { useState } from "react";
import {
    AlertTriangleIcon,
    ChevronDownIcon,
    CircleStopIcon,
    RefreshCwIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { cn } from "@/lib/utils";

/**
 * One name for a turn that errored, live or reloaded — the same failure must
 * not read as two different things either side of a refresh. "Stopped" is
 * reserved for turns that were deliberately interrupted.
 */
export const FAILED_TURN_TITLE = "Inference failed";

/**
 * The note under an assistant turn that didn't complete normally.
 *
 * `tone` separates the two very different cases: "error" is a failure the
 * user did not ask for (destructive styling, retry offered), "muted" is a
 * deliberate stop (neutral styling, never retried — the server keeps the
 * stopped row, so re-sending would duplicate the question).
 */
export interface TurnNotice {
    tone: "error" | "muted";
    /** Short label for what happened. */
    title: string;
    /** One-line, human-readable cause. */
    headline: string;
    /** Full raw error text for the disclosure; empty when there is no more. */
    detail: string;
    canRetry: boolean;
}

export const sameNotice = (a: TurnNotice | null, b: TurnNotice | null) =>
    a === b ||
    (!!a &&
        !!b &&
        a.tone === b.tone &&
        a.title === b.title &&
        a.headline === b.headline &&
        a.detail === b.detail &&
        a.canRetry === b.canRetry);

/** Collapsed dump of the raw error, styled like the tool-failure output. */
function ErrorDetails({ detail }: { detail: string }) {
    const [open, setOpen] = useState(false);
    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger asChild>
                <button
                    type="button"
                    className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                    <span>Details</span>
                    <ChevronDownIcon
                        className={cn(
                            "size-3 shrink-0 transition-transform",
                            open && "rotate-180"
                        )}
                    />
                </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
                <pre className="mt-1 whitespace-pre-wrap break-words max-h-40 overflow-y-auto rounded bg-destructive/10 p-1.5 text-[11px] font-mono text-muted-foreground">
                    {detail}
                </pre>
            </CollapsibleContent>
        </Collapsible>
    );
}

interface MessageTurnNoticeProps {
    notice: TurnNotice;
    onRetry: () => void;
    retryDisabled?: boolean;
}

/**
 * The note attached to an assistant turn that didn't finish — live (the
 * stream errored or died) or reloaded (`metadata.interrupted`). A failure
 * shows the server's own error text with the untrimmed version one click
 * away and a retry on the last turn; a deliberate stop is a quiet line.
 */
export function MessageTurnNotice({
    notice,
    onRetry,
    retryDisabled,
}: MessageTurnNoticeProps) {
    const isError = notice.tone === "error";
    return (
        <div
            className={cn(
                "mt-2 rounded-md border px-2.5 py-2 space-y-1.5",
                isError
                    ? "border-destructive/40 bg-destructive/5"
                    : "border-border/60 bg-muted/40"
            )}
        >
            <div
                className={cn(
                    "flex items-start gap-1.5",
                    isError ? "text-destructive" : "text-muted-foreground"
                )}
            >
                {isError ? (
                    <AlertTriangleIcon className="size-3.5 mt-0.5 shrink-0" />
                ) : (
                    <CircleStopIcon className="size-3.5 mt-0.5 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium">{notice.title}</p>
                    <p className="text-xs break-words">{notice.headline}</p>
                </div>
            </div>
            {notice.detail && notice.detail !== notice.headline && (
                <ErrorDetails detail={notice.detail} />
            )}
            {notice.canRetry && (
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1.5 text-xs"
                    disabled={retryDisabled}
                    onClick={onRetry}
                >
                    <RefreshCwIcon className="size-3.5" />
                    Retry
                </Button>
            )}
        </div>
    );
}

/** A user turn that got no answer: why, plus Retry / Edit. */
export interface UserTurnFailure {
    text: string;
    /** The turn's references, so "Edit" puts them back in the composer. */
    references: string[];
    headline: string;
    detail: string;
}

interface FailedTurnNoticeProps {
    failure: UserTurnFailure;
    retryDisabled: boolean;
    onRetry: () => void;
    onEdit: () => void;
}

/**
 * Error UX, case (b): the turn failed before any assistant content arrived,
 * so the failure belongs to the user's own message — offer retry/edit there,
 * with the server's actual error text.
 */
export function FailedTurnNotice({
    failure,
    retryDisabled,
    onRetry,
    onEdit,
}: FailedTurnNoticeProps) {
    return (
        <Message from="assistant">
            <MessageContent>
                <div className="text-sm space-y-2">
                    <div className="flex items-start gap-1.5 text-destructive">
                        <AlertTriangleIcon className="size-3.5 mt-0.5 shrink-0" />
                        <span className="break-words">{failure.headline}</span>
                    </div>
                    {failure.detail && failure.detail !== failure.headline && (
                        <ErrorDetails detail={failure.detail} />
                    )}
                    <div className="flex gap-2">
                        <Button
                            variant="default"
                            size="sm"
                            disabled={retryDisabled}
                            onClick={onRetry}
                        >
                            <RefreshCwIcon className="size-3.5" />
                            Retry
                        </Button>
                        <Button variant="ghost" size="sm" onClick={onEdit}>
                            Edit
                        </Button>
                    </div>
                </div>
            </MessageContent>
        </Message>
    );
}
