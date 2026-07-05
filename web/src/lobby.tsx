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
import { createLobby, getLobby, leaveLobby, publishLive, sendChat, upsertMember } from './lobbyApi';
import { ROLES } from './types';
import type { ChatMessage, LiveDraft, LiveSource, LobbyMember, Role } from './types';

function lobbyIdFromUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get('lobby');
}

// --- per-role champion pools, persisted locally so they survive new lobby links.
// One pool per role ({TOP:[...], JUNGLE:[...], ...}); selecting a role loads its
// saved pool, editing the pool saves it back. Per browser/device (like ld_name).
const ROLE_KEY = 'ld_role';
const POOLS_KEY = 'ld_pools';

function loadPoolsMap(): Partial<Record<Role, string[]>> {
  try {
    const p = JSON.parse(localStorage.getItem(POOLS_KEY) || '{}');
    if (!p || typeof p !== 'object') return {};
    const out: Partial<Record<Role, string[]>> = {};
    for (const r of ROLES) {
      if (Array.isArray(p[r])) out[r] = p[r].filter((x: unknown): x is string => typeof x === 'string');
    }
    return out;
  } catch {
    return {};
  }
}

function loadSavedRole(): Role | null {
  const r = localStorage.getItem(ROLE_KEY);
  return r && (ROLES as string[]).includes(r) ? (r as Role) : null;
}

function stableMemberId(): string {
  let id = localStorage.getItem('ld_member_id');
  if (!id) {
    id =
      (typeof crypto !== 'undefined' && crypto.randomUUID && crypto.randomUUID()) ||
      Math.random().toString(36).slice(2, 12);
    localStorage.setItem('ld_member_id', id);
  }
  return id;
}

function setLobbyParam(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('lobby', id);
  else url.searchParams.delete('lobby');
  window.history.replaceState({}, '', url.toString());
}

interface LobbyValue {
  lobbyId: string | null;
  connected: boolean; // lobby exists on the server
  members: LobbyMember[];
  messages: ChatMessage[];
  liveDraft: LiveDraft | null; // broadcast draft (null when none/stale)
  liveSource: LiveSource | null; // who's broadcasting it (null when none)
  me: { memberId: string; name: string; role: Role | null; pool: string[] };
  shareUrl: string | null;
  create: () => Promise<void>;
  leave: () => Promise<void>;
  setName: (name: string) => void;
  setRole: (role: Role | null) => void;
  togglePool: (championId: string) => void;
  sendMessage: (text: string) => Promise<void>;
  broadcastLive: (draft: LiveDraft | null) => Promise<void>; // host -> lobby
}

const LobbyContext = createContext<LobbyValue | null>(null);

