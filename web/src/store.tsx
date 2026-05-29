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
import { fetchLive } from './live';
import { CHAMPIONS_BY_ID } from './mock/champions';
import { ROLES } from './types';
import type { DraftState, LiveStatus, Recommendation, Role, TeamEval, Weights } from './types';

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
  // mutations
  toggleBan: (championId: string) => void;
  removeBan: (championId: string) => void;
  addPick: (side: Side, championId: string) => void;
  clearSlot: (side: Side, role: Role) => void;
  setPickingForRole: (role: Role) => void;
  setWeights: (patch: Partial<Weights>) => void;
  resetWeights: () => void;
  setPoolFilter: (ids: string[] | undefined) => void;
  reset: () => void;
}

const DraftContext = createContext<DraftContextValue | null>(null);

const MAX_BANS = 10; // full draft = 10 bans (5 per team)

export function DraftProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DraftState>(INITIAL);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);
  const [live, setLive] = useState(false);
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
      if (side === 'my') {
        const next = { ...s.myTeam };
        delete next[role];
        return { ...s, myTeam: next };
      }
      const next = { ...s.enemyTeam };
      delete next[role];
      return { ...s, enemyTeam: next };
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

  const reset = useCallback(() => setState(INITIAL), []);

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

  // --- live champ-select polling (LCU via /api/live) ---
  useEffect(() => {
    if (!live) {
      setLiveStatus({ connected: false, inChampSelect: false });
      return;
    }
    let cancelled = false;
    const tick = async () => {
      const st = await fetchLive();
      if (cancelled) return;
      setLiveStatus(st);
      if (st.inChampSelect && st.draft) {
        const d = st.draft;
        setState((s) => {
          const next = {
            bans: d.bans ?? [],
            myTeam: d.myTeam ?? {},
            enemyTeam: d.enemyTeam ?? {},
            pickingForRole: d.pickingForRole ?? s.pickingForRole,
          };
          const cur = {
            bans: s.bans,
            myTeam: s.myTeam,
            enemyTeam: s.enemyTeam,
            pickingForRole: s.pickingForRole,
          };
          // diff-guard so we don't refetch recommendations every poll
          return JSON.stringify(cur) === JSON.stringify(next) ? s : { ...s, ...next };
        });
      }
    };
    void tick();
    const id = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [live]);

  const value = useMemo<DraftContextValue>(
    () => ({
      state,
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
      toggleBan,
      removeBan,
      addPick,
      clearSlot,
      setPickingForRole,
      setWeights,
      resetWeights,
      setPoolFilter,
      reset,
    }),
    [
      state,
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
      toggleBan,
      removeBan,
      addPick,
      clearSlot,
      setPickingForRole,
      setWeights,
      resetWeights,
      setPoolFilter,
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
