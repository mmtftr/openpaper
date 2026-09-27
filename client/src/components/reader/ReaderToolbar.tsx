"use client";

import { useAtom, useAtomValue } from "jotai";
import {
	Columns2,
	Minus,
	PanelLeft,
	Plus,
	Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	actualScaleAtom,
	currentPageAtom,
	findOpenAtom,
	numPagesAtom,
	outlineOpenAtom,
	scaleValueAtom,
	spreadModeAtom,
} from "./atoms";
import { viewerApiAtom } from "./useViewer";
import {
	SupplementaryPicker,
	type SupplementaryPickerProps,
} from "./SupplementaryPicker";

export type ReaderToolbarProps = SupplementaryPickerProps;

function ToolButton({
	onClick,
	title,
	active,
	children,
}: {
	onClick: () => void;
	title: string;
	active?: boolean;
	children: React.ReactNode;
}) {
	return (
		<Button
			size="sm"
			variant="ghost"
			onClick={onClick}
			title={title}
			aria-label={title}
			aria-pressed={active}
			className={`h-7 w-7 p-0 pointer-coarse:h-9 pointer-coarse:w-9 ${active ? "bg-brand/10 text-brand hover:bg-brand/15 hover:text-brand" : "text-foreground/80"}`}
		>
			{children}
		</Button>
	);
}

/**
 * Reader chrome: document navigation on the left, view controls in the middle.
 * The right end is left free: on desktop the side panel's tool switcher sits
 * there in read mode (PaperSidebar).
 *
 * Search lives in the floating `FindBar` (⌘F) rather than here — pdf.js's find
 * controller owns match state, so a toolbar-embedded input would just be a
 * second place for it to get out of sync.
 */
export function ReaderToolbar(supplementary: ReaderToolbarProps) {
	const api = useAtomValue(viewerApiAtom);
	const [page, setPage] = useAtom(currentPageAtom);
	const numPages = useAtomValue(numPagesAtom);
	const actualScale = useAtomValue(actualScaleAtom);
	const [, setScaleValue] = useAtom(scaleValueAtom);
	const [spread, setSpread] = useAtom(spreadModeAtom);
	const [findOpen, setFindOpen] = useAtom(findOpenAtom);
	const [outlineOpen, setOutlineOpen] = useAtom(outlineOpenAtom);

	return (
		// A container query, not a viewport one: the reader is narrow on phones
		// and in a narrow desktop split alike.
		<div className="@container sticky top-0 z-10 w-full border-b border-border bg-background/85 backdrop-blur-md">
			<div className="flex items-center px-2 py-1.5 @md:px-3">
				<ToolButton
					title="Thumbnails and outline"
					onClick={() => setOutlineOpen(!outlineOpen)}
					active={outlineOpen}
				>
					<PanelLeft size={14} />
				</ToolButton>

				<div className="mx-1.5 h-5 w-px bg-border @max-md:mx-0.5 @max-md:bg-transparent" />

				<SupplementaryPicker {...supplementary} />

				<div className="mx-auto flex items-center gap-1 @max-sm:gap-0">
					<form
						className="flex items-center text-xs"
						onSubmit={(e) => {
							e.preventDefault();
							const target = Math.min(numPages || 1, Math.max(1, page));
							api?.goToPage(target);
						}}
					>
						<input
							type="number"
							min={1}
							max={numPages || undefined}
							value={page}
							aria-label="Page number"
							onChange={(e) => {
								// Clearing the field yields "", and Number("") is 0 — which
								// would render a page box showing 0. Keep the last valid page.
								const next = Number(e.target.value);
								if (Number.isFinite(next) && next >= 1) setPage(next);
							}}
							className="h-7 w-11 rounded-md border border-border bg-background px-1 text-center tabular-nums outline-none transition-[border-color,box-shadow] duration-150 focus:border-brand focus:ring-2 focus:ring-brand/20 pointer-coarse:h-9 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
						/>
						<span className="px-1 whitespace-nowrap tabular-nums text-muted-foreground">
							/ {numPages || "–"}
						</span>
					</form>

					<div className="mx-1.5 h-5 w-px bg-border @max-md:hidden" />

					<ToolButton title="Zoom out" onClick={() => api?.zoomOut()}>
						<Minus size={14} />
					</ToolButton>
					<button
						onClick={() => setScaleValue("page-width")}
						title="Fit width"
						aria-label={`Zoom ${Math.round(actualScale * 100)}%, fit width`}
						className="h-7 w-11 rounded-md px-1 text-center text-[11px] tabular-nums text-muted-foreground transition-colors hover:bg-accent hover:text-foreground pointer-coarse:h-9 @max-sm:hidden"
					>
						{Math.round(actualScale * 100)}%
					</button>
					<ToolButton title="Zoom in" onClick={() => api?.zoomIn()}>
						<Plus size={14} />
					</ToolButton>

					<div className="mx-1.5 h-5 w-px bg-border @max-md:hidden" />

					<div className="@max-md:hidden">
						<ToolButton
							title="Toggle two-page spread"
							active={spread !== "none"}
							onClick={() => setSpread(spread === "none" ? "odd" : "none")}
						>
							<Columns2 size={14} />
						</ToolButton>
					</div>
					<ToolButton
						title="Find (⌘F)"
						active={findOpen}
						onClick={() => setFindOpen(!findOpen)}
					>
						<Search size={14} />
					</ToolButton>
				</div>
			</div>
		</div>
	);
}