export function LobbyProvider({ children }: { children: ReactNode }) {
  const memberId = useRef(stableMemberId()).current;
  const [lobbyId, setLobbyId] = useState<string | null>(() => lobbyIdFromUrl());
  const [connected, setConnected] = useState(false);
  const [members, setMembers] = useState<LobbyMember[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [liveDraft, setLiveDraft] = useState<LiveDraft | null>(null);
  const [liveSource, setLiveSource] = useState<LiveSource | null>(null);
  const [name, setNameState] = useState(() => localStorage.getItem('ld_name') || 'Player');
  // the saved per-role pool library; the active `pool` mirrors poolsRef[role]
  const poolsRef = useRef<Partial<Record<Role, string[]>>>(loadPoolsMap());
  const [role, setRoleState] = useState<Role | null>(() => loadSavedRole());
  const [pool, setPool] = useState<string[]>(() => {
    const r = loadSavedRole();
    return r ? poolsRef.current[r] ?? [] : [];
  });
  const seeded = useRef(false); // have we reconciled local state with the server yet?

  const setName = useCallback((n: string) => {
    setNameState(n);
    localStorage.setItem('ld_name', n);
  }, []);

  // persist a role's pool into the local library
  const rememberPool = useCallback((r: Role | null, next: string[]) => {
    if (!r) return;
    poolsRef.current = { ...poolsRef.current, [r]: next };
    try {
      localStorage.setItem(POOLS_KEY, JSON.stringify(poolsRef.current));
    } catch {
      /* storage blocked — pools just won't persist this session */
    }
  }, []);

  // selecting a role loads that role's saved pool (deselecting leaves it as-is)
  const setRole = useCallback((r: Role | null) => {
    setRoleState(r);
    try {
      localStorage.setItem(ROLE_KEY, r ?? '');
    } catch {
      /* ignore */
    }
    if (r) setPool(poolsRef.current[r] ?? []);
  }, []);

  const togglePool = useCallback(
    (championId: string) => {
      setPool((p) => {
        const next = p.includes(championId) ? p.filter((x) => x !== championId) : [...p, championId];
        rememberPool(role, next); // save to the library under the current role
        return next;
      });
    },
    [role, rememberPool],
  );

  const create = useCallback(async () => {
    const res = await createLobby();
    if (!res) return;
    seeded.current = true; // brand-new lobby: local state is authoritative
    setLobbyParam(res.id);
    setLobbyId(res.id);
  }, []);

  const leave = useCallback(async () => {
    if (lobbyId) await leaveLobby(lobbyId, memberId);
    seeded.current = false;
    setLobbyParam(null);
    setLobbyId(null);
    setConnected(false);
    setMembers([]);
    setMessages([]);
    setLiveDraft(null);
    setLiveSource(null);
  }, [lobbyId, memberId]);

  // Push (or clear) our live champ-select draft to the lobby, tagged with who we
  // are. Updates our own copy immediately so we don't wait a poll cycle.
  const broadcastLive = useCallback(
    async (draft: LiveDraft | null) => {
      if (!lobbyId) return;
      const lob = await publishLive(lobbyId, draft, { memberId, name });
      if (lob) {
        setLiveDraft(lob.liveDraft ?? null);
        setLiveSource(lob.liveSource ?? null);
      }
    },
    [lobbyId, memberId, name],
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const t = text.trim();
      if (!lobbyId || !t) return;
      const lob = await sendChat(lobbyId, { memberId, name, text: t });
      if (lob) {
        setMembers(lob.members);
        setMessages(lob.messages ?? []); // sender sees their own message immediately
      }
    },
    [lobbyId, memberId, name],
  );

  // join + poll the lobby
  useEffect(() => {
    if (!lobbyId) {
      setConnected(false);
      setMembers([]);
      setMessages([]);
      setLiveDraft(null);
      setLiveSource(null);
      return;
    }
    let cancelled = false;
    const sync = async (first: boolean) => {
      const lob = await getLobby(lobbyId);
      if (cancelled) return;
      setConnected(!!lob);
      if (!lob) return;
      setMembers(lob.members);
      setMessages(lob.messages ?? []);
      setLiveDraft(lob.liveDraft ?? null);
      setLiveSource(lob.liveSource ?? null);
      if (first) {
        const mine = lob.members.find((m) => m.memberId === memberId);
        if (mine && !seeded.current) {
          // rejoining an existing lobby — adopt our prior name/role/pool and
          // fold that pool back into the local library (raw role set, no reload)
          setNameState(mine.name);
          setRoleState(mine.role);
          try {
            localStorage.setItem(ROLE_KEY, mine.role ?? '');
          } catch {
            /* ignore */
          }
          setPool(mine.pool);
          rememberPool(mine.role, mine.pool);
        } else if (!mine) {
          await upsertMember(lobbyId, { memberId, name, role, pool });
        }
        seeded.current = true;
      }
    };
    void sync(true);
    // 5s poll: snappy enough for pool coordination, and keeps a 5-stack well
    // within a free Redis tier's command budget when the lobby is server-backed.
    const iv = setInterval(() => void sync(false), 5000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lobbyId, memberId]);

  // push my own name/role/pool to the server when they change (debounced)
  useEffect(() => {
    if (!lobbyId || !seeded.current) return;
    const t = setTimeout(() => void upsertMember(lobbyId, { memberId, name, role, pool }), 300);
    return () => clearTimeout(t);
  }, [lobbyId, memberId, name, role, pool]);

  const shareUrl = useMemo(() => {
    if (!lobbyId || typeof window === 'undefined') return null;
    const url = new URL(window.location.origin + window.location.pathname);
    url.searchParams.set('lobby', lobbyId);
    return url.toString();
  }, [lobbyId]);

  const value = useMemo<LobbyValue>(
    () => ({
      lobbyId,
      connected,
      members,
      messages,
      liveDraft,
      liveSource,
      me: { memberId, name, role, pool },
      shareUrl,
      create,
      leave,
      setName,
      setRole,
      togglePool,
      sendMessage,
      broadcastLive,
    }),
    [lobbyId, connected, members, messages, liveDraft, liveSource, memberId, name, role, pool, shareUrl, create, leave, setName, togglePool, sendMessage, broadcastLive],
  );

  return <LobbyContext.Provider value={value}>{children}</LobbyContext.Provider>;
}

export function useLobby(): LobbyValue {
  const ctx = useContext(LobbyContext);
  if (!ctx) throw new Error('useLobby must be used within LobbyProvider');
  return ctx;
}
