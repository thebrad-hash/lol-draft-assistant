import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useLobby } from '../lobby';
import { ROLE_GLYPH, ROLES, type LobbyMember } from '../types';
import { ChampionAvatar } from './ChampionAvatar';
import { GuidePanel } from './GuidePanel';
import { Coachmark, useOnboarding } from './Onboarding';

// First-run tour ids, in reading order. One callout shows at a time (the three
// anchors sit inches apart — three open bubbles would overlap each other).
const LOBBY_HINTS = ['lobby-name', 'lobby-roles', 'lobby-pool'] as const;

// Collapse persists per device so the panel stays out of the way mid-draft.
const COLLAPSE_KEY = 'ld_lobby_collapsed';

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

// The premade roster + your own setup (name, role, champion pool). Outside a
// lobby it renders the summoning-circle invite instead; inside one it's a
// collapsible comms panel — roster left, team chat right (slide-over on
// phones). Updates live as friends change their picks.
export function LobbyPanel({ onEditPool }: { onEditPool: () => void }) {
  const { lobbyId, members, me, setName, setRole, create, join, lobbyRemote } = useLobby();
  const { dismissed, dismiss, reopenHints } = useOnboarding();
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [joinRaw, setJoinRaw] = useState('');
  const [joinErr, setJoinErr] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const rolesRef = useRef<HTMLDivElement>(null);
  const poolRef = useRef<HTMLButtonElement>(null);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? '0' : '1');
      } catch {
        /* private mode: collapse just won't persist */
      }
      return !c;
    });
  };

  // Empty state: no lobby yet — the arcane summoning circle + a clear CTA.
  if (!lobbyId) {
    return (
      <section className="lobby lobby--empty">
        {/* above the fold and part of the first impression — load eagerly */}
        <img
          className="lobby__circle"
          src="/art/summoning-circle.webp"
          alt=""
          aria-hidden="true"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
        <div className="lobby__invite">
          <h2 className="lobby__invite-title">Draft with your premade</h2>
          <p className="lobby__invite-copy">
            {lobbyRemote
              ? 'Create a lobby and share the link — friends open it on the website. Whoever is in champ select keeps Go Live on here so the board fills for everyone.'
              : 'Create a lobby and share the link — everyone sets a role and champion pool, and the board shows the smartest coordinated picks.'}
          </p>
        </div>
        <button className="btn lobby__invite-cta" onClick={() => void create()}>
          + Premade lobby
        </button>
        <div className="lobby__join-row">
          <input
            className="lobby__join-input"
            placeholder="Or paste a lobby link / id"
            value={joinRaw}
            onChange={(e) => {
              setJoinRaw(e.target.value);
              setJoinErr(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const ok = join(joinRaw);
                setJoinErr(!ok);
                if (ok) setJoinRaw('');
              }
            }}
            aria-label="Join with lobby link or id"
          />
          <button
            className="btn btn--ghost"
            onClick={() => {
              const ok = join(joinRaw);
              setJoinErr(!ok);
              if (ok) setJoinRaw('');
            }}
          >
            Join
          </button>
        </div>
        {joinErr && <p className="lobby__join-err">Couldn’t parse that link — paste the full share URL or lobby id.</p>}
      </section>
    );
  }

  const activeHint = collapsed ? undefined : LOBBY_HINTS.find((h) => !dismissed.includes(h));
  const skipAll = () => dismiss(...LOBBY_HINTS);

  // Render everyone; draw our own card from live local state (the poll lags ~2s).
  const roster: LobbyMember[] = members.map((m) =>
    m.memberId === me.memberId ? { ...m, name: me.name, role: me.role, pool: me.pool } : m,
  );
  if (!roster.some((m) => m.memberId === me.memberId)) {
    roster.unshift({ memberId: me.memberId, name: me.name, role: me.role, pool: me.pool });
  }

  return (
    <section className={'lobby' + (collapsed ? ' lobby--collapsed' : '')}>
      <div className="lobby__head">
        <h2>Premade Lobby</h2>
        <span className="lobby__count">{roster.length}/5</span>
        {collapsed && (
          <span className="lobby__summary">
            {me.name}
            {me.role ? ` · ${ROLE_GLYPH[me.role]}` : ''} · pool {me.pool.length}
          </span>
        )}
        <button
          className="lobby__help"
          onClick={reopenHints}
          title="Show hints again"
          aria-label="Show hints again"
        >
          ?
        </button>
        <button
          className="lobby__fold"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          aria-controls="lobby-body"
          title={collapsed ? 'Expand the lobby panel' : 'Collapse the lobby panel'}
        >
          {collapsed ? '▾' : '▴'}
        </button>
      </div>

      {!collapsed && (
        <div className="lobby__grid" id="lobby-body">
          <div className="lobby__main">
            <div className="lobby__me">
              <input
                ref={nameRef}
                className="lobby__name"
                value={me.name}
                maxLength={24}
                onChange={(e) => setName(e.target.value)}
                aria-label="Your name"
              />
              <div className="lobby__roles" ref={rolesRef}>
                {ROLES.map((r) => (
                  <button
                    key={r}
                    className={'rolechip' + (me.role === r ? ' is-on' : '')}
                    onClick={() => setRole(me.role === r ? null : r)}
                    title={r}
                  >
                    {ROLE_GLYPH[r]}
                  </button>
                ))}
              </div>
              <button className="btn btn--ghost" ref={poolRef} onClick={onEditPool}>
                Edit pool ({me.pool.length})
              </button>
            </div>

            {activeHint === 'lobby-name' && (
              <Coachmark id="lobby-name" title="Username" anchorRef={nameRef} onSkipAll={skipAll}>
                Choose your username — this is how teammates see you in the lobby and chat.
              </Coachmark>
            )}
            {activeHint === 'lobby-roles' && (
              <Coachmark id="lobby-roles" title="Roles" anchorRef={rolesRef} onSkipAll={skipAll}>
                Pick the role(s) you can play. Your picks get ranked for these roles.
              </Coachmark>
            )}
            {activeHint === 'lobby-pool' && (
              <Coachmark id="lobby-pool" title="Champion pool" anchorRef={poolRef} onSkipAll={skipAll}>
                Add the champions you actually play. The board shows your pool picks above the
                global best, so your premade can coordinate swaps.
              </Coachmark>
            )}

            <div className="roster">
              {roster.map((m) => (
                <MemberCard key={m.memberId} m={m} you={m.memberId === me.memberId} />
              ))}
            </div>

            <GuidePanel />
          </div>

          <LobbyChat />
        </div>
      )}
    </section>
  );
}

