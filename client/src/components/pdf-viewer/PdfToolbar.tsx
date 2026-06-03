"use client";

import { Ref, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Minus,
	Plus,
	ChevronUp,
	ChevronDown,
	Search,
	X,
	Maximize2,
	Minimize2,
	EllipsisVertical,
	ZoomIn,
	ZoomOut,
	Paperclip,
	Check,
} from "lucide-react";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PaperUploadJobStatusResponse, SupplementaryMaterialSummary } from "@/lib/schema";
import { uploadSupplementaryFile } from "@/lib/uploadUtils";
import { fetchFromApi } from "@/lib/api";

const SUPPLEMENTARY_MAX_SIZE_MB = 30;
const SUPPLEMENTARY_POLL_INTERVAL_MS = 2000;

interface PdfToolbarProps {
	// Search
	searchText: string;
	showSearchInput: boolean;
	setShowSearchInput: (show: boolean) => void;
	searchInputRef: Ref<HTMLInputElement | null>;
	handleSearchChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	handleSearchSubmit: (e: React.FormEvent) => void;
	handleClearSearch: () => void;
	isSearching: boolean;
	matchPages: number[];
	currentMatchIndex: number;
	goToPreviousMatch: () => void;
	goToNextMatch: () => void;
	lastSearchTermRef: Ref<string | undefined>;

	// Zoom
	scale: number;
	zoomIn: () => void;
	zoomOut: () => void;

	/** Focus / read mode: expand PDF to fill the viewport, hiding the side panel. */
	isReadMode?: boolean;
	onToggleReadMode?: () => void;

	// Supplementary materials. Only rendered when parentPaperId is set, so the
	// toolbar still works on the home/upload flow where these aren't relevant.
	parentPaperId?: string;
	displayedPaperId?: string;
	supplementaryMaterials?: SupplementaryMaterialSummary[];
	onChangeDisplayed?: (paperId: string) => void;
	onSupplementaryUploaded?: () => void;
	parentPaperTitle?: string;
}

