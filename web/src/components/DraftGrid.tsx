import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useLobby } from '../lobby';
import { useDraft, type Side } from '../store';
import { ROLE_GLYPH, ROLE_LABEL, ROLES, type Role } from '../types';
import { ChampionAvatar } from './ChampionAvatar';
import { CoachlessLink } from './CoachlessLink';

const TEAM_SIZE = 5;

interface Props {
  onAdd: (side: Side) => void;
  side?: Side; // render a single team rail; omit to render both (legacy grid)
}

// Premade member who owns a role (fresh local `me` beats the polled copy) +
// how deep their pool runs for that role — the rail's coordination readout.
interface SlotMember {
  name: string;
  isMe: boolean;
  roleDepth: number;
}

function RoleSelector() {
  const { state, setPickingForRole } = useDraft();
  return (
    <div className="roleselect" role="group" aria-label="Pick for role">
      <span className="roleselect__label">picking</span>
      {ROLES.map((r) => (
        <button
          key={r}
          className={'roleselect__btn' + (state.pickingForRole === r ? ' is-active' : '')}
          onClick={() => setPickingForRole(r)}
          title={`Recommend for ${ROLE_LABEL[r]}`}
          aria-pressed={state.pickingForRole === r}
        >
          {ROLE_GLYPH[r]}
        </button>
      ))}
    </div>
  );
}

function poolDepth(pool: string[], role: Role): number {
  return pool.filter((id) => CHAMPIONS_BY_ID[id]?.roles.includes(role)).length;
}

function FilledRow({
  side,
  role,
  champId,
  member,
}: {
  side: Side;
  role: Role;
  champId: string;
  member: SlotMember | null;
}) {
  const { clearSlot, focus, toggleFocus } = useDraft();
  const champ = CHAMPIONS_BY_ID[champId];
  const focused = focus.includes(champId);
  const verb = side === 'enemy' ? 'picks against' : 'synergy with';
  const pips = member ? Math.min(5, member.roleDepth) : 0;
  return (
    <div className={'pick pick--filled' + (focused ? ' pick--focused' : '')}>
      <button
        className="pick__focus"
        onClick={() => toggleFocus(champId)}
        aria-pressed={focused}
        title={
          focused
            ? `Prioritizing ${verb} ${champ?.name ?? champId} — click to clear`
            : `Prioritize ${verb} ${champ?.name ?? champId} in Best Picks`
        }
      >
        {focused && <span className="pick__focus-mark" aria-hidden="true">⚔</span>}
        <ChampionAvatar id={champId} name={champ?.name ?? champId} size={38} />
        <span className="pick__id">
          <span className="pick__name">{champ?.name ?? champId}</span>
          {member && (
            <span className="pick__member">
              <span className={'pick__member-name' + (member.isMe ? ' is-me' : '')}>
                {member.name}
                {member.isMe ? ' (you)' : ''}
              </span>
              <span
                className="pick__pips"
                title={`${member.roleDepth} pool champion${member.roleDepth === 1 ? '' : 's'} for ${ROLE_LABEL[role]}`}
              >
                {Array.from({ length: 5 }, (_, i) => (
                  <span key={i} className={'pick__pip' + (i < pips ? ' is-on' : '')} />
                ))}
              </span>
            </span>
          )}
        </span>
      </button>
      <span className="rolechip" title={ROLE_LABEL[role]}>
        {ROLE_GLYPH[role]}
      </span>
      <CoachlessLink championId={champId} compact />
      <button className="pick__clear" onClick={() => clearSlot(side, role)} title="Remove">
        ×
      </button>
    </div>
  );
}

function TeamBoard({
  side,
  title,
  variant,
  onAdd,
}: {
  side: Side;
  title: string;
  variant: 'ally' | 'enemy';
  onAdd: (side: Side) => void;
}) {
  const { state, live, liveStatus } = useDraft();
  const { lobbyId, members, me } = useLobby();
  const team = side === 'my' ? state.myTeam : state.enemyTeam;
  // pick order = insertion order of role keys
  const filled = (Object.keys(team) as Role[]).map((role) => ({ role, champId: team[role]! }));
  const empties = Math.max(0, TEAM_SIZE - filled.length);
  const openRoles = ROLES.filter((r) => !(r in team));
  const liveOn = live && liveStatus.inChampSelect;

  // ally rail in a premade: who owns this role + their pool depth for it
  const memberFor = (role: Role): SlotMember | null => {
    if (side !== 'my' || !lobbyId) return null;
    if (me.role === role) return { name: me.name, isMe: true, roleDepth: poolDepth(me.pool, role) };
    const m = members.find((x) => x.memberId !== me.memberId && x.role === role);
    return m ? { name: m.name, isMe: false, roleDepth: poolDepth(m.pool, role) } : null;
  };

  return (
    <div className={`team team--${variant}`}>
      <div className="team__head">
        <span className="team__title">
          {title}
          {liveOn && (
            <span
              className="team__live"
              title="Live-synced from champ select"
              aria-label="live-synced"
            />
          )}
        </span>
        {side === 'my' && <RoleSelector />}
      </div>
      <div className="team__slots">
        {filled.map(({ role, champId }) => (
          // keyed by role+champ so a fill/change remounts → the lock-in slam plays
          <FilledRow
            key={`${role}:${champId}`}
            side={side}
            role={role}
            champId={champId}
            member={memberFor(role)}
          />
        ))}
        {Array.from({ length: empties }).map((_, i) => (
          <button key={`empty-${i}`} className="pick pick--empty" onClick={() => onAdd(side)}>
            <span className="pick__add">＋ add pick</span>
            {/* open roles, on the first empty slab only — a summary, not a
                promise: the champion you add picks its own role */}
            {i === 0 && openRoles.length > 0 && (
              <span
                className="pick__ghosts"
                title={`Open: ${openRoles.map((r) => ROLE_LABEL[r]).join(', ')} — the champion you add is assigned its own role`}
              >
                {openRoles.map((r) => (
                  <span key={r} className="pick__ghost">
                    {ROLE_GLYPH[r]}
                  </span>
                ))}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

export function DraftGrid({ onAdd, side }: Props) {
  // Single-rail mode (champ-select layout): render just one team.
  if (side === 'my') {
    return <TeamBoard side="my" title="MY TEAM" variant="ally" onAdd={onAdd} />;
  }
  if (side === 'enemy') {
    return <TeamBoard side="enemy" title="ENEMY TEAM" variant="enemy" onAdd={onAdd} />;
  }
  // Legacy both-teams grid (kept for any caller that wants the old layout).
  return (
    <div className="grid">
      <TeamBoard side="my" title="MY TEAM" variant="ally" onAdd={onAdd} />
      <TeamBoard side="enemy" title="ENEMY TEAM" variant="enemy" onAdd={onAdd} />
    </div>
  );
}
