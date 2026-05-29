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

// --- team-vs-team evaluation (the full-draft verdict) ---
export interface EvalLane {
  role: Role;
  a: string; // your champion id
  b: string; // enemy champion id
  dpp: number; // your-perspective lane winrate delta (pct points)
  favored: 'A' | 'B' | 'even';
}

export interface EvalSwing {
  aChamp: string;
  aRole: Role;
  bChamp: string;
  bRole: Role;
  dpp: number; // your-perspective
}

export interface SynergyCombo {
  a: string;
  b: string;
  z: number;
}

export interface TeamEval {
  complete: boolean;
  score: { a: number; b: number }; // your / enemy, sums to 100
  winProbA: number;
  components: {
    laneEdge: number; // your-perspective lane pp (sum of 5 lanes)
    crossEdge: number;
    synergyA: number;
    synergyB: number;
    synergyDiff: number; // your synergy - enemy synergy (z)
  };
  lanes: EvalLane[];
  synergyBest: { a: SynergyCombo | null; b: SynergyCombo | null };
  swings: { aBest: EvalSwing[]; aWorst: EvalSwing[] };
  threats: {
    topEnemy: { champ: string; pressure: number } | null;
    yourCarry: { champ: string; pressure: number } | null;
  };
  winConditions: string[];
}

export type GetEvaluation = (state: DraftState) => Promise<TeamEval | null>;

// --- pick-order suggestion (which open role to pick next) ---
export interface PickOrderRole {
  role: Role;
  bestChamp: string;
  bestEv: number;
  top: { champ: string; ev: number }[];
  urgency: number; // EV drop-off from best to 3rd-best in this role
}

export interface PickOrderResult {
  openRoles: PickOrderRole[]; // sorted by bestEv desc
  suggested: Role | null;
}

// --- premade lobby ---
export interface LobbyMember {
  memberId: string;
  name: string;
  role: Role | null;
  pool: string[]; // champion ids
}

export interface Lobby {
  id: string;
  members: LobbyMember[];
}
