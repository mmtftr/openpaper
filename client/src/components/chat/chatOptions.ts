import type { Schemas } from "@/lib/api/client";

/**
 * Picker options for the paper chat composer (model / reasoning effort /
 * context mode), shared by the composer and the chat session's request body.
 */

type PaperChatRequest = Schemas["PaperChatRequest"];

export type ReasoningEffort = NonNullable<PaperChatRequest["reasoning_effort"]>;

export const REASONING_EFFORT_OPTIONS: { id: ReasoningEffort; label: string }[] = [
    { id: "low", label: "Low" },
    { id: "medium", label: "Medium" },
    { id: "high", label: "High" },
    { id: "xhigh", label: "xhigh" },
];

export const REASONING_EFFORT_VALUES: ReasoningEffort[] = ["low", "medium", "high", "xhigh"];

export type ContextMode = NonNullable<PaperChatRequest["context_mode"]>;

export interface ContextModeOption {
    id: ContextMode;
    label: string;
    subtitle: string;
    recommended: boolean;
    forParser: "mistral" | "pymupdf";
}

// Each mode is gated by which parser ran on the paper. The picker hides
// modes that don't apply (Raw only on pymupdf-parsed papers; the rest only
// on Mistral-parsed papers).
export const CONTEXT_MODE_OPTIONS: ContextModeOption[] = [
    {
        id: "adaptive",
        label: "Adaptive",
        subtitle:
            "Includes abstract, intro and conclusion with model-selected access to the rest",
        recommended: true,
        forParser: "mistral",
    },
    {
        id: "comprehensive",
        label: "Comprehensive",
        subtitle:
            "Includes the main paper content and all the figures, excl. references and appendix",
        recommended: true,
        forParser: "mistral",
    },
    {
        id: "full",
        label: "Full",
        subtitle: "Includes the full paper content (slow, expensive)",
        recommended: false,
        forParser: "mistral",
    },
    {
        id: "raw",
        label: "Raw",
        subtitle:
            "Includes references and appendices, no figures (fallback parsing)",
        recommended: false,
        forParser: "pymupdf",
    },
];

/** `GET /api/message/models` -> `models[]`. */
export type ModelOption = Schemas["ChatModelOption"];

// The same model id can exist under two providers (e.g. gpt-5.5 on Azure
// AND on the codex proxy), so selection is keyed by provider too.
export const modelKey = (m: Pick<ModelOption, "id" | "provider">) =>
    `${m.provider}::${m.id}`;
