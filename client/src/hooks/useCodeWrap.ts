"use client";

import { useSyncExternalStore } from "react";

/**
 * "Wrap long code lines" preference, shared by the repo code viewer and the
 * fenced code blocks in chat so code reads the same way everywhere.
 *
 * A module-level store (not context): the two surfaces live in different
 * trees, and this is one boolean. Persisted like the other chat prefs.
 */

const STORAGE_KEY = "openpaper:code-wrap";

type Listener = () => void;

const listeners = new Set<Listener>();
let cached: boolean | null = null;

function read(): boolean {
    if (cached !== null) return cached;
    if (typeof window === "undefined") return false;
    cached = window.localStorage.getItem(STORAGE_KEY) === "true";
    return cached;
}

export function getCodeWrap(): boolean {
    return read();
}

export function setCodeWrap(next: boolean): void {
    cached = next;
    if (typeof window !== "undefined") {
        try {
            window.localStorage.setItem(STORAGE_KEY, String(next));
        } catch (error) {
            console.error("Could not persist the code-wrap preference:", error);
        }
    }
    for (const listener of listeners) listener();
}

function subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}

/** Current preference; re-renders every consumer when it flips. */
export function useCodeWrap(): boolean {
    // Server snapshot is the default (off) — localStorage isn't readable there.
    return useSyncExternalStore(subscribe, getCodeWrap, () => false);
}