// Matches the CSS slide-over breakpoint; drives inert/aria-hidden below.
const NARROW_QUERY = '(max-width: 920px)';

function useNarrow(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const mq = window.matchMedia(NARROW_QUERY);
      mq.addEventListener('change', notify);
      return () => mq.removeEventListener('change', notify);
    },
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  );
}

function LobbyChat() {
  const { messages, sendMessage, me } = useLobby();
  const [text, setText] = useState('');
  // phones: the chat is a slide-over toggled by a floating button
  const [open, setOpen] = useState(false);
  const narrow = useNarrow();
  // off-screen slide-over: remove it from tab order + the a11y tree entirely
  // (transform alone leaves focusable inputs floating below the viewport)
  const parked = narrow && !open;
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // keep the log pinned to the newest message
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // slide-over: Esc closes, opening moves focus to the input
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    void sendMessage(t);
    setText('');
  };

  const fmtTime = (ts: number) =>
    new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <>
      <button
        className="chat-fab"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="team-chat"
        title={open ? 'Close team chat' : 'Open team chat'}
      >
        💬 Chat
      </button>
      <div
        className={'chat' + (open ? ' is-open' : '')}
        id="team-chat"
        aria-hidden={parked || undefined}
        // React 18's types don't know `inert` yet; the empty string sets it
        {...(parked ? ({ inert: '' } as Record<string, string>) : {})}
      >
        <div className="chat__head">
          Team Chat
          <button
            className="chat__close"
            onClick={() => setOpen(false)}
            aria-label="Close chat"
          >
            ×
          </button>
        </div>
        <div className="chat__log" ref={logRef} role="log" aria-live="polite" aria-label="Team chat messages">
          {messages.length === 0 ? (
            <div className="chat__empty">No messages yet — coordinate your picks here.</div>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className={'chatmsg' + (m.memberId === me.memberId ? ' is-me' : '')}
                title={fmtTime(m.ts)}
              >
                <span className="chatmsg__name">{m.name}</span>
                <span className="chatmsg__text">{m.text}</span>
              </div>
            ))
          )}
        </div>
        <form className="chat__form" onSubmit={submit}>
          <input
            ref={inputRef}
            className="chat__input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={300}
            placeholder="Message your premade…"
            aria-label="Chat message"
          />
          <button className="btn chat__send" type="submit" disabled={!text.trim()}>
            Send
          </button>
        </form>
      </div>
    </>
  );
}

function MemberCard({ m, you }: { m: LobbyMember; you: boolean }) {
  return (
    <div className={'member' + (you ? ' member--you' : '')}>
      <div className="member__top">
        <span className="member__name">
          {m.name}
          {you ? ' (you)' : ''}
        </span>
        {m.role && <span className="rolechip rolechip--sm is-on">{ROLE_GLYPH[m.role]}</span>}
      </div>
      <div className="member__pool">
        {m.pool.slice(0, 10).map((id) => (
          <ChampionAvatar key={id} id={id} name={CHAMPIONS_BY_ID[id]?.name ?? id} size={22} />
        ))}
        {m.pool.length === 0 && <span className="member__nopool">no pool yet</span>}
        {m.pool.length > 10 && <span className="member__more">+{m.pool.length - 10}</span>}
      </div>
    </div>
  );
}
