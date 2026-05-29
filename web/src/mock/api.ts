// ---------------------------------------------------------------------------
// MOCK backend. Returns plausible, *deterministic* recommendations so the UI is
// fully functional standalone. Determinism (seeded by champion+target pair)
// means scores don't randomly jitter as the user types — they only change when
// the draft/weights actually change. Mirrors the real Python engine's structure:
//   totalEv = wIn*Σ(in-lane z) + wOut*Σ(out-of-lane z)
//           + wSyn*Σ(synergy z) + wBlind*(blindability z)
//
// To wire the real backend, replace the export in src/api.ts — nothing else.
// ---------------------------------------------------------------------------
import type {
  Contribution,
  DraftState,
  GetRecommendations,
  Recommendation,
  Role,
} from '../types';
import { ROLES } from '../types';
import { CHAMPIONS, CHAMPIONS_BY_ID } from './champions';

// FNV-1a hash -> uint32
function hash(str: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function uniform(seed: string): number {
  return (hash(seed) % 1_000_000) / 1_000_000; // [0,1)
}

// Approx N(0,1) via Irwin–Hall(3): mean 1.5, sd 0.5 -> (sum-1.5)/0.5.
// Yields a believable spread of z-scores mostly within [-3, 3].
function zscore(a: string, b: string, salt: string): number {
  const s =
    uniform(`${a}~${b}~${salt}~1`) +
    uniform(`${a}~${b}~${salt}~2`) +
    uniform(`${a}~${b}~${salt}~3`);
  return Math.round(((s - 1.5) / 0.5) * 100) / 100;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function scoreCandidate(champId: string, state: DraftState): Recommendation {
  const { myTeam, enemyTeam, pickingForRole, weights } = state;
  const contributions: Contribution[] = [];
  let inLaneSum = 0;
  let outSum = 0;
  let synSum = 0;

  // Counters: candidate vs each known enemy.
  for (const role of ROLES) {
    const enemy = enemyTeam[role];
    if (!enemy) continue;
    const inLane = role === pickingForRole;
    const value = zscore(champId, enemy, 'matchup');
    if (inLane) inLaneSum += value;
    else outSum += value;
    contributions.push({
      kind: 'counter',
      targetChampion: enemy,
      targetRole: role,
      side: 'enemy',
      metric: inLane ? 'in_lane_z' : 'out_of_lane_z',
      value,
    });
  }

  // Synergy: candidate with each known ally (excluding our own slot).
  for (const role of ROLES) {
    if (role === pickingForRole) continue;
    const ally = myTeam[role];
    if (!ally) continue;
    const value = zscore(champId, ally, 'synergy');
    synSum += value;
    contributions.push({
      kind: 'synergy',
      targetChampion: ally,
      targetRole: role,
      side: 'ally',
      metric: 'synergy_z',
      value,
    });
  }

  // Blind-pick safety vs the meta field (a per-champion base term).
  const blindabilityZ = zscore(champId, pickingForRole, 'blind');

  const totalEv =
    weights.inLane * inLaneSum +
    weights.outOfLane * outSum +
    weights.synergy * synSum +
    weights.blindability * blindabilityZ;

  // Sort contributions: strongest magnitude first for a readable breakdown.
  contributions.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  return {
    championId: champId,
    championName: CHAMPIONS_BY_ID[champId]?.name ?? champId,
    totalEv: Math.round(totalEv * 100) / 100,
    contributions,
    blindabilityZ,
  };
}

export const getRecommendations: GetRecommendations = async (state) => {
  await sleep(120); // simulate network latency

  const used = new Set<string>([
    ...state.bans,
    ...Object.values(state.myTeam),
    ...Object.values(state.enemyTeam),
  ].filter(Boolean) as string[]);

  const poolSet =
    state.poolFilter && state.poolFilter.length > 0
      ? new Set(state.poolFilter)
      : null;

  const candidates = CHAMPIONS.filter(
    (c) =>
      c.roles.includes(state.pickingForRole) &&
      !used.has(c.id) &&
      (!poolSet || poolSet.has(c.id)),
  );

  return candidates
    .map((c) => scoreCandidate(c.id, state))
    .sort((a, b) => b.totalEv - a.totalEv)
    .slice(0, 15);
};

// Convenience for the picker: candidates playable in a role.
export function championsForRole(role: Role) {
  return CHAMPIONS.filter((c) => c.roles.includes(role));
}
