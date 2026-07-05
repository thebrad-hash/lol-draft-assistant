import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { getRecommendations } from './api';
import { getAutoWeights } from './weightsApi';
import { getEvaluation } from './evaluateApi';
import { getModels } from './modelsApi';
import { fetchLive } from './live';
import { useLobby } from './lobby';
import { CHAMPIONS_BY_ID } from './mock/champions';
import { ROLES } from './types';
import type { DraftState, LiveDraft, LiveStatus, ModelDataset, Recommendation, Role, TeamEval, Weights } from './types';

// Optional override of the host/friend role for live sync (?live=host|follow).
// Useful when the host opened the tunnel URL instead of localhost, or for testing.
const LIVE_OVERRIDE: 'host' | 'follow' | null = (() => {
  if (typeof window === 'undefined') return null;
  const v = new URLSearchParams(window.location.search).get('live');
  return v === 'host' || v === 'follow' ? v : null;
})();

export type Side = 'my' | 'enemy';

const DEFAULT_WEIGHTS: Weights = {
  inLane: 0.7,
  outOfLane: 0.5,
  synergy: 1.0,
  blindability: 0.9,
};

const INITIAL: DraftState = {
  bans: [],
  myTeam: {},
  enemyTeam: {},
  pickingForRole: 'MID',
  poolFilter: undefined,
  weights: DEFAULT_WEIGHTS,
};

interface DraftContextValue {
  state: DraftState;
  focus: string[]; // already-picked champ ids to prioritize the ranking around
  recommendations: Recommendation[];
  loading: boolean;
  live: boolean;
  liveStatus: LiveStatus;
  evaluation: TeamEval | null;
  evalLoading: boolean;
  autoWeights: boolean;
  appliedWeights: Weights; // weights actually used (== base unless auto is on)
  weightNotes: string[];
  setAutoWeights: (on: boolean) => void;
  setLive: (on: boolean) => void;
  // win-prob model dataset toggle (all-patch backbone vs a specific patch)
  datasets: ModelDataset[]; // available datasets from /api/models ([] => hide toggle)
  dataset: string; // selected dataset id (defaults to the server default)
  setDataset: (id: string) => void;
  // mutations
  toggleBan: (championId: string) => void;
  removeBan: (championId: string) => void;
  addPick: (side: Side, championId: string) => void;
  clearSlot: (side: Side, role: Role) => void;
  setPickingForRole: (role: Role) => void;
  setWeights: (patch: Partial<Weights>) => void;
  resetWeights: () => void;
  setPoolFilter: (ids: string[] | undefined) => void;
  toggleFocus: (championId: string) => void;
  reset: () => void;
}

const DraftContext = createContext<DraftContextValue | null>(null);

const MAX_BANS = 10; // full draft = 10 bans (5 per team)

