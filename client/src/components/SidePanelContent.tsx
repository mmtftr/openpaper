import {
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
} from '@/lib/schema';
import { RenderedHighlightPosition } from '@/components/reader';
import { Sparkle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AnnotationsView } from '@/components/AnnotationsView';
import { AudioOverviewPanel } from '@/components/AudioOverview';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';
import 'katex/dist/katex.min.css';
import { CopyableTable } from '@/components/AnimatedMarkdown';
import { useMemo } from 'react';
import type { Components } from 'react-markdown';
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
    id: string;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    setRightSideFunction: (value: string) => void;
    setExplicitSearchTerm: (value: string) => void;
    handleCitationClick: (key: string, messageIndex: number) => void;
    userMessageReferences: string[];
    setUserMessageReferences: React.Dispatch<React.SetStateAction<string[]>>;
    isMobile: boolean;
    renderedHighlightPositions?: Map<string, RenderedHighlightPosition>;
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
    id,
    matchesCurrentCitation,
    flashesCurrentCitation,
    setRightSideFunction,
    setExplicitSearchTerm,
    handleCitationClick,
    userMessageReferences,
    setUserMessageReferences,
    isMobile,
    renderedHighlightPositions,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
}: SidePanelContentProps) {
    const { user } = useAuth();

    const memoizedOverviewContent = useMemo(() => {
        if (!paperData?.summary) return null;
        if (paperData.summary === 'None') return null;

        // The summary's inline citation map was removed; strip any leftover
        // footnote-definition lines first, then inline [^N] markers, so
        // older summaries render clean prose.
        const summaryText = paperData.summary
            .replace(/^\[\^\d+\]:.*$/gm, '')
            .replace(/\s*\[\^\d+(?:,\s*\^?\d+)*\]/g, '');

        const components = {
            table: CopyableTable,
        } as Components;

        return (
            <Markdown
                remarkPlugins={[[remarkMath, { singleDollarTextMath: false }], remarkGfm]}
                rehypePlugins={[rehypeKatex]}
                components={components}
            >
                {summaryText}
            </Markdown>
        );
    }, [paperData?.summary]);

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
                        addAnnotation={addAnnotation}
                        updateAnnotation={updateAnnotation}
                        removeAnnotation={removeAnnotation}
                    />
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
