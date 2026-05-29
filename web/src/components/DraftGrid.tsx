import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft, type Side } from '../store';
import { ROLE_GLYPH, ROLE_LABEL, ROLES, type Role } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

const TEAM_SIZE = 5;

interface Props {
  onAdd: (side: Side) => void;
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

function FilledRow({ side, role, champId }: { side: Side; role: Role; champId: string }) {
  const { clearSlot } = useDraft();
  const champ = CHAMPIONS_BY_ID[champId];
  return (
    <div className="pick pick--filled">
      <ChampionAvatar id={champId} name={champ?.name ?? champId} size={34} />
      <span className="pick__name">{champ?.name ?? champId}</span>
      <span className="rolechip" title={ROLE_LABEL[role]}>
        {ROLE_GLYPH[role]}
      </span>
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
  const { state } = useDraft();
  const team = side === 'my' ? state.myTeam : state.enemyTeam;
  // pick order = insertion order of role keys
  const filled = (Object.keys(team) as Role[]).map((role) => ({ role, champId: team[role]! }));
  const empties = Math.max(0, TEAM_SIZE - filled.length);

  return (
    <div className={`team team--${variant}`}>
      <div className="team__head">
        <span className="team__title">{title}</span>
        {side === 'my' && <RoleSelector />}
      </div>
      <div className="team__slots">
        {filled.map(({ role, champId }) => (
          <FilledRow key={role} side={side} role={role} champId={champId} />
        ))}
        {Array.from({ length: empties }).map((_, i) => (
          <button key={`empty-${i}`} className="pick pick--empty" onClick={() => onAdd(side)}>
            <span className="pick__add">＋ add pick</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function DraftGrid({ onAdd }: Props) {
  return (
    <div className="grid">
      <TeamBoard side="my" title="MY TEAM" variant="ally" onAdd={onAdd} />
      <TeamBoard side="enemy" title="ENEMY TEAM" variant="enemy" onAdd={onAdd} />
    </div>
  );
}
