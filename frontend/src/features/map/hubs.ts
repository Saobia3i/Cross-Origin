import type { Hub } from '../../api/schemas';

/** The 14 headline hubs from the hackathon brief (hub_ids as produced by the dataset normalizer). */
export const MAIN_HUB_IDS = [
  'MOTIJHEEL', 'BANASREE', 'MALIBAGH', 'SHAHBAGH', 'FARMGATE', 'SCIENCE_LAB', 'DHANMONDI',
  'MOHAMMADPUR', 'MIRPUR_10', 'AGARGAON', 'MOHAKHALI', 'BADDA', 'UTTARA_NORTH', 'JATRABARI',
];

export const MAIN_GROUP = 'Main hubs';
export const OTHER_GROUP = 'Other stops';

export interface HubOption { name: string; group: string }

/** Dropdown options: the 14 main hubs first (brief order), then every other stop alphabetically. */
export function hubOptions(hubs: Hub[] | undefined): HubOption[] {
  const list = hubs ?? [];
  const byId = new Map(list.map((h) => [h.hub_id, h]));
  const main = MAIN_HUB_IDS.flatMap((id) => (byId.has(id) ? [{ name: byId.get(id)!.name, group: MAIN_GROUP }] : []));
  const mainIds = new Set(MAIN_HUB_IDS);
  const other = list
    .filter((h) => !mainIds.has(h.hub_id))
    .map((h) => ({ name: h.name, group: OTHER_GROUP }))
    .sort((a, b) => a.name.localeCompare(b.name));
  return [...main, ...other];
}

/**
 * Display-only fallback positions (approximate centres) for hubs the dataset has NO coordinates for, so routes to
 * them can still be drawn. The backend data is never changed; map hubs using these are flagged `approx`.
 */
export const APPROX_HUB_COORDS: Record<string, [number, number]> = {
  BADDA: [23.7806, 90.4258], BANASREE: [23.7634, 90.4334], DHANMONDI: [23.7461, 90.3742],
  GABTOLI: [23.7787, 90.3445], JATRABARI: [23.7104, 90.4346], MALIBAGH: [23.7497, 90.4127],
  MIRPUR_11: [23.8186, 90.3650], MOHAMMADPUR: [23.7662, 90.3587], PALLABI: [23.8266, 90.3654],
  SCIENCE_LAB: [23.7396, 90.3836], UTTARA_NORTH: [23.8756, 90.3985],
};
