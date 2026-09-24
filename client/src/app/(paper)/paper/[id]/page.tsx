'use client';

import { PdfReader, RenderedHighlightPosition, type HighlightJumpRequest } from '@/components/reader';
import { Button } from '@/components/ui/button';
import { fetchFromApi } from '@/lib/api';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';


import {
    FileText,
    Highlighter,
    MessageCircle,
} from 'lucide-react';
import { toast } from "sonner";

import { useAnnotations } from '@/hooks/PdfAnnotation';
import { useHighlighterHighlights } from '@/hooks/PdfHighlighterHighlights';

import {
    PaperData,
    PaperHighlight,
    PaperUploadJobStatusResponse,
    SupplementaryMaterialSummary,
} from '@/lib/schema';

import { PaperSidebar } from '@/components/PaperSidebar';
import { useAuth } from '@/lib/auth';

import PaperViewSkeleton from '@/components/PaperViewSkeleton';
import ReportSkeleton from '@/components/ReportSkeleton';
import { usePaperHeader } from '@/components/PaperHeaderContext';

import { SidePanelContent } from '@/components/SidePanelContent';
import { PaperMarkdownReader } from '@/components/PaperMarkdownReader';
import { useIsMobile } from '@/hooks/use-mobile';
import { Book, Box, ScrollText } from 'lucide-react';

const ChatTool = {
    name: "Chat",
    label: "Show chat",
    icon: MessageCircle,
}

const AnnotationsTool = {
    name: "Annotations",
    label: "All annotations",
    icon: Highlighter,
}

const DocTool = {
    name: "Doc",
    label: "Notes",
    icon: FileText,
}

const PaperToolset = {
    nav: [
        ChatTool,
        AnnotationsTool,
        DocTool,
    ],
}

