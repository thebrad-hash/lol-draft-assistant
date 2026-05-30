import { useEffect, useRef, useState } from 'react';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { getPickOrder } from '../pickOrderApi';
import { useDraft } from '../store';
import { ROLE_LABEL, type PickOrderResult } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

const champName = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const fmtEv = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);
const fmtPct = (v: number) => (v * 100).toFixed(1) + '%';

// "Who picks next": open roles ranked by their best available pick — calibrated
// win probability when the model is live, else additive EV. Recomputed as
// picks/bans change. Hidden once fewer than two roles are open.
export function PickOrder() {
  const { state, autoWeights } = useDraft();
  const [data, setData] = useState<PickOrderResult | null>(null);
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    const t = setTimeout(() => {
      getPickOrder(state, autoWeights)
        .then((d) => {
          if (id === reqId.current) setData(d);
        })
        .catch(() => {});
    }, 180);
    return () => clearTimeout(t);
  }, [state, autoWeights]);

  const open = data?.openRoles ?? [];
  if (open.length < 2) return null; // only meaningful when there's a real choice
  const winMode = open[0]?.bestWin != null;

  return (
    <section className="pickorder">
      <div className="pickorder__head">
        <h2>Who picks next</h2>
        <span className="pickorder__hint">{winMode ? 'by best available win %' : 'by best available EV'}</span>
      </div>
      <ol className="pickorder__list">
        {open.map((r, i) => {
          const hasWin = r.bestWin != null;
          const favorable = hasWin ? (r.bestWin as number) >= 0.5 : (r.bestEv ?? 0) >= 0;
          const steep = hasWin ? r.urgency >= 0.02 : r.urgency >= 0.4;
          const metric = hasWin ? fmtPct(r.bestWin as number) : fmtEv(r.bestEv ?? 0);
          const detail = hasWin ? `${fmtPct(r.bestWin as number)} win` : `${fmtEv(r.bestEv ?? 0)} EV`;
          return (
            <li
              key={r.role}
              className={'pickorder__row' + (i === 0 ? ' is-top' : '')}
              title={`best available ${champName(r.bestChamp)} (${detail})` +
                (steep ? ' · steep drop-off after — lock it in' : '')}
            >
              <span className="pickorder__rank">{i + 1}</span>
              <span className="pickorder__role">{ROLE_LABEL[r.role]}</span>
              <ChampionAvatar id={r.bestChamp} name={champName(r.bestChamp)} size={22} />
              <span className="pickorder__champ">{champName(r.bestChamp)}</span>
              <span className={'pickorder__ev ' + (favorable ? 'pos' : 'neg')}>{metric}</span>
              {i === 0 && <span className="pickorder__badge">pick next</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
