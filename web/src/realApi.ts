// ---------------------------------------------------------------------------
// REAL backend client. POSTs the DraftState to the Python FastAPI server
// (lol_draft.server) and returns real machineloling z-scores.
//
// The server already speaks the web contract (it maps UI roles BOT/SUPPORT ->
// ADC/SUP and camel weights -> snake internally), so we send the DraftState
// as-is. We only enrich the response with nice local display names, since the
// engine returns champion ids.
//
// If the API is unreachable (server not started), we fall back to the mock so
// the UI keeps working — a console warning makes the fallback obvious.
// ---------------------------------------------------------------------------
import type { GetRecommendations, Recommendation } from './types';
import { CHAMPIONS_BY_ID } from './mock/champions';

const ENDPOINT = '/api/recommend';

async function mockFallback(state: Parameters<GetRecommendations>[0]) {
  const mod = await import('./mock/api');
  return mod.getRecommendations(state);
}

export const getRecommendations: GetRecommendations = async (state) => {
  let res: Response;
  try {
    res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(state),
    });
  } catch (err) {
    console.warn(
      `[draft] API unreachable at ${ENDPOINT} — using mock data. ` +
        `Start the backend with: python -m lol_draft.server`,
      err,
    );
    return mockFallback(state);
  }

  if (!res.ok) {
    console.warn(`[draft] API responded ${res.status} — using mock data.`);
    return mockFallback(state);
  }

  const data = (await res.json()) as Recommendation[];
  // Engine returns champion ids; prefer the nicer local display name.
  return data.map((r) => ({
    ...r,
    championName: CHAMPIONS_BY_ID[r.championId]?.name ?? r.championName,
  }));
};
