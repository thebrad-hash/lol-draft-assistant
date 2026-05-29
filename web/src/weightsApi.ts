// ---------------------------------------------------------------------------
// Context-adaptive weights client. Asks the engine (/api/weights) to rescale the
// base weights for the role on the clock given both teams' picks, returning the
// adjusted weights + a short rationale. Pure server-side math (no DB). Returns
// null on failure so callers fall back to the base weights.
// ---------------------------------------------------------------------------
import type { AutoWeightsResult, DraftState } from './types';

export async function getAutoWeights(state: DraftState): Promise<AutoWeightsResult | null> {
  try {
    const res = await fetch('/api/weights', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        myTeam: state.myTeam,
        enemyTeam: state.enemyTeam,
        pickingForRole: state.pickingForRole,
        weights: state.weights,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as AutoWeightsResult;
  } catch {
    return null;
  }
}
