import { useEffect, useMemo, useRef, useState } from 'react';
import { CHAMPIONS } from '../mock/champions';
import type { Role } from '../types';
import { ROLE_LABEL } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

interface Props {
  title: string;
  roleFilter: Role | null; // null = all champions (used for bans)
  /** ids that are unavailable (banned / already picked) -> greyed, not selectable */
  unavailable: Set<string>;
  /** ids currently chosen for this target (e.g. existing bans) -> marked */
  selected?: Set<string>;
  onSelect: (championId: string) => void;
  onClose: () => void;
}

export function ChampionPicker({
  title,
  roleFilter,
  unavailable,
  selected,
  onSelect,
  onClose,
}: Props) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return CHAMPIONS.filter((c) => {
      if (roleFilter && !c.roles.includes(roleFilter)) return false;
      if (!q) return true;
      return c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q);
    });
  }, [query, roleFilter]);

  return (
    <div className="modal" onMouseDown={onClose}>
      <div className="picker" onMouseDown={(e) => e.stopPropagation()}>
        <div className="picker__head">
          <div className="picker__title">
            {title}
            {roleFilter && <span className="picker__role">{ROLE_LABEL[roleFilter]}</span>}
          </div>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <input
          ref={inputRef}
          className="picker__search"
          placeholder="Search champion…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="picker__grid">
          {results.map((c) => {
            const isUnavailable = unavailable.has(c.id);
            const isSelected = selected?.has(c.id);
            return (
              <button
                key={c.id}
                className={
                  'champcell' +
                  (isUnavailable ? ' champcell--off' : '') +
                  (isSelected ? ' champcell--sel' : '')
                }
                disabled={isUnavailable && !isSelected}
                onClick={() => onSelect(c.id)}
                title={isUnavailable ? `${c.name} (unavailable)` : c.name}
              >
                <ChampionAvatar id={c.id} name={c.name} size={34} dimmed={isUnavailable} />
                <span className="champcell__name">{c.name}</span>
                {isSelected && <span className="champcell__tag">banned</span>}
              </button>
            );
          })}
          {results.length === 0 && <div className="picker__empty">No champions match.</div>}
        </div>
      </div>
    </div>
  );
}
