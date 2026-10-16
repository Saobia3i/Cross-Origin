import { create } from 'zustand';

/**
 * Tiny bridge so other features can paint on the map WITHOUT importing map internals:
 *   SimulatorPanel:  useMapOverlay.getState().setSimulation(response)   // after POST /api/simulations
 *                    useMapOverlay.getState().clear()                    // on reset
 * ConnectedMap adapts the raw response (adapters.toMapSimulation), so contract drift stays in one file.
 * Client-side only => per-browser, so one judge's rain scenario never changes another judge's view.
 */
interface OverlayState {
  simulation: unknown | null;
  setSimulation: (raw: unknown) => void;
  clear: () => void;
}
export const useMapOverlay = create<OverlayState>()((set) => ({
  simulation: null,
  setSimulation: (simulation) => set({ simulation }),
  clear: () => set({ simulation: null }),
}));
