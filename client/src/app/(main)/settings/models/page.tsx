"use client"

import { Fragment, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectSeparator,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { useModelSettings } from "@/hooks/useModelSettings";
import { useAuth } from "@/lib/auth";
import {
	ModelProvider,
	ModelSettings,
	ModelSlot,
	ModelSlotUpdate,
	OcrService,
	ReasoningEffort,
	SelectableModel,
} from "@/lib/schema";

const DEFAULT_VALUE = "__default__";

const REASONING_EFFORT_OPTIONS: { id: ReasoningEffort; label: string }[] = [
	{ id: "low", label: "Low" },
	{ id: "medium", label: "Medium" },
	{ id: "high", label: "High" },
	{ id: "xhigh", label: "xhigh" },
	{ id: "max", label: "max" },
];

// Same labelling as the chat model picker.
const providerLabel = (provider: string) =>
	provider.charAt(0).toUpperCase() + provider.slice(1);

// `provider::model`; an empty model means "the provider's own model for
// the slot's role" (follows the provider's env config).
const choiceKey = (provider: string, model?: string | null) =>
	`${provider}::${model ?? ""}`;

function parseChoiceKey(key: string): { provider: string | null; model: string | null } {
	if (key === DEFAULT_VALUE) return { provider: null, model: null };
	const [provider, model] = key.split("::");
	return { provider: provider || null, model: model || null };
}

function currentChoiceKey(slot: ModelSlot): string {
	const o = slot.override;
	if (!o || (!o.provider && !o.model)) return DEFAULT_VALUE;
	return choiceKey(o.provider ?? "", o.model);
}

function describe(choice: ModelSlot["default"]) {
	return `${choice.model_name} (${providerLabel(choice.provider)})`;
}

function SlotRow({
	slot,
	providers,
	modelsByProvider,
	models,
	onSave,
}: {
	slot: ModelSlot;
	providers: ModelProvider[];
	modelsByProvider: [string, SelectableModel[]][];
	models: SelectableModel[];
	onSave: (slot: string, update: ModelSlotUpdate) => Promise<void>;
}) {
	const [saving, setSaving] = useState(false);
	const selected = currentChoiceKey(slot);
	const effort = (slot.override?.reasoning_effort as ReasoningEffort | null) ?? null;

	const findModel = (provider: string, id: string) =>
		models.find((m) => m.provider === provider && m.id === id);

	const effective = findModel(slot.effective.provider, slot.effective.model);
	const supportsEffort = effective?.supports_reasoning_effort ?? false;

	// A stored choice the server no longer lists still needs an item to show.
	const knownKeys = new Set<string>([
		DEFAULT_VALUE,
		...providers.map((p) => choiceKey(p.id, null)),
		...models.map((m) => choiceKey(m.provider, m.id)),
	]);

	const save = async (update: ModelSlotUpdate) => {
		setSaving(true);
		try {
			await onSave(slot.slot, update);
		} finally {
			setSaving(false);
		}
	};

	const onModelChange = (key: string) => {
		const { provider, model } = parseChoiceKey(key);
		// Keep the effort only if the newly picked model can take it; the
		// server rejects an effort on a model without one.
		let target: SelectableModel | undefined;
		if (provider && model) {
			target = findModel(provider, model);
		} else if (provider) {
			const p = providers.find((x) => x.id === provider);
			const roleModel = slot.role === "fast" ? p?.fast_model : p?.default_model;
			target = roleModel ? findModel(provider, roleModel) : undefined;
		} else {
			target = findModel(slot.default.provider, slot.default.model);
		}
		const keepEffort = effort && target?.supports_reasoning_effort ? effort : null;
		void save({ provider, model, reasoning_effort: keepEffort });
	};

	const onEffortChange = (value: string) => {
		const { provider, model } = parseChoiceKey(selected);
		void save({
			provider,
			model,
			reasoning_effort: value === DEFAULT_VALUE ? null : (value as ReasoningEffort),
		});
	};

	return (
		<div className="space-y-3 rounded-xl border p-4">
			<div className="space-y-1">
				<div className="flex items-start gap-2">
					<h3 className="min-w-0 font-medium leading-snug">{slot.description || slot.slot}</h3>
					{saving && <Loader2 className="mt-1 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />}
				</div>
				<p className="break-all font-mono text-xs text-muted-foreground">{slot.slot}</p>
			</div>

			<div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
				<Select value={selected} onValueChange={onModelChange} disabled={saving}>
					<SelectTrigger aria-label="Model" className="w-full min-w-0 data-[size=default]:h-10 sm:w-80 sm:data-[size=default]:h-9">
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						<SelectItem value={DEFAULT_VALUE}>
							Default: {describe(slot.default)}
						</SelectItem>
						{!knownKeys.has(selected) && (
							<SelectItem value={selected}>
								{slot.override?.model ?? slot.override?.provider} (unavailable)
							</SelectItem>
						)}
						{modelsByProvider.map(([provider, items]) => {
							const p = providers.find((x) => x.id === provider);
							const roleModel =
								slot.role === "fast" ? p?.fast_model : p?.default_model;
							return (
								<SelectGroup key={provider}>
									<SelectSeparator />
									<SelectLabel>{providerLabel(provider)}</SelectLabel>
									{p && (
										<SelectItem value={choiceKey(provider, null)}>
											Its {slot.role} model ({roleModel})
										</SelectItem>
									)}
									{items.map((m) => (
										<SelectItem
											key={choiceKey(m.provider, m.id)}
											value={choiceKey(m.provider, m.id)}
										>
											{m.name}
										</SelectItem>
									))}
								</SelectGroup>
							);
						})}
					</SelectContent>
				</Select>

				{supportsEffort && (
					<Select
						value={effort ?? DEFAULT_VALUE}
						onValueChange={onEffortChange}
						disabled={saving}
					>
						<SelectTrigger aria-label="Reasoning effort" className="w-full data-[size=default]:h-10 sm:w-44 sm:data-[size=default]:h-9">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value={DEFAULT_VALUE}>
								Effort: {slot.default.reasoning_effort ?? "model default"}
							</SelectItem>
							{REASONING_EFFORT_OPTIONS.map((o) => (
								<SelectItem key={o.id} value={o.id}>
									Effort: {o.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				)}
			</div>

			<p className="text-xs text-muted-foreground">
				Using {describe(slot.effective)}
				{slot.effective.reasoning_effort
					? `, effort ${slot.effective.reasoning_effort}`
					: ""}
			</p>
			{slot.override_error && (
				<p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
					<AlertTriangle className="h-3.5 w-3.5 shrink-0" />
					Saved choice is unavailable ({slot.override_error}); using the default.
				</p>
			)}
		</div>
	);
}

// The OCR model is env config (MISTRAL_OCR_MODEL in server/.env), not a slot:
// shown so every ingest model is on this page, but not editable here.
function OcrRow({ ocr }: { ocr: OcrService }) {
	return (
		<div className="space-y-3 rounded-xl border p-4">
			<div className="space-y-1">
				<h3 className="min-w-0 font-medium leading-snug">{ocr.description}</h3>
				<p className="break-all font-mono text-xs text-muted-foreground">{ocr.slot}</p>
			</div>
			<div className="flex h-10 w-full min-w-0 items-center rounded-md border bg-muted/50 px-3 text-sm text-muted-foreground sm:h-9 sm:w-80">
				<span className="truncate">{ocr.model}</span>
			</div>
			<p className="text-xs text-muted-foreground">
				Set by <code className="font-mono">MISTRAL_OCR_MODEL</code> in server/.env; not
				changeable here.
			</p>
			{!ocr.configured && (
				<p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500">
					<AlertTriangle className="h-3.5 w-3.5 shrink-0" />
					MISTRAL_API_KEY is not set, so OCR fails until it is.
				</p>
			)}
		</div>
	);
}

function groupByProvider(settings: ModelSettings): [string, SelectableModel[]][] {
	const groups = new Map<string, SelectableModel[]>();
	for (const m of settings.models) {
		const list = groups.get(m.provider) ?? [];
		list.push(m);
		groups.set(m.provider, list);
	}
	return Array.from(groups.entries());
}

export default function ModelSettingsPage() {
	const { user, loading } = useAuth();
	const router = useRouter();
	const { settings, error, isLoading, updateSlot } = useModelSettings();

	useEffect(() => {
		if (!loading && !user) {
			router.push("/login?returnTo=/settings/models");
		}
	}, [user, loading, router]);

	const modelsByProvider = useMemo(
		() => (settings ? groupByProvider(settings) : []),
		[settings]
	);

	// OCR goes right before the first ingest slot (it runs before them).
	const ocrIndex = useMemo(() => {
		if (!settings) return -1;
		const i = settings.slots.findIndex((s) => s.slot.startsWith("ingest."));
		return i === -1 ? settings.slots.length : i;
	}, [settings]);

	const onSave = async (slot: string, update: ModelSlotUpdate) => {
		try {
			await updateSlot(slot, update);
			toast.success("Model setting saved.");
		} catch (err) {
			toast.error(err instanceof Error ? err.message : "Failed to save model setting.");
		}
	};

	if (loading || !user || isLoading) {
		return (
			<div className="flex items-center justify-center p-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="animate-rise-in space-y-6 px-4 py-6 sm:px-6">
			<div className="space-y-1">
				<h2 className="text-lg font-medium">Models</h2>
				<p className="text-sm text-muted-foreground">
					Which model each feature uses. &quot;Default&quot; follows the server
					configuration.
				</p>
			</div>
			{error || !settings ? (
				<p className="text-sm text-destructive">
					{error instanceof Error ? error.message : "Failed to load model settings."}
				</p>
			) : (
				<div className="space-y-3">
					{settings.slots.map((slot, i) => (
						<Fragment key={slot.slot}>
							{i === ocrIndex && <OcrRow ocr={settings.ocr} />}
							<SlotRow
								slot={slot}
								providers={settings.providers}
								modelsByProvider={modelsByProvider}
								models={settings.models}
								onSave={onSave}
							/>
						</Fragment>
					))}
					{ocrIndex === settings.slots.length && <OcrRow ocr={settings.ocr} />}
				</div>
			)}
		</div>
	);
}
