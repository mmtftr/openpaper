import {
    PaperHighlightAnnotation
} from '@/lib/schema';
import { api, unwrap } from '@/lib/api/client';
import useSWR from 'swr';

const EMPTY: PaperHighlightAnnotation[] = [];

export function useAnnotations(paperId: string) {
    // Cached per paper: a response (or a save) for a paper the reader has
    // since switched away from lands in that paper's entry, not this one.
    const { data, mutate } = useSWR(
        paperId ? ['/api/annotation/{paper_id}', paperId] : null,
        async ([, id]: [string, string]): Promise<PaperHighlightAnnotation[]> => {
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

    const addAnnotation = async (highlightId: string, content: string) => {
        try {
            const savedAnnotation = await unwrap(api.POST('/api/annotation', {
                body: { highlight_id: highlightId, paper_id: paperId, content },
            }));
            await mutate(prev => [...(prev ?? []), savedAnnotation], { revalidate: false });
            return savedAnnotation;
        } catch (error) {
            console.error('Error saving annotation:', error);
            throw error;
        }
    };

    const removeAnnotation = async (annotationId: string) => {
        try {
            await unwrap(api.DELETE('/api/annotation/{annotation_id}', {
                params: { path: { annotation_id: annotationId } },
            }));
            await mutate(prev => prev?.filter(a => a.id !== annotationId), { revalidate: false });
        } catch (error) {
            console.error('Error removing annotation:', error);
            throw error;
        }
    };

    const updateAnnotation = async (annotationId: string, content: string) => {
        try {
            const updatedAnnotation = await unwrap(api.PATCH('/api/annotation/{annotation_id}', {
                params: { path: { annotation_id: annotationId } },
                body: { content },
            }));
            await mutate(
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
