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

export interface DraftFeatures {
  lane_z: number;
  counter_z: number;
  synergy_z: number;
  champ_strength: number;
}

export interface Recommendation {
  championId: string;
  championName: string;
  totalEv: number;
  contributions: Contribution[];
  // Blind-pick safety vs the meta field (standardized z). Folded into totalEv
  // via weights.blindability. Optional so a backend may omit it.
  blindabilityZ?: number;
  // Calibrated P(win) in [0,1] from the logistic model (the headline number),
  // plus the team feature vector behind it. Present from the real backend;
  // omitted by the offline mock, in which case the UI falls back to totalEv.
  winProb?: number | null;
  features?: DraftFeatures | null;
}

// --- all-roles board (top picks for every role at once) ---
export interface BoardRole {
  role: Role;
  picked: string | null; // champion id your team already locked in this role
  picks: Recommendation[];
}

export interface BoardResult {
  roles: BoardRole[];
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
  bestEv: number | null; // additive-z EV; null when ranked by win probability
  bestWin?: number | null; // calibrated P(win) of the best available pick (0..1)
  top: { champ: string; ev?: number; win?: number }[];
  urgency: number; // drop-off (best -> 3rd) in the active metric: win prob, else EV
}

export interface PickOrderResult {
  openRoles: PickOrderRole[]; // sorted by the active metric (win prob, else EV) desc
  suggested: Role | null;
}

// --- context-adaptive ("auto") weights ---
export interface AutoWeightsResult {
  weights: Weights; // rescaled for the current pick context
  notes: string[]; // human-readable rationale
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
