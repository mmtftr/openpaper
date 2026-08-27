import {
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
} from '@/lib/schema';
import { RenderedHighlightPosition } from '@/components/reader';
import { Loader, Share2Icon, LockIcon, Sparkle } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { AnnotationsView } from '@/components/AnnotationsView';
import { AudioOverviewPanel } from '@/components/AudioOverview';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import CustomCitationLink from '@/components/utils/CustomCitationLink';
import { CopyableTable } from '@/components/AnimatedMarkdown';
import { useMemo } from 'react';
import type { Components } from 'react-markdown';
import { toast } from 'sonner';
import { useAuth } from '@/lib/auth';
import { PaperChatPanel } from '@/components/chat/PaperChatPanel';
import { MetadataPopover } from '@/components/chat/MetadataPopover';
import { PaperDocEditor } from '@/components/PaperDocEditor';

interface SidePanelContentProps {
    rightSideFunction: string;
    paperData: PaperData;
    annotations: PaperHighlightAnnotation[];
    highlights: PaperHighlight[];
    handleHighlightClick: (highlight: PaperHighlight) => void;
    activeHighlight: PaperHighlight | null;
    isSharing: boolean;
    handleShare: () => void;
    handleUnshare: () => void;
    id: string;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    handleCitationClickFromSummary: (citationKey: string, messageIndex: number) => void;
    setRightSideFunction: (value: string) => void;
    setExplicitSearchTerm: (value: string) => void;
    handleCitationClick: (key: string, messageIndex: number) => void;
    userMessageReferences: string[];
    setUserMessageReferences: React.Dispatch<React.SetStateAction<string[]>>;
    isMobile: boolean;
    renderedHighlightPositions?: Map<string, RenderedHighlightPosition>;
    composeHighlightId?: string | null;
    onComposeHighlightDismiss?: (cancelledHighlightId?: string | null) => void;
    addAnnotation?: (highlightId: string, content: string) => Promise<PaperHighlightAnnotation>;
    updateAnnotation?: (annotationId: string, content: string) => Promise<unknown> | void;
    removeAnnotation?: (annotationId: string) => void;
}

