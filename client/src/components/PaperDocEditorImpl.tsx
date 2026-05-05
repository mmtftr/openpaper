'use client';

import { Crepe } from '@milkdown/crepe';
import { replaceAll } from '@milkdown/utils';
import { useEffect, useRef } from 'react';

// Crepe's batteries-included theme + KaTeX. We pick the "frame" theme which
// gives a clean borderless look that fits a side panel; switching to dark
// happens via the `.dark` class higher up the tree (the dark variant uses
// `prefers-color-scheme`, but tailwind's `dark:` class is what the rest of
// the app uses, so we ship both stylesheets and let CSS sort it).
import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';
import '@milkdown/crepe/theme/frame-dark.css';
import 'katex/dist/katex.min.css';

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
        });

        return () => {
            destroyed = true;
            crepeRef.current = null;
            crepe.destroy().catch(() => {
                // best-effort
            });
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

    return <div ref={rootRef} className="paper-doc-editor h-full" />;
}
