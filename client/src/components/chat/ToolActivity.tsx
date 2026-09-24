"use client";

import { useMemo, useState } from "react";
import { ChevronDownIcon, WrenchIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import {
    toolIsPending,
    toolLabel,
    toolOutputFiles,
    toolOutputView,
    type ToolPartView,
} from "@/lib/chatMessages";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Loader } from "@/components/ai-elements/loader";
import { RepoFileChips } from "@/components/code/RepoFileChips";
import { ToolPayloadBlock } from "@/components/chat/ToolPayloadBlock";

/**
 * One agent tool call as a compact row. Shared by the chat panel and the
 * code viewer's quick-question panel, which speak the same UIMessage
 * protocol and so render the same tool parts.
 */

export interface ToolActivityProps {
    part: ToolPartView;
}

// Compact row for one agent tool call: pretty label + expandable
// input/output details. Pending calls show a spinner.
export function ToolActivity({ part }: ToolActivityProps) {
    const [open, setOpen] = useState(false);
    const pending = toolIsPending(part);
    const failed = part.state === "output-error";
    // Repo files the sandbox touched; the server keeps `files` on a truncated
    // result too, so the chips render regardless of output size.
    const touchedFiles = useMemo(
        () => (part.type === "tool-run_python" ? toolOutputFiles(part) : []),
        [part]
    );

    const inputPayload = useMemo(
        () => ({ kind: "json", value: part.input }) as const,
        [part.input]
    );
    const output = useMemo(() => toolOutputView(part), [part]);

    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger asChild>
                <button
                    type="button"
                    className={cn(
                        "flex max-w-full min-w-0 items-center gap-1.5 text-xs rounded px-1.5 py-0.5 transition-colors",
                        failed
                            ? "text-destructive hover:bg-destructive/10"
                            : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
                    )}
                >
                    {pending ? (
                        <Loader size={12} />
                    ) : (
                        <WrenchIcon className="size-3 shrink-0" />
                    )}
                    <span className="truncate">{toolLabel(part)}</span>
                    {failed && <span className="shrink-0">(failed)</span>}
                    <ChevronDownIcon
                        className={cn(
                            "size-3 shrink-0 transition-transform",
                            open && "rotate-180"
                        )}
                    />
                </button>
            </CollapsibleTrigger>
            <RepoFileChips files={touchedFiles} />
            <CollapsibleContent>
                <div className="ml-4 mt-1 mb-1 space-y-1 text-[11px] font-mono text-muted-foreground">
                    {part.input !== undefined && (
                        <ToolPayloadBlock
                            payload={inputPayload}
                            // The input is a partial object while it streams;
                            // highlight once it has settled.
                            highlight={!pending}
                        />
                    )}
                    {failed && part.errorText && (
                        <ToolPayloadBlock
                            payload={{ kind: "text", text: part.errorText }}
                            tone="destructive"
                        />
                    )}
                    {!failed && output && (
                        <ToolPayloadBlock
                            payload={output.payload}
                            note={output.note}
                        />
                    )}
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
}
