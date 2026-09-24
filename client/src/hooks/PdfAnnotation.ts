import {
    PaperHighlightAnnotation
} from '@/lib/schema';
import { api, unwrap } from '@/lib/api/client';
import useSWR, { useSWRConfig } from 'swr';

const EMPTY: PaperHighlightAnnotation[] = [];

// `refreshKey` changes when ingest's AI highlights stage finishes: its
// highlights come with annotation threads, and `AnnotationsView` hides a
// highlight whose thread isn't loaded (see `useStageRefreshKey`).
const annotationsKey = (paperId: string, refreshKey: string): [string, string, string] => [
    '/api/annotation/{paper_id}',
    paperId,
    refreshKey,
];

export function useAnnotations(paperId: string, refreshKey = '') {
    // Cached per paper. The hook's bound `mutate` always targets the CURRENT
    // key, so writes after an await go through the global `mutate` with the
    // key captured before the request: if the reader switched papers
    // meanwhile, the result lands in the paper it was made for.
    const { data, mutate } = useSWR(
        paperId ? annotationsKey(paperId, refreshKey) : null,
        async ([, id]: [string, string, string]): Promise<PaperHighlightAnnotation[]> => {
            try {
                return await unwrap(api.GET('/api/annotation/{paper_id}', {
                    params: { path: { paper_id: id } },
                }));
            } catch (error) {
                console.error('Error loading annotations:', error);
                throw error;
            }
        },
    );
    const annotations = data ?? EMPTY;
    const { mutate: globalMutate } = useSWRConfig();

    const addAnnotation = async (highlightId: string, content: string) => {
        const key = annotationsKey(paperId, refreshKey);
        try {
            const savedAnnotation = await unwrap(api.POST('/api/annotation', {
                body: { highlight_id: highlightId, paper_id: paperId, content },
            }));
            await globalMutate<PaperHighlightAnnotation[]>(
                key,
                prev => [...(prev ?? []), savedAnnotation],
                { revalidate: false },
            );
            return savedAnnotation;
        } catch (error) {
            console.error('Error saving annotation:', error);
            throw error;
        }
    };

    const removeAnnotation = async (annotationId: string) => {
        const key = annotationsKey(paperId, refreshKey);
        try {
            await unwrap(api.DELETE('/api/annotation/{annotation_id}', {
                params: { path: { annotation_id: annotationId } },
            }));
            await globalMutate<PaperHighlightAnnotation[]>(
                key,
                prev => prev?.filter(a => a.id !== annotationId),
                { revalidate: false },
            );
        } catch (error) {
            console.error('Error removing annotation:', error);
            throw error;
        }
    };

    const updateAnnotation = async (annotationId: string, content: string) => {
        const key = annotationsKey(paperId, refreshKey);
        try {
            const updatedAnnotation = await unwrap(api.PATCH('/api/annotation/{annotation_id}', {
                params: { path: { annotation_id: annotationId } },
                body: { content },
            }));
            await globalMutate<PaperHighlightAnnotation[]>(
                key,
                prev => prev?.map(a => (a.id === annotationId ? updatedAnnotation : a)),
                { revalidate: false },
            );
            return updatedAnnotation;
        } catch (error) {
            console.error('Error updating annotation:', error);
            throw error;
        }
    };

    const refreshAnnotations = async () => {
        await mutate();
    };

    return {
        annotations,
        addAnnotation,
        removeAnnotation,
        updateAnnotation,
        refreshAnnotations,
    };
}