export function SidePanelContent({
    rightSideFunction,
    paperData,
    annotations,
    highlights,
    handleHighlightClick,
    activeHighlight,
    isSharing,
    handleShare,
    handleUnshare,
    id,
    matchesCurrentCitation,
    flashesCurrentCitation,
    handleCitationClickFromSummary,
    setRightSideFunction,
    setExplicitSearchTerm,
    handleCitationClick,
    userMessageReferences,
    setUserMessageReferences,
    isMobile,
    renderedHighlightPositions,
    composeHighlightId,
    onComposeHighlightDismiss,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
}: SidePanelContentProps) {
    const { user } = useAuth();

    const memoizedOverviewContent = useMemo(() => {
        if (!paperData?.summary) return null;
        if (paperData.summary === 'None') return null;

        const citations = paperData.summary_citations?.map((citation) => ({
            key: String(citation.index),
            reference: citation.text,
        })) || [];

        const inject = (props: object) => (
            <CustomCitationLink
                {...(props as Record<string, unknown>)}
                handleCitationClick={handleCitationClickFromSummary}
                messageIndex={0}
                citations={citations}
            />
        );

        const components = {
            p: inject,
            li: inject,
            div: inject,
            td: inject,
            table: CopyableTable,
        } as Components;

        return (
            <Markdown
                remarkPlugins={[[remarkMath, { singleDollarTextMath: false }], remarkGfm]}
                rehypePlugins={[rehypeKatex]}
                components={components}
            >
                {paperData.summary}
            </Markdown>
        );
    }, [paperData?.summary, paperData?.summary_citations, handleCitationClickFromSummary]);

    const heightClass = isMobile ? 'h-[calc(100vh-128px)]' : 'h-[calc(100vh-64px)]';

    if (rightSideFunction === 'Read') {
        return null;
    }

    return (
        <div className={`flex-grow h-full overflow-hidden ${isMobile ? '' : 'pr-[60px]'}`}>
            {rightSideFunction === 'Annotations' && user && (
                <div className={`flex flex-col ${heightClass} overflow-y-auto`}>
                    <AnnotationsView
                        annotations={annotations}
                        highlights={highlights}
                        user={user}
                        onHighlightClick={handleHighlightClick}
                        activeHighlight={activeHighlight}
                        renderedHighlightPositions={renderedHighlightPositions}
                        composeHighlightId={composeHighlightId}
                        onComposeHighlightDismiss={onComposeHighlightDismiss}
                        addAnnotation={addAnnotation}
                        updateAnnotation={updateAnnotation}
                        removeAnnotation={removeAnnotation}
                    />
                </div>
            )}

            {rightSideFunction === 'Share' && paperData && (
                <div className={`flex flex-col ${heightClass} p-4 space-y-4`}>
                    <h3 className="text-lg font-semibold">Share Paper</h3>
                    {paperData.share_id ? (
                        <div className="space-y-3">
                            <p className="text-sm text-muted-foreground">
                                This paper is currently public. Anyone with the link can view it.
                            </p>
                            <div className="flex items-center space-x-2">
                                <Input
                                    readOnly
                                    value={`${window.location.origin}/paper/share/${paperData.share_id}`}
                                    className="flex-1"
                                />
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={async () => {
                                        await navigator.clipboard.writeText(
                                            `${window.location.origin}/paper/share/${paperData.share_id}`
                                        );
                                        toast.success('Link copied!');
                                    }}
                                >
                                    Copy Link
                                </Button>
                            </div>
                            <Button
                                variant="destructive"
                                onClick={handleUnshare}
                                disabled={isSharing}
                                className="w-fit"
                            >
                                {isSharing ? <Loader className="animate-spin mr-2 h-4 w-4" /> : null}
                                <LockIcon /> Make Private
                            </Button>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <p className="text-sm text-muted-foreground">
                                Make this paper public to share it with others via a unique link. All of your{' '}
                                <b>annotations and chats</b> will be visible to anyone with the link.
                            </p>
                            <Button
                                onClick={handleShare}
                                disabled={isSharing}
                                className="w-fit"
                            >
                                {isSharing ? <Loader className="animate-spin mr-2 h-4 w-4" /> : null}
                                <Share2Icon /> Share
                            </Button>
                        </div>
                    )}
                </div>
            )}

            {rightSideFunction === 'Overview' && paperData.summary && (
                <div
                    className={`flex flex-col ${heightClass} md:px-2 overflow-y-auto m-2 relative animate-fade-in`}
                >
                    <div className="prose dark:prose-invert !max-w-full text-sm">
                        {paperData.title && (
                            <h1 className="text-2xl font-bold">{paperData.title}</h1>
                        )}
                        {memoizedOverviewContent}
                        {paperData.summary_citations && paperData.summary_citations.length > 0 && (
                            <div
                                className="mt-0 pt-0 border-t border-gray-300 dark:border-gray-700"
                                id="references-section"
                            >
                                <h4 className="text-sm font-semibold mb-2">References</h4>
                                <ul className="list-none p-0">
                                    {paperData.summary_citations.map((citation, index) => (
                                        <div
                                            key={index}
                                            className={`flex flex-row gap-2 ${matchesCurrentCitation(`${citation.index}`, 0)
                                                ? 'bg-blue-100 dark:bg-blue-900 rounded p-1 transition-colors duration-300'
                                                : ''
                                                }`}
                                            id={`citation-${citation.index}-${index}`}
                                            onClick={() => handleCitationClickFromSummary(`${citation.index}`, 0)}
                                        >
                                            <div className="text-xs text-secondary-foreground">
                                                <span>{citation.index}</span>
                                            </div>
                                            <div
                                                id={`citation-ref-${citation.index}-${index}`}
                                                className="text-xs text-secondary-foreground"
                                            >
                                                {citation.text}
                                            </div>
                                        </div>
                                    ))}
                                </ul>
                            </div>
                        )}
                        <div className="sticky bottom-4 right-4 flex justify-end">
                            <Button
                                variant="default"
                                className="w-fit bg-blue-500 hover:bg-blue-400 dark:hover:bg-blue-600 cursor-pointer z-10 shadow-md"
                                onClick={() => {
                                    setRightSideFunction('Chat');
                                }}
                            >
                                <Sparkle className="mr-1" />
                                Ask a Question
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            {rightSideFunction === 'Audio' && (
                <div className={`flex flex-col ${heightClass} md:px-2 overflow-y-auto`}>
                    <AudioOverviewPanel
                        paper_id={id}
                        paper_title={paperData.title}
                        setExplicitSearchTerm={setExplicitSearchTerm}
                    />
                </div>
            )}

            {rightSideFunction === 'Doc' && (
                <div className={`flex flex-col ${heightClass}`}>
                    <PaperDocEditor paperId={id} />
                </div>
            )}

            {rightSideFunction === 'Chat' && (
                <PaperChatPanel
                    id={id}
                    paperData={paperData}
                    isMobile={isMobile}
                    userMessageReferences={userMessageReferences}
                    setUserMessageReferences={setUserMessageReferences}
                    handleCitationClick={handleCitationClick}
                    matchesCurrentCitation={matchesCurrentCitation}
                    flashesCurrentCitation={flashesCurrentCitation}
                    setExplicitSearchTerm={setExplicitSearchTerm}
                    headerSlot={<MetadataPopover paperData={paperData} />}
                />
            )}
        </div>
    );
}
