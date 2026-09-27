"use client";

import { createContext } from "react";

/**
 * True while the pane holding the reader is being resized (divider drag, or a
 * burst of arrow-key presses on it). The reader then shows a frozen snapshot
 * of its pages, scaled to follow the pane, instead of letting pdf.js refit and
 * re-render on every frame; the real fit happens once when this goes false.
 *
 * A context rather than a reader atom: the layout that owns the divider sits
 * outside the reader's per-instance jotai Provider. Kept free of pdf.js
 * imports so the layout can use it without pulling the reader bundle in.
 */
export const PaneResizingContext = createContext(false);
