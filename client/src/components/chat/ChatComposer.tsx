"use client";

import {
    FormEvent,
    forwardRef,
    memo,
    useImperativeHandle,
    useMemo,
    useState,
} from "react";
import {
    AlertTriangleIcon,
    BookOpenIcon,
    BrainIcon,
    CheckIcon,
    CpuIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    PromptInput,
    PromptInputBody,
    PromptInputFooter,
    PromptInputMessage,
    PromptInputSubmit,
    PromptInputTextarea,
    PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import {
    CONTEXT_MODE_OPTIONS,
    ContextMode,
    ContextModeOption,
    ModelOption,
    modelKey,
    REASONING_EFFORT_OPTIONS,
    ReasoningEffort,
} from "@/components/chat/chatOptions";

export interface ChatComposerHandle {
    /** Replace the draft (cleared after a send, restored by "Edit"). */
    setText: (text: string) => void;
}

interface ChatComposerProps {
    isStreaming: boolean;
    /** False until the conversation exists. */
    canSend: boolean;
    /** Send `text`. The parent clears the draft through the handle. */
    onSubmit: (text: string) => void;
    onStop: () => void;

    availableModels: ModelOption[];
    selectedModel: string;
    onSelectModel: (key: string) => void;

    supportsReasoningEffort: boolean;
    reasoningEffort: ReasoningEffort;
    onSelectReasoningEffort: (effort: ReasoningEffort) => void;

    availableContextModes: ContextModeOption[];
    contextMode: ContextMode;
    onSelectContextMode: (mode: ContextMode) => void;
}

const providerLabel = (provider: string) =>
    provider.charAt(0).toUpperCase() + provider.slice(1);

/**
 * The chat input plus its model / reasoning-effort / context pickers.
 *
 * The draft text lives here, not in the panel: a keystroke re-renders only
 * the composer, never the transcript.
 */
export const ChatComposer = memo(
    forwardRef<ChatComposerHandle, ChatComposerProps>(function ChatComposer(
        {
            isStreaming,
            canSend,
            onSubmit,
            onStop,
            availableModels,
            selectedModel,
            onSelectModel,
            supportsReasoningEffort,
            reasoningEffort,
            onSelectReasoningEffort,
            availableContextModes,
            contextMode,
            onSelectContextMode,
        },
        ref
    ) {
        const [text, setText] = useState("");
        useImperativeHandle(ref, () => ({ setText }), []);

        const modelLabel =
            availableModels.find((m) => modelKey(m) === selectedModel)?.name ??
            "Model";

        const modelsByProvider = useMemo(() => {
            const groups = new Map<string, ModelOption[]>();
            for (const m of availableModels) {
                const list = groups.get(m.provider) ?? [];
                list.push(m);
                groups.set(m.provider, list);
            }
            return Array.from(groups.entries());
        }, [availableModels]);

        const reasoningEffortLabel =
            REASONING_EFFORT_OPTIONS.find((o) => o.id === reasoningEffort)
                ?.label ?? "Medium";

        const contextModeLabel =
            CONTEXT_MODE_OPTIONS.find((m) => m.id === contextMode)?.label ??
            "Adaptive";

        const handleSubmit = (msg: PromptInputMessage, e: FormEvent) => {
            e.preventDefault();
            onSubmit(msg.text);
        };

        return (
            <PromptInput onSubmit={handleSubmit}>
                <PromptInputBody>
                    <PromptInputTextarea
                        value={text}
                        onChange={(e) => setText(e.currentTarget.value)}
                        placeholder="Ask something about this paper."
                        disabled={isStreaming}
                    />
                </PromptInputBody>
                <PromptInputFooter>
                    <PromptInputTools>
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                    disabled={isStreaming}
                                >
                                    <CpuIcon className="h-3.5 w-3.5" />
                                    <span className="truncate max-w-[10rem]">
                                        {modelLabel}
                                    </span>
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                align="start"
                                className="w-64 max-h-[60vh] overflow-y-auto"
                            >
                                {availableModels.length === 0 ? (
                                    <DropdownMenuItem disabled>
                                        No models available
                                    </DropdownMenuItem>
                                ) : (
                                    modelsByProvider.map(([provider, items], gi) => (
                                        <div key={provider}>
                                            {gi > 0 && <DropdownMenuSeparator />}
                                            <DropdownMenuLabel className="text-xs text-muted-foreground">
                                                {providerLabel(provider)}
                                            </DropdownMenuLabel>
                                            {items.map((m) => (
                                                <DropdownMenuItem
                                                    key={modelKey(m)}
                                                    onClick={() =>
                                                        onSelectModel(modelKey(m))
                                                    }
                                                    className="flex items-center justify-between"
                                                >
                                                    <span className="truncate">
                                                        {m.name}
                                                    </span>
                                                    {modelKey(m) === selectedModel && (
                                                        <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0" />
                                                    )}
                                                </DropdownMenuItem>
                                            ))}
                                        </div>
                                    ))
                                )}
                            </DropdownMenuContent>
                        </DropdownMenu>

                        {supportsReasoningEffort && (
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                        disabled={isStreaming}
                                        aria-label={`Reasoning effort: ${reasoningEffortLabel}`}
                                    >
                                        <BrainIcon className="h-3.5 w-3.5" />
                                        <span className="truncate max-w-[7rem]">
                                            {reasoningEffortLabel}
                                        </span>
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="start" className="w-44">
                                    <DropdownMenuLabel className="text-xs text-muted-foreground">
                                        Reasoning effort
                                    </DropdownMenuLabel>
                                    <DropdownMenuSeparator />
                                    {REASONING_EFFORT_OPTIONS.map((opt) => (
                                        <DropdownMenuItem
                                            key={opt.id}
                                            onClick={() => onSelectReasoningEffort(opt.id)}
                                            className="flex items-center justify-between"
                                        >
                                            <span>{opt.label}</span>
                                            {opt.id === reasoningEffort && (
                                                <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0" />
                                            )}
                                        </DropdownMenuItem>
                                    ))}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}

                        {availableContextModes.length > 1 && (
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                        disabled={isStreaming}
                                        aria-label={`Context: ${contextModeLabel}`}
                                    >
                                        <BookOpenIcon className="h-3.5 w-3.5" />
                                        <span className="truncate max-w-[8rem]">
                                            {contextModeLabel}
                                        </span>
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="start" className="w-80">
                                    <DropdownMenuLabel className="text-xs text-muted-foreground">
                                        Paper context
                                    </DropdownMenuLabel>
                                    <DropdownMenuSeparator />
                                    {availableContextModes.map((opt) => (
                                        <DropdownMenuItem
                                            key={opt.id}
                                            onClick={() => onSelectContextMode(opt.id)}
                                            className="flex items-start gap-2 py-2"
                                        >
                                            <div className="flex flex-col flex-1 min-w-0">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="font-medium">
                                                        {opt.label}
                                                    </span>
                                                    {!opt.recommended && (
                                                        <AlertTriangleIcon className="h-3 w-3 text-amber-500" />
                                                    )}
                                                </div>
                                                <span className="text-xs text-muted-foreground">
                                                    {opt.subtitle}
                                                </span>
                                            </div>
                                            {opt.id === contextMode && (
                                                <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0 mt-1" />
                                            )}
                                        </DropdownMenuItem>
                                    ))}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}
                    </PromptInputTools>
                    <PromptInputSubmit
                        status={isStreaming ? "streaming" : ("ready" as const)}
                        type={isStreaming ? "button" : "submit"}
                        aria-label={isStreaming ? "Stop generating" : "Send message"}
                        onClick={
                            isStreaming
                                ? (e) => {
                                      e.preventDefault();
                                      onStop();
                                  }
                                : undefined
                        }
                        disabled={isStreaming ? false : !text.trim() || !canSend}
                    />
                </PromptInputFooter>
            </PromptInput>
        );
    })
);
