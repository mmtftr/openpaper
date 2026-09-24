'use client';

import { useEffect } from 'react';

import PaperViewSkeleton from '@/components/PaperViewSkeleton';
import { DesktopPaperLayout } from '@/components/paper/DesktopPaperLayout';
import { MobilePaperLayout } from '@/components/paper/MobilePaperLayout';
import { paperAtom, paperLoadingAtom } from '@/components/paper/paperStore';
import { usePaperAtomValue } from '@/components/paper/PaperStoreProvider';
import { usePaperDocumentTitle, usePaperLoader } from '@/components/paper/usePaperLoader';
import { usePaperHighlightsSync } from '@/components/paper/usePaperHighlights';
import { useSidePanelTabSync } from '@/components/paper/useSidePanelTabSync';
import { useIsMobile } from '@/hooks/use-mobile';
import { useAuth } from '@/lib/auth';

/**
 * The paper page. Its state lives in the paper store (`components/paper/`,
 * provided by the paper layout); this component mounts the page-level
 * upkeep once and picks the desktop or mobile layout.
 */
export default function PaperView() {
    const { user, loading: authLoading } = useAuth();
    useEffect(() => {
        if (!authLoading && !user) window.location.href = '/login';
    }, [authLoading, user]);

    usePaperLoader();
    usePaperDocumentTitle();
    useSidePanelTabSync();
    usePaperHighlightsSync();

    const isMobile = useIsMobile();
    const loading = usePaperAtomValue(paperLoadingAtom);
    const paper = usePaperAtomValue(paperAtom);

    if (loading) return <PaperViewSkeleton />;
    if (!paper) return null;
    return isMobile ? <MobilePaperLayout /> : <DesktopPaperLayout />;
}
