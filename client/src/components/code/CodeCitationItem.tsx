"use client";

import { ExternalLinkIcon, FileCodeIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/schema";
import { codeCitationLabel, commitShaFromGithubUrl } from "@/lib/chatMessages";
import { useCodeViewer } from "@/components/code/CodeViewerProvider";

/**
 * One code citation in the sources list: a compact reference, never the code
 * itself. The model quotes code with ordinary fenced blocks in its answer;
 * this row just says which file and lines backed the claim, and opens the
 * snapshot there. Unlike PDF citations these never reach the document
 * highlighter.
 */

interface CodeCitationItemProps {
    citation: Citation;
    messageIndex: number;
    /** Briefly emphasized after its inline [n] marker was clicked. */
    focused?: boolean;
}

export function CodeCitationItem({
    citation,
    messageIndex,
    focused = false,
}: CodeCitationItemProps) {
    const { openCodeViewer } = useCodeViewer();

    // An unverified quote couldn't be located in the file, so its line numbers
    // are not something to point at.
    const verified = citation.verified !== false;
    const label =
        (verified ? codeCitationLabel(citation) : citation.file) || "code";

    const open = () => {
        if (!citation.file) return;
        openCodeViewer({
            path: citation.file,
            startLine: verified ? citation.start_line : null,
            endLine: verified ? citation.end_line : null,
            commitSha: commitShaFromGithubUrl(citation.github_url),
        });
    };

    return (
        <li
            id={`citation-${citation.key}-${messageIndex}`}
            role="button"
            tabIndex={0}
            aria-label={`Open ${label} in the code viewer`}
            onClick={open}
            onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    open();
                }
            }}
            className={cn(
                "flex cursor-pointer items-baseline gap-1.5 rounded px-1.5 py-0.5 transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                focused && "bg-primary/10 ring-1 ring-primary/40"
            )}
        >
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                [{citation.key}]
            </span>
            <FileCodeIcon
                className={cn(
                    "size-3 shrink-0 self-center",
                    verified
                        ? "text-muted-foreground"
                        : "text-muted-foreground/60"
                )}
            />
            <span
                id={`citation-ref-${citation.key}-${messageIndex}`}
                className={cn(
                    "min-w-0 flex-1 truncate font-mono text-[11px]",
                    verified
                        ? "text-foreground"
                        : "text-muted-foreground/70 italic"
                )}
                title={citation.file}
            >
                {label}
            </span>
            {citation.github_url && (
                <a
                    href={citation.github_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    // Keep Enter on the link from also opening the viewer
                    // through the row's key handler.
                    onKeyDown={(event) => event.stopPropagation()}
                    className="shrink-0 self-center text-muted-foreground hover:text-foreground"
                    aria-label="Open on GitHub"
                    title="Open on GitHub"
                >
                    <ExternalLinkIcon className="size-3" />
                </a>
            )}
        </li>
    );
}
