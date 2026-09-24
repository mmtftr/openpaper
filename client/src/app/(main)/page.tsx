"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import { fetchFromApi } from "@/lib/api";
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
import { PaperItem, PaperUploadJobStatusResponse, Project } from "@/lib/schema";
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

	const [relevantPapers, setRelevantPapers] = useState<PaperItem[]>([]);
	const [projects, setProjects] = useState<Project[]>([]);
	const [isLoadingData, setIsLoadingData] = useState(true);
	const [showErrorAlert, setShowErrorAlert] = useState(false);
	const [errorAlertMessage, setErrorAlertMessage] = useState(DEFAULT_PAPER_UPLOAD_ERROR_MESSAGE);

	const { user, loading: authLoading } = useAuth();
	const router = useRouter();
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

	// Loading experience state
	const [elapsedTime, setElapsedTime] = useState(0);
	const [messageIndex, setMessageIndex] = useState(0);
	const [fileSize, setFileSize] = useState<number | null>(null);
	const [displayedMessage, setDisplayedMessage] = useState("");
	const [celeryMessage, setCeleryMessage] = useState<string | null>(null);

	const celeryMessageRef = useRef<string | null>(null);

	useEffect(() => {
		celeryMessageRef.current = celeryMessage;
	}, [celeryMessage]);

	const loadingMessages = useMemo(() => [
		`Processing bits and bytes...`,
		`Processing ${fileSize ? (fileSize / 1024 / 1024).toFixed(2) + 'mb' : '...'} `,
		"Uploading to the cloud",
		"Extracting metadata",
		"Crafting grounded citations",
	], [fileSize]);

	// Effect for timer and message cycling
	useEffect(() => {
		let timer: NodeJS.Timeout | undefined;
		let messageTimer: NodeJS.Timeout | undefined;

		if (isUploading) {
			setElapsedTime(0);
			setMessageIndex(0);

			timer = setInterval(() => {
				setElapsedTime((prevTime) => prevTime + 1);
			}, 1000);

			messageTimer = setInterval(() => {
				if (celeryMessageRef.current) {
					if (messageTimer) {
						clearInterval(messageTimer);
						messageTimer = undefined;
					}
					return;
				}

				setMessageIndex((prevIndex) => {
					if (prevIndex < loadingMessages.length - 1) {
						return prevIndex + 1;
					}
					return prevIndex;
				});
			}, 8000);
		}

		return () => {
			if (timer) clearInterval(timer);
			if (messageTimer) clearInterval(messageTimer);
		};
	}, [isUploading, fileSize, loadingMessages]);

	// Typewriter effect
	useEffect(() => {
		setDisplayedMessage("");
		let i = 0;
		const typingTimer = setInterval(() => {
			const currentMessage = celeryMessage || loadingMessages[messageIndex];
			if (i < currentMessage.length) {
				setDisplayedMessage(currentMessage.slice(0, i + 1));
				i++;
			} else {
				clearInterval(typingTimer);
			}
		}, 50);

		return () => clearInterval(typingTimer);
	}, [messageIndex, loadingMessages, celeryMessage]);

	// Poll job status
	const pollJobStatus = async (jobId: string) => {
		try {
			const response: PaperUploadJobStatusResponse = await fetchFromApi(`/api/paper/upload/status/${jobId}`);

			if (response.celery_progress_message) {
				setCeleryMessage(response.celery_progress_message);
			}

			if (response.paper_id) {
				const redirectUrl = new URL(`/paper/${response.paper_id}`, window.location.origin);
				redirectUrl.searchParams.append('job_id', jobId);
				setTimeout(() => {
					window.location.href = redirectUrl.toString();
				}, 500);
			} else if (response.status === 'failed') {
				console.error('Upload job failed');
				setShowErrorAlert(true);
				setIsUploading(false);
			} else {
				setTimeout(() => pollJobStatus(jobId), 2000);
			}
		} catch (error) {
			console.error('Error polling job status:', error);
			setShowErrorAlert(true);
			setIsUploading(false);
		}
	};

	// Fetch papers and projects
	useEffect(() => {
		if (!user) {
			setIsLoadingData(false);
			return;
		}

		const fetchData = async () => {
			setIsLoadingData(true);
			try {
				const [papersResponse, projectsResponse] = await Promise.all([
					fetchFromApi("/api/paper/relevant"),
					fetchFromApi("/api/projects?detailed=true")
				]);
				setRelevantPapers(papersResponse?.papers || []);
				setProjects(projectsResponse || []);
			} catch (error) {
				console.error("Error fetching data:", error);
				setRelevantPapers([]);
				setProjects([]);
			} finally {
				setIsLoadingData(false);
			}
		};

		fetchData();
	}, [user]);

	const refreshData = async () => {
		if (!user) return;
		try {
			const [papersResponse, projectsResponse] = await Promise.all([
				fetchFromApi("/api/paper/relevant"),
				fetchFromApi("/api/projects?detailed=true")
			]);
			setRelevantPapers(papersResponse?.papers || []);
			setProjects(projectsResponse || []);
		} catch (error) {
			console.error("Error refreshing data:", error);
		}
	};

	// Handle file upload with custom loading experience
	const handleUploadStart = async (files: File[]) => {
		if (files.length === 0) return;

		const file = files[0];
		setIsUploading(true);
		setFileSize(file.size);
		setCeleryMessage(null);
		setMessageIndex(0);

		try {
			const jobs = await uploadFiles(files);
			if (jobs.length > 0) {
				pollJobStatus(jobs[0].jobId);
			}
		} catch (error) {
			console.error('Error uploading file:', error);
			setShowErrorAlert(true);
			if (error instanceof Error) {
				setErrorAlertMessage(error.message);
			} else if (typeof error === 'object' && error !== null) {
				setErrorAlertMessage(JSON.stringify(error));
			} else {
				setErrorAlertMessage(String(error));
			}
			setIsUploading(false);
		}
	};

	// Handle URL import with custom loading experience
	const handleUrlImportStart = async (url: string) => {
		setIsUploading(true);
		setFileSize(null);
		setCeleryMessage(null);
		setMessageIndex(0);

		try {
			const job = await uploadFromUrlWithFallback(url);
			pollJobStatus(job.jobId);
		} catch (error) {
			setShowErrorAlert(true);
			if (error instanceof Error) {
				setErrorAlertMessage(error.message);
			} else if (typeof error === 'object' && error !== null) {
				setErrorAlertMessage(JSON.stringify(error));
			} else {
				setErrorAlertMessage(String(error));
			}
			setIsUploading(false);
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
		<div className="min-h-[calc(100vh-64px)] bg-gradient-to-b from-background to-muted/20 flex flex-col">
			<div
				className={`max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 sm:py-12 flex-1 w-full rounded-xl transition-colors duration-200 ${isDragging ? 'bg-primary/5 ring-2 ring-primary ring-dashed' : ''}`}
				onDragEnter={handleDragEnter}
				onDragLeave={handleDragLeave}
				onDragOver={handleDragOver}
				onDrop={handleDrop}
			>
				{/* Header with branding and search */}
				<header className="mb-10">
					<div className="flex flex-col items-center gap-6">
						{/* Search Bar */}
						{hasContent && <HomeSearch />}
					</div>
				</header>

				{/* Main Content */}
				{!isLoadingData && !hasContent ? (
					<HomeEmptyState
						onUploadComplete={refreshData}
						onUploadStart={handleUploadStart}
						onUrlImportStart={handleUrlImportStart}
					/>
				) : (
					<div className="space-y-12">
						{/* Quick Actions */}
						<section>
							<QuickActions
								onUploadComplete={refreshData}
								onProjectCreated={refreshData}
								onUploadStart={handleUploadStart}
								onUrlImportStart={handleUrlImportStart}
							/>
						</section>

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
				<div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
					<div className="flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-muted-foreground">
						<div className="flex items-center gap-4">
							<a
								href="https://github.com/khoj-ai/openpaper"
								target="_blank"
								rel="noopener noreferrer"
								className="hover:text-foreground transition-colors"
							>
								GitHub
							</a>
						</div>
					</div>
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
						<DialogTitle className="text-center">Processing Your Paper</DialogTitle>
						<DialogDescription className="text-center">
							This might take up to two minutes...
						</DialogDescription>
					</DialogHeader>
					<div className="flex flex-col items-center justify-center py-8 space-y-6 w-full">
						<EnigmaticLoadingExperience />
						<div className="flex items-center gap-3">
							<div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
							<p className="text-sm text-muted-foreground">{displayedMessage}</p>
							<span className="text-xs text-muted-foreground/50 tabular-nums">{elapsedTime}s</span>
						</div>
					</div>
				</DialogContent>
			</Dialog>
		</div>
	);
}
