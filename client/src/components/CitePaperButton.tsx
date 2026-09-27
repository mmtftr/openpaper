"use client";

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle, DrawerTrigger } from '@/components/ui/drawer';
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
export interface CitablePaper {
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
        <Button
            variant={variant}
            size="sm"
            className={cn(variant === "outline" && "h-8 px-3 text-xs")}
            aria-label={isBibliography ? 'Bibliography' : 'Cite'}
        >
            {(!minimalist || variant === "outline") && <Quote className="h-3.5 w-3.5 mr-1.5" />}
            <span className={cn(minimalist && variant !== "outline" && "text-sm")}>{isBibliography ? 'Bibliography' : 'Cite'}</span>
        </Button>
    );

    const content = !paperData ? (
        <div className="flex items-center justify-center h-24">
            <Loader className="animate-spin h-6 w-6" />
        </div>
    ) : (
        <CitationView papers={paperData} paperId={effectivePaperId ?? undefined} />
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

/** Short chip labels; the full style name is the chip's tooltip and the copy toast. */
const STYLE_LABELS: Record<string, string> = {
    'MLA 9th Edition': 'MLA',
    'Chicago 17th (Author-Date)': 'Chicago',
    'APA 7th Edition': 'APA',
    'AMA 11th Edition': 'AMA',
};

function savedCitationStyle(): string {
    if (typeof window === 'undefined') return citationStyles[0].name;
    const saved = localStorage.getItem('citationStyle');
    // A saved style that no longer exists falls back to the default.
    return saved && citationStyles.some(style => style.name === saved) ? saved : citationStyles[0].name;
}

const toPaperBase = (p: CitablePaper, id?: string): PaperBase => ({
    id: id || p.id || '',
    title: p.title || '',
    authors: p.authors || [],
    created_at: p.publish_date || p.created_at || undefined,
    journal: p.journal ?? undefined,
    publisher: p.publisher ?? undefined,
    doi: p.doi ?? undefined,
});

/**
 * Style picker (inline chips, no nested menu), the formatted citation or
 * bibliography, and a copy button. The chosen style is remembered.
 */
export function CitationView({ papers, paperId, className }: { papers: CitablePaper[]; paperId?: string; className?: string }) {
    const [selectedStyle, setSelectedStyle] = useState(savedCitationStyle);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        localStorage.setItem('citationStyle', selectedStyle);
    }, [selectedStyle]);

    useEffect(() => {
        if (!copied) return;
        const timer = setTimeout(() => setCopied(false), 2000);
        return () => clearTimeout(timer);
    }, [copied]);

    const style = citationStyles.find(s => s.name === selectedStyle) ?? citationStyles[0];
    const citation = papers.length === 1
        ? style.generator(toPaperBase(papers[0], paperId))
        : papers
            .map((p, index) => {
                const single = style.generator(toPaperBase(p));
                // Numbered styles get their numbers.
                return style.name === 'IEEE' ? `[${index + 1}] ${single}` : single;
            })
            .join('\n\n');

    return (
        <div className={cn("space-y-2", className)}>
            <div role="radiogroup" aria-label="Citation style" className="flex flex-wrap gap-1">
                {citationStyles.map(({ name }) => {
                    const active = name === style.name;
                    return (
                        <button
                            key={name}
                            type="button"
                            role="radio"
                            aria-checked={active}
                            title={name}
                            onClick={() => {
                                setSelectedStyle(name);
                                setCopied(false);
                            }}
                            className={cn(
                                "rounded-md border px-2 py-1 text-xs transition-colors duration-150 ease-out-soft focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 max-md:py-1.5",
                                active
                                    ? "border-brand/40 bg-brand/10 font-medium text-foreground"
                                    : "border-border/70 text-muted-foreground hover:bg-accent hover:text-foreground"
                            )}
                        >
                            {STYLE_LABELS[name] ?? name}
                        </button>
                    );
                })}
            </div>
            <div className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-muted p-2.5 text-xs leading-relaxed select-text">
                {citation}
            </div>
            <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => {
                    copyToClipboard(citation, style.name);
                    setCopied(true);
                }}
            >
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? 'Copied' : `Copy ${STYLE_LABELS[style.name] ?? style.name}`}
            </Button>
        </div>
    );
}
