'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { ChevronDown, FileText, Loader } from 'lucide-react';

import { fetchFromApi } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useIsMobile } from '@/hooks/use-mobile';
import { SupplementaryMaterialSummary } from '@/lib/schema';

const CrepeMarkdownReader = dynamic(() => import('./PaperMarkdownReaderImpl'), {
    ssr: false,
    loading: () => (
        <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
            <Loader className="mr-2 h-4 w-4 animate-spin" />
            Rendering markdown...
        </div>
    ),
});

interface PaperMarkdownReaderProps {
    endpoint: string;
    title?: string;
    paperId?: string;
    // Supplementary switcher props (mirror PdfToolbar). Only rendered when
    // parentPaperId is set.
    parentPaperId?: string;
    displayedPaperId?: string;
    parentPaperTitle?: string;
    supplementaryMaterials?: SupplementaryMaterialSummary[];
    onChangeDisplayed?: (paperId: string) => void;
}

interface PaperMarkdownResponse {
    markdown: string;
    source: 'mistral' | 'pymupdf';
}

function resolveMarkdownImageUrls(markdown: string, paperId?: string) {
    if (!paperId) return markdown;

    return markdown.replace(/!\[([^\]]*)\]\(([^)\s]+)(\s+"[^"]*")?\)/g, (match, alt, src, title = '') => {
        if (src.startsWith('http') || src.startsWith('/') || src.startsWith('data:')) return match;
        const figureUrl = `/api/paper/${encodeURIComponent(paperId)}/figure/${encodeURIComponent(src)}`;
        return `![${alt}](${figureUrl}${title})`;
    });
}

export function PaperMarkdownReader({
    endpoint,
    title,
    paperId,
    parentPaperId,
    displayedPaperId,
    parentPaperTitle,
    supplementaryMaterials = [],
    onChangeDisplayed,
}: PaperMarkdownReaderProps) {
    const isMobile = useIsMobile();
    const [data, setData] = useState<PaperMarkdownResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [switcherOpen, setSwitcherOpen] = useState(false);

    useEffect(() => {
        let cancelled = false;

        async function fetchMarkdown() {
            setLoading(true);
            setError(null);
            try {
                const response: PaperMarkdownResponse = await fetchFromApi(endpoint);
                if (!cancelled) setData(response);
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : 'Failed to load markdown');
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        }

        fetchMarkdown();

        return () => {
            cancelled = true;
        };
    }, [endpoint]);

    const renderedMarkdown = useMemo(
        () => resolveMarkdownImageUrls(data?.markdown || '', paperId),
        [data?.markdown, paperId]
    );

    const showSwitcher = Boolean(parentPaperId);
    const isParentDisplayed =
        !displayedPaperId || (parentPaperId !== undefined && displayedPaperId === parentPaperId);
    const currentSupplementaryIndex = isParentDisplayed
        ? -1
        : supplementaryMaterials.findIndex((s) => s.id === displayedPaperId);
    const shortLabel = isParentDisplayed
        ? 'Main'
        : currentSupplementaryIndex >= 0
            ? `Suppl ${currentSupplementaryIndex + 1}`
            : 'Suppl';
    const longTitle = title?.trim() || (isParentDisplayed ? parentPaperTitle?.trim() || 'Main paper' : 'Untitled');

    const header = (
        <div className="flex items-center justify-between border-b border-border px-2 py-1 gap-2 shrink-0">
            {showSwitcher ? (
                <Popover open={switcherOpen} onOpenChange={setSwitcherOpen}>
                    <PopoverTrigger asChild>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 max-w-[70%] justify-start gap-1 font-medium"
                            title={longTitle}
                        >
                            <FileText className="h-3.5 w-3.5 shrink-0" />
                            <span className="shrink-0 text-sm">{shortLabel}</span>
                            <span className="truncate text-xs text-muted-foreground">— {longTitle}</span>
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-72 p-1" align="start">
                        <div className="max-h-72 overflow-y-auto">
                            <button
                                type="button"
                                onClick={() => {
                                    if (parentPaperId && !isParentDisplayed) {
                                        onChangeDisplayed?.(parentPaperId);
                                    }
                                    setSwitcherOpen(false);
                                }}
                                className={`w-full flex items-start gap-2 px-2 py-1.5 rounded-sm text-left hover:bg-accent ${isParentDisplayed ? 'bg-accent/60' : ''}`}
                            >
                                <FileText className="h-3.5 w-3.5 mt-0.5 shrink-0 opacity-70" />
                                <span className="flex flex-col min-w-0 flex-1">
                                    <span className="text-sm">Main</span>
                                    <span className="text-[10px] text-muted-foreground truncate">
                                        {parentPaperTitle?.trim() || 'Main paper'}
                                    </span>
                                </span>
                            </button>
                            {supplementaryMaterials.length > 0 && (
                                <>
                                    <div className="border-t border-border my-1" />
                                    {supplementaryMaterials.map((item, idx) => {
                                        const isCompleted = item.status === 'completed';
                                        const isCurrent = displayedPaperId === item.id;
                                        const subLabel = isCompleted
                                            ? (item.title?.trim() || 'Untitled')
                                            : `Processing… (${item.status})`;
                                        return (
                                            <button
                                                key={item.id}
                                                type="button"
                                                disabled={!isCompleted}
                                                onClick={() => {
                                                    if (isCompleted && !isCurrent) {
                                                        onChangeDisplayed?.(item.id);
                                                    }
                                                    setSwitcherOpen(false);
                                                }}
                                                className={`w-full flex items-start gap-2 px-2 py-1.5 rounded-sm text-left hover:bg-accent disabled:opacity-60 disabled:cursor-not-allowed ${isCurrent ? 'bg-accent/60' : ''}`}
                                            >
                                                <FileText className="h-3.5 w-3.5 mt-0.5 shrink-0 opacity-70" />
                                                <span className="flex flex-col min-w-0 flex-1">
                                                    <span className="text-sm">Suppl {idx + 1}</span>
                                                    <span className="text-[10px] text-muted-foreground truncate">{subLabel}</span>
                                                </span>
                                            </button>
                                        );
                                    })}
                                </>
                            )}
                        </div>
                    </PopoverContent>
                </Popover>
            ) : (
                <div className="flex items-center gap-1.5 px-2 py-1 text-sm font-medium">
                    <FileText className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{longTitle}</span>
                </div>
            )}
            {data?.source && (
                <span className="text-[10px] uppercase tracking-wide text-muted-foreground pr-1 hidden sm:inline">
                    {data.source === 'mistral' ? 'OCR' : 'PDF text'}
                </span>
            )}
        </div>
    );

    return (
        <div className="flex flex-col h-full">
            {header}
            <div className={`flex-1 overflow-y-auto ${isMobile ? 'pb-24' : ''}`}>
                {loading ? (
                    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                        <Loader className="mr-2 h-4 w-4 animate-spin" />
                        Loading markdown...
                    </div>
                ) : error ? (
                    <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">{error}</div>
                ) : !data?.markdown?.trim() ? (
                    <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
                        No parsed markdown is available for this paper.
                    </div>
                ) : (
                    <CrepeMarkdownReader markdown={renderedMarkdown} />
                )}
            </div>
        </div>
    );
}
