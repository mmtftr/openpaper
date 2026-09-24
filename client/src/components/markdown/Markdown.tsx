'use client';

import { createElement, memo, useMemo } from 'react';
import {
    Streamdown,
    type Components,
    type LinkSafetyConfig,
    type PluginConfig,
} from 'streamdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

import { MarkdownPre } from '@/components/code/CodeBlock';
import { CopyableTable } from '@/components/markdown/CopyableTable';

/**
 * The app's one markdown renderer for chat answers (paper chat, quick
 * questions, citation blurbs): Streamdown, which parses incomplete markdown
 * while a message streams and memoises finished blocks.
 *
 * On top of Streamdown's defaults (GFM, raw-HTML sanitising) it adds:
 * - KaTeX math (`$$…$$` only — single dollars are too common in prose),
 * - our Shiki code blocks (`MarkdownPre`) instead of Streamdown's,
 * - `CopyableTable` (copy as TSV / export CSV),
 * - plain elements for the typographic tags, so the surrounding `prose`
 *   classes style them exactly as before instead of Streamdown's own utility
 *   classes.
 * Links open in a new tab without Streamdown's "open external link?" modal.
 */

const PLUGINS: PluginConfig = {
    math: {
        name: 'katex',
        type: 'math',
        remarkPlugin: [remarkMath, { singleDollarTextMath: false }],
        rehypePlugin: rehypeKatex,
    },
};

const LINK_SAFETY: LinkSafetyConfig = { enabled: false };

const PLAIN_TAGS = [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'strong', 'hr', 'blockquote',
    'thead', 'tbody', 'tr', 'th', 'td', 'code',
] as const;

function plainComponent(tag: string) {
    // Drop the hast `node` Streamdown passes to every component.
    const Plain = ({ node: _node, ...props }: Record<string, unknown>) =>
        createElement(tag, props);
    Plain.displayName = `Markdown.${tag}`;
    return Plain;
}

const BASE_COMPONENTS: Components = {
    ...Object.fromEntries(PLAIN_TAGS.map((tag) => [tag, plainComponent(tag)])),
    pre: MarkdownPre,
    table: CopyableTable,
} as Components;

export type MarkdownComponents = Components;

// Streamdown's memo ignores `components`, so a new override map (fresh
// citations, a new click handler) would never reach already-rendered text.
// Keying the renderer on the map's identity remounts it instead; callers keep
// that identity stable so this only happens when the overrides really change.
const componentsIds = new WeakMap<object, number>();
let nextComponentsId = 0;
function componentsId(components: object): number {
    let id = componentsIds.get(components);
    if (id === undefined) {
        id = ++nextComponentsId;
        componentsIds.set(components, id);
    }
    return id;
}

interface MarkdownProps {
    children: string;
    /** Overrides merged over the defaults. Keep the object identity stable. */
    components?: Components;
    /** The text is still streaming in: repair unclosed markdown as it grows. */
    streaming?: boolean;
    className?: string;
}

export const Markdown = memo(function Markdown({
    children,
    components,
    streaming = false,
    className,
}: MarkdownProps) {
    const merged = useMemo(
        () => (components ? { ...BASE_COMPONENTS, ...components } : BASE_COMPONENTS),
        [components]
    );
    return (
        <Streamdown
            key={componentsId(merged)}
            mode={streaming ? 'streaming' : 'static'}
            isAnimating={streaming}
            components={merged}
            plugins={PLUGINS}
            linkSafety={LINK_SAFETY}
            className={className}
        >
            {children}
        </Streamdown>
    );
});