export function PdfToolbar({
	searchText,
	showSearchInput,
	setShowSearchInput,
	searchInputRef,
	handleSearchChange,
	handleSearchSubmit,
	handleClearSearch,
	isSearching,
	matchPages,
	currentMatchIndex,
	goToPreviousMatch,
	goToNextMatch,
	lastSearchTermRef,
	scale,
	zoomIn,
	zoomOut,
	isReadMode = false,
	onToggleReadMode,
	parentPaperId,
	displayedPaperId,
	supplementaryMaterials = [],
	onChangeDisplayed,
	onSupplementaryUploaded,
	parentPaperTitle,
}: PdfToolbarProps) {
	const supplementaryFileInputRef = useRef<HTMLInputElement | null>(null);
	const [isUploadingSupplementary, setIsUploadingSupplementary] = useState(false);

	const showSupplementaryControls = Boolean(parentPaperId);
	const parentTitle = parentPaperTitle?.trim() || "Main paper";
	const isParentDisplayed =
		!displayedPaperId || (parentPaperId !== undefined && displayedPaperId === parentPaperId);
	const currentSupplementaryIndex = isParentDisplayed
		? -1
		: supplementaryMaterials.findIndex((s) => s.id === displayedPaperId);
	const currentLabel = isParentDisplayed
		? "Main"
		: currentSupplementaryIndex >= 0
			? `Suppl ${currentSupplementaryIndex + 1}`
			: "Suppl";

	const pollSupplementaryStatus = (jobId: string, fileName: string): Promise<void> => {
		return new Promise((resolve, reject) => {
			const poll = async () => {
				try {
					const response: PaperUploadJobStatusResponse = await fetchFromApi(
						`/api/paper/upload/status/${jobId}`,
					);
					if (response.status === "completed") {
						resolve();
					} else if (response.status === "failed") {
						reject(new Error(`Failed to process ${fileName}`));
					} else {
						setTimeout(poll, SUPPLEMENTARY_POLL_INTERVAL_MS);
					}
				} catch (err) {
					reject(err instanceof Error ? err : new Error(String(err)));
				}
			};
			poll();
		});
	};

	const handleSupplementaryFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
		const file = e.target.files?.[0];
		// Reset input early so the same file can be re-selected.
		if (e.target) e.target.value = "";
		if (!file || !parentPaperId) return;

		if (file.type !== "application/pdf") {
			toast.error("Invalid file type. Please upload a PDF.");
			return;
		}
		if (file.size > SUPPLEMENTARY_MAX_SIZE_MB * 1024 * 1024) {
			toast.error(`File size exceeds the ${SUPPLEMENTARY_MAX_SIZE_MB}MB limit.`);
			return;
		}

		const toastId = toast.loading(`Uploading ${file.name}…`);
		setIsUploadingSupplementary(true);
		try {
			const job = await uploadSupplementaryFile(parentPaperId, file);
			toast.loading(`Processing ${file.name}…`, { id: toastId });
			await pollSupplementaryStatus(job.jobId, file.name);
			toast.success("Supplementary uploaded.", { id: toastId });
			onSupplementaryUploaded?.();
		} catch (err) {
			console.error("Supplementary upload failed", err);
			toast.error(
				err instanceof Error ? err.message : "Failed to upload supplementary PDF.",
				{ id: toastId },
			);
		} finally {
			setIsUploadingSupplementary(false);
		}
	};
	return (
		<div className="sticky top-0 z-10 flex items-center bg-white/80 dark:bg-black/80 backdrop-blur-sm px-3 py-1.5 w-full border-b border-gray-300">
			{/* Source picker (compact) + supplementary upload */}
			{showSupplementaryControls && (
				<>
					<div className="flex items-center gap-0.5">
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<Button
									size="sm"
									variant="ghost"
									className="h-7 px-2 gap-0.5 text-xs"
									title={isParentDisplayed ? parentTitle : (supplementaryMaterials[currentSupplementaryIndex]?.title || "Supplementary")}
								>
									<span>{currentLabel}</span>
									<ChevronDown size={12} className="opacity-60" />
								</Button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="start" className="min-w-56">
								<DropdownMenuLabel className="text-xs text-muted-foreground">
									Displayed PDF
								</DropdownMenuLabel>
								<DropdownMenuItem
									onClick={() => {
										if (parentPaperId && onChangeDisplayed && !isParentDisplayed) {
											onChangeDisplayed(parentPaperId);
										}
									}}
								>
									<span className="flex items-center gap-2 flex-1 min-w-0">
										{isParentDisplayed ? (
											<Check size={14} className="shrink-0" />
										) : (
											<span className="w-[14px] shrink-0" />
										)}
										<span className="flex flex-col min-w-0">
											<span className="text-sm">Main</span>
											<span className="text-[10px] text-muted-foreground truncate">{parentTitle}</span>
										</span>
									</span>
								</DropdownMenuItem>
								{supplementaryMaterials.length > 0 && (
									<>
										<DropdownMenuSeparator />
										{supplementaryMaterials.map((item, idx) => {
											const isCompleted = item.status === "completed";
											const isCurrent = displayedPaperId === item.id;
											const subLabel = isCompleted
												? (item.title?.trim() || "Untitled")
												: `Processing… (${item.status})`;
											return (
												<DropdownMenuItem
													key={item.id}
													disabled={!isCompleted}
													onClick={() => {
														if (isCompleted && !isCurrent) {
															onChangeDisplayed?.(item.id);
														}
													}}
												>
													<span className="flex items-center gap-2 flex-1 min-w-0">
														{isCurrent ? (
															<Check size={14} className="shrink-0" />
														) : (
															<span className="w-[14px] shrink-0" />
														)}
														<span className="flex flex-col min-w-0">
															<span className="text-sm">Suppl {idx + 1}</span>
															<span className="text-[10px] text-muted-foreground truncate">{subLabel}</span>
														</span>
													</span>
												</DropdownMenuItem>
											);
										})}
									</>
								)}
							</DropdownMenuContent>
						</DropdownMenu>

						<Button
							size="sm"
							variant="ghost"
							className="h-7 w-7 p-0"
							onClick={() => supplementaryFileInputRef.current?.click()}
							disabled={isUploadingSupplementary}
							title="Attach supplementary PDF"
							aria-label="Attach supplementary PDF"
						>
							<Paperclip size={14} />
						</Button>
						<input
							ref={supplementaryFileInputRef}
							type="file"
							accept=".pdf"
							className="hidden"
							onChange={handleSupplementaryFileChange}
						/>
					</div>

					<div className="h-5 w-px bg-gray-300 mx-1.5 md:mx-3" />
				</>
			)}

			{/* Search */}
			<div className="flex items-center gap-1">
				{showSearchInput ? (
					<form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
						<div className="relative">
							<Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
							<Input
								ref={searchInputRef}
								type="text"
								placeholder="Search..."
								value={searchText}
								onChange={handleSearchChange}
								className="h-7 w-40 pl-7 pr-7 text-xs"
								autoFocus
							/>
							{searchText && (
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="absolute right-0 top-1/2 -translate-y-1/2 h-6 w-6 p-0"
									onClick={handleClearSearch}
								>
									<X size={12} />
								</Button>
							)}
						</div>
						{isSearching ? (
							<span className="text-xs text-muted-foreground">
								Searching...
							</span>
						) : matchPages.length > 0 ? (
							<div className="flex items-center gap-1">
								<span className="text-xs text-muted-foreground">
									{currentMatchIndex + 1}/{matchPages.length}
								</span>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="h-6 w-6 p-0"
									onClick={goToPreviousMatch}
									title="Previous match"
								>
									<ChevronUp size={14} />
								</Button>
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="h-6 w-6 p-0"
									onClick={goToNextMatch}
									title="Next match"
								>
									<ChevronDown size={14} />
								</Button>
							</div>
						) : searchText && (lastSearchTermRef as React.RefObject<string | undefined>).current === searchText ? (
							<span className="text-xs text-muted-foreground">
								No results
							</span>
						) : null}
					</form>
				) : (
					<Button
						onClick={() => {
							setShowSearchInput(true);
							setTimeout(() => (searchInputRef as React.RefObject<HTMLInputElement | null>).current?.focus(), 0);
						}}
						size="sm"
						variant="ghost"
						className="h-7 w-7 p-0"
						title="Search (Cmd+F)"
					>
						<Search size={14} />
					</Button>
				)}
			</div>

			{/* Spacer */}
			<div className="flex-1" />

			{/* Right: Zoom + Focus (desktop) */}
			<div className="hidden md:flex items-center gap-2">
				<div className="flex items-center gap-0.5">
					<Button onClick={zoomOut} size="sm" variant="ghost" className="h-7 w-7 p-0">
						<Minus size={14} />
					</Button>
					<span className="text-[11px] w-10 text-center tabular-nums text-muted-foreground">
						{Math.round(scale * 100)}%
					</span>
					<Button onClick={zoomIn} size="sm" variant="ghost" className="h-7 w-7 p-0">
						<Plus size={14} />
					</Button>
				</div>

				{onToggleReadMode && (
					<Button
						size="sm"
						variant="ghost"
						className="h-7 w-7 p-0"
						onClick={onToggleReadMode}
						title={isReadMode ? "Exit focus mode" : "Focus mode"}
						aria-label={isReadMode ? "Exit focus mode" : "Focus mode"}
					>
						{isReadMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
					</Button>
				)}
			</div>

			{/* Right: overflow menu (mobile) */}
			<div className="flex md:hidden items-center">
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button size="sm" variant="ghost" className="h-7 w-7 p-0">
							<EllipsisVertical size={14} />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end" className="min-w-40">
						<DropdownMenuItem onClick={zoomIn}>
							<ZoomIn size={14} className="mr-2" />
							Zoom in
						</DropdownMenuItem>
						<DropdownMenuItem onClick={zoomOut}>
							<ZoomOut size={14} className="mr-2" />
							Zoom out
						</DropdownMenuItem>
					</DropdownMenuContent>
				</DropdownMenu>
			</div>
		</div>
	);
}
