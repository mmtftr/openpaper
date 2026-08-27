"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { Check, ChevronDown, Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
	PaperUploadJobStatusResponse,
	SupplementaryMaterialSummary,
} from "@/lib/schema";
import { uploadSupplementaryFile } from "@/lib/uploadUtils";
import { fetchFromApi } from "@/lib/api";

const SUPPLEMENTARY_MAX_SIZE_MB = 30;
const SUPPLEMENTARY_POLL_INTERVAL_MS = 2000;

export interface SupplementaryPickerProps {
	parentPaperId?: string;
	displayedPaperId?: string;
	parentPaperTitle?: string;
	supplementaryMaterials?: SupplementaryMaterialSummary[];
	onChangeDisplayed?: (paperId: string) => void;
	onSupplementaryUploaded?: () => void;
}

/**
 * Switch the rendered PDF between a paper and its supplementary files, and
 * attach new ones.
 *
 * Lifted out of the old `PdfToolbar` unchanged — it is openpaper-specific and
 * has nothing to do with which PDF engine renders the pages.
 */
export function SupplementaryPicker({
	parentPaperId,
	displayedPaperId,
	parentPaperTitle,
	supplementaryMaterials = [],
	onChangeDisplayed,
	onSupplementaryUploaded,
}: SupplementaryPickerProps) {
	const fileInputRef = useRef<HTMLInputElement | null>(null);
	const [isUploading, setIsUploading] = useState(false);

	if (!parentPaperId) return null;

	const parentTitle = parentPaperTitle?.trim() || "Main paper";
	const isParentDisplayed =
		!displayedPaperId || displayedPaperId === parentPaperId;
	const currentIndex = isParentDisplayed
		? -1
		: supplementaryMaterials.findIndex((s) => s.id === displayedPaperId);
	const currentLabel = isParentDisplayed
		? "Main"
		: currentIndex >= 0
			? `Suppl ${currentIndex + 1}`
			: "Suppl";

	const pollStatus = (jobId: string, fileName: string): Promise<void> =>
		new Promise((resolve, reject) => {
			const poll = async () => {
				try {
					const response: PaperUploadJobStatusResponse = await fetchFromApi(
						`/api/paper/upload/status/${jobId}`
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

	const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
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
		setIsUploading(true);
		try {
			const job = await uploadSupplementaryFile(parentPaperId, file);
			toast.loading(`Processing ${file.name}…`, { id: toastId });
			await pollStatus(job.jobId, file.name);
			toast.success("Supplementary uploaded.", { id: toastId });
			onSupplementaryUploaded?.();
		} catch (err) {
			console.error("Supplementary upload failed", err);
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to upload supplementary PDF.",
				{ id: toastId }
			);
		} finally {
			setIsUploading(false);
		}
	};

	return (
		<div className="flex items-center gap-0.5">
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						size="sm"
						variant="ghost"
						className="h-7 px-2 gap-0.5 text-xs"
						title={
							isParentDisplayed
								? parentTitle
								: supplementaryMaterials[currentIndex]?.title || "Supplementary"
						}
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
							if (onChangeDisplayed && !isParentDisplayed) {
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
								<span className="text-[10px] text-muted-foreground truncate">
									{parentTitle}
								</span>
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
									? item.title?.trim() || "Untitled"
									: `Processing… (${item.status})`;
								return (
									<DropdownMenuItem
										key={item.id}
										disabled={!isCompleted}
										onClick={() => {
											if (isCompleted && !isCurrent) onChangeDisplayed?.(item.id);
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
												<span className="text-[10px] text-muted-foreground truncate">
													{subLabel}
												</span>
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
				onClick={() => fileInputRef.current?.click()}
				disabled={isUploading}
				title="Attach supplementary PDF"
				aria-label="Attach supplementary PDF"
			>
				<Paperclip size={14} />
			</Button>
			<input
				ref={fileInputRef}
				type="file"
				accept=".pdf"
				className="hidden"
				onChange={handleFileChange}
			/>
		</div>
	);
}
