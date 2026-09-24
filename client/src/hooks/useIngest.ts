"use client";

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import { api, unwrap, type Schemas } from "@/lib/api/client";

/**
 * Ingest progress for one paper (`GET /api/paper/{paper_id}/ingest`).
 *
 * Polls every second while a stage is pending/queued/running (every 5 s
 * while the worker is offline) and stops once nothing is left to do. Every
 * component on the page shares the one SWR entry per paper, so calling this
 * from several places costs one request per tick.
 *
 * Papers without stage rows (processed before ingest v2) come back with
 * `legacy: true` and every feature enabled.
 */

export type IngestStatus = Schemas["IngestStatusResponse"];
export type IngestStage = Schemas["IngestStageState"];
export type IngestFeature = keyof Schemas["IngestFeatures"];

type IngestKey = readonly ["/api/paper/{paper_id}/ingest", string];

export const ingestKey = (paperId: string): IngestKey => ["/api/paper/{paper_id}/ingest", paperId];

const loadIngest = ([, paperId]: IngestKey) =>
    unwrap(api.GET("/api/paper/{paper_id}/ingest", { params: { path: { paper_id: paperId } } }));

export function stageOf(status: IngestStatus | undefined, name: string): IngestStage | undefined {
    return status?.stages.find((stage) => stage.name === name);
}

function pollInterval(status: IngestStatus | undefined): number {
    if (!status?.active) return 0;
    return status.worker_online ? 1000 : 5000;
}

export function useIngest(paperId: string | null | undefined) {
    // A plain number, not SWR's function form: once that returns 0 SWR's
    // polling loop ends for good, and a Retry wouldn't start it again.
    const [interval, setPollInterval] = useState(0);
    const { data, error, isLoading, mutate } = useSWR(paperId ? ingestKey(paperId) : null, loadIngest, {
        refreshInterval: interval,
        // The default 2 s dedupe window would swallow every other 1 s poll.
        dedupingInterval: 500,
    });
    const wanted = pollInterval(data);
    if (wanted !== interval) setPollInterval(wanted); // adjusting state while rendering

    const retry = useCallback(
        async (stage: string) => {
            if (!paperId) return;
            const next = await unwrap(
                api.POST("/api/paper/{paper_id}/ingest/{stage}/retry", {
                    params: { path: { paper_id: paperId, stage } },
                }),
            );
            await mutate(next, { revalidate: false });
        },
        [paperId, mutate],
    );

    const reprocess = useCallback(
        async (stage: string) => {
            if (!paperId) return;
            const next = await unwrap(
                api.POST("/api/paper/{paper_id}/ingest/{stage}/reprocess", {
                    params: { path: { paper_id: paperId, stage } },
                }),
            );
            await mutate(next, { revalidate: false });
        },
        [paperId, mutate],
    );

    return { status: data, error, isLoading, retry, reprocess };
}

/**
 * A value to put in the SWR key (or effect deps) of a view built from the
 * output of `stages`: "" until one of them finishes (again) while the page is
 * open, then that stage's `finished_at`. The value seen on the first load is
 * the baseline, so opening a finished paper doesn't fetch everything twice.
 */
export function useStageRefreshKey(paperId: string | null | undefined, stages: readonly string[]): string {
    const { status } = useIngest(paperId);
    const current = status ? stages.map((name) => stageOf(status, name)?.finished_at ?? "").join("|") : null;
    const [baseline, setBaseline] = useState<{ paperId: string; value: string } | null>(null);
    const baselineStale = !baseline || baseline.paperId !== paperId;
    if (current !== null && paperId && baselineStale) {
        // Adjusting state while rendering: React re-renders right away.
        setBaseline({ paperId, value: current });
    }
    if (current === null || baselineStale || baseline.value === current) return "";
    return current;
}

/** `Date.now()`, re-rendered every second while `ticking`. */
export function useNow(ticking: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!ticking) return;
        setNow(Date.now());
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [ticking]);
    return now;
}

/** Seconds until a queued stage's backed-off retry, or null. */
export function retryInSeconds(stage: IngestStage, now: number): number | null {
    if (stage.status !== "queued" || !stage.next_attempt_at) return null;
    const seconds = Math.ceil((Date.parse(stage.next_attempt_at) - now) / 1000);
    return seconds > 0 ? seconds : null;
}

export interface FeatureGateState {
    /** The status has loaded (or failed to load, which doesn't gate anything). */
    ready: boolean;
    enabled: boolean;
    /** Why it's disabled: "Needs OCR — retrying in 18 s". */
    message: string | null;
    /** A failed stage to offer a Retry button for. */
    retryStage: string | null;
    /** False when the feature doesn't apply to this document at all. */
    applicable: boolean;
    inProgress: boolean;
    workerOffline: boolean;
    retry: () => Promise<void>;
}

/** Whether `feature` is usable for this paper, and if not, why. */
export function useFeatureGate(paperId: string | null | undefined, feature: IngestFeature): FeatureGateState {
    const { status, error, retry } = useIngest(paperId);
    const state = status?.features[feature];
    const cause = state?.cause ? stageOf(status, state.cause) : undefined;
    const now = useNow(!!cause && cause.status === "queued" && !!cause.next_attempt_at);
    const retryCause = useCallback(async () => {
        if (cause) await retry(cause.name);
    }, [cause, retry]);

    const base = { retry: retryCause, retryStage: null, inProgress: false, workerOffline: false };
    // No paper, a failed status request, or a feature the server doesn't
    // know: never block the page on the progress endpoint.
    if (!paperId || error || (status && !state)) {
        return { ...base, ready: true, enabled: true, message: null, applicable: true };
    }
    if (!status || !state) {
        return { ...base, ready: false, enabled: false, message: null, applicable: true };
    }
    if (state.enabled) {
        return { ...base, ready: true, enabled: true, message: null, applicable: true };
    }
    if (!cause) {
        return {
            ...base,
            ready: true,
            enabled: false,
            message: state.reason ?? "Not available for this document",
            applicable: false,
        };
    }

    const needs = `Needs ${cause.label}`;
    const common = { ...base, ready: true, enabled: false, applicable: true };
    if (cause.status === "failed") {
        const detail = cause.error_message ? `: ${cause.error_message}` : "";
        return { ...common, message: `${needs} — failed${detail}`, retryStage: cause.name };
    }
    if (!status.worker_online) {
        return { ...common, message: "Ingest worker offline", workerOffline: true };
    }
    const retryIn = retryInSeconds(cause, now);
    let detail = "waiting";
    if (retryIn !== null) detail = `retrying in ${retryIn} s`;
    else if (cause.status === "running" && cause.progress_total) {
        detail = `${cause.progress_done ?? 0}/${cause.progress_total}`;
    } else if (cause.status === "running" || cause.status === "queued") detail = "in progress";
    return { ...common, message: `${needs} — ${detail}`, inProgress: true };
}
