"use client";

import {
    Children,
    isValidElement,
    useEffect,
    useMemo,
    useState,
    type ReactElement,
    type ReactNode,
} from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { highlightToHtml, normalizeLanguage } from "@/lib/shiki";
import { useCodeWrap } from "@/hooks/useCodeWrap";

/**
 * Fenced code blocks in chat markdown.
 *
 * Streaming-aware: the plain `<pre>` renders immediately and highlighting only
 * runs once the block has stopped growing for a beat, so a streamed answer
 * isn't re-tokenized on every chunk. The highlighted HTML is kept together
 * with the exact source it was produced from — a mismatch means the block grew
 * again, and we fall back to plain until the next pass lands (no flicker
 * between two different highlighted states).
 */

const HIGHLIGHT_DEBOUNCE_MS = 300;
/** Past this, highlighting costs more than it's worth in a chat bubble. */
const MAX_HIGHLIGHT_CHARS = 100_000;

interface CodeBlockProps {
    code: string;
    lang: string;
    className?: string;
}

export function CodeBlock({ code, lang, className }: CodeBlockProps) {
    const [highlighted, setHighlighted] = useState<{
        code: string;
        html: string;
    } | null>(null);
    const [copied, setCopied] = useState(false);
    const wrap = useCodeWrap();

    useEffect(() => {
        if (!code.trim() || code.length > MAX_HIGHLIGHT_CHARS) return;
        let cancelled = false;
        const timer = setTimeout(() => {
            highlightToHtml(code, lang)
                .then((html) => {
                    if (!cancelled) setHighlighted({ code, html });
                })
                .catch((error) => {
                    console.error("Failed to highlight code block:", error);
                });
        }, HIGHLIGHT_DEBOUNCE_MS);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [code, lang]);

    useEffect(() => {
        if (!copied) return;
        const timer = setTimeout(() => setCopied(false), 2000);
        return () => clearTimeout(timer);
    }, [copied]);

    const html = highlighted?.code === code ? highlighted.html : null;

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
        } catch (error) {
            console.error("Failed to copy code:", error);
        }
    };

    return (
        <div
            className={cn(
                "group/code relative my-2 max-w-full min-w-0",
                className
            )}
        >
            <button
                type="button"
                onClick={handleCopy}
                aria-label="Copy code"
                title="Copy code"
                className="absolute top-1.5 right-1.5 z-10 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover/code:opacity-100 focus-visible:opacity-100"
            >
                {copied ? (
                    <CheckIcon className="size-3.5 text-green-500" />
                ) : (
                    <CopyIcon className="size-3.5" />
                )}
            </button>
            {html ? (
                <div
                    className={cn("op-code-block", wrap && "op-code-wrap")}
                    // Shiki escapes the code it renders; the only markup here
                    // is its own <pre>/<span> scaffolding.
                    dangerouslySetInnerHTML={{ __html: html }}
                />
            ) : (
                <pre
                    className={cn(
                        "op-code-block op-code-block-plain",
                        wrap && "op-code-wrap"
                    )}
                >
                    <code>{code}</code>
                </pre>
            )}
        </div>
    );
}

/** Flatten a markdown-rendered children tree down to its text content. */
function textContent(node: ReactNode): string {
    if (node === null || node === undefined || typeof node === "boolean") {
        return "";
    }
    if (typeof node === "string" || typeof node === "number") {
        return String(node);
    }
    if (Array.isArray(node)) return node.map(textContent).join("");
    if (isValidElement(node)) {
        const props = node.props as { children?: ReactNode };
        return textContent(props.children);
    }
    return "";
}

/**
 * Markdown `pre` override. The renderer hands us `<pre><code
 * class="language-x">…</code></pre>`; we unwrap it into a CodeBlock and leave
 * inline `code` alone (prose styles already handle that).
 */
export function MarkdownPre({ children }: { children?: ReactNode }) {
    const parsed = useMemo(() => {
        const child = Children.toArray(children).find((node) =>
            isValidElement(node)
        ) as ReactElement<{ className?: string; children?: ReactNode }> | undefined;
        if (!child) return null;
        const match = /language-([\w#+.-]+)/.exec(child.props.className ?? "");
        const code = textContent(child.props.children).replace(/\n$/, "");
        if (!code) return null;
        return { code, lang: normalizeLanguage(match?.[1]) };
    }, [children]);

    if (!parsed) {
        return <pre className="op-code-block op-code-block-plain">{children}</pre>;
    }
    return <CodeBlock code={parsed.code} lang={parsed.lang} />;
}
