"use client";

import { useAtom, useAtomValue } from "jotai";
import {
	Columns2,
	Maximize2,
	Minimize2,
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

export interface ReaderToolbarProps extends SupplementaryPickerProps {
	/** Focus mode: expand the PDF to fill the viewport, hiding the side panel. */
	isReadMode?: boolean;
	onToggleReadMode?: () => void;
}

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
			className={`h-7 w-7 p-0 ${active ? "bg-blue-500/10 text-blue-500" : ""}`}
		>
			{children}
		</Button>
	);
}

/**
 * Reader chrome: document navigation on the left, view controls in the middle,
 * openpaper's panel controls on the right.
 *
 * Search lives in the floating `FindBar` (⌘F) rather than here — pdf.js's find
 * controller owns match state, so a toolbar-embedded input would just be a
 * second place for it to get out of sync.
 */
export function ReaderToolbar({
	isReadMode,
	onToggleReadMode,
	...supplementary
}: ReaderToolbarProps) {
	const api = useAtomValue(viewerApiAtom);
	const [page, setPage] = useAtom(currentPageAtom);
	const numPages = useAtomValue(numPagesAtom);
	const actualScale = useAtomValue(actualScaleAtom);
	const [, setScaleValue] = useAtom(scaleValueAtom);
	const [spread, setSpread] = useAtom(spreadModeAtom);
	const [findOpen, setFindOpen] = useAtom(findOpenAtom);
	const [outlineOpen, setOutlineOpen] = useAtom(outlineOpenAtom);

	return (
		<div className="sticky top-0 z-10 flex items-center bg-white/80 dark:bg-black/80 backdrop-blur-sm px-3 py-1.5 w-full border-b border-border">
			<ToolButton
				title="Thumbnails and outline"
				onClick={() => setOutlineOpen(!outlineOpen)}
				active={outlineOpen}
			>
				<PanelLeft size={14} />
			</ToolButton>

			<div className="h-5 w-px bg-border mx-1.5" />

			<SupplementaryPicker {...supplementary} />

			<div className="mx-auto flex items-center gap-1">
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
						className="h-7 w-12 rounded-md border border-border bg-background px-1 text-center tabular-nums outline-none focus:border-blue-500"
					/>
					<span className="px-1 text-muted-foreground">
						/ {numPages || "–"}
					</span>
				</form>

				<div className="mx-1.5 h-5 w-px bg-border" />

				<ToolButton title="Zoom out" onClick={() => api?.zoomOut()}>
					<Minus size={14} />
				</ToolButton>
				<button
					onClick={() => setScaleValue("page-width")}
					title="Fit width"
					className="w-11 rounded-md px-1 text-center text-[11px] tabular-nums text-muted-foreground hover:text-foreground"
				>
					{Math.round(actualScale * 100)}%
				</button>
				<ToolButton title="Zoom in" onClick={() => api?.zoomIn()}>
					<Plus size={14} />
				</ToolButton>

				<div className="mx-1.5 h-5 w-px bg-border" />

				<ToolButton
					title="Toggle two-page spread"
					active={spread !== "none"}
					onClick={() => setSpread(spread === "none" ? "odd" : "none")}
				>
					<Columns2 size={14} />
				</ToolButton>
				<ToolButton
					title="Find (⌘F)"
					active={findOpen}
					onClick={() => setFindOpen(!findOpen)}
				>
					<Search size={14} />
				</ToolButton>
			</div>

			<div className="flex items-center gap-0.5">
				{onToggleReadMode && (
					<ToolButton
						title={isReadMode ? "Exit focus mode" : "Focus mode"}
						onClick={onToggleReadMode}
					>
						{isReadMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
					</ToolButton>
				)}
			</div>
		</div>
	);
}
