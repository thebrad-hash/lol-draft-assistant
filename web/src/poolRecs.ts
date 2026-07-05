// ---------------------------------------------------------------------------
// Shared, cached pool-filtered recommendations. Both the all-roles board and the
// "who picks next" panel want each premade member's best picks FROM THEIR POOL
// for a role, given the current draft. This memoizes those /api/recommend calls
// by (role, pool, draft signature) so the two components don't double-fetch and
// re-renders don't refetch — one source of truth for "what does this pool play".
// ---------------------------------------------------------------------------
import { getRecommendations } from './api';
import type { DraftState, Recommendation, Role } from './types';

const TTL = 4000; // ms
const cache = new Map<string, { promise: Promise<Recommendation[]>; ts: number }>();

function stateSig(state: DraftState): string {
  // pool recs depend only on the board state (+ weights + model dataset), not
  // pickingForRole. dataset is included so swapping the patch model busts the cache.
  return JSON.stringify([state.myTeam, state.enemyTeam, state.bans, state.weights, state.dataset]);
}

export function poolKey(state: DraftState, role: Role, pool: string[]): string {
  return role + '|' + [...pool].sort().join(',') + '|' + stateSig(state);
}

export function fetchPoolRecs(state: DraftState, role: Role, pool: string[]): Promise<Recommendation[]> {
  const key = poolKey(state, role, pool);
  const now = Date.now();
  const hit = cache.get(key);
  if (hit && now - hit.ts < TTL) return hit.promise;
  const promise = getRecommendations({ ...state, pickingForRole: role, poolFilter: pool }).catch(
    () => [] as Recommendation[],
  );
  cache.set(key, { promise, ts: now });
  if (cache.size > 60) {
    // drop the oldest handful so the cache can't grow unbounded across a session
    for (const k of cache.keys()) {
      cache.delete(k);
      if (cache.size <= 48) break;
    }
  }
  return promise;
}
