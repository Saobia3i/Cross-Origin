import { create } from 'zustand';

export interface RouteLeg {
  mode:        'bus' | 'mrt' | 'rickshaw' | 'leguna' | 'cng' | 'walk';
  from:        string;
  to:          string;
  fare:        number;
  studentFare?: number;
  durationMin: number;
  boarding?:   string;
  alighting?:  string;
  routeId?:    string;
}

export interface RouteOption {
  id:            string;
  legs:          RouteLeg[];
  totalFare:     number;
  totalDuration: number;
  transfers:     number;
  withinBudget:  boolean;
  disrupted:     boolean;
  groundingOk:   boolean;
}

interface TripState {
  origin:          string;
  destination:     string;
  budget:          number | null;
  studentMode:     boolean;
  avoidHubs:       string[];
  results:         RouteOption[];
  selectedRoute:   RouteOption | null;
  sortBy:          'cheapest' | 'fastest' | 'fewest';
  setSearch: (params: Partial<Pick<TripState, 'origin' | 'destination' | 'budget' | 'studentMode' | 'avoidHubs'>>) => void;
  setResults: (results: RouteOption[]) => void;
  selectRoute: (route: RouteOption | null) => void;
  setSortBy: (sort: TripState['sortBy']) => void;
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
  setResults: (results) => set({ results }),
  selectRoute: (route) => set({ selectedRoute: route }),
  setSortBy: (sortBy) => set({ sortBy }),
  clearResults: () => set({ results: [], selectedRoute: null }),
}));
