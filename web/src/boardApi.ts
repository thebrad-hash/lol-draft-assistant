// ---------------------------------------------------------------------------
// All-roles board client. Asks the engine (/api/board) for the top picks of
// every role at once (same scoring as /api/recommend per role). Returns null on
// failure. `auto` applies per-role context-adaptive weights (additive fallback).
// ---------------------------------------------------------------------------
import type { BoardResult, DraftState } from './types';

export async function getBoard(state: DraftState, auto = false): Promise<BoardResult | null> {
  try {
    const res = await fetch('/api/board', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        myTeam: state.myTeam,
        enemyTeam: state.enemyTeam,
        bans: state.bans,
        weights: state.weights,
        auto,
        limit: 15,
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as BoardResult;
  } catch {
    return null;
  }
}
