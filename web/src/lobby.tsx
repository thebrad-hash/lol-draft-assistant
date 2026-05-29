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
import { createLobby, getLobby, leaveLobby, upsertMember } from './lobbyApi';
import type { LobbyMember, Role } from './types';

function lobbyIdFromUrl(): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get('lobby');
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
  me: { memberId: string; name: string; role: Role | null; pool: string[] };
  shareUrl: string | null;
  create: () => Promise<void>;
  leave: () => Promise<void>;
  setName: (name: string) => void;
  setRole: (role: Role | null) => void;
  togglePool: (championId: string) => void;
}

const LobbyContext = createContext<LobbyValue | null>(null);

export function LobbyProvider({ children }: { children: ReactNode }) {
  const memberId = useRef(stableMemberId()).current;
  const [lobbyId, setLobbyId] = useState<string | null>(() => lobbyIdFromUrl());
  const [connected, setConnected] = useState(false);
  const [members, setMembers] = useState<LobbyMember[]>([]);
  const [name, setNameState] = useState(() => localStorage.getItem('ld_name') || 'Player');
  const [role, setRole] = useState<Role | null>(null);
  const [pool, setPool] = useState<string[]>([]);
  const seeded = useRef(false); // have we reconciled local state with the server yet?

  const setName = useCallback((n: string) => {
    setNameState(n);
    localStorage.setItem('ld_name', n);
  }, []);

  const togglePool = useCallback((championId: string) => {
    setPool((p) => (p.includes(championId) ? p.filter((x) => x !== championId) : [...p, championId]));
  }, []);

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
  }, [lobbyId, memberId]);

  // join + poll the lobby
  useEffect(() => {
    if (!lobbyId) {
      setConnected(false);
      setMembers([]);
      return;
    }
    let cancelled = false;
    const sync = async (first: boolean) => {
      const lob = await getLobby(lobbyId);
      if (cancelled) return;
      setConnected(!!lob);
      if (!lob) return;
      setMembers(lob.members);
      if (first) {
        const mine = lob.members.find((m) => m.memberId === memberId);
        if (mine && !seeded.current) {
          // rejoining an existing lobby — adopt our prior name/role/pool
          setNameState(mine.name);
          setRole(mine.role);
          setPool(mine.pool);
        } else if (!mine) {
          await upsertMember(lobbyId, { memberId, name, role, pool });
        }
        seeded.current = true;
      }
    };
    void sync(true);
    const iv = setInterval(() => void sync(false), 2000);
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
      me: { memberId, name, role, pool },
      shareUrl,
      create,
      leave,
      setName,
      setRole,
      togglePool,
    }),
    [lobbyId, connected, members, memberId, name, role, pool, shareUrl, create, leave, setName, togglePool],
  );

  return <LobbyContext.Provider value={value}>{children}</LobbyContext.Provider>;
}

export function useLobby(): LobbyValue {
  const ctx = useContext(LobbyContext);
  if (!ctx) throw new Error('useLobby must be used within LobbyProvider');
  return ctx;
}
