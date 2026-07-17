// Runtime bootstrap from the local (or hosted) Python server.
// Desktop sets publicOrigin so share links open the website for friends.

export interface RuntimeInfo {
  publicOrigin: string | null;
  lobbyRemote: boolean;
  lobbyOrigin: string | null;
  lcuCapable: boolean;
}

let cached: RuntimeInfo | null = null;
let inflight: Promise<RuntimeInfo> | null = null;

const FALLBACK: RuntimeInfo = {
  publicOrigin: null,
  lobbyRemote: false,
  lobbyOrigin: null,
  lcuCapable: false,
};

export async function fetchRuntime(): Promise<RuntimeInfo> {
  if (cached) return cached;
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await fetch('/api/runtime');
      if (!res.ok) return FALLBACK;
      const j = (await res.json()) as Partial<RuntimeInfo>;
      cached = {
        publicOrigin: typeof j.publicOrigin === 'string' ? j.publicOrigin : null,
        lobbyRemote: !!j.lobbyRemote,
        lobbyOrigin: typeof j.lobbyOrigin === 'string' ? j.lobbyOrigin : null,
        lcuCapable: j.lcuCapable !== false,
      };
      return cached;
    } catch {
      return FALLBACK;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** Parse a pasted share URL or bare lobby id into a lobby id, or null. */
export function parseLobbyInput(raw: string): string | null {
  const s = raw.trim();
  if (!s) return null;
  try {
    if (s.includes('lobby=') || s.includes('://')) {
      const url = s.includes('://') ? new URL(s) : new URL(s, 'https://example.invalid');
      const lid = url.searchParams.get('lobby');
      if (lid) return lid.trim() || null;
    }
  } catch {
    /* fall through */
  }
  const m = s.match(/[?&]lobby=([^&]+)/i);
  if (m) {
    try {
      return decodeURIComponent(m[1]).trim() || null;
    } catch {
      return m[1].trim() || null;
    }
  }
  // bare id (urlsafe token)
  if (/^[A-Za-z0-9_-]{6,24}$/.test(s)) return s;
  return null;
}
