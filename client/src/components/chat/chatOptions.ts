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
}

export const CONTEXT_MODE_OPTIONS: ContextModeOption[] = [
    {
        id: "adaptive",
        label: "Adaptive",
        subtitle:
            "Includes abstract, intro and conclusion with model-selected access to the rest",
        recommended: true,
    },
    {
        id: "comprehensive",
        label: "Comprehensive",
        subtitle:
            "Includes the main paper content and all the figures, excl. references and appendix",
        recommended: true,
    },
    {
        id: "full",
        label: "Full",
        subtitle: "Includes the full paper content (slow, expensive)",
        recommended: false,
    },
];

/** `GET /api/message/models` -> `models[]`. */
export type ModelOption = Schemas["ChatModelOption"];

// The same model id can exist under two providers (e.g. gpt-5.5 on Azure
// AND on the codex proxy), so selection is keyed by provider too.
export const modelKey = (m: Pick<ModelOption, "id" | "provider">) =>
    `${m.provider}::${m.id}`;

/**
 * Picker value for "no explicit pick": the request carries no model,
 * provider or reasoning effort, so the server resolves the `chat.default`
 * (or `quick_question`) slot, including any override set in settings.
 */
export const DEFAULT_MODEL_KEY = "default";
