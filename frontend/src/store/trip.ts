import { create } from 'zustand';
import type { RouteOption } from '../api/schemas';

export type SortBy = 'cheapest' | 'fastest' | 'fewest_transfers';

interface TripState {
  origin:          string;
  destination:     string;
  budget:          number | null;
  studentMode:     boolean;
  avoidHubs:       string[];
  /** Routes currently shown in the panel (searchResults or aiResults, per `source`). */
  results:         RouteOption[];
  /** Deterministic results of the Search form. */
  searchResults:   RouteOption[];
  /** Route options generated through the chatbot only. */
  aiResults:       RouteOption[];
  source:          'search' | 'ai';
  selectedRoute:   RouteOption | null;
  sortBy:          SortBy;
  setSearch: (params: Partial<Pick<TripState, 'origin' | 'destination' | 'budget' | 'studentMode' | 'avoidHubs'>>) => void;
  setResults: (results: RouteOption[]) => void;
  setAiResults: (results: RouteOption[]) => void;
  setSource: (source: 'search' | 'ai') => void;
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
  searchResults: [],
  aiResults:     [],
  source:        'search',
  selectedRoute: null,
  sortBy:        'cheapest',
  setSearch: (params) => set(params),
  // Search form: plain engine routes; any chatbot-generated options are dropped from the panel.
  setResults: (results) => set({ results, searchResults: results, aiResults: [], source: 'search', selectedRoute: results[0] ?? null }),
  // Chatbot: AI route options replace the panel list; legs + map follow the selected AI route.
  setAiResults: (aiResults) => set((s) => ({
    aiResults, source: 'ai', results: aiResults, selectedRoute: aiResults[0] ?? s.selectedRoute,
  })),
  setSource: (source) => set((s) => {
    const results = source === 'ai' ? s.aiResults : s.searchResults;
    return { source, results, selectedRoute: results[0] ?? null };
  }),
  selectRoute: (route) => set({ selectedRoute: route }),
  setSortBy: (sortBy) => set({ sortBy }),
  clearResults: () => set({ results: [], searchResults: [], aiResults: [], source: 'search', selectedRoute: null }),
}));
