// ---------------------------------------------------------------------------
// Team-vs-team evaluation client. POSTs both full teams to the Python engine
// (/api/evaluate, proxied to FastAPI) and returns the verdict. Returns null if
// the API is unreachable or errors — the Team Analysis panel then simply hides
// (there is no offline mock for the full-draft verdict).
// ---------------------------------------------------------------------------
import type { GetEvaluation, TeamEval } from './types';

const ENDPOINT = '/api/evaluate';

export const getEvaluation: GetEvaluation = async (state) => {
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        myTeam: state.myTeam,
        enemyTeam: state.enemyTeam,
        weights: state.weights,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as TeamEval;
  } catch {
    return null;
  }
};