export function DraftProvider({ children }: { children: ReactNode }) {
  // Live sync coordinates with the premade lobby: the host reads its local League
  // client and broadcasts the shared draft; friends follow it with their own role.
  const { lobbyId, me, liveDraft, liveSource, broadcastLive } = useLobby();
  const [state, setState] = useState<DraftState>(INITIAL);
  const [focus, setFocus] = useState<string[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);
  const [live, setLive] = useState(false);
  // null = not yet known; resolved from the server's per-request isLocal (or the
  // ?live override). true => host (read LCU + publish), false => friend (follow).
  const [isHost, setIsHost] = useState<boolean | null>(
    LIVE_OVERRIDE ? LIVE_OVERRIDE === 'host' : null,
  );
  const [liveStatus, setLiveStatus] = useState<LiveStatus>({
    connected: false,
    inChampSelect: false,
  });
  const [evaluation, setEvaluation] = useState<TeamEval | null>(null);
  const [evalLoading, setEvalLoading] = useState(false);
  const evalReqId = useRef(0);
  const [autoWeights, setAutoWeightsState] = useState<boolean>(
    () => typeof localStorage !== 'undefined' && localStorage.getItem('ld_auto_weights') === '1',
  );
  const [appliedWeights, setAppliedWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  const [weightNotes, setWeightNotes] = useState<string[]>([]);
  const setAutoWeights = useCallback((on: boolean) => {
    setAutoWeightsState(on);
    localStorage.setItem('ld_auto_weights', on ? '1' : '0');
  }, []);

  // --- win-prob model dataset toggle ---
  // Discover available datasets once; the chosen id rides in DraftState.dataset so
  // every recommend/board/pick-order request picks it up (server falls back to its
  // default for an unknown/omitted id). Restore the last choice from localStorage.
  const [datasets, setDatasets] = useState<ModelDataset[]>([]);
  const setDataset = useCallback((id: string) => {
    setState((s) => (s.dataset === id ? s : { ...s, dataset: id }));
    localStorage.setItem('ld_dataset', id);
  }, []);
  useEffect(() => {
    let cancelled = false;
    getModels().then((res) => {
      if (cancelled || !res) return;
      setDatasets(res.datasets);
      const saved = localStorage.getItem('ld_dataset');
      const valid = saved && res.datasets.some((d) => d.id === saved);
      // Default to the saved choice if still available, else the server default.
      setState((s) => ({ ...s, dataset: valid ? (saved as string) : res.default }));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // --- mutations ---
  const toggleBan = useCallback((championId: string) => {
    setState((s) => {
      if (s.bans.includes(championId)) {
        return { ...s, bans: s.bans.filter((b) => b !== championId) };
      }
      if (s.bans.length >= MAX_BANS) return s;
      return { ...s, bans: [...s.bans, championId] };
    });
  }, []);

  const removeBan = useCallback((championId: string) => {
    setState((s) => ({ ...s, bans: s.bans.filter((b) => b !== championId) }));
  }, []);

  // Add a champion to a team; its role-key is inferred from the champion's
  // most-played role (falling back to the first role it plays that's still
  // open, else its primary). One champion per role per team.
  const addPick = useCallback((side: Side, championId: string) => {
    setState((s) => {
      const team = side === 'my' ? s.myTeam : s.enemyTeam;
      const roles = CHAMPIONS_BY_ID[championId]?.roles ?? [];
      const role: Role = roles.find((r) => !(r in team)) ?? roles[0] ?? 'MID';
      const next = { ...team, [role]: championId };
      return side === 'my' ? { ...s, myTeam: next } : { ...s, enemyTeam: next };
    });
  }, []);

  const clearSlot = useCallback((side: Side, role: Role) => {
    setState((s) => {
      const team = side === 'my' ? s.myTeam : s.enemyTeam;
      const removed = team[role];
      if (removed) setFocus((f) => f.filter((c) => c !== removed)); // keep focus ⊆ picked
      const next = { ...team };
      delete next[role];
      return side === 'my' ? { ...s, myTeam: next } : { ...s, enemyTeam: next };
    });
  }, []);

  const setPickingForRole = useCallback((role: Role) => {
    setState((s) => ({ ...s, pickingForRole: role }));
  }, []);

  const setWeights = useCallback((patch: Partial<Weights>) => {
    setState((s) => ({ ...s, weights: { ...s.weights, ...patch } }));
  }, []);

  const resetWeights = useCallback(() => {
    setState((s) => ({ ...s, weights: DEFAULT_WEIGHTS }));
  }, []);

  const setPoolFilter = useCallback((ids: string[] | undefined) => {
    setState((s) => ({ ...s, poolFilter: ids && ids.length ? ids : undefined }));
  }, []);

  const toggleFocus = useCallback((championId: string) => {
    setFocus((f) => (f.includes(championId) ? f.filter((c) => c !== championId) : [...f, championId]));
  }, []);

  const reset = useCallback(() => {
    setState(INITIAL);
    setFocus([]);
  }, []);

  // --- reactive recompute (debounced, stale-guarded). In auto mode the weights
  // are first rescaled to the pick context, then used for the recommendation. ---
  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    const t = setTimeout(async () => {
      let weights = state.weights;
      let notes: string[] = [];
      if (autoWeights) {
        const aw = await getAutoWeights(state);
        if (aw) {
          weights = aw.weights;
          notes = aw.notes;
        }
      }
      if (id !== reqId.current) return;
      setAppliedWeights(weights);
      setWeightNotes(notes);
      try {
        const recs = await getRecommendations({ ...state, weights });
        if (id === reqId.current) {
          setRecommendations(recs);
          setLoading(false);
        }
      } catch {
        if (id === reqId.current) setLoading(false);
      }
    }, 150);
    return () => clearTimeout(t);
  }, [state, autoWeights]);

  // --- full-draft evaluation (only once BOTH teams are locked in) ---
  useEffect(() => {
    const full = (t: Partial<Record<Role, string>>) => ROLES.every((r) => Boolean(t[r]));
    if (!full(state.myTeam) || !full(state.enemyTeam)) {
      setEvaluation(null);
      setEvalLoading(false);
      return;
    }
    const id = ++evalReqId.current;
    setEvalLoading(true);
    const t = setTimeout(() => {
      getEvaluation(state)
        .then((ev) => {
          if (id === evalReqId.current) {
            setEvaluation(ev);
            setEvalLoading(false);
          }
        })
        .catch(() => {
          if (id === evalReqId.current) setEvalLoading(false);
        });
    }, 200);
    return () => clearTimeout(t);
  }, [state]);

  // Apply a champ-select draft to local state. `role` overrides pickingForRole
  // (the host uses their LCU lane; a friend uses their own lobby role). Diff-
  // guarded so an unchanged poll doesn't churn recommendations.
  const applyLiveDraft = useCallback((d: LiveDraft, role: Role | null) => {
    setState((s) => {
      const next = {
        bans: d.bans ?? [],
        myTeam: d.myTeam ?? {},
        enemyTeam: d.enemyTeam ?? {},
        pickingForRole: role ?? d.pickingForRole ?? s.pickingForRole,
      };
      const cur = {
        bans: s.bans,
        myTeam: s.myTeam,
        enemyTeam: s.enemyTeam,
        pickingForRole: s.pickingForRole,
      };
      return JSON.stringify(cur) === JSON.stringify(next) ? s : { ...s, ...next };
    });
  }, []);

  // Auto-enable live sync when you ENTER a lobby (you'll have League open before
  // opening a premade link). Only on entry — respects a later manual toggle-off.
  const prevLobbyRef = useRef<string | null>(null);
  useEffect(() => {
    if (lobbyId && prevLobbyRef.current !== lobbyId && !live) setLive(true);
    prevLobbyRef.current = lobbyId;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lobbyId]);

  // --- HOST live sync: poll the local LCU, apply locally, broadcast to lobby ---
  const publishedRef = useRef(false);
  useEffect(() => {
    if (!live) {
      setLiveStatus({ connected: false, inChampSelect: false });
      if (lobbyId && publishedRef.current) {
        publishedRef.current = false;
        void broadcastLive(null); // stop broadcasting when we go offline
      }
      return;
    }
    if (isHost === false) return; // friend: handled by the consumer effect below
    let cancelled = false;
    const tick = async () => {
      const st = await fetchLive();
      if (cancelled) return;
      const local = LIVE_OVERRIDE ? LIVE_OVERRIDE === 'host' : st.isLocal;
      if (typeof local === 'boolean' && local !== isHost) setIsHost(local);
      if (local === false) return; // we're a friend; re-run hands off below
      setLiveStatus({ ...st, isLocal: true });
      if (st.inChampSelect && st.draft) {
        applyLiveDraft(st.draft, st.draft.pickingForRole ?? null);
        if (lobbyId) {
          publishedRef.current = true;
          void broadcastLive({
            bans: st.draft.bans ?? [],
            myTeam: st.draft.myTeam ?? {},
            enemyTeam: st.draft.enemyTeam ?? {},
            pickingForRole: null, // shared draft carries no per-player role
          });
        }
      } else if (lobbyId && publishedRef.current) {
        publishedRef.current = false;
        void broadcastLive(null); // left champ select -> clear the broadcast
      }
    };
    void tick();
    const id = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [live, isHost, lobbyId, applyLiveDraft, broadcastLive]);

  // --- FRIEND live sync: follow a teammate's lobby broadcast, with YOUR role ---
  useEffect(() => {
    if (!live || isHost !== false) return;
    if (liveDraft) {
      applyLiveDraft(liveDraft, me.role ?? null);
      setLiveStatus({
        connected: true,
        inChampSelect: true,
        isLocal: false,
        following: true,
        sourceName: liveSource?.name ?? null,
      });
    } else {
      setLiveStatus({ connected: true, inChampSelect: false, isLocal: false, following: true });
    }
  }, [live, isHost, liveDraft, liveSource, me.role, applyLiveDraft]);

  const value = useMemo<DraftContextValue>(
    () => ({
      state,
      focus,
      recommendations,
      loading,
      live,
      liveStatus,
      evaluation,
      evalLoading,
      autoWeights,
      appliedWeights,
      weightNotes,
      setAutoWeights,
      setLive,
      datasets,
      dataset: state.dataset ?? 'all',
      setDataset,
      toggleBan,
      removeBan,
      addPick,
      clearSlot,
      setPickingForRole,
      setWeights,
      resetWeights,
      setPoolFilter,
      toggleFocus,
      reset,
    }),
    [
      state,
      focus,
      recommendations,
      loading,
      live,
      liveStatus,
      evaluation,
      evalLoading,
      autoWeights,
      appliedWeights,
      weightNotes,
      setAutoWeights,
      setLive,
      datasets,
      setDataset,
      toggleBan,
      removeBan,
      addPick,
      clearSlot,
      setPickingForRole,
      setWeights,
      resetWeights,
      setPoolFilter,
      toggleFocus,
      reset,
    ],
  );

  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export function useDraft(): DraftContextValue {
  const ctx = useContext(DraftContext);
  if (!ctx) throw new Error('useDraft must be used within DraftProvider');
  return ctx;
}
