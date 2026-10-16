import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { fetchEvents } from '../api/endpoints';
import type { ApiEvent } from '../api/schemas';

const INTERVAL_MS = Number(import.meta.env.VITE_EVENT_POLL_INTERVAL_MS) || 12_000;

/**
 * Polls GET /api/events using the returned `next_since_id` cursor and invalidates the
 * matching React Query caches. Event types: report.*, simulation.*, route.recalculated,
 * grounding.blocked, chat.message, rideshare.*
 */
export function useEventPolling(onEvents?: (events: ApiEvent[]) => void) {
  const qc = useQueryClient();
  const cursor = useRef(0);
  const cb = useRef(onEvents);
  cb.current = onEvents;

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const data = await fetchEvents(cursor.current);
        if (stopped) return;
        cursor.current = data.next_since_id;
        if (data.events.length) {
          const types = data.events.map((e) => e.event_type);
          if (types.some((t) => t.startsWith('report.') || t === 'route.recalculated')) {
            qc.invalidateQueries({ queryKey: ['alerts'] });
            qc.invalidateQueries({ queryKey: ['network'] });
          }
          cb.current?.(data.events);
        }
      } catch {
        /* transient: retry on next tick */
      }
    };
    tick();
    const id = setInterval(tick, INTERVAL_MS);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [qc]);
}
