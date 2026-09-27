'use client';

import { Crepe } from '@milkdown/crepe';
import { replaceAll } from '@milkdown/utils';
import { RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useRef, useState, type TouchList } from 'react';
import { createPortal } from 'react-dom';

import { crepeCodeMirrorConfig } from '@/lib/crepeCodeTheme';
import type { HighlightColor } from '@/lib/schema';
import { findPassageRange } from './markdownPassage';

import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';
import 'katex/dist/katex.min.css';

export interface MarkdownPassageJump {
    text: string;
    color: HighlightColor;
    expectedFraction: number | null;
    nonce: number;
}

interface PaperMarkdownReaderImplProps {
    markdown: string;
    /** A passage to scroll to and mark, once the document has rendered. */
    jump?: MarkdownPassageJump | null;
    onJumpSettled?: (nonce: number, found: boolean) => void;
}

const PASSAGE_COLORS: HighlightColor[] = ['yellow', 'green', 'blue', 'pink', 'purple'];

/**
 * Tints `range` through the CSS Custom Highlight API (`::highlight(md-passage-*)`
 * in globals.css) rather than wrapping it in an element: ProseMirror owns the
 * DOM, and a range can cross paragraphs and formulas.
 */
function markPassage(range: Range | null, color: HighlightColor) {
    if (typeof CSS === 'undefined' || !CSS.highlights) return;
    for (const c of PASSAGE_COLORS) CSS.highlights.delete(`md-passage-${c}`);
    if (range) CSS.highlights.set(`md-passage-${color}`, new Highlight(range));
}

function scrollParent(element: HTMLElement): HTMLElement | null {
    for (let node = element.parentElement; node; node = node.parentElement) {
        if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node;
    }
    return null;
}

/** Scroll `container` so the start of `range` sits in its upper third. */
function scrollToRange(container: HTMLElement, range: Range) {
    const top = range.getBoundingClientRect().top - container.getBoundingClientRect().top;
    container.scrollTo({ top: container.scrollTop + top - container.clientHeight * 0.3, behavior: 'instant' });
}

/** How long to keep the passage in place while figures and fonts above it load. */
const FOLLOW_LAYOUT_MS = 4000;

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const clampScale = (value: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));

function touchDistance(touches: TouchList) {
    return Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
}

