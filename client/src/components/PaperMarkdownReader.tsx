'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { FileText, Loader } from 'lucide-react';

import { fetchFromApi } from '@/lib/api';

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

export function PaperMarkdownReader({ endpoint, title, paperId }: PaperMarkdownReaderProps) {
    const [data, setData] = useState<PaperMarkdownResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

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

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                <Loader className="mr-2 h-4 w-4 animate-spin" />
                Loading markdown...
            </div>
        );
    }

    if (error) {
        return <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">{error}</div>;
    }

    if (!data?.markdown?.trim()) {
        return (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
                No parsed markdown is available for this paper.
            </div>
        );
    }

    return (
        <article className="h-full overflow-y-auto bg-background px-2 py-3 sm:px-8 sm:py-6">
            <div className="mx-auto max-w-3xl">
                <div className="mb-3 rounded-xl border bg-muted/30 p-3 dark:border-gray-800 sm:mb-6 sm:rounded-2xl sm:p-4">
                    <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        <FileText className="h-4 w-4" />
                        Markdown view
                    </div>
                    {title && <h1 className="mt-2 text-xl font-semibold leading-tight">{title}</h1>}
                    <p className="mt-2 text-xs text-muted-foreground">
                        Rendered from the parsed {data.source === 'mistral' ? 'OCR markdown' : 'PDF text'} for easier mobile reading.
                    </p>
                </div>
                <div className="min-h-[60vh] overflow-hidden rounded-2xl border bg-background dark:border-gray-800">
                    <CrepeMarkdownReader markdown={renderedMarkdown} />
                </div>
            </div>
        </article>
    );
}
