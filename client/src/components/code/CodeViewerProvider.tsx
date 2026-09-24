"use client";

import {
    createContext,
    useCallback,
    useContext,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from "react";

import { CodeViewerDialog } from "@/components/code/CodeViewerDialog";
import type { CodeQuestionModel } from "@/lib/quickQuestionApi";

/**
 * Single code-viewer instance for a paper's chat surface. Citations, tool
 * chips and the repo-connect UI all open the same dialog through this context
 * instead of threading dialog state through every message component.
 */

export interface CodeViewerTarget {
    /** Repo-root-relative path. Omit to open the browser with no file. */
    path?: string;
    startLine?: number | null;
    endLine?: number | null;
    /**
     * Revision the line numbers were verified against (a citation's
     * permalink sha). The dialog warns when the connected snapshot differs.
     */
    commitSha?: string | null;
}

export interface CodeViewerRequest extends CodeViewerTarget {
    /** Bumped on every open so re-clicking the same citation re-scrolls. */
    requestId: number;
}

interface CodeViewerContextValue {
    openCodeViewer: (target?: CodeViewerTarget) => void;
    /**
     * Attach a snippet to the chat composer's pending references, the same
     * place PDF text selections land. Undefined when the host surface has no
     * composer to attach to — callers hide the affordance then.
     */
    attachReference?: (reference: string) => void;
}

const NOOP_VIEWER: CodeViewerContextValue = { openCodeViewer: () => {} };

const CodeViewerContext = createContext<CodeViewerContextValue | null>(null);

/**
 * Opens the repo code viewer. Outside a provider this is a no-op, so shared
 * markdown/citation components stay safe on surfaces without a repo.
 */
export function useCodeViewer(): CodeViewerContextValue {
    return useContext(CodeViewerContext) ?? NOOP_VIEWER;
}

interface CodeViewerProviderProps {
    paperId: string;
    /** Adds a snippet to the chat composer's pending references. */
    onAttachReference?: (reference: string) => void;
    /**
     * Provider/model/effort the chat model picker currently has selected, so
     * an inline quick question is answered by the same model as the chat.
     */
    chatModel?: CodeQuestionModel | null;
    children: ReactNode;
}

export function CodeViewerProvider({
    paperId,
    onAttachReference,
    chatModel,
    children,
}: CodeViewerProviderProps) {
    const [open, setOpen] = useState(false);
    const [request, setRequest] = useState<CodeViewerRequest | null>(null);
    const requestCounter = useRef(0);

    const openCodeViewer = useCallback((target?: CodeViewerTarget) => {
        requestCounter.current += 1;
        setRequest({ ...(target ?? {}), requestId: requestCounter.current });
        setOpen(true);
    }, []);

    const value = useMemo(
        () => ({ openCodeViewer, attachReference: onAttachReference }),
        [openCodeViewer, onAttachReference]
    );

    return (
        <CodeViewerContext.Provider value={value}>
            {children}
            <CodeViewerDialog
                paperId={paperId}
                open={open}
                onOpenChange={setOpen}
                request={request}
                chatModel={chatModel}
            />
        </CodeViewerContext.Provider>
    );
}
