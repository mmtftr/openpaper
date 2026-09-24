"use client";

import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useAtom, useAtomValue, useSetAtom } from "jotai";
import type { Atom, WritableAtom } from "jotai";
import { createPaperStore, type PaperStore } from "./paperStore";

const PaperStoreContext = createContext<PaperStore | null>(null);

/**
 * Scopes a paper store to the route's paper id. Mounted in the paper layout
 * so the header buttons share it with the page.
 *
 * The page rewrites its URL with `history.replaceState` (supplementary
 * redirect, `rsf` and `display` params); Next keeps the route params as they
 * were, so that never swaps the store. Navigating to another paper does.
 */
export function PaperStoreProvider({ children }: { children: ReactNode }) {
    const params = useParams();
    const searchParams = useSearchParams();
    const rawId = params.id;
    const routeId = (Array.isArray(rawId) ? rawId[0] : rawId) ?? "";

    const [scoped, setScoped] = useState(() => ({
        id: routeId,
        store: createPaperStore(routeId, searchParams.get("display")),
    }));
    let current = scoped;
    if (scoped.id !== routeId) {
        // Another paper: a fresh store, swapped in during this render.
        current = { id: routeId, store: createPaperStore(routeId, searchParams.get("display")) };
        setScoped(current);
    }

    return <PaperStoreContext.Provider value={current.store}>{children}</PaperStoreContext.Provider>;
}

export function usePaperStore(): PaperStore {
    const store = useContext(PaperStoreContext);
    if (!store) throw new Error("usePaperStore must be used inside <PaperStoreProvider>");
    return store;
}

export function usePaperAtomValue<Value>(anAtom: Atom<Value>): Awaited<Value> {
    return useAtomValue(anAtom, { store: usePaperStore() });
}

export function useSetPaperAtom<Value, Args extends unknown[], Result>(
    anAtom: WritableAtom<Value, Args, Result>
): (...args: Args) => Result {
    return useSetAtom(anAtom, { store: usePaperStore() });
}

export function usePaperAtom<Value, Args extends unknown[], Result>(
    anAtom: WritableAtom<Value, Args, Result>
): [Awaited<Value>, (...args: Args) => Result] {
    return useAtom(anAtom, { store: usePaperStore() });
}
