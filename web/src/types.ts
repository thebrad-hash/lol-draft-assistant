// ---------------------------------------------------------------------------
// Data contract. The backend (the Python EV engine) will later fulfill the
// `getRecommendations` signature; until then a local mock module does. Swapping
// mock -> real is a one-line change in src/api.ts.
// ---------------------------------------------------------------------------

export type Role = 'TOP' | 'JUNGLE' | 'MID' | 'BOT' | 'SUPPORT';

export const ROLES: Role[] = ['TOP', 'JUNGLE', 'MID', 'BOT', 'SUPPORT'];

export const ROLE_LABEL: Record<Role, string> = {
  TOP: 'Top',
  JUNGLE: 'Jungle',
  MID: 'Mid',
  BOT: 'Bot',
  SUPPORT: 'Support',
};

// Short glyph used on slot chips.
export const ROLE_GLYPH: Record<Role, string> = {
  TOP: 'TOP',
  JUNGLE: 'JNG',
  MID: 'MID',
  BOT: 'BOT',
  SUPPORT: 'SUP',
};

export interface Champion {
  id: string;
  name: string;
  roles: Role[];
}

export interface Weights {
  inLane: number;
  outOfLane: number;
  synergy: number;
  blindability: number;
}

export interface DraftState {
  bans: string[]; // champion ids
  myTeam: Partial<Record<Role, string>>;
  enemyTeam: Partial<Record<Role, string>>;
  pickingForRole: Role;
  poolFilter?: string[]; // optional restriction to these champion ids
  weights: Weights;
}

export interface Contribution {
  kind: 'counter' | 'synergy';
  targetChampion: string; // champion id of the enemy/ally driving this factor
  targetRole: Role;
  side: 'enemy' | 'ally';
  metric: 'in_lane_z' | 'out_of_lane_z' | 'synergy_z';
  value: number; // signed z-score (raw, pre-weight)
}

export interface Recommendation {
  championId: string;
  championName: string;
  totalEv: number;
  contributions: Contribution[];
  // Blind-pick safety vs the meta field (standardized z). Folded into totalEv
  // via weights.blindability. Optional so a backend may omit it.
  blindabilityZ?: number;
}

// The single async function the whole app depends on.
export type GetRecommendations = (state: DraftState) => Promise<Recommendation[]>;

// --- live champ-select (LCU) ---
export interface LiveDraft {
  bans: string[];
  myTeam: Partial<Record<Role, string>>;
  enemyTeam: Partial<Record<Role, string>>;
  pickingForRole: Role | null;
}

export interface LiveStatus {
  connected: boolean;
  inChampSelect: boolean;
  demo?: boolean;
  reason?: string;
  draft?: LiveDraft | null;
}
