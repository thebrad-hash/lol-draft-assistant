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
  // Which win-prob model dataset to rank with ('all' backbone, or a patch like
  // '16.12'). Omitted => the server's default. Lives in DraftState so it flows
  // through every recommend/board/pick-order request that spreads the state.
  dataset?: string;
}

// --- win-prob model datasets (the patch toggle: GET /api/models) ---
export interface ModelDataset {
  id: string; // 'all' | '16.12' | ...
  label: string; // 'All patches' | 'Patch 16.12'
  patch: string | null;
  rank: string | null;
  nMatches: number | null;
  nRows: number | null;
  cvAuc: number | null;
  cvLogloss: number | null;
  nullLogloss: number | null;
  baselineLogloss: number | null;
  cvEce: number | null;
  beatsNull: boolean; // OOS log-loss actually beats the 50/50 null (else it's noise)
  builtAt: string | null;
  hasUncertainty: boolean; // bootstrap ensemble present => error bars
}

export interface ModelsResponse {
  datasets: ModelDataset[];
  default: string;
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
  // Bootstrap error bars (present only when the coefficient ensemble has been
  // built: `python -m lol_draft.bootstrap`). winProbMedian is the central
  // estimate; [winProbLo, winProbHi] is the ciLoPct–ciHiPct interval (default
  // 5th–95th). tiedWithTop marks picks the bootstrap can't distinguish from the
  // top pick: probTopBetter (fraction of resamples where the top pick beats this
  // one) fell below tieThreshold. None of these are on the top pick itself.
  winProbMedian?: number;
  winProbLo?: number;
  winProbHi?: number;
  winProbStd?: number;
  ciLoPct?: number;
  ciHiPct?: number;
  tiedWithTop?: boolean;
  probTopBetter?: number | null;
  tieThreshold?: number;
  // Relative value vs the role's field: winProbField is the mean win% across ALL
  // candidates for this role; winProbDelta is this pick's win% minus that mean.
  // Lets "best available" read as a positive choice even when the whole board's
  // absolute win% sits below 50% (a team-level baseline deficit).
  winProbField?: number | null;
  winProbDelta?: number | null;
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
  // Whether THIS client is the host (server reads its local League client) or a
  // remote friend who should follow the lobby broadcast instead. Set by the
  // server per-request; may be overridden locally via ?live=host|follow.
  isLocal?: boolean;
  // friend mode: we're applying the lobby's broadcast draft from a teammate
  following?: boolean;
  // friend mode: name of the teammate currently broadcasting (when known)
  sourceName?: string | null;
}

// Who is currently broadcasting the live draft into a lobby (any member can be).
export interface LiveSource {
  memberId: string | null;
  name: string;
}

// --- team-vs-team evaluation (the full-draft verdict) ---
export interface EvalLane {
  role: Role;
  a: string; // your champion id
  b: string; // enemy champion id
  dpp: number | null; // your-perspective lane winrate delta (pp); null = no matchup data (off-role pick)
  favored: 'A' | 'B' | 'even';
  noData?: boolean; // both picked, but the source data has no head-to-head for this pairing
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

export interface ChatMessage {
  id: string;
  memberId: string;
  name: string;
  text: string;
  ts: number; // unix seconds (server clock)
}

export interface Lobby {
  id: string;
  members: LobbyMember[];
  messages?: ChatMessage[];
  // The broadcast champ-select draft (shared across the premade — no per-player
  // role), the server stamp of when it last refreshed, and who's broadcasting.
  // Absent (null) when nobody is publishing or the broadcast has gone stale.
  liveDraft?: LiveDraft | null;
  liveAt?: number | null;
  liveSource?: LiveSource | null;
}
