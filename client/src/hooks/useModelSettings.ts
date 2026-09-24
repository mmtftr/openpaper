import useSWR from 'swr';
import { fetchFromApi } from '@/lib/api';
import { ModelSettings, ModelSlot, ModelSlotUpdate } from '@/lib/schema';

const MODEL_SETTINGS_URL = '/api/settings/models';

/** Settings -> Models: every model slot plus the selectable models. */
export function useModelSettings() {
	const { data, error, isLoading, mutate } = useSWR<ModelSettings>(
		MODEL_SETTINGS_URL,
		(url: string) => fetchFromApi(url),
	);

	/** Save a slot's override (all fields null = back to the default). */
	const updateSlot = async (slot: string, update: ModelSlotUpdate): Promise<ModelSlot> => {
		const updated: ModelSlot = await fetchFromApi(
			`${MODEL_SETTINGS_URL}/${encodeURIComponent(slot)}`,
			{ method: 'PUT', body: JSON.stringify(update) },
		);
		await mutate(
			current =>
				current && {
					...current,
					slots: current.slots.map(s => (s.slot === slot ? updated : s)),
				},
			{ revalidate: false },
		);
		return updated;
	};

	return { settings: data, error, isLoading, updateSlot };
}
