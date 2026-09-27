
"use client"

import { z } from "zod";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { PdfDropzone } from "@/components/PdfDropzone"
import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { FileText } from "lucide-react"
import { uploadFiles, uploadFromUrlWithFallback, type UploadedPaper } from "@/lib/uploadUtils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import LoadingIndicator from "@/components/utils/Loading"
import { isEditableTarget, parsePaperLink } from "@/lib/paperLink"

interface UploadModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    uploadLimit?: number;
    /** Called for each uploaded paper (its PDF is readable at once). A single upload also opens it. */
    onUploadComplete?: (paperId: string) => void;
    /** Called when files are selected, allowing parent to handle upload with custom loading experience */
    onUploadStart?: (files: File[]) => void;
    /** Called when URL import is initiated, allowing parent to handle with custom loading experience */
    onUrlImportStart?: (url: string) => void;
}

function UrlImportDialog({
    open,
    onOpenChange,
    onImport,
    initialUrl,
}: {
    open: boolean
    onOpenChange: (open: boolean) => void
    onImport: (url: string) => void
    /** Prefilled when opened by pasting a link into the upload dialog. */
    initialUrl?: string
}) {
    const [url, setUrl] = useState("");
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    useEffect(() => {
        if (open && initialUrl) setUrl(initialUrl);
    }, [open, initialUrl]);

    const urlSchema = z.string().url({ message: "Please enter a valid URL." });
    const arxiv = parsePaperLink(url, { bareIds: true });

    const handleSubmit = () => {
        const result = urlSchema.safeParse(url);
        if (arxiv?.kind === "arxiv" || result.success) {
            onImport(url);
            onOpenChange(false);
            setUrl("");
            setErrorMessage(null);
        } else {
            setErrorMessage(result.error.issues[0].message);
        }
    }

    const handleOpenChange = (newOpen: boolean) => {
        if (!newOpen) {
            setUrl("");
            setErrorMessage(null);
        }
        onOpenChange(newOpen);
    }

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Import from URL</DialogTitle>
                    <DialogDescription>
                        Paste an arXiv link or the URL of a PDF.
                    </DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 py-4">
                    <Input
                        type="url"
                        inputMode="url"
                        autoFocus
                        aria-label="PDF URL"
                        placeholder="https://arxiv.org/pdf/..."
                        value={url}
                        onChange={e => setUrl(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && handleSubmit()}
                    />
                    {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}
                    <Button onClick={handleSubmit}>
                        {arxiv?.kind === "arxiv" ? `Import from arXiv: ${arxiv.id}${arxiv.version}` : "Import"}
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    )
}

const DEFAULT_UPLOAD_LIMIT = 10;

export function UploadModal({ open, onOpenChange, uploadLimit = DEFAULT_UPLOAD_LIMIT, onUploadComplete, onUploadStart, onUrlImportStart }: UploadModalProps) {
    const router = useRouter();
    const [uploaded, setUploaded] = useState<UploadedPaper[]>([]);
    const [isUrlDialogOpen, setIsUrlDialogOpen] = useState(false);
    const [importError, setImportError] = useState<string | null>(null);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [pastedUrl, setPastedUrl] = useState<string | undefined>();
    const UPLOAD_LIMIT = uploadLimit;

    // Pasting a paper link into the dialog offers to import it.
    const handlePaste = (e: React.ClipboardEvent) => {
        if (isEditableTarget(e.target) || isSubmitting) return;
        const link = parsePaperLink(e.clipboardData.getData("text/plain"));
        if (!link) return;
        e.preventDefault();
        setPastedUrl(link.text);
        onUrlClick();
    };

    // Papers are readable as soon as the upload returns: open a single one
    // right away, list several so each can be opened.
    const finish = (papers: UploadedPaper[]) => {
        papers.forEach(p => onUploadComplete?.(p.paperId));
        if (papers.length === 1 && uploaded.length === 0) {
            onOpenChange(false);
            router.push(`/paper/${papers[0].paperId}`);
            return;
        }
        setUploaded(prev => [...prev, ...papers]);
    }

    const handleFileSelect = async (files: File[]) => {
        setImportError(null);
        if (uploaded.length + files.length > UPLOAD_LIMIT) {
            setImportError(`This would exceed the upload limit of ${UPLOAD_LIMIT} files.`);
            return;
        }

        // If parent wants to handle upload, close modal and delegate
        if (onUploadStart) {
            onOpenChange(false);
            onUploadStart(files);
            return;
        }

        setIsSubmitting(true);
        try {
            finish(await uploadFiles(files));
        } catch (error) {
            console.error("Failed to upload file", error);
            const errorMessage = error instanceof Error ? error.message : "Failed to upload the file. Please try again.";
            setImportError(errorMessage);
        } finally {
            setIsSubmitting(false);
        }
    }

    const onUrlClick = () => {
        if (uploaded.length >= UPLOAD_LIMIT) {
            setImportError(`You have reached the upload limit of ${UPLOAD_LIMIT} files.`);
            return;
        }
        setIsUrlDialogOpen(true);
    }

    const handleUrlImport = async (url: string) => {
        // If parent wants to handle upload, close modal and delegate
        if (onUrlImportStart) {
            onOpenChange(false);
            onUrlImportStart(url);
            return;
        }

        try {
            setImportError(null);
            if (uploaded.length >= UPLOAD_LIMIT) {
                setImportError(`You have reached the upload limit of ${UPLOAD_LIMIT} files.`);
                return;
            }
            setIsSubmitting(true);
            finish([await uploadFromUrlWithFallback(url)]);
        } catch (error) {
            console.error("Failed to import from URL", error);
            const errorMessage = error instanceof Error ? error.message : "Failed to import from URL. Please check the URL and try again.";
            setImportError(errorMessage);
        } finally {
            setIsSubmitting(false);
        }
    }

    return (
        <>
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className="sm:max-w-xl" onPaste={handlePaste}>
                    <DialogHeader>
                        <DialogTitle>Upload Papers</DialogTitle>
                        <DialogDescription>
                            Each paper opens right away; OCR, metadata and highlights finish in the background.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        {isSubmitting ? (
                            <div className="flex flex-col items-center justify-center h-64 space-y-4">
                                <LoadingIndicator />
                                <p className="text-sm text-muted-foreground">Uploading your papers...</p>
                            </div>
                        ) : (
                            <PdfDropzone
                                maxPapers={UPLOAD_LIMIT - uploaded.length}
                                disabled={uploaded.length >= UPLOAD_LIMIT}
                                onFileSelect={handleFileSelect}
                                onUrlClick={onUrlClick}
                            />
                        )}
                        {importError && (
                            <p className="text-sm text-destructive">{importError}</p>
                        )}
                        {uploaded.length > 0 && (
                            <ul className="space-y-1">
                                {uploaded.map(p => (
                                    <li key={p.paperId}>
                                        <Link
                                            href={`/paper/${p.paperId}`}
                                            className="flex items-center gap-2 text-sm hover:underline"
                                        >
                                            <FileText className="h-4 w-4 text-muted-foreground" />
                                            <span className="truncate">{p.fileName}</span>
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </DialogContent>
            </Dialog>
            <UrlImportDialog
                open={isUrlDialogOpen}
                onOpenChange={(next) => {
                    setIsUrlDialogOpen(next);
                    if (!next) setPastedUrl(undefined);
                }}
                onImport={handleUrlImport}
                initialUrl={pastedUrl}
            />
        </>
    )
}
