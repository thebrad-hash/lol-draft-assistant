// Live champ-select client. Talks to the Python LCU connector via /api/live
// (proxied to the FastAPI server). Append ?demo to the page URL
// (e.g. http://localhost:5173/?demo) to preview live mode with a synthetic
// champ select when you're not actually in a game.
import type { LiveStatus } from './types';

export const LIVE_DEMO =
  typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('demo');

export async function fetchLive(): Promise<LiveStatus> {
  try {
    const res = await fetch(`/api/live${LIVE_DEMO ? '?demo=1' : ''}`);
    if (!res.ok) return { connected: false, inChampSelect: false, reason: `HTTP ${res.status}` };
    return (await res.json()) as LiveStatus;
  } catch (err) {
    return { connected: false, inChampSelect: false, reason: 'API unreachable' };
  }
}
