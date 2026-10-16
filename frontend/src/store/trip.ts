import { create } from 'zustand';
import type { RouteOption } from '../api/schemas';

export type SortBy = 'cheapest' | 'fastest' | 'fewest_transfers';

interface TripState {
  origin:          string;
  destination:     string;
  budget:          number | null;
  studentMode:     boolean;
  avoidHubs:       string[];
  results:         RouteOption[];
  selectedRoute:   RouteOption | null;
  sortBy:          SortBy;
  setSearch: (params: Partial<Pick<TripState, 'origin' | 'destination' | 'budget' | 'studentMode' | 'avoidHubs'>>) => void;
  setResults: (results: RouteOption[]) => void;
  selectRoute: (route: RouteOption | null) => void;
  setSortBy: (sort: SortBy) => void;
  clearResults: () => void;
}

export const useTripStore = create<TripState>()((set) => ({
  origin:        '',
  destination:   '',
  budget:        null,
  studentMode:   false,
  avoidHubs:     [],
  results:       [],
  selectedRoute: null,
  sortBy:        'cheapest',
  setSearch: (params) => set(params),
  setResults: (results) => set({ results, selectedRoute: results[0] ?? null }),
  selectRoute: (route) => set({ selectedRoute: route }),
  setSortBy: (sortBy) => set({ sortBy }),
  clearResults: () => set({ results: [], selectedRoute: null }),
}));
