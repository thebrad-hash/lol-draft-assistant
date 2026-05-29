import { useMemo, useState } from 'react';
import { CHAMPIONS, CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import type { Weights } from '../types';

const SLIDERS: { key: keyof Weights; label: string; hint: string }[] = [
  { key: 'inLane', label: 'In-lane', hint: 'matchup vs your direct opponent' },
  { key: 'outOfLane', label: 'Out-of-lane', hint: 'matchups vs the other enemies' },
  { key: 'synergy', label: 'Synergy', hint: 'fit with your allies' },
  { key: 'blindability', label: 'Blindability', hint: 'safety vs the meta field' },
];

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

// id/name lookup for resolving the pool filter text.
const LOOKUP: Record<string, string> = {};
for (const c of CHAMPIONS) {
  LOOKUP[norm(c.id)] = c.id;
  LOOKUP[norm(c.name)] = c.id;
}

export function WeightsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state, setWeights, resetWeights, setPoolFilter } = useDraft();
  const [poolText, setPoolText] = useState(
    () => (state.poolFilter ?? []).map((id) => CHAMPIONS_BY_ID[id]?.name ?? id).join(', '),
  );

  const { matched, unmatched } = useMemo(() => {
    const tokens = poolText.split(',').map((t) => t.trim()).filter(Boolean);
    const m: string[] = [];
    const u: string[] = [];
    for (const tok of tokens) {
      const id = LOOKUP[norm(tok)];
      if (id && !m.includes(id)) m.push(id);
      else if (!id) u.push(tok);
    }
    return { matched: m, unmatched: u };
  }, [poolText]);

  const applyPool = () => setPoolFilter(matched.length ? matched : undefined);
  const clearPool = () => {
    setPoolText('');
    setPoolFilter(undefined);
  };

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <div className={'drawer' + (open ? ' drawer--open' : '')}>
        <div className="drawer__head">
          <h2>Scoring weights</h2>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="drawer__section">
          {SLIDERS.map(({ key, label, hint }) => (
            <label key={key} className="wslider">
              <div className="wslider__top">
                <span className="wslider__label">{label}</span>
                <span className="wslider__val">{state.weights[key].toFixed(2)}</span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={state.weights[key]}
                onChange={(e) => setWeights({ [key]: Number(e.target.value) } as Partial<Weights>)}
              />
              <span className="wslider__hint">{hint}</span>
            </label>
          ))}
          <button className="btn btn--ghost" onClick={resetWeights}>
            Reset to defaults
          </button>
        </div>

        <div className="drawer__section">
          <h3>Champion pool filter</h3>
          <p className="drawer__hint">
            Restrict recommendations to specific champions (comma-separated). Leave empty to
            consider all.
          </p>
          <textarea
            className="pool__input"
            rows={3}
            placeholder="e.g. Aatrox, Ornn, K'Sante, Sett"
            value={poolText}
            onChange={(e) => setPoolText(e.target.value)}
          />
          <div className="pool__chips">
            {matched.map((id) => (
              <span key={id} className="chip chip--ok">
                {CHAMPIONS_BY_ID[id]?.name ?? id}
              </span>
            ))}
            {unmatched.map((t) => (
              <span key={t} className="chip chip--bad" title="No match">
                {t}?
              </span>
            ))}
          </div>
          <div className="pool__actions">
            <button className="btn" onClick={applyPool}>
              Apply pool ({matched.length})
            </button>
            <button className="btn btn--ghost" onClick={clearPool}>
              Clear
            </button>
          </div>
          {state.poolFilter && (
            <p className="pool__active">Active: {state.poolFilter.length} champions</p>
          )}
        </div>
      </div>
    </>
  );
}
