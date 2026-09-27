'use client';

import { Crepe, type CrepeConfig } from '@milkdown/crepe';
import { replaceAll } from '@milkdown/utils';
import { useEffect, useRef } from 'react';

import { crepeCodeMirrorConfig } from '@/lib/crepeCodeTheme';

// Only the light "frame" palette: frame-dark.css is just as unscoped, so
// importing both made the dark one win in light mode too. The palette is
// remapped onto the app's light/dark tokens in globals.css.
import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';
import 'katex/dist/katex.min.css';

type TopBarConfig = NonNullable<NonNullable<CrepeConfig['featureConfigs']>[typeof Crepe.Feature.TopBar]>;

// The always-visible formatting bar: Crepe's default has ~16 buttons, which
// wraps into several rows in the side panel. Keep the note-taking ones, in
// groups, with the label each button gets as its tooltip.
const TOP_BAR_LAYOUT: [key: string, label: string][][] = [
    [['heading-selector', '']],
    [['bold', 'Bold (Mod+B)'], ['italic', 'Italic (Mod+I)'], ['code', 'Inline code (Mod+E)'], ['link', 'Link']],
    [['bullet-list', 'Bulleted list (Mod+Alt+8)'], ['ordered-list', 'Numbered list (Mod+Alt+7)'], ['task-list', 'Task list']],
    [['quote', 'Quote (Mod+Shift+B)'], ['math', 'Math block']],
];

const topBarConfig: TopBarConfig = {
    headingOptions: [
        { label: 'Text', level: null },
        { label: 'H1', level: 1 },
        { label: 'H2', level: 2 },
        { label: 'H3', level: 3 },
    ],
    buildTopBar: (builder) => {
        const items = new Map(builder.build().flatMap((group) => group.items).map((item) => [item.key, item]));
        builder.clear();
        TOP_BAR_LAYOUT.forEach((keys, index) => {
            const group = builder.addGroup(`group-${index}`, '');
            for (const [key] of keys) {
                const item = items.get(key);
                if (item) group.addItem(key, item);
            }
        });
    },
};

// Crepe renders the bar's buttons without labels; add them (DOM order follows
// TOP_BAR_LAYOUT). The buttons are keyed, so Crepe's re-renders keep them.
function labelTopBar(root: HTMLElement) {
    const mod = /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl';
    const alt = mod === '⌘' ? '⌥' : 'Alt';
    const labels = TOP_BAR_LAYOUT.flat()
        .slice(1)
        .map(([, label]) => label.replace('Mod', mod).replace('Alt', alt));
    root.querySelectorAll<HTMLButtonElement>('.milkdown-top-bar .top-bar-item').forEach((button, i) => {
        const label = labels[i];
        if (!label) return;
        button.title = label;
        button.setAttribute('aria-label', label.replace(/ \(.*\)$/, ''));
    });
    root.querySelector('.milkdown-top-bar .top-bar-heading-button')?.setAttribute('aria-label', 'Block type');
}

interface PaperDocEditorImplProps {
    initialContent: string;
    onChange: (markdown: string) => void;
    /**
     * Bumped by the parent to forcibly replace the editor's content (initial
     * load, 409-conflict reload, agent `write_main_doc` landing). The impl
     * compares the previous value to distinguish "parent wants to overwrite"
     * from normal re-renders during user typing.
     */
    overwriteToken: number;
    overwriteContent: string;
}

export default function PaperDocEditorImpl({
    initialContent,
    onChange,
    overwriteToken,
    overwriteContent,
}: PaperDocEditorImplProps) {
    const rootRef = useRef<HTMLDivElement | null>(null);
    const crepeRef = useRef<Crepe | null>(null);
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;

    // We capture the initial content on first mount only; later updates flow
    // through the overwriteToken path.
    const initialContentRef = useRef(initialContent);

    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        const crepe = new Crepe({
            root,
            defaultValue: initialContentRef.current,
            features: { [Crepe.Feature.TopBar]: true },
            featureConfigs: {
                [Crepe.Feature.CodeMirror]: crepeCodeMirrorConfig(),
                [Crepe.Feature.TopBar]: topBarConfig,
                [Crepe.Feature.Placeholder]: { text: 'Type / for headings, lists, math…', mode: 'block' },
                // The handle sits in the editor's left gutter (globals.css).
                [Crepe.Feature.BlockEdit]: { blockHandle: { getOffset: () => 4 } },
            },
        });
        crepe.on((listener) => {
            listener.markdownUpdated((_, md) => {
                onChangeRef.current(md);
            });
        });

        let destroyed = false;
        crepe.create().then(() => {
            if (destroyed) return;
            crepeRef.current = crepe;
            labelTopBar(root);
        });

        return () => {
            destroyed = true;
            crepeRef.current = null;
            crepe.destroy().catch(() => {
                // best-effort
            });
        };
    }, []);

    // Phones: the on-screen keyboard shrinks only the visual viewport, while
    // ProseMirror keeps the caret inside the layout viewport, so typing near
    // the bottom could go on under the keyboard. Scroll the editor's scroller
    // until the caret clears it. Inactive unless the visual viewport is
    // markedly shorter than the layout one (keyboard up, not zoomed).
    useEffect(() => {
        const root = rootRef.current;
        const viewport = window.visualViewport;
        if (!root || !viewport) return;
        let frame = 0;
        const keepCaretVisible = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                if (viewport.scale > 1.01 || window.innerHeight - viewport.height < 120) return;
                const selection = document.getSelection();
                const node = selection?.focusNode;
                if (!selection?.rangeCount || !node || !root.contains(node)) return;
                const scroller = root.closest<HTMLElement>('[data-doc-scroll]');
                if (!scroller) return;
                const range = selection.getRangeAt(0).cloneRange();
                range.collapse(false);
                let caret = range.getBoundingClientRect();
                if (!caret.height) {
                    const element = node instanceof Element ? node : node.parentElement;
                    if (!element) return;
                    caret = element.getBoundingClientRect();
                }
                const visibleBottom = Math.min(
                    viewport.offsetTop + viewport.height,
                    scroller.getBoundingClientRect().bottom
                );
                const hidden = caret.bottom + 24 - visibleBottom;
                if (hidden > 0) scroller.scrollTop += hidden;
            });
        };
        viewport.addEventListener('resize', keepCaretVisible);
        document.addEventListener('selectionchange', keepCaretVisible);
        return () => {
            cancelAnimationFrame(frame);
            viewport.removeEventListener('resize', keepCaretVisible);
            document.removeEventListener('selectionchange', keepCaretVisible);
        };
    }, []);

    const lastOverwriteTokenRef = useRef(overwriteToken);
    useEffect(() => {
        if (overwriteToken === lastOverwriteTokenRef.current) return;
        lastOverwriteTokenRef.current = overwriteToken;
        const crepe = crepeRef.current;
        if (!crepe) return;
        crepe.editor.action(replaceAll(overwriteContent));
    }, [overwriteToken, overwriteContent]);

    return <div ref={rootRef} className="paper-doc-editor" />;
}
