import {
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
} from '@/lib/schema';
import { RenderedHighlightPosition } from '@/components/reader';
import { AnnotationsView } from '@/components/AnnotationsView';
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
    jumpToText: (term: string, page?: number) => void;
    handleCitationClick: (key: string, messageIndex: number, paperId?: string, page?: number) => void;
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
    jumpToText,
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
                    jumpToText={jumpToText}
                    headerSlot={<MetadataPopover paperData={paperData} />}
                />
            )}
        </div>
    );
}
