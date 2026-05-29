// ---------------------------------------------------------------------------
// Premade-lobby client. Talks to the in-memory lobby on the Python server
// (/api/lobby*, proxied to FastAPI). Every call returns null on failure so the
// UI can degrade gracefully (e.g. a lobby that no longer exists).
// ---------------------------------------------------------------------------
import type { Lobby, Role } from './types';

async function asJson<T>(p: Promise<Response>): Promise<T | null> {
  try {
    const res = await p;
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export interface MemberUpdate {
  memberId: string;
  name: string;
  role: Role | null;
  pool: string[];
}

export function createLobby(): Promise<{ id: string } | null> {
  return asJson(fetch('/api/lobby', { method: 'POST' }));
}

export function getLobby(id: string): Promise<Lobby | null> {
  return asJson(fetch(`/api/lobby/${encodeURIComponent(id)}`));
}

export function upsertMember(id: string, m: MemberUpdate): Promise<Lobby | null> {
  return asJson(
    fetch(`/api/lobby/${encodeURIComponent(id)}/member`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(m),
    }),
  );
}

export function leaveLobby(id: string, memberId: string): Promise<Lobby | null> {
  return asJson(
    fetch(`/api/lobby/${encodeURIComponent(id)}/member/${encodeURIComponent(memberId)}`, {
      method: 'DELETE',
    }),
  );
}
