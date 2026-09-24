"use client";

import type { MarkdownComponents } from "@/components/markdown/Markdown";
import CustomCitationLink from "@/components/utils/CustomCitationLink";
import type { Citation } from "@/lib/schema";

/**
 * Markdown overrides that turn `[^N]` markers in an answer into citation
 * links. CustomCitationLink calls `onCitationClick(key, messageIndex)` without
 * a paper id (it's shared with non-chat surfaces); the handler resolves the
 * citation — including whether it's a code citation, which goes to the code
 * viewer instead of the PDF.
 */
export function citationComponents(
    onCitationClick: (key: string, messageIndex: number) => void,
    messageIndex: number,
    citations: Citation[]
): MarkdownComponents {
    const inject = (props: object) => (
        <CustomCitationLink
            {...(props as Record<string, unknown>)}
            handleCitationClick={onCitationClick}
            messageIndex={messageIndex}
            citations={citations}
        />
    );
    return {
        p: inject,
        li: inject,
        div: inject,
        td: inject,
    } as MarkdownComponents;
}

type ChildrenProps = { children?: React.ReactNode };

/**
 * Citation blurbs in the sources list are short: strip block-level styling so
 * they sit inline with the [N] marker and the line-clamp.
 */
export const CITATION_BLURB_COMPONENTS = {
    p: ({ children }: ChildrenProps) => <>{children}</>,
    h1: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    h2: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    h3: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    h4: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    h5: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    h6: ({ children }: ChildrenProps) => <strong>{children}</strong>,
    code: ({ children }: ChildrenProps) => (
        <code className="font-mono text-[11px]">{children}</code>
    ),
} as MarkdownComponents;
