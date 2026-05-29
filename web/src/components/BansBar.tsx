import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import { ChampionAvatar } from './ChampionAvatar';

const MAX_BANS = 5;

export function BansBar({ onOpenBanPicker }: { onOpenBanPicker: () => void }) {
  const { state, removeBan } = useDraft();
  const slots = Array.from({ length: MAX_BANS }, (_, i) => state.bans[i]);

  return (
    <div className="bans">
      <span className="bans__label">BANS</span>
      <div className="bans__slots">
        {slots.map((id, i) => {
          if (!id) {
            return (
              <button
                key={`empty-${i}`}
                className="banslot banslot--empty"
                onClick={onOpenBanPicker}
                title="Add ban"
              >
                +
              </button>
            );
          }
          const champ = CHAMPIONS_BY_ID[id];
          return (
            <button
              key={id}
              className="banslot banslot--filled"
              onClick={() => removeBan(id)}
              title={`${champ?.name ?? id} — click to remove`}
            >
              <ChampionAvatar id={id} name={champ?.name ?? id} size={34} dimmed />
              <span className="banslot__x">×</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
