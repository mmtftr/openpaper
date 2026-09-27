"use client";

import React, { useState, useRef, useCallback } from 'react';
import { Button } from "@/components/ui/button";
import { UploadCloud } from 'lucide-react';
import { MAX_UPLOAD_SIZE_MB } from '@/lib/uploadUtils';

const MAX_PAPERS_TO_UPLOAD = 10;

interface PdfDropzoneProps {
    onFileSelect: (files: File[]) => void;
    onUrlClick: () => void;
    maxSizeMb?: number;
    disabled?: boolean;
    maxPapers?: number;
}

export function PdfDropzone({ onFileSelect, onUrlClick, maxSizeMb = MAX_UPLOAD_SIZE_MB, disabled = false, maxPapers = MAX_PAPERS_TO_UPLOAD }: PdfDropzoneProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const maxSize = maxSizeMb * 1024 * 1024; // Convert MB to bytes

    const handleFileValidation = (file: File): boolean => {
        if (file.type !== 'application/pdf') {
            setError('Invalid file type. Please upload a PDF.');
            return false;
        }
        if (file.size > maxSize) {
            setError(`File size exceeds the ${maxSizeMb}MB limit.`);
            return false;
        }
        return true;
    };

    const processFiles = (files: File[]) => {
        if (files.length > maxPapers) {
            setError(`You can upload a maximum of ${maxPapers} papers at a time.`);
            return;
        }
        const validFiles = files.filter(handleFileValidation);
        if (validFiles.length > 0) {
            onFileSelect(validFiles);
        }
    };

    const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.relatedTarget && !(e.currentTarget.contains(e.relatedTarget as Node))) {
            setIsDragging(false);
        } else if (!e.relatedTarget) {
            setIsDragging(false);
        }
    };

    const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
        setError(null);

        const files = Array.from(e.dataTransfer.files);
        processFiles(files);

        if (e.dataTransfer) {
            e.dataTransfer.items.clear();
        }
    }, [onFileSelect, maxSize, maxSizeMb, disabled, maxPapers]);

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (disabled) return;
        const files = Array.from(e.target.files || []);
        processFiles(files);

        if (e.target) {
            e.target.value = '';
        }
    };

    const handleClick = () => {
        if (disabled) return;
        fileInputRef.current?.click();
    };

    return (
        <div className="flex flex-col items-center space-y-6 w-full max-w-lg mx-auto">
            <div
                role="button"
                tabIndex={disabled ? -1 : 0}
                aria-disabled={disabled}
                onClick={handleClick}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleClick();
                    }
                }}
                onDrop={handleDrop}
                onDragOver={handleDragOver}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                className={`flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition-[border-color,background-color] duration-200 ease-out-soft focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 sm:p-8
                    ${isDragging && !disabled ? 'border-brand bg-brand/10' : 'border-border'}
                    ${error ? 'border-destructive' : ''}
                    ${disabled ? 'cursor-not-allowed bg-secondary/50' : 'cursor-pointer hover:border-brand/40 hover:bg-brand/[0.04]'}
                `}
                style={{ minHeight: '200px' }}
            >
                <input
                    type="file"
                    ref={fileInputRef}
                    accept=".pdf"
                    className="hidden"
                    onChange={handleFileInputChange}
                    multiple={maxPapers > 1}
                    disabled={disabled}
                />
                <UploadCloud className={`mb-4 h-12 w-12 transition-[color,transform] duration-200 ease-out-soft ${isDragging && !disabled ? 'scale-110 text-brand' : 'text-muted-foreground'}`} />
                <p className="text-base font-medium sm:text-lg">
                    Click to upload, or drag and drop
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                    {maxPapers === 1
                        ? `Upload a paper up to ${maxSizeMb}MB`
                        : `Select up to ${maxPapers} papers, up to ${maxSizeMb}MB each`}
                </p>
                {error && <p className="text-sm text-destructive mt-2">{error}</p>}
            </div>
            <div className="flex items-center w-full">
                <div className="flex-grow border-t border-border"></div>
                <span className="flex-shrink mx-4 text-muted-foreground text-sm">or</span>
                <div className="flex-grow border-t border-border"></div>
            </div>
            <Button variant="outline" onClick={onUrlClick} disabled={disabled}>
                Import from URL
            </Button>
        </div>
    );
}
