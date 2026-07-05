// ---------------------------------------------------------------------------
// Pick-order client. POSTs the draft (both teams' picks + bans) to the engine
// (/api/pick-order) and gets the open roles ranked by the EV of their best
// still-available champion — i.e. which role to pick next. null on failure.
// ---------------------------------------------------------------------------
import type { DraftState, PickOrderResult } from './types';

const ENDPOINT = '/api/pick-order';

export async function getPickOrder(state: DraftState, auto = false): Promise<PickOrderResult | null> {
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        myTeam: state.myTeam,
        enemyTeam: state.enemyTeam,
        bans: state.bans,
        weights: state.weights,
        dataset: state.dataset,
        auto,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as PickOrderResult;
  } catch {
    return null;
  }
}
