import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import { ChampionAvatar } from './ChampionAvatar';

// A full draft has 10 bans (5 per team); live sync surfaces both teams'.
const MAX_BANS = 10;

// One ban slot: empty → opens the picker; filled → struck-through portrait,
// click to remove. Keyed by champion so a fill remounts and plays the strike.
function BanSlot({ id, onOpenBanPicker }: { id: string | undefined; onOpenBanPicker: () => void }) {
  const { removeBan } = useDraft();
  if (!id) {
    return (
      <button className="banslot banslot--empty" onClick={onOpenBanPicker} title="Add ban">
        +
      </button>
    );
  }
  const champ = CHAMPIONS_BY_ID[id];
  return (
    <button
      className="banslot banslot--filled"
      onClick={() => removeBan(id)}
      title={`${champ?.name ?? id} — click to remove`}
    >
      <ChampionAvatar id={id} name={champ?.name ?? id} size={34} dimmed />
      <span className="banslot__x">×</span>
    </button>
  );
}

// Ten angular slots as two five-slot wings converging on a center gem — the
// champ-select ban shelf. NOTE: bans carry no team attribution in the draft
// model (manual entry is arbitrary order; live sync arrives in draft-action
// order), so the wings are deliberately symmetric — no ally/enemy tinting.
export function BansBar({ onOpenBanPicker }: { onOpenBanPicker: () => void }) {
  const { state } = useDraft();
  const slots = Array.from({ length: MAX_BANS }, (_, i) => state.bans[i]);

  const wing = (side: 'l' | 'r', ids: (string | undefined)[], offset: number) => (
    <div className={`bans__wing bans__wing--${side}`}>
      {ids.map((id, i) => (
        <BanSlot key={id ?? `empty-${offset + i}`} id={id} onOpenBanPicker={onOpenBanPicker} />
      ))}
    </div>
  );

  return (
    <div className="bans" role="group" aria-label="Bans">
      {wing('l', slots.slice(0, 5), 0)}
      <div className="bans__center" aria-hidden="true">
        <span className="bans__label">Bans</span>
        <span className="bans__gem" />
      </div>
      {wing('r', slots.slice(5), 5)}
    </div>
  );
}
