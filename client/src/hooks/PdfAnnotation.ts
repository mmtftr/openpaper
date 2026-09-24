import {
    PaperHighlightAnnotation
} from '@/lib/schema';
import { fetchFromApi } from '@/lib/api';
import { useEffect, useRef, useState } from 'react';

export function useAnnotations(paperId: string) {
    const [annotations, setAnnotations] = useState<PaperHighlightAnnotation[]>([]);
    // The paper whose annotations are wanted right now; responses for a paper
    // the reader has since switched away from are dropped.
    const currentPaperIdRef = useRef(paperId);
    currentPaperIdRef.current = paperId;

    const addAnnotation = async (highlightId: string, content: string) => {
        const newAnnotation: Partial<PaperHighlightAnnotation> = {
            highlight_id: highlightId,
            paper_id: paperId,
            content,
        };

        try {
            const savedAnnotation: PaperHighlightAnnotation = await fetchFromApi('/api/annotation/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(newAnnotation),
            });
            if (currentPaperIdRef.current === paperId) {
                setAnnotations(prev => [...prev, savedAnnotation]);
            }
            return savedAnnotation;
        } catch (error) {
            console.error('Error saving annotation:', error);
            throw error;
        }
    };

    const removeAnnotation = async (annotationId: string) => {
        try {
            await fetchFromApi(`/api/annotation/${annotationId}`, {
                method: 'DELETE',
            });

            setAnnotations(prev => prev.filter(a => a.id !== annotationId));
        } catch (error) {
            console.error('Error removing annotation:', error);
            throw error;
        }
    };

    const updateAnnotation = async (annotationId: string, content: string) => {
        try {
            const updatedAnnotation: PaperHighlightAnnotation = await fetchFromApi(`/api/annotation/${annotationId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    content,
                }),
            });

            setAnnotations(prev =>
                prev.map(a => (a.id === annotationId ? updatedAnnotation : a))
            );
            return updatedAnnotation;
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

            if (currentPaperIdRef.current === paperId) {
                setAnnotations(loadedAnnotations);
            }
            return loadedAnnotations;
        } catch (error) {
            console.error('Error loading annotations:', error);
            throw error;
        }
    };

    const refreshAnnotations = async () => {
        await fetchAnnotations();
    };

    // Load on mount and whenever the displayed paper changes.
    useEffect(() => {
        setAnnotations([]);
        fetchAnnotations().catch(() => {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [paperId]);

    return {
        annotations,
        addAnnotation,
        removeAnnotation,
        updateAnnotation,
        refreshAnnotations,
    };
}
