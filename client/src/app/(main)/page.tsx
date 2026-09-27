"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { MessageCircleWarning } from "lucide-react";
import { useAuth } from "@/lib/auth";
import EnigmaticLoadingExperience from "@/components/EnigmaticLoadingExperience";
import { uploadFiles, uploadFromUrlWithFallback } from "@/lib/uploadUtils";

// New components for redesigned home
import { HomeSearch } from "@/components/HomeSearch";
import { QuickActions } from "@/components/QuickActions";
import { ProjectsPreview } from "@/components/ProjectsPreview";
import { RecentPapersGrid } from "@/components/RecentPapersGrid";
import { HomeEmptyState } from "@/components/HomeEmptyState";

const DEFAULT_PAPER_UPLOAD_ERROR_MESSAGE = "We encountered an error processing your request. Please check the file or URL and try again.";

export default function Home() {
	const [isUploading, setIsUploading] = useState(false);

	const [showErrorAlert, setShowErrorAlert] = useState(false);
	const [errorAlertMessage, setErrorAlertMessage] = useState(DEFAULT_PAPER_UPLOAD_ERROR_MESSAGE);

	const { user, loading: authLoading } = useAuth();
	const router = useRouter();

	// Relevant papers and projects, once signed in
	const {
		data: papersResponse,
		isLoading: isLoadingPapers,
		mutate: mutatePapers,
	} = useSWR(
		user ? ["/api/paper/relevant"] : null,
		() => unwrap(api.GET("/api/paper/relevant")),
		{ onError: (error) => console.error("Error fetching data:", error) },
	);
	const {
		data: projectsResponse,
		isLoading: isLoadingProjects,
		mutate: mutateProjects,
	} = useSWR(
		user ? ["/api/projects", { detailed: true }] : null,
		() => unwrap(api.GET("/api/projects", { params: { query: { detailed: true } } })),
		{ onError: (error) => console.error("Error fetching data:", error) },
	);
	const relevantPapers = papersResponse?.papers ?? [];
	const projects = projectsResponse ?? [];
	const isLoadingData = isLoadingPapers || isLoadingProjects;
	const [isDragging, setIsDragging] = useState(false);

	const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		e.stopPropagation();
		setIsDragging(true);
	};

	const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		e.stopPropagation();
		if (e.relatedTarget && !(e.currentTarget.contains(e.relatedTarget as Node))) {
			setIsDragging(false);
		} else if (!e.relatedTarget) {
			setIsDragging(false);
		}
	};

	const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		e.stopPropagation();
		setIsDragging(true);
	};

	const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		e.stopPropagation();
		setIsDragging(false);

		const files = Array.from(e.dataTransfer.files).filter(
			file => file.type === 'application/pdf'
		);

		if (files.length > 0) {
			handleUploadStart(files.slice(0, 1));
		}

		if (e.dataTransfer) {
			e.dataTransfer.items.clear();
		}
	};

	const refreshData = async () => {
		if (!user) return;
		await Promise.all([mutatePapers(), mutateProjects()]);
	};

	const showUploadError = (error: unknown) => {
		console.error('Error uploading paper:', error);
		setShowErrorAlert(true);
		if (error instanceof Error) {
			setErrorAlertMessage(error.message);
		} else if (typeof error === 'object' && error !== null) {
			setErrorAlertMessage(JSON.stringify(error));
		} else {
			setErrorAlertMessage(String(error));
		}
		setIsUploading(false);
	};

	// The upload returns once the PDF is stored: open the paper right away
	// (the reader works at once; its header shows the rest of ingest).
	const handleUploadStart = async (files: File[]) => {
		if (files.length === 0) return;
		setIsUploading(true);
		try {
			const [uploaded] = await uploadFiles(files.slice(0, 1));
			router.push(`/paper/${uploaded.paperId}`);
		} catch (error) {
			showUploadError(error);
		}
	};

	const handleUrlImportStart = async (url: string) => {
		setIsUploading(true);
		try {
			const uploaded = await uploadFromUrlWithFallback(url);
			router.push(`/paper/${uploaded.paperId}`);
		} catch (error) {
			showUploadError(error);
		}
	};

	if (authLoading) {
		return null;
	}

	if (!user) {
		router.push('/login');
		return null;
	}

	if (isLoadingData) {
		return null;
	}

	const hasContent = relevantPapers.length > 0 || projects.length > 0;

	return (
		<div className="flex flex-1 flex-col">
			<div
				className={`mx-auto w-full max-w-6xl flex-1 rounded-xl px-4 py-5 transition-[background-color,box-shadow] duration-200 ease-out-soft sm:px-6 sm:py-10 lg:px-8 ${isDragging ? 'bg-brand/5 ring-2 ring-brand/60' : ''}`}
				onDragEnter={handleDragEnter}
				onDragLeave={handleDragLeave}
				onDragOver={handleDragOver}
				onDrop={handleDrop}
			>
				{/* Main Content */}
				{!isLoadingData && !hasContent ? (
					<HomeEmptyState
						onUploadComplete={refreshData}
						onUploadStart={handleUploadStart}
						onUrlImportStart={handleUrlImportStart}
					/>
				) : (
					<div className="space-y-8 sm:space-y-12">
						<div className="space-y-4 sm:space-y-6">
							<HomeSearch />
							<section aria-label="Quick actions">
								<QuickActions
									onUploadComplete={refreshData}
									onProjectCreated={refreshData}
									onUploadStart={handleUploadStart}
									onUrlImportStart={handleUrlImportStart}
								/>
							</section>
						</div>

						{projects.length > 0 && (
							<section>
								<ProjectsPreview limit={4} />
							</section>
						)}

						<section>
							<RecentPapersGrid papers={relevantPapers} limit={8} />
						</section>
					</div>
				)}
			</div>

			{/* Footer */}
			<footer className="mt-auto border-t border-border/40">
				<div className="mx-auto flex max-w-6xl items-center px-4 py-5 text-sm text-muted-foreground sm:px-6 sm:py-6 lg:px-8">
					<a
						href="https://github.com/khoj-ai/openpaper"
						target="_blank"
						rel="noopener noreferrer"
						className="transition-colors hover:text-foreground"
					>
						GitHub
					</a>
				</div>
			</footer>

			{/* Error Dialog */}
			{showErrorAlert && (
				<Dialog open={showErrorAlert} onOpenChange={setShowErrorAlert}>
					<DialogContent>
						<DialogTitle>Upload Failed</DialogTitle>
						<DialogDescription className="space-y-4 inline-flex items-center">
							<MessageCircleWarning className="h-6 w-6 text-slate-500 mr-2 flex-shrink-0" />
							{errorAlertMessage ?? DEFAULT_PAPER_UPLOAD_ERROR_MESSAGE}
						</DialogDescription>
					</DialogContent>
				</Dialog>
			)}

			{/* Upload Progress Dialog */}
			<Dialog open={isUploading} onOpenChange={(open) => !open && setIsUploading(false)}>
				<DialogContent
					className="sm:max-w-md"
					showCloseButton={false}
					onInteractOutside={(e) => {
						e.preventDefault();
					}}>
					<DialogHeader>
						<DialogTitle className="text-center">Uploading Your Paper</DialogTitle>
						<DialogDescription className="text-center">
							It opens as soon as the PDF is stored.
						</DialogDescription>
					</DialogHeader>
					<div className="flex flex-col items-center justify-center py-8 w-full">
						<EnigmaticLoadingExperience />
					</div>
				</DialogContent>
			</Dialog>
		</div>
	);
}
