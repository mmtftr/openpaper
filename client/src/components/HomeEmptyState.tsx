"use client";

import { useState, useCallback } from "react";
import { Upload, FolderKanban, MessageSquare, Library } from "lucide-react";
import { Button } from "@/components/ui/button";
import { UploadModal } from "@/components/UploadModal";

interface HomeEmptyStateProps {
    onUploadComplete?: () => void;
    onUploadStart?: (files: File[]) => void;
    onUrlImportStart?: (url: string) => void;
}

export function HomeEmptyState({ onUploadComplete, onUploadStart, onUrlImportStart }: HomeEmptyStateProps) {
    const [isUploadModalOpen, setUploadModalOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);

    // The upload modal opens the paper itself; just refresh the lists.
    const handleUploadComplete = () => {
        onUploadComplete?.();
    };

    const handleUploadClick = () => {
        setUploadModalOpen(true);
    };

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

    const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const files = Array.from(e.dataTransfer.files).filter(
            file => file.type === 'application/pdf'
        );

        if (files.length > 0 && onUploadStart) {
            onUploadStart(files.slice(0, 1));
        }

        if (e.dataTransfer) {
            e.dataTransfer.items.clear();
        }
    }, [onUploadStart]);

    return (
        <div
            className={`mx-auto flex min-h-[60vh] max-w-2xl animate-rise-in flex-col items-center justify-center rounded-xl px-4 py-16 text-center transition-[background-color,box-shadow] duration-200 ease-out-soft ${isDragging ? 'bg-brand/5 ring-2 ring-brand/60' : ''
                }`}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
        >
            <h2 className="text-2xl font-bold text-foreground mb-3">
                Your Personal Research Assistant
            </h2>
            <p className="text-muted-foreground mb-8 max-w-md">
                Drop a PDF anywhere or click below to get started.
            </p>

            <Button
                size="lg"
                className="bg-brand text-brand-foreground hover:bg-brand/90"
                onClick={handleUploadClick}
            >
                <Upload className="h-4 w-4 mr-2" />
                Upload your first paper
            </Button>

            {/* Feature highlights */}
            <div className="mt-12 flex items-start justify-center gap-6 text-brand sm:gap-8">
                <div className="flex flex-col items-center gap-1 max-w-24">
                    <Library className="h-5 w-5" />
                    <span className="text-xs font-medium text-foreground">Library</span>
                    <span className="text-xs text-muted-foreground text-center">Store and organize your papers</span>
                </div>
                <div className="flex flex-col items-center gap-1 max-w-24">
                    <FolderKanban className="h-5 w-5" />
                    <span className="text-xs font-medium text-foreground">Projects</span>
                    <span className="text-xs text-muted-foreground text-center">Group papers by topic</span>
                </div>
                <div className="flex flex-col items-center gap-1 max-w-24">
                    <MessageSquare className="h-5 w-5" />
                    <span className="text-xs font-medium text-foreground">Chat</span>
                    <span className="text-xs text-muted-foreground text-center">Ask questions about your papers</span>
                </div>
            </div>

            <UploadModal
                open={isUploadModalOpen}
                uploadLimit={1}
                onOpenChange={setUploadModalOpen}
                onUploadComplete={handleUploadComplete}
                onUploadStart={onUploadStart}
                onUrlImportStart={onUrlImportStart}
            />
        </div>
    );
}