export default function PaperView() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const id = params.id as string;
    const { user, loading: authLoading } = useAuth();
    // `paperData` always refers to the *parent* paper. When the route id is a
    // supplementary, an effect below resolves the parent and re-fetches into
    // this slot. The chat / doc panels bind to this.
    const [paperData, setPaperData] = useState<PaperData | null>(null);
    const [loading, setLoading] = useState(true);
    // The id of the parent paper (= route id for normal papers; = paperData.supplementary_of_paper_id
    // when the user landed directly on a supplementary's URL).
    const [parentPaperId, setParentPaperId] = useState<string>(id);
    // The id of the paper whose PDF is currently rendered. Defaults to the
    // `display` query param if present, else the route id.
    const [displayedPaperId, setDisplayedPaperId] = useState<string>(() => {
        if (typeof window === 'undefined') return id;
        const display = new URLSearchParams(window.location.search).get('display');
        return display || id;
    });
    // PaperData for the currently displayed PDF. When displayedPaperId === parentPaperId,
    // we just reuse `paperData`. Otherwise we fetch the supplementary's PaperData here.
    const [displayedPaperData, setDisplayedPaperData] = useState<PaperData | null>(null);
    const [supplementaryMaterials, setSupplementaryMaterials] = useState<SupplementaryMaterialSummary[] | null>(null);

    // Highlights and annotations belong to the PDF being shown, not the parent:
    // a supplementary's highlights render on (and new ones attach to) the
    // supplementary. Both hooks re-fetch when the displayed paper changes.
    const {
        highlights,
        activeHighlight,
        setActiveHighlight,
        addHighlight,
        removeHighlight,
        recolorHighlight,
        fetchHighlights
    } = useHighlighterHighlights(displayedPaperId);

    const {
        annotations,
        addAnnotation,
        removeAnnotation,
        updateAnnotation,
        refreshAnnotations,
    } = useAnnotations(displayedPaperId);

    const [activeCitationKey, setActiveCitationKey] = useState<string | null>(null);
    const [activeCitationMessageIndex, setActiveCitationMessageIndex] = useState<number | null>(null);
    const [flashCitation, setFlashCitation] = useState<{ key: string; messageIndex: number } | null>(null);
    const pendingCitationLookupRef = useRef<{ key: string; messageIndex: number; term: string } | null>(null);
    // Tracks which paper id is currently held in `displayedPaperData` so the
    // direct-visit branch (which seeds `displayedPaperData` from the route
    // fetch) doesn't trigger a duplicate fetch in the effect below.
    const displayedPaperDataIdRef = useRef<string | null>(null);
    const flashCitationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [highlightJumpRequest, setHighlightJumpRequest] = useState<HighlightJumpRequest | null>(null);
    const [explicitSearchTerm, setExplicitSearchTerm] = useState<string | undefined>(undefined);
    const [userMessageReferences, setUserMessageReferences] = useState<string[]>([]);
    const [renderedHighlightPositions, setRenderedHighlightPositions] = useState<Map<string, RenderedHighlightPosition>>(new Map());

    // Callback for DOM overlay highlights. This reflects the current rendered overlay set.
    const handleOverlaysCreated = useCallback((positions: Map<string, RenderedHighlightPosition>) => {
        setRenderedHighlightPositions(new Map(positions));
    }, []);

    const [jobId, setJobId] = useState<string | null>(null);
    const [loadingMessage, setLoadingMessage] = useState<string | null>(null);
    const [sidePanelDisplayedText, setSidePanelDisplayedText] = useState('');
    const [elapsedTime, setElapsedTime] = useState(0);

    const [rightSideFunction, setRightSideFunction] = useState<string>('Chat');
    const annotationsPanelActive = rightSideFunction === 'Annotations';

    const [toolset, setToolset] = useState(PaperToolset);
    const initialRsfRef = useRef<string | null>(null);
    const hasInitializedRsf = useRef(false);

    // Capture the initial rsf from URL on first render
    useEffect(() => {
        if (initialRsfRef.current === null) {
            let rsf = searchParams.get('rsf')?.toLowerCase() || null;
            if (rsf === 'focus') rsf = 'read'; // legacy URL param when tool was named Focus
            initialRsfRef.current = rsf;
        }
    }, [searchParams]);

    useEffect(() => {
        if (paperData) {
            // Use the captured initial rsf value, not the current searchParams
            const rsf = hasInitializedRsf.current ? null : initialRsfRef.current;

            // Derive the available tools first
            const newNav = PaperToolset.nav;

            const validTools = newNav.map(tool => tool.name.toLowerCase());

            // Only set from URL on first initialization
            if (!hasInitializedRsf.current) {
                hasInitializedRsf.current = true;
                if (rsf === 'read') {
                    setRightSideFunction('Read');
                } else if (rsf && validTools.includes(rsf)) {
                    const toolName = newNav.find(tool => tool.name.toLowerCase() === rsf);
                    setRightSideFunction(toolName ? toolName.name : 'Chat');
                } else {
                    setRightSideFunction('Chat');
                }
            }

            // Update the toolset state for the UI
            setToolset({ nav: newNav });
        }
    }, [paperData]);

    useEffect(() => {
        // Only update URL after we've initialized from the original rsf
        if (!hasInitializedRsf.current) return;

        const params = new URLSearchParams(window.location.search);
        params.set('rsf', rightSideFunction.toLowerCase());
        // Use history.replaceState directly instead of router.replace. Next.js
        // App Router's `router.replace` issues an RSC payload fetch (`?_rsc=`)
        // for the new URL; if that fetch fails (extension blocking, transient
        // network error, etc.) Next falls back to a full browser navigation,
        // which reloads the page. That reload re-runs the paperData effect
        // that sets `rightSideFunction`, which triggers this effect again,
        // which triggers another router.replace, which fails again — an
        // infinite reload loop. This URL update is purely cosmetic (the rsf
        // param is read on next mount to restore the panel), so doing it
        // through the History API avoids the navigation entirely.
        const nextUrl = `${window.location.pathname}?${params.toString()}`;
        if (nextUrl !== `${window.location.pathname}${window.location.search}`) {
            window.history.replaceState(null, '', nextUrl);
        }
    }, [rightSideFunction]);
    const [leftPanelWidth, setLeftPanelWidth] = useState(60); // percentage
    const [isDragging, setIsDragging] = useState(false);
    const isMobile = useIsMobile();
    const [mobileView, setMobileView] = useState<'reader' | 'markdown' | 'panel'>('reader');

    const isReadMode = rightSideFunction === 'Read';

    /** Tracks the last non-Read panel so we can restore it when exiting focus mode. */
    const lastNonReadFunctionRef = useRef<string>('Chat');
    const prevRightSideRef = useRef(rightSideFunction);
    useEffect(() => {
        if (prevRightSideRef.current !== 'Read') {
            lastNonReadFunctionRef.current = prevRightSideRef.current;
        }
        prevRightSideRef.current = rightSideFunction;
    }, [rightSideFunction]);

    const handleToggleReadMode = useCallback(() => {
        if (isReadMode) {
            const target = lastNonReadFunctionRef.current;
            const validTools = toolset.nav.map(t => t.name);
            setRightSideFunction(validTools.includes(target) ? target : 'Chat');
        } else {
            setRightSideFunction('Read');
        }
    }, [isReadMode, toolset.nav]);

    useEffect(() => {
        if (jobId) {
            const timer = setInterval(() => {
                setElapsedTime(prevTime => prevTime + 1);
            }, 1000);
            return () => clearInterval(timer);
        } else {
            setElapsedTime(0); // Reset timer when job is done
        }
    }, [jobId]);


    useEffect(() => {
        if (!jobId) {
            setSidePanelDisplayedText('');
            return;
        }
        if (!loadingMessage) {
            setSidePanelDisplayedText('Processing your paper...');
            return;
        }

        let charIndex = 0;
        setSidePanelDisplayedText('');

        const typingInterval = setInterval(() => {
            if (charIndex < loadingMessage.length) {
                setSidePanelDisplayedText(loadingMessage.slice(0, charIndex + 1));
                charIndex++;
            } else {
                clearInterval(typingInterval);
            }
        }, 50); // 50ms per character for smooth typing

        return () => clearInterval(typingInterval);
    }, [loadingMessage, jobId]);

    useEffect(() => {
        const url = new URL(window.location.href);
        const jobIdFromUrl = url.searchParams.get('job_id');
        if (jobIdFromUrl) {
            setJobId(jobIdFromUrl);
            pollJobStatus(jobIdFromUrl);
        }
    }, []);

    const pollJobStatus = async (jobId: string) => {
        try {
            const response: PaperUploadJobStatusResponse = await fetchFromApi(`/api/paper/upload/status/${jobId}`);
            setLoadingMessage(response.celery_progress_message);

            if (response.status === 'completed') {
                setJobId(null);
            } else if (response.status === 'failed') {
                setJobId(null);
                toast.error("Failed to process your paper", {
                    description: "There was an error indexing your paper. Please try uploading again.",
                    duration: 10000,
                    action: {
                        label: "Go Home",
                        onClick: () => router.push('/'),
                    },
                });
            } else {
                setTimeout(() => pollJobStatus(jobId), 2000);
            }
        } catch (error) {
            console.error('Error polling job status:', error);
        }
    };

    // Add this function to handle citation clicks. When `paperId` is provided
    // and refers to a supplementary, flip the displayed PDF first so the
    // explicit search term lands on the right document.
    const handleCitationClick = useCallback((key: string, messageIndex: number, paperId?: string) => {
        setHighlightJumpRequest(null);
        setActiveCitationKey(key);
        setActiveCitationMessageIndex(messageIndex);

        // Backwards-compat: a missing paperId means "the parent". Only switch
        // displayed paper when the citation explicitly references something
        // other than the parent.
        if (paperId && paperId !== parentPaperId && paperId !== displayedPaperId) {
            setDisplayedPaperId(paperId);
        }

        // Scroll to the citation
        const element = document.getElementById(`citation-${key}-${messageIndex}`);
        if (element) {

            const refValueElement = document.getElementById(`citation-ref-${key}-${messageIndex}`);
            if (refValueElement) {
                const refValueText = refValueElement.innerText;
                let searchTerm = refValueText.replace(/^\[\^(\d+|[a-zA-Z]+)\]/, '').trim();

                // Only remove quotes if the text is actually wrapped in quotes
                if ((searchTerm.startsWith('"') && searchTerm.endsWith('"')) ||
                    (searchTerm.startsWith("'") && searchTerm.endsWith("'"))) {
                    searchTerm = searchTerm.substring(1, searchTerm.length - 1);
                }
                pendingCitationLookupRef.current = { key, messageIndex, term: searchTerm };
                // Reset any prior flash so a re-click can re-flash if it again has no match.
                if (flashCitationTimeoutRef.current) {
                    clearTimeout(flashCitationTimeoutRef.current);
                    flashCitationTimeoutRef.current = null;
                }
                setFlashCitation(null);
                setExplicitSearchTerm(searchTerm);
            }
        }

        // Clear the highlight after a few seconds
        setTimeout(() => setActiveCitationKey(null), 3000);
    }, [parentPaperId, displayedPaperId]);

    const handleSearchComplete = useCallback((term: string, matchCount: number) => {
        const pending = pendingCitationLookupRef.current;
        if (!pending || pending.term !== term) return;
        pendingCitationLookupRef.current = null;
        if (matchCount === 0) {
            setFlashCitation({ key: pending.key, messageIndex: pending.messageIndex });
            if (flashCitationTimeoutRef.current) clearTimeout(flashCitationTimeoutRef.current);
            flashCitationTimeoutRef.current = setTimeout(() => {
                setFlashCitation(null);
                flashCitationTimeoutRef.current = null;
            }, 2500);
        }
    }, []);

    useEffect(() => {
        return () => {
            if (flashCitationTimeoutRef.current) clearTimeout(flashCitationTimeoutRef.current);
        };
    }, []);

    useEffect(() => { setHighlightJumpRequest(null); }, [displayedPaperId]);

    const handleHighlightClick = useCallback((highlight: PaperHighlight) => {
        setActiveHighlight(highlight);
        setExplicitSearchTerm(undefined);
        if (isMobile) setMobileView('reader');
        if (highlight.id) {
            const highlightId = highlight.id;
            setHighlightJumpRequest(previous => ({ highlightId, nonce: (previous?.nonce ?? 0) + 1 }));
        }
    }, [isMobile]);


    useEffect(() => {
        if (!authLoading && !user) {
            // Redirect to login if user is not authenticated
            window.location.href = `/login`;
        }
    }, [authLoading, user]);

    // The paper page is a client component, so the static `title: "Open Paper"`
    // from the paper layout metadata is what initially lands in the tab. Patch
    // document.title once the paper loads, and restore it on unmount so
    // navigating away doesn't leave a stale paper title on the next route.
    useEffect(() => {
        const title = paperData?.title?.trim();
        if (!title) return;
        const previous = document.title;
        document.title = `${title} - Open Paper`;
        return () => {
            document.title = previous;
        };
    }, [paperData?.title]);

    useEffect(() => {
        if (activeHighlight) {
            // Only open the associated annotation view if the highlight is from the assistant to reduce some user confusion?
            if (activeHighlight.role === 'assistant') {
                setRightSideFunction('Annotations');
            }
        }
    }, [activeHighlight]);

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isDragging) return;

            const containerWidth = window.innerWidth;
            const newLeftWidth = (e.clientX / containerWidth) * 100;

            // Constrain between 30% and 80%
            const constrainedWidth = Math.min(Math.max(newLeftWidth, 30), 80);
            setLeftPanelWidth(constrainedWidth);
        };

        const handleMouseUp = () => {
            setIsDragging(false);
            document.body.style.cursor = 'default';
            document.body.style.userSelect = 'auto';
        };

        if (isDragging) {
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }

        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }, [isDragging]);

    useEffect(() => {
        // Only fetch data when id is available
        if (!id) return;

        async function fetchPaper() {
            try {
                const response: PaperData = await fetchFromApi(`/api/paper?id=${id}`);
                if (response.supplementary_of_paper_id) {
                    // The route id is a supplementary. Rewrite the URL so the user
                    // sees /paper/<parent>?display=<supp> and re-fetch the parent's
                    // PaperData (which is what the chat/annotations panels bind to).
                    // Use history.replaceState rather than router.replace for the same
                    // reason as the rsf URL sync above: avoid Next.js RSC fetches
                    // that can trigger full reloads on transient failures.
                    const newParentId = response.supplementary_of_paper_id;
                    try {
                        const params = new URLSearchParams(window.location.search);
                        params.set('display', id);
                        const nextUrl = `/paper/${newParentId}?${params.toString()}`;
                        const currentUrl = `${window.location.pathname}${window.location.search}`;
                        if (nextUrl !== currentUrl) {
                            window.history.replaceState(null, '', nextUrl);
                        }
                    } catch (err) {
                        console.error('Error rewriting supplementary URL:', err);
                    }
                    setDisplayedPaperId(id);
                    setParentPaperId(newParentId);
                    // Cache the supplementary's PaperData since we already have it
                    setDisplayedPaperData(response);
                    displayedPaperDataIdRef.current = id;
                    try {
                        const parentResponse: PaperData = await fetchFromApi(`/api/paper?id=${newParentId}`);
                        setPaperData(parentResponse);
                    } catch (parentErr) {
                        console.error('Error fetching parent paper:', parentErr);
                    }
                } else {
                    // Route id is the parent. Honor any existing `display` query param.
                    setParentPaperId(id);
                    setPaperData(response);
                }
            } catch (error) {
                console.error('Error fetching paper:', error);
            } finally {
                setLoading(false);
            }
        }

        if (jobId) return;

        fetchPaper();
        refreshAnnotations();
        fetchHighlights();
    }, [id, jobId]);

    useEffect(() => {
        if (userMessageReferences.length > 0) {
            setRightSideFunction('Chat');
        }
    }, [userMessageReferences]);

    // Fetch the displayed paper's data when it diverges from the parent.
    // When they match, the parent's `paperData` is the source of truth and we
    // just clear `displayedPaperData` so the consumer falls back to it.
    useEffect(() => {
        if (!displayedPaperId) return;
        if (displayedPaperId === parentPaperId) {
            setDisplayedPaperData(null);
            displayedPaperDataIdRef.current = null;
            return;
        }
        if (displayedPaperDataIdRef.current === displayedPaperId) return;
        let cancelled = false;
        async function fetchDisplayed() {
            try {
                const response: PaperData = await fetchFromApi(`/api/paper?id=${displayedPaperId}`);
                if (!cancelled) {
                    setDisplayedPaperData(response);
                    displayedPaperDataIdRef.current = displayedPaperId;
                }
            } catch (err) {
                console.error('Error fetching displayed paper:', err);
            }
        }
        fetchDisplayed();
        return () => {
            cancelled = true;
        };
    }, [displayedPaperId, parentPaperId]);

    // Fetch the supplementary list whenever the parent id is known.
    const refetchSupplementaryMaterials = useCallback(async () => {
        if (!parentPaperId) return;
        try {
            const response = await fetchFromApi(`/api/paper/${parentPaperId}/supplementary`);
            setSupplementaryMaterials(Array.isArray(response) ? response : []);
        } catch (err) {
            console.error('Error fetching supplementary materials:', err);
        }
    }, [parentPaperId]);

    useEffect(() => {
        if (!parentPaperId) return;
        refetchSupplementaryMaterials();
    }, [parentPaperId, refetchSupplementaryMaterials]);

    // Keep the `display` query param in sync as the user flips between PDFs.
    // history.replaceState (not router.replace) for the same reason described
    // in the rsf URL-sync effect above.
    useEffect(() => {
        if (typeof window === 'undefined') return;
        const params = new URLSearchParams(window.location.search);
        if (!displayedPaperId || displayedPaperId === parentPaperId) {
            params.delete('display');
        } else {
            params.set('display', displayedPaperId);
        }
        const qs = params.toString();
        const nextUrl = qs
            ? `${window.location.pathname}?${qs}`
            : window.location.pathname;
        const currentUrl = `${window.location.pathname}${window.location.search}`;
        if (nextUrl !== currentUrl) {
            window.history.replaceState(null, '', nextUrl);
        }
    }, [displayedPaperId, parentPaperId]);

    const matchesCurrentCitation = useCallback((key: string, messageIndex: number) => {
        return activeCitationKey === key.toString() && activeCitationMessageIndex === messageIndex;
    }, [activeCitationKey, activeCitationMessageIndex]);

    const flashesCurrentCitation = useCallback((key: string, messageIndex: number) => {
        if (!flashCitation) return false;
        return flashCitation.key === key.toString() && flashCitation.messageIndex === messageIndex;
    }, [flashCitation]);


    // Refresh the currently displayed PDF's signed URL (called on 403). This
    // operates on the displayed paper, NOT the parent — when a supplementary is
    // shown it's the supplementary's URL that's stale.
    const refreshPdfUrl = useCallback(async (): Promise<string | null> => {
        if (!displayedPaperId) return null;
        try {
            const response: PaperData = await fetchFromApi(`/api/paper?id=${displayedPaperId}`);
            if (response.file_url) {
                if (displayedPaperId === parentPaperId) {
                    setPaperData(response);
                } else {
                    setDisplayedPaperData(response);
                }
                return response.file_url;
            }
            return null;
        } catch (error) {
            console.error('Error refreshing PDF URL:', error);
            return null;
        }
    }, [displayedPaperId, parentPaperId]);

    const paperHeader = usePaperHeader();
    const setPaperHeaderContext = paperHeader?.setPaperContext;
    const headerPaperStatus = paperHeader?.paperStatus ?? null;

    // Publish the parent paper's id/status/title to the layout's header so the
    // status dropdown can live there. Re-sync whenever any of those change.
    useEffect(() => {
        if (!setPaperHeaderContext) return;
        setPaperHeaderContext(parentPaperId, paperData?.status ?? null, paperData?.title ?? null);
        return () => setPaperHeaderContext(null, null, null);
    }, [setPaperHeaderContext, parentPaperId, paperData?.status, paperData?.title]);

    // Mirror header-initiated status changes back into local paperData so any
    // remaining consumers (toasts, downstream effects) stay in sync.
    useEffect(() => {
        if (!headerPaperStatus) return;
        setPaperData(prev => (prev && prev.status !== headerPaperStatus ? { ...prev, status: headerPaperStatus } : prev));
    }, [headerPaperStatus]);

    const onAskStarted = useCallback(() => {
        setRightSideFunction('Chat');
        // On mobile the panel is a separate view; switching to it would unmount
        // the reader and lose the page, so just confirm where the quote went.
        if (isMobile) toast.success('Added to chat');
    }, [isMobile]);

    /** "Open in Annotations" from a highlight's note popover. */
    const onOpenThread = useCallback((highlight: PaperHighlight) => {
        setRightSideFunction('Annotations');
        setActiveHighlight(highlight);
        if (isMobile) setMobileView('panel');
        // Once the panel has rendered, put keyboard focus on the thread.
        requestAnimationFrame(() =>
            requestAnimationFrame(() => {
                if (!highlight.id) return;
                document
                    .querySelector<HTMLElement>(
                        `[data-annotation-sidebar-row][data-thread-id="${CSS.escape(highlight.id)}"]`
                    )
                    // Scrolls it into view too, even if it was already the active thread.
                    ?.focus();
            })
        );
    }, [isMobile, setActiveHighlight]);

    if (loading) return <PaperViewSkeleton />;

    if (!paperData) return null;

    // Resolve the PaperData for the displayed PDF. When viewing the parent
    // directly it's just `paperData`; otherwise it's the supplementary's
    // `displayedPaperData` (which the effect above keeps in sync).
    const effectiveDisplayedPaperData =
        displayedPaperId === parentPaperId
            ? paperData
            : displayedPaperData;

    const pdfUrlForViewer = effectiveDisplayedPaperData?.file_url;

    const sidePanelProps = {
        rightSideFunction,
        // Chat / doc are bound to the parent even when a supplementary PDF
        // is shown; the annotations list follows the displayed PDF.
        paperData,
        annotations,
        highlights,
        handleHighlightClick,
        activeHighlight,
        id: parentPaperId,
        matchesCurrentCitation,
        flashesCurrentCitation,
        setExplicitSearchTerm,
        handleCitationClick,
        userMessageReferences,
        setUserMessageReferences,
        renderedHighlightPositions,
        addAnnotation,
        updateAnnotation,
        removeAnnotation,
    };

    if (isMobile) {
        return (
            <div className="flex flex-col w-full h-[calc(100vh-64px)]">
                <div className="flex-grow overflow-auto min-h-0">
                    {mobileView === 'reader' ? (
                        <div className="relative w-full h-full">
                            {pdfUrlForViewer && (
                                <PdfReader
                                    pdfUrl={pdfUrlForViewer}
                                    highlightJumpRequest={highlightJumpRequest}
                                    explicitSearchTerm={explicitSearchTerm}
                                    onSearchComplete={handleSearchComplete}
                                    highlights={highlights}
                                    annotations={annotations}
                                    activeHighlight={activeHighlight}
                                    setActiveHighlight={setActiveHighlight}
                                    addHighlight={addHighlight}
                                    removeHighlight={removeHighlight}
                                    recolorHighlight={recolorHighlight}
                                    addAnnotation={addAnnotation}
                                    updateAnnotation={updateAnnotation}
                                    removeAnnotation={removeAnnotation}
                                    setUserMessageReferences={setUserMessageReferences}
                                    onOverlaysCreated={handleOverlaysCreated}
                                    onRefreshUrl={refreshPdfUrl}
                                    currentUser={user}
                                    onAskStarted={onAskStarted}
                                    onOpenThread={onOpenThread}
                                    parentPaperId={parentPaperId}
                                    displayedPaperId={displayedPaperId}
                                    parentPaperTitle={paperData?.title ?? undefined}
                                    supplementaryMaterials={supplementaryMaterials ?? []}
                                    onChangeDisplayed={setDisplayedPaperId}
                                    onSupplementaryUploaded={refetchSupplementaryMaterials}
                                />
                            )}
                        </div>
                    ) : mobileView === 'markdown' ? (
                        <PaperMarkdownReader
                            endpoint={`/api/paper/markdown?id=${encodeURIComponent(displayedPaperId)}`}
                            paperId={displayedPaperId}
                            title={effectiveDisplayedPaperData?.title ?? paperData.title}
                            parentPaperId={parentPaperId}
                            displayedPaperId={displayedPaperId}
                            parentPaperTitle={paperData.title ?? undefined}
                            supplementaryMaterials={supplementaryMaterials ?? []}
                            onChangeDisplayed={setDisplayedPaperId}
                        />
                    ) : (
                        <div className="w-full h-full">
                            <div
                                className="flex flex-row h-full relative"
                            >
                                {jobId ? (
                                    <div className="flex flex-col h-full w-full">
                                        <div className="flex items-center justify-center w-full px-6 py-4 border-b border-gray-100 dark:border-gray-800/50">
                                            <div className="flex items-center gap-3">
                                                <div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                                                <p className="text-sm text-muted-foreground">{sidePanelDisplayedText}</p>
                                                <span className="text-xs text-muted-foreground/50 tabular-nums">{elapsedTime}s</span>
                                            </div>
                                        </div>
                                        <ReportSkeleton />
                                    </div>
                                ) : (
                                    <>
                                        <SidePanelContent {...sidePanelProps} isMobile={true} />
                                        <PaperSidebar
                                            rightSideFunction={rightSideFunction}
                                            setRightSideFunction={setRightSideFunction}
                                            PaperToolset={toolset}
                                        />
                                    </>
                                )}
                            </div>
                        </div>
                    )}
                </div>
                <div className="flex-shrink-0 border-t border-gray-200 dark:border-gray-800">
                    <div className="flex justify-around items-center h-16">
                        <Button variant="ghost" onClick={() => setMobileView('reader')} className={`flex flex-col items-center gap-1 ${mobileView === 'reader' ? 'text-blue-500' : ''}`}>
                            <Book size={24} />
                            <span className="text-xs">Reader</span>
                        </Button>
                        <Button variant="ghost" onClick={() => setMobileView('markdown')} className={`flex flex-col items-center gap-1 ${mobileView === 'markdown' ? 'text-blue-500' : ''}`}>
                            <ScrollText size={24} />
                            <span className="text-xs">Markdown</span>
                        </Button>
                        <Button variant="ghost" onClick={() => setMobileView('panel')} className={`flex flex-col items-center gap-1 ${mobileView === 'panel' ? 'text-blue-500' : ''}`}>
                            <Box size={24} />
                            <span className="text-xs">Tools</span>
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="flex flex-row w-full h-[calc(100vh-64px)]">
            <div className="w-full h-full flex items-center justify-center gap-0">
                {/* PDF Viewer Section */}
                <div
                    className="border-r-2 dark:border-gray-800 border-gray-200 p-0 h-full"
                    style={{
                        width: rightSideFunction === 'Read' ? '100%' : `${leftPanelWidth}%`,
                        transition: isDragging ? 'none' : 'width 300ms ease',
                    }}
                >
                    {pdfUrlForViewer && (
                        <div className="relative w-full h-full">
                            <PdfReader
                                pdfUrl={pdfUrlForViewer}
                                highlightJumpRequest={highlightJumpRequest}
                                explicitSearchTerm={explicitSearchTerm}
                                onSearchComplete={handleSearchComplete}
                                highlights={highlights}
                                annotations={annotations}
                                activeHighlight={activeHighlight}
                                setActiveHighlight={setActiveHighlight}
                                addHighlight={addHighlight}
                                removeHighlight={removeHighlight}
                                recolorHighlight={recolorHighlight}
                                addAnnotation={addAnnotation}
                                updateAnnotation={updateAnnotation}
                                removeAnnotation={removeAnnotation}
                                setUserMessageReferences={setUserMessageReferences}
                                onOverlaysCreated={handleOverlaysCreated}
                                onRefreshUrl={refreshPdfUrl}
                                currentUser={user}
                                annotationsPanelActive={annotationsPanelActive}
                                onAskStarted={onAskStarted}
                                onOpenThread={onOpenThread}
                                isReadMode={isReadMode}
                                onToggleReadMode={handleToggleReadMode}
                                parentPaperId={parentPaperId}
                                displayedPaperId={displayedPaperId}
                                parentPaperTitle={paperData?.title ?? undefined}
                                supplementaryMaterials={supplementaryMaterials ?? []}
                                onChangeDisplayed={setDisplayedPaperId}
                                onSupplementaryUploaded={refetchSupplementaryMaterials}
                            />
                        </div>
                    )}
                </div>

                {/* Resizable Divider */}
                {rightSideFunction !== 'Read' && (
                    <div
                        className="w-2 bg-background hover:bg-blue-100 dark:hover:bg-blue-400 cursor-col-resize transition-colors duration-200 flex-shrink-0 h-full rounded-2xl"
                        onMouseDown={(e) => {
                            e.preventDefault();
                            setIsDragging(true);
                        }}
                    />
                )}

                {/* Right Side Panel */}
                <div
                    className="flex flex-row h-full relative"
                    style={{
                        width: rightSideFunction !== 'Read' ? `${100 - leftPanelWidth}%` : 'auto',
                        transition: isDragging ? 'none' : 'width 300ms ease',
                    }}
                >
                    {jobId ? (
                        <div className="flex flex-col h-full w-full">
                            <div className="flex items-center justify-center w-full px-6 py-4 border-b border-gray-100 dark:border-gray-800/50">
                                <div className="flex items-center gap-3">
                                    <div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                                    <p className="text-sm text-muted-foreground">{sidePanelDisplayedText}</p>
                                    <span className="text-xs text-muted-foreground/50 tabular-nums">{elapsedTime}s</span>
                                </div>
                            </div>
                            <ReportSkeleton />
                        </div>
                    ) : (
                        <>
                            <SidePanelContent {...sidePanelProps} isMobile={false} />
                            <PaperSidebar
                                rightSideFunction={rightSideFunction}
                                setRightSideFunction={setRightSideFunction}
                                PaperToolset={toolset}
                            />
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
