"use client";

import { useState } from "react";
import { Info } from "lucide-react";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { PaperData } from "@/lib/schema";
import { isDateValid } from "@/lib/utils";
import { IngestStatusDialog, IngestStatusInfoButton } from "@/components/ingest/IngestStatus";

interface MetadataPopoverProps {
    paperData: PaperData;
}

const googleScholarUrl = (q: string) =>
    `https://scholar.google.com/scholar?q=${encodeURIComponent(q)}`;

export function MetadataPopover({ paperData }: MetadataPopoverProps) {
    const authors = paperData.authors ?? [];
    const institutions = paperData.institutions ?? [];
    const publishDate = paperData.publish_date;
    const hasAuthors = authors.length > 0;
    const hasInstitutions = institutions.length > 0;
    const hasDate = !!publishDate && isDateValid(publishDate);
    const [open, setOpen] = useState(false);
    // Lives outside the popover: the popover closes as the dialog opens.
    const [processingOpen, setProcessingOpen] = useState(false);

    return (
        <>
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground hover:text-foreground"
                    title="Paper info"
                    aria-label="Paper info"
                >
                    <Info className="size-4" aria-hidden="true" />
                </Button>
            </PopoverTrigger>
            <PopoverContent
                align="end"
                side="bottom"
                className="w-80 max-h-[60vh] overflow-y-auto"
            >
                <div className="space-y-3">
                    <h3 className="text-sm font-semibold leading-snug">
                        {paperData.title}
                    </h3>
                    {hasDate && (
                        <div className="text-xs text-muted-foreground">
                            {new Date(publishDate).toLocaleDateString()}
                        </div>
                    )}
                    {hasAuthors && (
                        <div className="space-y-1">
                            <div className="text-xs font-medium text-muted-foreground">
                                Authors
                            </div>
                            <div className="flex flex-wrap gap-x-2 gap-y-1">
                                {authors.map((a, i) => (
                                    <a
                                        key={i}
                                        href={googleScholarUrl(a)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                                    >
                                        {a}
                                    </a>
                                ))}
                            </div>
                        </div>
                    )}
                    {hasInstitutions && (
                        <div className="space-y-1">
                            <div className="text-xs font-medium text-muted-foreground">
                                Institutions
                            </div>
                            <div className="flex flex-wrap gap-x-2 gap-y-1">
                                {institutions.map((inst, i) => (
                                    <a
                                        key={i}
                                        href={googleScholarUrl(inst)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                                    >
                                        {inst}
                                    </a>
                                ))}
                            </div>
                        </div>
                    )}
                    {!hasAuthors && !hasInstitutions && !hasDate && (
                        <p className="text-xs text-muted-foreground">
                            No additional metadata available.
                        </p>
                    )}
                    <IngestStatusInfoButton
                        onOpen={() => {
                            setOpen(false);
                            setProcessingOpen(true);
                        }}
                    />
                </div>
            </PopoverContent>
        </Popover>
        <IngestStatusDialog open={processingOpen} onOpenChange={setProcessingOpen} />
        </>
    );
}
