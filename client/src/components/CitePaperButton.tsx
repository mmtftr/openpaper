"use client";

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle, DrawerTrigger } from '@/components/ui/drawer';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { citationStyles, copyToClipboard, PaperBase } from '@/components/utils/paperUtils';
import { useIsMobile } from '@/hooks/use-mobile';
import { useFeatureGate, useStageRefreshKey } from '@/hooks/useIngest';
import { api, unwrap } from '@/lib/api/client';
import { Check, Copy, Loader, Quote } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { toast } from 'sonner';

/** The citation fields of any paper shape the API returns (detail, library, project list). */
interface CitablePaper {
    id?: string;
    title?: string | null;
    authors?: string[] | null;
    publish_date?: string | null;
    created_at?: string | null;
    journal?: string | null;
    publisher?: string | null;
    doi?: string | null;
}

interface CitePaperButtonProps {
    paper?: CitablePaper[];
    paperId?: string;
    minimalist?: boolean;
    variant?: "ghost" | "outline";
    iconOnly?: boolean;
}

export function CitePaperButton({ paper, paperId: providedPaperId, minimalist = false, variant = "ghost", iconOnly = false }: CitePaperButtonProps) {
    const pathname = usePathname();
    const [derivedPaperId, setDerivedPaperId] = useState<string | null>(null);
    const [isOpen, setIsOpen] = useState(false);
    const [selectedStyle, setSelectedStyle] = useState<string>(() => {
        if (typeof window !== 'undefined') {
            const saved = localStorage.getItem('citationStyle');
            // Validate saved preference exists in current options, otherwise use default
            const isValid = saved && citationStyles.some(style => style.name === saved);
            return isValid ? saved : citationStyles[0].name;
        }
        return citationStyles[0].name;
    });
    const [copied, setCopied] = useState(false);
    const isMobile = useIsMobile();

    // Determine the paper ID to use (only for single paper mode)
    const effectivePaperId = providedPaperId || derivedPaperId;

    // A single paper looked up by id waits for ingest's metadata stages
    // (title / authors / DOI), and is re-read when they finish.
    const ingestPaperId = paper ? null : effectivePaperId;
    const metadataGate = useFeatureGate(ingestPaperId, "metadata");
    const metadataRefreshKey = useStageRefreshKey(ingestPaperId, ["metadata", "metadata_fallback"]);
    const metadataBlocked = metadataGate.ready && !metadataGate.enabled ? metadataGate.message : null;

    // Without paper data from props, fetch the paper once the dialog opens.
    const fetchPaperId = !(paper && paper.length > 0) && isOpen ? effectivePaperId : null;
    const { data: fetchedPaper } = useSWR(
        fetchPaperId ? ["/api/paper", fetchPaperId, metadataRefreshKey] : null,
        ([, id]) => unwrap(api.GET("/api/paper", { params: { query: { id } } })),
        { keepPreviousData: true, onError: () => toast.error("Failed to fetch paper details.") },
    );
    const paperData: CitablePaper[] | null =
        paper && paper.length > 0 ? paper : fetchedPaper ? [fetchedPaper] : paper ?? null;

    // Check if we're in bibliography mode (more than one paper)
    const isBibliography = paperData && paperData.length > 1;

    // Save selected style to localStorage whenever it changes
    useEffect(() => {
        if (typeof window !== 'undefined') {
            localStorage.setItem('citationStyle', selectedStyle);
        }
    }, [selectedStyle]);

    useEffect(() => {
        // Paper data from props is used directly
        if (paper) return;

        // Otherwise, try to derive paper ID from pathname (single paper mode only)
        if (pathname && !providedPaperId) {
            const segments = pathname.split('/');
            if (segments[1] === 'paper' && segments.length === 3 && segments[2]) {
                setDerivedPaperId(segments[2]);
            } else {
                setDerivedPaperId(null);
            }
        }
    }, [pathname, paper, providedPaperId]);

    if (!effectivePaperId && !paper) {
        return null;
    }

    if (metadataBlocked) {
        return (
            <span title={metadataBlocked} className="inline-flex">
                <Button
                    variant={iconOnly ? "ghost" : variant}
                    size="sm"
                    disabled
                    aria-label={`Cite: ${metadataBlocked}`}
                    className={iconOnly ? "h-7 w-7 p-0" : variant === "outline" ? "h-8 px-3 text-xs" : ""}
                >
                    <Quote className="h-3.5 w-3.5" />
                    {!iconOnly && <span>Cite</span>}
                </Button>
            </span>
        );
    }

    const triggerButton = iconOnly ? (
        <Button variant="ghost" className="h-7 w-7 p-0 rounded-md text-secondary-foreground hover:bg-blue-100 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-foreground">
            <Quote className="h-4 w-4" />
        </Button>
    ) : (
        <Button variant={variant} size="sm" className={variant === "outline" ? "h-8 px-3 text-xs" : ""}>
            {(!minimalist || variant === "outline") && <Quote className="h-3.5 w-3.5 mr-1.5" />}
            <span className={minimalist && variant !== "outline" ? "text-sm" : ""}>{isBibliography ? 'Bibliography' : 'Cite'}</span>
        </Button>
    );

    const content = (
        <div className="grid gap-4">
            {!paperData ? (
                <div className="flex items-center justify-center h-24">
                    <Loader className="animate-spin h-6 w-6" />
                </div>
            ) : (
                <div className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Citation Style</label>
                        <Select value={selectedStyle} onValueChange={setSelectedStyle}>
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select citation style" />
                            </SelectTrigger>
                            <SelectContent>
                                {citationStyles.map((style) => (
                                    <SelectItem key={style.name} value={style.name}>
                                        {style.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {(() => {
                        const selectedStyleObj = citationStyles.find(s => s.name === selectedStyle);
                        if (!selectedStyleObj || !paperData) return null;

                        const paperAsPaperBase = (p: CitablePaper, id?: string): PaperBase => ({
                            id: id || p.id || '',
                            title: p.title || '',
                            authors: p.authors || [],
                            created_at: p.publish_date || p.created_at || undefined,
                            journal: p.journal ?? undefined,
                            publisher: p.publisher ?? undefined,
                            doi: p.doi ?? undefined,
                        });

                        // Generate citation(s) - special handling for single paper (length === 1)
                        let citation: string;
                        if (paperData.length === 1) {
                            // Single paper citation
                            const singlePaper = paperData[0];
                            const paperBase = paperAsPaperBase(singlePaper, effectivePaperId || undefined);
                            citation = selectedStyleObj.generator(paperBase);
                        } else {
                            // Generate bibliography from multiple papers
                            citation = paperData.map((p, index) => {
                                const paperBase = paperAsPaperBase(p);
                                const singleCitation = selectedStyleObj.generator(paperBase);
                                // For numbered styles like IEEE, add numbering
                                if (selectedStyle === 'IEEE') {
                                    return `[${index + 1}] ${singleCitation}`;
                                }
                                return singleCitation;
                            }).join('\n\n');
                        }

                        return (
                            <div className="space-y-2">
                                <div className="text-xs bg-muted p-3 rounded overflow-x-auto overflow-y-auto max-h-96 whitespace-pre-wrap">
                                    {citation}
                                </div>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="w-full"
                                    onClick={() => {
                                        copyToClipboard(citation, selectedStyle);
                                        setCopied(true);
                                        setTimeout(() => setCopied(false), 2000);
                                    }}
                                >
                                    {copied ? (
                                        <>
                                            <Check className="h-4 w-4 mr-2" />
                                            Copied
                                        </>
                                    ) : (
                                        <>
                                            <Copy className="h-4 w-4 mr-2" />
                                            Copy
                                        </>
                                    )}
                                </Button>
                            </div>
                        );
                    })()}
                </div>
            )}
        </div>
    );

    if (isMobile) {
        return (
            <Drawer open={isOpen} onOpenChange={setIsOpen}>
                <DrawerTrigger asChild>
                    {triggerButton}
                </DrawerTrigger>
                <DrawerContent>
                    <DrawerHeader>
                        <DrawerTitle>{isBibliography ? 'Bibliography' : 'Cite Paper'}</DrawerTitle>
                    </DrawerHeader>
                    <div className="px-4 pb-4">
                        {content}
                    </div>
                </DrawerContent>
            </Drawer>
        );
    }

    return (
        <Dialog open={isOpen} onOpenChange={setIsOpen}>
            <DialogTrigger asChild>
                {triggerButton}
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{isBibliography ? 'Bibliography' : 'Cite Paper'}</DialogTitle>
                </DialogHeader>
                {content}
            </DialogContent>
        </Dialog>
    );
}
