import {
    PaperHighlightAnnotation
} from '@/lib/schema';
import { fetchFromApi } from '@/lib/api';
import { useEffect, useState } from 'react';
import {
    cacheAnnotations,
    getCachedAnnotations,
    queueAnnotationCreate,
    queueAnnotationDelete,
    queueAnnotationUpdate,
    replayOutbox,
} from '@/lib/offline';
import { nanoid } from 'nanoid';

export function useAnnotations(paperId: string) {
    const [annotations, setAnnotations] = useState<PaperHighlightAnnotation[]>([]);

    const addAnnotation = async (highlightId: string, content: string) => {
        const localAnnotation: PaperHighlightAnnotation = {
            id: `local:${nanoid()}`,
            highlight_id: highlightId,
            paper_id: paperId,
            content,
            role: 'user',
            created_at: new Date().toISOString(),
        };

        try {
            const nextAnnotations = [...annotations, localAnnotation];
            setAnnotations(nextAnnotations);
            await cacheAnnotations(paperId, nextAnnotations);
            await queueAnnotationCreate({
                localId: localAnnotation.id,
                paperId,
                highlightId,
                content,
            });
            if (typeof navigator === 'undefined' || navigator.onLine) {
                void replayOutbox();
            }
            return localAnnotation;
        } catch (error) {
            console.error('Error saving annotation:', error);
            throw error;
        }
    };

    const removeAnnotation = async (annotationId: string) => {
        try {
            const nextAnnotations = annotations.filter(a => a.id !== annotationId);
            setAnnotations(nextAnnotations);
            await cacheAnnotations(paperId, nextAnnotations);
            await queueAnnotationDelete({ annotationId }, paperId);
            if (typeof navigator === 'undefined' || navigator.onLine) {
                void replayOutbox();
            }
        } catch (error) {
            console.error('Error removing annotation:', error);
            throw error;
        }
    };

    const updateAnnotation = async (annotationId: string, content: string) => {
        try {
            const nextAnnotations = annotations.map(a =>
                a.id === annotationId ? { ...a, content } : a
            );
            setAnnotations(nextAnnotations);
            await cacheAnnotations(paperId, nextAnnotations);
            await queueAnnotationUpdate({ annotationId, content }, paperId);
            if (typeof navigator === 'undefined' || navigator.onLine) {
                void replayOutbox();
            }
            return nextAnnotations.find(a => a.id === annotationId);
        } catch (error) {
            console.error('Error updating annotation:', error);
            throw error;
        }
    };

    const fetchAnnotations = async () => {
        try {
            const loadedAnnotations: PaperHighlightAnnotation[] = await fetchFromApi(`/api/annotation/${paperId}`, {
                method: 'GET',
            });

            setAnnotations(loadedAnnotations);
            await cacheAnnotations(paperId, loadedAnnotations);
            return loadedAnnotations;
        } catch (error) {
            console.error('Error loading annotations:', error);
            const cached = await getCachedAnnotations(paperId).catch(() => null);
            if (cached) {
                setAnnotations(cached.annotations);
                return cached.annotations;
            }
            throw error;
        }
    };

    const refreshAnnotations = async () => {
        await fetchAnnotations();
    };

    const renderAnnotations = (highlights: PaperHighlightAnnotation[]) => {
        for (const h of highlights) {
            const highlightAnnotations = annotations.filter(a => a.highlight_id === h.id);
            if (highlightAnnotations.length > 0) {
                // Find the highlight in the DOM, identified by the `data-highlight-id` attribute
                const highlightElement = document.querySelector(`[data-highlight-id="${h.id}"]`);
                if (highlightElement) {

                    const existingAnnotations = highlightElement.getElementsByClassName('annotation-tooltip');
                    if (existingAnnotations.length > 0) {
                        return; // Annotations already rendered
                    }
                    // Create a new div element for the annotation
                    const annotationElement = document.createElement('div');
                    annotationElement.classList.add('annotation-tooltip', 'absolute', 'bg-white', 'border', 'rounded', 'p-2', 'shadow-md', 'top-2', '-right-2', 'z-10', 'bg-yellow-300', 'rounded-full', 'w-4', 'h-4', 'z-10');

                    // Append the annotation element to the highlight element
                    highlightElement.appendChild(annotationElement);
                }
            }
        }
    };

    const getAnnotationsForHighlight = (highlightId: string) => {
        return annotations.filter(a => a.highlight_id === highlightId);
    };

    useEffect(() => {
        fetchAnnotations();
    }, []);

    return {
        annotations,
        setAnnotations,
        addAnnotation,
        removeAnnotation,
        updateAnnotation,
        fetchAnnotations,
        refreshAnnotations,
        getAnnotationsForHighlight,
        renderAnnotations,
    };
}
