import { useEffect, useRef, useState } from 'react';
import { getBoard } from '../boardApi';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import { ROLE_LABEL, type BoardResult, type Contribution, type Recommendation } from '../types';
import { ChampionAvatar } from './ChampionAvatar';
import { CoachlessLink } from './CoachlessLink';

const champName = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const fmtSigned = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);

function scoreText(p: Recommendation): string {
  if (typeof p.winProb === 'number') return `${(p.winProb * 100).toFixed(1)}%`;
  return fmtSigned(p.totalEv);
}
const favorable = (p: Recommendation) =>
  typeof p.winProb === 'number' ? p.winProb >= 0.5 : p.totalEv >= 0;

function describe(c: Contribution): string {
  const target = champName(c.targetChampion);
  const role = ROLE_LABEL[c.targetRole];
  if (c.kind === 'counter') {
    return c.value >= 0 ? `counters ${role} ${target}` : `weak into ${role} ${target}`;
  }
  return c.value >= 0 ? `synergy w/ ${role} ${target}` : `anti-synergy w/ ${role} ${target}`;
}

// Expanded advantages/disadvantages for one pick (counters, synergies, blind safety).
function PickDetail({ rec }: { rec: Recommendation }) {
  const rows = rec.contributions.map((c) => ({ val: c.value, text: describe(c) }));
  if (typeof rec.blindabilityZ === 'number') rows.push({ val: rec.blindabilityZ, text: 'blind-pick safety' });
  return (
    <div className="boardpick__detail">
      {rows.length === 0 && (
        <div className="bpd__row bpd__muted">no picks known yet — ranked by blind safety</div>
      )}
      {rows.map((r, i) => (
        <div key={i} className={'bpd__row ' + (r.val >= 0 ? 'pos' : 'neg')}>
          <span className="bpd__val">{fmtSigned(r.val)}</span>
          <span className="bpd__text">{r.text}</span>
        </div>
      ))}
    </div>
  );
}

// The all-roles board: top picks for every role at once, in the space below the
// draft grid. Click any pick to expand its advantages/disadvantages. Roles your
// team has locked show the locked champion instead of a list.
export function AllRolesBoard() {
  const { state, autoWeights } = useDraft();
  const [data, setData] = useState<BoardResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const reqId = useRef(0);

  const toggle = (key: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    const t = setTimeout(() => {
      getBoard(state, autoWeights)
        .then((d) => {
          if (id === reqId.current) {
            setData(d);
            setLoading(false);
          }
        })
        .catch(() => {
          if (id === reqId.current) setLoading(false);
        });
    }, 200);
    return () => clearTimeout(t);
  }, [state, autoWeights]);

  if (!data) return null;

  return (
    <section className="board">
      <div className="board__head">
        <h2>Best picks · all roles</h2>
        <span className="board__hint">click a pick for advantages / disadvantages</span>
        {loading && <span className="recos__spinner" aria-label="Updating" />}
      </div>
      <div className="board__cols">
        {data.roles.map((r) => (
          <div
            key={r.role}
            className={'board__col' + (r.role === state.pickingForRole ? ' is-active' : '')}
          >
            <div className="board__role">
              <span>{ROLE_LABEL[r.role]}</span>
              {r.picked && <span className="board__locked">✓ {champName(r.picked)}</span>}
            </div>
            {r.picked ? (
              <div className="board__lockedpick">
                <ChampionAvatar id={r.picked} name={champName(r.picked)} size={30} />
                <span className="boardpick__name">{champName(r.picked)}</span>
              </div>
            ) : (
              <ol className="board__picks">
                {r.picks.map((p, i) => {
                  const key = `${r.role}:${p.championId}`;
                  const isOpen = open.has(key);
                  return (
                    <li key={p.championId} className={'boardpick' + (isOpen ? ' is-open' : '')}>
                      <div
                        className="boardpick__row"
                        role="button"
                        tabIndex={0}
                        aria-expanded={isOpen}
                        onClick={() => toggle(key)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            toggle(key);
                          }
                        }}
                      >
                        <span className="boardpick__rank">{i + 1}</span>
                        <ChampionAvatar id={p.championId} name={champName(p.championId)} size={26} />
                        <span className="boardpick__name">{champName(p.championId)}</span>
                        <CoachlessLink championId={p.championId} compact />
                        <span className={'boardpick__ev ' + (favorable(p) ? 'pos' : 'neg')}>
                          {scoreText(p)}
                        </span>
                      </div>
                      {isOpen && <PickDetail rec={p} />}
                    </li>
                  );
                })}
                {r.picks.length === 0 && <li className="boardpick boardpick--empty">no candidates</li>}
              </ol>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
