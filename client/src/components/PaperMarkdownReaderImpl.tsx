'use client';

import { Crepe } from '@milkdown/crepe';
import { replaceAll } from '@milkdown/utils';
import { RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';
import '@milkdown/crepe/theme/frame-dark.css';
import 'katex/dist/katex.min.css';

interface PaperMarkdownReaderImplProps {
    markdown: string;
}

export default function PaperMarkdownReaderImpl({ markdown }: PaperMarkdownReaderImplProps) {
    const rootRef = useRef<HTMLDivElement | null>(null);
    const crepeRef = useRef<Crepe | null>(null);
    const initialMarkdownRef = useRef(markdown);
    const [zoomedImage, setZoomedImage] = useState<{ src: string; alt: string } | null>(null);
    const [scale, setScale] = useState(1);

    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        const crepe = new Crepe({
            root,
            defaultValue: initialMarkdownRef.current,
        });
        crepe.setReadonly(true);

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

    useEffect(() => {
        const crepe = crepeRef.current;
        if (!crepe) return;
        crepe.editor.action(replaceAll(markdown));
    }, [markdown]);

    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        const handleClick = (event: MouseEvent) => {
            const target = event.target as HTMLElement | null;
            const image = target?.closest('img');
            if (!image) return;
            event.preventDefault();
            setZoomedImage({
                src: image.currentSrc || image.src,
                alt: image.alt || 'Paper figure',
            });
            setScale(1);
        };

        root.addEventListener('click', handleClick);
        return () => root.removeEventListener('click', handleClick);
    }, []);

    return (
        <>
            <div
                ref={rootRef}
                className="paper-markdown-reader h-full [&_img]:cursor-zoom-in max-sm:[&_.milkdown]:px-2 max-sm:[&_.ProseMirror]:px-2"
            />
            {zoomedImage && (
                <div
                    className="fixed inset-0 z-50 flex flex-col bg-black/95 p-3"
                    role="dialog"
                    aria-modal="true"
                    aria-label="Figure viewer"
                >
                    <div className="mb-2 flex items-center justify-end gap-2">
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm disabled:opacity-40"
                            onClick={() => setScale((value) => Math.max(0.5, value - 0.25))}
                            disabled={scale <= 0.5}
                            aria-label="Zoom out"
                        >
                            <ZoomOut className="h-5 w-5" />
                        </button>
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm"
                            onClick={() => setScale(1)}
                            aria-label="Reset zoom"
                        >
                            <RotateCcw className="h-5 w-5" />
                        </button>
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm disabled:opacity-40"
                            onClick={() => setScale((value) => Math.min(4, value + 0.25))}
                            disabled={scale >= 4}
                            aria-label="Zoom in"
                        >
                            <ZoomIn className="h-5 w-5" />
                        </button>
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm"
                            onClick={() => setZoomedImage(null)}
                            aria-label="Close figure viewer"
                        >
                            <X className="h-5 w-5" />
                        </button>
                    </div>
                    <div className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-lg bg-black touch-pan-x touch-pan-y">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={zoomedImage.src}
                            alt={zoomedImage.alt}
                            className="mx-auto h-auto max-w-none object-contain transition-[width]"
                            style={{ width: `${scale * 100}%` }}
                        />
                    </div>
                    <p className="mt-2 text-center text-xs text-white/60">Use controls or pinch to zoom, then pan around the figure</p>
                </div>
            )}
        </>
    );
}
