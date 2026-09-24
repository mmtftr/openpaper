import useSWR from 'swr';
import { api, unwrap } from '@/lib/api/client';
import { ModelSettings, ModelSlot, ModelSlotUpdate } from '@/lib/schema';

// Compat: the settings page still holds the hand-written `ModelSettings` /
// `ModelSlot` (`role` as a literal union, override fields always present).

/** Settings -> Models: every model slot plus the selectable models. */
export function useModelSettings() {
	const { data, error, isLoading, mutate } = useSWR<ModelSettings>(
		'/api/settings/models',
		async () => (await unwrap(api.GET('/api/settings/models'))) as ModelSettings,
	);

	/** Save a slot's override (all fields null = back to the default). */
	const updateSlot = async (slot: string, update: ModelSlotUpdate): Promise<ModelSlot> => {
		const updated = (await unwrap(
			api.PUT('/api/settings/models/{slot}', { params: { path: { slot } }, body: update }),
		)) as ModelSlot;
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
