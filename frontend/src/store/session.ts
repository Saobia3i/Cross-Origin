import { create } from 'zustand';
import { persist } from 'zustand/middleware';

function newSessionId(): string {
  const rand =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 16)
      : Math.random().toString(36).slice(2, 18);
  return `web_${rand}`;
}

interface SessionState {
  sessionId: string;
  resetSession: () => void;
}

/** Stable per-browser session id. Sent as `session_id` on search, chat, simulation, events and reports. */
export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessionId: newSessionId(),
      resetSession: () => set({ sessionId: newSessionId() }),
    }),
    { name: 'dhaka_session' },
  ),
);

export const getSessionId = () => useSessionStore.getState().sessionId;
