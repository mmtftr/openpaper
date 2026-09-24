"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import {
    CONTEXT_MODE_OPTIONS,
    ContextMode,
    DEFAULT_MODEL_KEY,
    ModelOption,
    modelKey,
    REASONING_EFFORT_VALUES,
    ReasoningEffort,
} from "./chatOptions";

const CONTEXT_MODE_LS_KEY = "openpaper:paper-context-mode";
const SELECTED_MODEL_LS_KEY = "openpaper:paper-chat-model";
const REASONING_EFFORT_LS_KEY = "openpaper:paper-reasoning-effort";

const readStored = (key: string) =>
    typeof window === "undefined" ? null : window.localStorage.getItem(key);

/**
 * The composer's model / reasoning effort / context mode choices. Each sticks
 * in localStorage; a stored model the server no longer offers falls back to
 * "Default" (the server's slot), a stored context mode that no longer exists
 * (the old "Raw") to the first option.
 */
export function useChatModelOptions() {
    const [selectedModel, setSelectedModel] = useState<string>(() =>
        typeof window === "undefined" ? "" : readStored(SELECTED_MODEL_LS_KEY) || DEFAULT_MODEL_KEY
    );
    useEffect(() => {
        if (selectedModel) window.localStorage.setItem(SELECTED_MODEL_LS_KEY, selectedModel);
    }, [selectedModel]);

    const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(() => {
        const stored = readStored(REASONING_EFFORT_LS_KEY);
        return REASONING_EFFORT_VALUES.includes(stored as ReasoningEffort)
            ? (stored as ReasoningEffort)
            : "medium";
    });
    useEffect(() => {
        window.localStorage.setItem(REASONING_EFFORT_LS_KEY, reasoningEffort);
    }, [reasoningEffort]);

    const availableContextModes = CONTEXT_MODE_OPTIONS;
    const [contextMode, setContextMode] = useState<ContextMode>(
        () => (readStored(CONTEXT_MODE_LS_KEY) as ContextMode | null) ?? "adaptive"
    );
    useEffect(() => {
        if (
            !availableContextModes.some((m) => m.id === contextMode) &&
            availableContextModes[0]
        ) {
            setContextMode(availableContextModes[0].id);
        }
    }, [availableContextModes, contextMode]);
    useEffect(() => {
        window.localStorage.setItem(CONTEXT_MODE_LS_KEY, contextMode);
    }, [contextMode]);

    const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
    const { data: chatModels } = useSWR(
        "/api/message/models",
        () => unwrap(api.GET("/api/message/models")),
        { onError: (err) => console.error("Error fetching available models:", err) }
    );
    useEffect(() => {
        if (!chatModels) return;
        const models = chatModels.models;
        if (models.length === 0) return;
        setAvailableModels(models);
        // Honor the persisted choice if it's still offered. (Legacy stored
        // values are bare model ids — upgrade them to the provider-qualified key.)
        setSelectedModel((current) => {
            if (current === DEFAULT_MODEL_KEY) return current;
            if (current && models.some((m) => modelKey(m) === current)) return current;
            const legacy = models.find((m) => m.id === current);
            return legacy ? modelKey(legacy) : DEFAULT_MODEL_KEY;
        });
    }, [chatModels]);

    // "Default (<model>)": what the server resolves when no model is sent.
    const defaultModelLabel = useMemo(() => {
        if (!chatModels) return "Default";
        const resolved = chatModels.models.find(
            (m) => m.id === chatModels.default && m.provider === chatModels.default_provider
        );
        const effort = chatModels.default_reasoning_effort;
        return `Default (${resolved?.name ?? chatModels.default}${effort ? `, ${effort}` : ""})`;
    }, [chatModels]);

    const selectedModelOption = useMemo(
        () => availableModels.find((m) => modelKey(m) === selectedModel),
        [availableModels, selectedModel]
    );
    const supportsReasoningEffort = selectedModelOption?.supports_reasoning_effort ?? false;

    return {
        availableModels,
        defaultModelLabel,
        selectedModel,
        setSelectedModel,
        selectedModelOption,
        supportsReasoningEffort,
        reasoningEffort,
        setReasoningEffort,
        availableContextModes,
        contextMode,
        setContextMode,
    };
}

export type ChatModelOptions = ReturnType<typeof useChatModelOptions>;