export default function PaperMarkdownReaderImpl({ markdown, jump, onJumpSettled }: PaperMarkdownReaderImplProps) {
    const rootRef = useRef<HTMLDivElement | null>(null);
    const crepeRef = useRef<Crepe | null>(null);
    const initialMarkdownRef = useRef(markdown);
    const [ready, setReady] = useState(false);
    const handledJumpRef = useRef<number | null>(null);
    const stopFollowRef = useRef<(() => void) | null>(null);
    const [zoomedImage, setZoomedImage] = useState<{ src: string; alt: string } | null>(null);
    const [scale, setScale] = useState(1);
    // Two-finger pinch in the viewer; the browser's own pinch is disabled
    // there (touch-action) so it can't zoom the page underneath instead.
    const pinchRef = useRef<{ distance: number; scale: number } | null>(null);
    const [pinching, setPinching] = useState(false);

    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        const crepe = new Crepe({
            root,
            defaultValue: initialMarkdownRef.current,
            featureConfigs: { [Crepe.Feature.CodeMirror]: crepeCodeMirrorConfig() },
        });
        crepe.setReadonly(true);

        let destroyed = false;
        crepe.create().then(() => {
            if (destroyed) return;
            crepeRef.current = crepe;
            setReady(true);
        });

        return () => {
            destroyed = true;
            crepeRef.current = null;
            stopFollowRef.current?.();
            markPassage(null, 'blue');
            crepe.destroy().catch(() => {
                // best-effort
            });
        };
    }, []);

    useEffect(() => {
        const crepe = crepeRef.current;
        if (!crepe) return;
        crepe.editor.action(replaceAll(markdown));
        // The marked range was in the replaced DOM.
        markPassage(null, 'blue');
    }, [markdown]);

    useEffect(() => {
        const editor = rootRef.current?.querySelector<HTMLElement>('.ProseMirror');
        if (!ready || !jump || !editor || handledJumpRef.current === jump.nonce) return;
        handledJumpRef.current = jump.nonce;
        stopFollowRef.current?.();

        const range = findPassageRange(editor, jump.text, jump.expectedFraction);
        markPassage(range, jump.color);
        onJumpSettled?.(jump.nonce, range !== null);
        const container = scrollParent(editor);
        if (!range || !container) return;
        scrollToRange(container, range);

        // Figures above the passage load after this and push it down; keep
        // it in place until they settle or the reader scrolls themselves.
        const observer = new ResizeObserver(() => scrollToRange(container, range));
        observer.observe(editor);
        const events = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
        const stop = () => {
            observer.disconnect();
            clearTimeout(timer);
            for (const type of events) container.removeEventListener(type, stop);
            if (stopFollowRef.current === stop) stopFollowRef.current = null;
        };
        const timer = setTimeout(stop, FOLLOW_LAYOUT_MS);
        for (const type of events) container.addEventListener(type, stop, { passive: true });
        stopFollowRef.current = stop;
    }, [ready, jump, onJumpSettled]);

    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        const handleClick = (event: MouseEvent) => {
            const target = event.target as HTMLElement | null;
            // Crepe's image block wraps the <img>; a tap can land on the wrapper.
            const image =
                target?.closest('.milkdown-image-block')?.querySelector<HTMLImageElement>('img[data-type="image-block"]') ??
                target?.closest('img');
            if (!image || image.classList.contains('ProseMirror-separator')) return;
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

    useEffect(() => {
        if (!zoomedImage) return;
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') setZoomedImage(null);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [zoomedImage]);

    return (
        <>
            <div
                ref={rootRef}
                className="paper-markdown-reader h-full [&_img]:cursor-zoom-in"
            />
            {/*
              * Portaled: inside the pane it would stack under the app header
              * (the pane's layer is its own stacking context), which covered
              * the close button on phones.
              */}
            {zoomedImage && createPortal(
                <div
                    className="fixed inset-0 z-50 flex flex-col bg-black px-3 pt-[max(env(safe-area-inset-top),0.75rem)] pb-[max(env(safe-area-inset-bottom),0.75rem)] animate-in fade-in duration-200"
                    role="dialog"
                    aria-modal="true"
                    aria-label="Figure viewer"
                >
                    <div className="mb-2 flex items-center justify-end gap-2">
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm disabled:opacity-40"
                            onClick={() => setScale((value) => clampScale(value - 0.25))}
                            disabled={scale <= MIN_SCALE}
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
                            onClick={() => setScale((value) => clampScale(value + 0.25))}
                            disabled={scale >= MAX_SCALE}
                            aria-label="Zoom in"
                        >
                            <ZoomIn className="h-5 w-5" />
                        </button>
                        <button
                            type="button"
                            className="rounded-full bg-white/10 p-2 text-white backdrop-blur-sm"
                            onClick={() => setZoomedImage(null)}
                            aria-label="Close figure viewer"
                            autoFocus
                        >
                            <X className="h-5 w-5" />
                        </button>
                    </div>
                    <div
                        className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-lg bg-black touch-pan-x touch-pan-y"
                        onClick={(event) => {
                            // A tap on the backdrop (not the figure) closes, like a lightbox.
                            if (event.target === event.currentTarget) setZoomedImage(null);
                        }}
                        onTouchStart={(event) => {
                            if (event.touches.length !== 2) return;
                            pinchRef.current = { distance: touchDistance(event.touches), scale };
                            setPinching(true);
                        }}
                        onTouchMove={(event) => {
                            const pinch = pinchRef.current;
                            if (!pinch || event.touches.length !== 2) return;
                            setScale(clampScale((pinch.scale * touchDistance(event.touches)) / pinch.distance));
                        }}
                        onTouchEnd={(event) => {
                            if (event.touches.length >= 2) return;
                            pinchRef.current = null;
                            setPinching(false);
                        }}
                    >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={zoomedImage.src}
                            alt={zoomedImage.alt}
                            className={`paper-figure mx-auto h-auto max-w-none object-contain ${pinching ? '' : 'transition-[width] duration-150 ease-out-soft'}`}
                            style={{ width: `${scale * 100}%` }}
                        />
                    </div>
                    <p className="mt-2 text-center text-xs text-white/60">Pinch or use the controls to zoom · tap outside the figure to close</p>
                </div>,
                document.body
            )}
        </>
    );
}
