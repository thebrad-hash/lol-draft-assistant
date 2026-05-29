import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useLobby } from '../lobby';
import { ROLE_GLYPH, ROLES, type LobbyMember } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

// The premade roster + your own setup (name, role, champion pool). Shown only
// when in a lobby (?lobby=<id>). Updates live as friends change their picks.
export function LobbyPanel({ onEditPool }: { onEditPool: () => void }) {
  const { lobbyId, members, me, setName, setRole } = useLobby();
  if (!lobbyId) return null;

  // Render everyone; draw our own card from live local state (the poll lags ~2s).
  const roster: LobbyMember[] = members.map((m) =>
    m.memberId === me.memberId ? { ...m, name: me.name, role: me.role, pool: me.pool } : m,
  );
  if (!roster.some((m) => m.memberId === me.memberId)) {
    roster.unshift({ memberId: me.memberId, name: me.name, role: me.role, pool: me.pool });
  }

  return (
    <section className="lobby">
      <div className="lobby__head">
        <h2>Premade Lobby</h2>
        <span className="lobby__count">{roster.length}/5</span>
      </div>

      <div className="lobby__me">
        <input
          className="lobby__name"
          value={me.name}
          maxLength={24}
          onChange={(e) => setName(e.target.value)}
          aria-label="Your name"
        />
        <div className="lobby__roles">
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
        <button className="btn btn--ghost" onClick={onEditPool}>
          Edit pool ({me.pool.length})
        </button>
      </div>

      <div className="roster">
        {roster.map((m) => (
          <MemberCard key={m.memberId} m={m} you={m.memberId === me.memberId} />
        ))}
      </div>
    </section>
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
