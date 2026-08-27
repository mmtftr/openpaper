"use client";

import { useEffect, useRef } from "react";
import { useAtom, useAtomValue } from "jotai";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { findMatchCountAtom, findOpenAtom, findQueryAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";

/**
 * Find-in-document bar. Unlike the old `usePdfSearch` (which re-implemented
 * search over the text layer by hand), this just drives pdf.js's own
 * PDFFindController — so match counts, highlight-all and diacritic handling
 * come from the library.
 */
export default function FindBar() {
	const [open, setOpen] = useAtom(findOpenAtom);
	const [query, setQuery] = useAtom(findQueryAtom);
	const matches = useAtomValue(findMatchCountAtom);
	const api = useAtomValue(viewerApiAtom);
	const inputRef = useRef<HTMLInputElement>(null);
	const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.code === "KeyF")) {
				e.preventDefault();
				setOpen(true);
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [setOpen]);

	useEffect(() => {
		if (open) inputRef.current?.focus();
	}, [open]);

	useEffect(
		() => () => {
			if (debounceRef.current) clearTimeout(debounceRef.current);
		},
		[]
	);

	if (!open) return null;

	const onInput = (value: string) => {
		setQuery(value);
		if (debounceRef.current) clearTimeout(debounceRef.current);
		debounceRef.current = setTimeout(() => api?.find(value), 200);
	};

	const close = () => {
		api?.findClose();
		setQuery("");
		setOpen(false);
	};

	return (
		<div className="absolute top-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1 rounded-xl border border-border bg-background/95 px-2 py-1.5 shadow-lg backdrop-blur">
			<Search className="mx-1 size-4 shrink-0 text-muted-foreground" />
			<input
				ref={inputRef}
				value={query}
				onChange={(e) => onInput(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Enter") {
						e.preventDefault();
						api?.find(query, e.shiftKey, query.length > 0);
					} else if (e.key === "Escape") {
						close();
					}
				}}
				placeholder="Find in document"
				aria-label="Find in document"
				className="h-7 w-56 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
			/>
			<span className="min-w-14 text-center text-xs tabular-nums text-muted-foreground">
				{matches.total > 0
					? `${matches.current}/${matches.total}`
					: query
						? "0/0"
						: ""}
			</span>
			<button
				title="Previous match (Shift+Enter)"
				aria-label="Previous match"
				onClick={() => api?.find(query, true, true)}
				className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
			>
				<ChevronUp className="size-4" />
			</button>
			<button
				title="Next match (Enter)"
				aria-label="Next match"
				onClick={() => api?.find(query, false, true)}
				className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
			>
				<ChevronDown className="size-4" />
			</button>
			<button
				title="Close (Escape)"
				aria-label="Close find bar"
				onClick={close}
				className="ml-0.5 flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
			>
				<X className="size-4" />
			</button>
		</div>
	);
}
