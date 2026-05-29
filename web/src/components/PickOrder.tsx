import { useEffect, useRef, useState } from 'react';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { getPickOrder } from '../pickOrderApi';
import { useDraft } from '../store';
import { ROLE_LABEL, type PickOrderResult } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

const champName = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const fmtEv = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);

// "Who picks next": open roles ranked by the EV of their best available pick,
// recomputed as picks/bans change. Hidden once fewer than two roles are open.
export function PickOrder() {
  const { state } = useDraft();
  const [data, setData] = useState<PickOrderResult | null>(null);
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    const t = setTimeout(() => {
      getPickOrder(state)
        .then((d) => {
          if (id === reqId.current) setData(d);
        })
        .catch(() => {});
    }, 180);
    return () => clearTimeout(t);
  }, [state]);

  const open = data?.openRoles ?? [];
  if (open.length < 2) return null; // only meaningful when there's a real choice

  return (
    <section className="pickorder">
      <div className="pickorder__head">
        <h2>Who picks next</h2>
        <span className="pickorder__hint">by best available EV</span>
      </div>
      <ol className="pickorder__list">
        {open.map((r, i) => (
          <li
            key={r.role}
            className={'pickorder__row' + (i === 0 ? ' is-top' : '')}
            title={`best available ${champName(r.bestChamp)} (${fmtEv(r.bestEv)} EV)` +
              (r.urgency >= 0.4 ? ` · steep drop-off after — lock it in` : '')}
          >
            <span className="pickorder__rank">{i + 1}</span>
            <span className="pickorder__role">{ROLE_LABEL[r.role]}</span>
            <ChampionAvatar id={r.bestChamp} name={champName(r.bestChamp)} size={22} />
            <span className="pickorder__champ">{champName(r.bestChamp)}</span>
            <span className={'pickorder__ev ' + (r.bestEv >= 0 ? 'pos' : 'neg')}>{fmtEv(r.bestEv)}</span>
            {i === 0 && <span className="pickorder__badge">pick next</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}
