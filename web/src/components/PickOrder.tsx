import { useEffect, useMemo, useRef, useState } from 'react';
import { useLobby } from '../lobby';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { getPickOrder } from '../pickOrderApi';
import { fetchPoolRecs } from '../poolRecs';
import { useDraft } from '../store';
import { ROLE_LABEL, ROLES, type PickOrderResult, type Role } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

const champName = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const fmtEv = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);
const fmtPct = (v: number) => (v * 100).toFixed(1) + '%';

interface PoolBest {
  champ: string;
  win: number;
  urgency: number; // win drop from best to 3rd within the pool
  who: string;
}
interface OrderRow {
  role: Role;
  champ: string;
  win: number | null;
  ev: number | null;
  urgency: number;
  who: string | null; // premade member name when this row comes from a pool
}

// "Who picks next": open roles ranked by their best available pick. In a premade
// lobby this is driven by each role's MEMBER POOL (their best pool champ) rather
// than the global best pick; roles without a pooled member fall back to global.
export function PickOrder() {
  const { state, autoWeights } = useDraft();
  const { lobbyId, members, me } = useLobby();
  const [data, setData] = useState<PickOrderResult | null>(null);
  const [poolBest, setPoolBest] = useState<Record<string, PoolBest>>({});
  const reqId = useRef(0);
  const poolReqId = useRef(0);

  // premade members with a role + non-empty pool (my own fresh local state wins).
  const roster = useMemo(() => {
    if (!lobbyId) return [] as { role: Role; pool: string[]; name: string }[];
    const out: { role: Role; pool: string[]; name: string }[] = [];
    if (me.role && me.pool.length) out.push({ role: me.role, pool: me.pool, name: me.name });
    for (const m of members) {
      if (m.memberId === me.memberId) continue;
      if (m.role && m.pool.length) out.push({ role: m.role, pool: m.pool, name: m.name });
    }
    return out;
  }, [lobbyId, members, me]);
  const rosterKey = useMemo(() => roster.map((r) => `${r.role}:${r.pool.join(',')}`).join('|'), [roster]);

  // global pick-order (covers every open role; the fallback for un-pooled roles)
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

  // best pick from each pooled member's pool, for OPEN roles only
  useEffect(() => {
    const openRoles = ROLES.filter((r) => !state.myTeam[r]);
    const pooled = roster.filter((m) => openRoles.includes(m.role));
    if (!pooled.length) {
      setPoolBest({});
      return;
    }
    const id = ++poolReqId.current;
    const t = setTimeout(async () => {
      const entries = await Promise.all(
        pooled.map(async (m) => {
          const recs = await fetchPoolRecs(state, m.role, m.pool);
          if (!recs.length) return null;
          const win = (r: (typeof recs)[number]) => r.winProbMedian ?? r.winProb ?? 0;
          const best = win(recs[0]);
          const third = win(recs[Math.min(2, recs.length - 1)]);
          return [m.role, { champ: recs[0].championId, win: best, urgency: best - third, who: m.name }] as const;
        }),
      );
      if (id === poolReqId.current) {
        setPoolBest(Object.fromEntries(entries.filter(Boolean) as [Role, PoolBest][]));
      }
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, rosterKey]);

  // merge: pool overrides the global row where a pooled member owns that role
  const rows = useMemo<OrderRow[]>(() => {
    const merged: OrderRow[] = (data?.openRoles ?? []).map((g) => {
      const pb = poolBest[g.role];
      if (pb) return { role: g.role, champ: pb.champ, win: pb.win, ev: null, urgency: pb.urgency, who: pb.who };
      return { role: g.role, champ: g.bestChamp, win: g.bestWin ?? null, ev: g.bestEv, urgency: g.urgency, who: null };
    });
    merged.sort((a, b) => (b.win ?? -1) - (a.win ?? -1) || (b.ev ?? -1) - (a.ev ?? -1));
    return merged;
  }, [data, poolBest]);

  if (rows.length < 2) return null; // only meaningful when there's a real choice
  const winMode = rows[0]?.win != null;
  const fromPools = rows.some((r) => r.who);

  return (
    <section className="pickorder" aria-label="Who picks next">
      <div className="pickorder__head">
        <h2>Who picks next</h2>
        <span className="pickorder__hint">
          {fromPools ? 'by best pick from each pool' : winMode ? 'by best available win %' : 'by best available EV'}
        </span>
      </div>
      {/* horizontal turn-tracker: open roles left→right in pick-priority order */}
      <ol className="pickorder__track">
        {rows.map((r, i) => {
          const hasWin = r.win != null;
          const favorable = hasWin ? (r.win as number) >= 0.5 : (r.ev ?? 0) >= 0;
          const steep = hasWin ? r.urgency >= 0.02 : r.urgency >= 0.4;
          const metric = hasWin ? fmtPct(r.win as number) : fmtEv(r.ev ?? 0);
          return (
            <li
              key={r.role}
              className={'pickorder__stop' + (i === 0 ? ' is-top' : '')}
              title={
                `${r.who ? `${r.who}'s ` : 'best available '}${champName(r.champ)}` +
                (steep ? ' · steep drop-off after — lock it in' : '')
              }
            >
              <span className="pickorder__rank" aria-hidden="true">{i + 1}</span>
              <ChampionAvatar id={r.champ} name={champName(r.champ)} size={24} />
              <span className="pickorder__info">
                <span className="pickorder__role">{ROLE_LABEL[r.role]}</span>
                <span className="pickorder__champ">
                  {champName(r.champ)}
                  {r.who && <span className="pickorder__who"> · {r.who}</span>}
                </span>
              </span>
              <span className={'pickorder__ev ' + (favorable ? 'pos' : 'neg')}>
                {metric}
                {steep && (
                  <span
                    className="pickorder__steep"
                    title="steep drop-off after this pick — lock it in"
                    aria-label="steep drop-off after this pick"
                  >
                    ⚠
                  </span>
                )}
              </span>
              {i === 0 && <span className="pickorder__badge">pick next</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
