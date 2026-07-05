import { useEffect, useMemo, useRef, useState } from 'react';
import { getBoard } from '../boardApi';
import { useLobby } from '../lobby';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { fetchPoolRecs } from '../poolRecs';
import { useDraft } from '../store';
import { ROLE_LABEL, type BoardResult, type Contribution, type Recommendation, type Role } from '../types';
import { ChampionAvatar } from './ChampionAvatar';
import { CoachlessLink } from './CoachlessLink';
import { ConfidenceBar } from './ConfidenceBar';
import { Tooltip } from './Onboarding';

// Global lists show this many picks per role before the per-column "show all"
// toggle — headline first, full depth one click away.
const GLOBAL_PREVIEW = 5;

const champName = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const fmtSigned = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);

// "Focus" priority: how well a candidate matches up vs the focused enemies /
// synergizes with the focused allies, summed from its own contribution data
// (counter z is positive when the candidate beats that enemy; synergy z positive
// when it pairs well). Higher = better against/with the champs you've focused.
export function focusScore(rec: Recommendation, focusSet: Set<string>): number {
  if (focusSet.size === 0) return 0;
  let s = 0;
  for (const c of rec.contributions) if (focusSet.has(c.targetChampion)) s += c.value;
  return s;
}

const sortKey = (r: Recommendation): number => r.winProbMedian ?? r.winProb ?? r.totalEv ?? 0;

// Re-rank a candidate list to prioritize the focused champions (counter/synergy),
// win-prob as the tiebreaker. No focus -> original order untouched.
export function applyFocus(recs: Recommendation[], focusSet: Set<string>): Recommendation[] {
  if (focusSet.size === 0) return recs;
  return [...recs].sort((a, b) => {
    const d = focusScore(b, focusSet) - focusScore(a, focusSet);
    return d !== 0 ? d : sortKey(b) - sortKey(a);
  });
}

function scoreText(p: Recommendation): string {
  if (typeof p.winProbMedian === 'number') return `${(p.winProbMedian * 100).toFixed(1)}%`;
  if (typeof p.winProb === 'number') return `${(p.winProb * 100).toFixed(1)}%`;
  return fmtSigned(p.totalEv);
}
const favorable = (p: Recommendation) =>
  typeof p.winProbMedian === 'number'
    ? p.winProbMedian >= 0.5
    : typeof p.winProb === 'number'
      ? p.winProb >= 0.5
      : p.totalEv >= 0;

// Signed "vs an average pick for this role" delta (percentage points). Colored by
// sign so the best available pick reads as positive even when the board's absolute
// win% is below 50% (a team-level baseline deficit no single pick can overcome).
function deltaText(p: Recommendation): string | null {
  if (typeof p.winProbDelta !== 'number') return null;
  const pp = p.winProbDelta * 100;
  return `${pp >= 0 ? '+' : ''}${pp.toFixed(1)}`;
}

const tieTitle = (p: Recommendation) =>
  p.probTopBetter != null
    ? `Too close to call: top pick wins only ${(p.probTopBetter * 100).toFixed(0)}% of ` +
      `bootstrap resamples (below the ${((p.tieThreshold ?? 0.85) * 100).toFixed(0)}% bar). ` +
      `Pick on comfort.`
    : undefined;

function describe(c: Contribution): string {
  const target = champName(c.targetChampion);
  const role = ROLE_LABEL[c.targetRole];
  if (c.kind === 'counter') {
    return c.value >= 0 ? `counters ${role} ${target}` : `weak into ${role} ${target}`;
  }
  return c.value >= 0 ? `synergy w/ ${role} ${target}` : `anti-synergy w/ ${role} ${target}`;
}

// Expanded advantages/disadvantages for one pick (counters, synergies, blind
// safety) — plus the shared hextech ConfidenceBar for the bootstrap interval.
function PickDetail({ rec, lowData }: { rec: Recommendation; lowData: boolean }) {
  const rows = rec.contributions.map((c) => ({ val: c.value, text: describe(c) }));
  if (typeof rec.blindabilityZ === 'number') rows.push({ val: rec.blindabilityZ, text: 'blind-pick safety' });
  return (
    <div className="boardpick__detail">
      {typeof rec.winProbMedian === 'number' && (
        <div className="bpd__ci">
          <ConfidenceBar
            median={rec.winProbMedian as number}
            lo={rec.winProbLo as number}
            hi={rec.winProbHi as number}
            sd={rec.winProbStd}
            ciLoPct={rec.ciLoPct ?? 5}
            ciHiPct={rec.ciHiPct ?? 95}
            tied={rec.tiedWithTop === true}
            lowData={lowData}
            size="sm"
            showText
          />
          {rec.tiedWithTop && (
            <div className="bpd__tie-note">
              {tieTitle(rec) ?? 'Tied with the top pick — too close to call. Pick on comfort.'}
            </div>
          )}
        </div>
      )}
      {rows.length === 0 && (
        <div className="bpd__row bpd__muted">no picks known yet — ranked by blind safety</div>
      )}
      {rows.map((r, i) => (
        <div key={i} className={'bpd__row ' + (r.val >= 0 ? 'pos' : 'neg')}>
          <span className="bpd__val">{fmtSigned(r.val)}</span>
          <span className="bpd__text">{r.text}</span>
        </div>
      ))}
      <CoachlessLink championId={rec.championId} />
    </div>
  );
}

// One ranked pick row, shared by the global list and the per-member pool lists.
function BoardPickRow({
  rec,
  rank,
  rowKey,
  open,
  toggle,
  focusVal,
  lowData,
}: {
  rec: Recommendation;
  rank: number;
  rowKey: string;
  open: Set<string>;
  toggle: (k: string) => void;
  focusVal?: number; // matchup/synergy vs focused champs (shown when focusing)
  lowData: boolean;
}) {
  const isOpen = open.has(rowKey);
  return (
    <li className={'boardpick' + (isOpen ? ' is-open' : '')}>
      <div
        className="boardpick__row"
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        onClick={() => toggle(rowKey)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggle(rowKey);
          }
        }}
      >
        <ChampionAvatar id={rec.championId} name={champName(rec.championId)} size={30} />
        <span className="boardpick__body">
          <span className="boardpick__line">
            <span className="boardpick__name" title={champName(rec.championId)}>
              {champName(rec.championId)}
            </span>
            {focusVal != null && (
              <span
                className={'boardpick__focus ' + (focusVal >= 0 ? 'pos' : 'neg')}
                title="matchup/synergy vs your focused champs (z)"
              >
                ⚔ {fmtSigned(focusVal)}
              </span>
            )}
          </span>
          <span className="boardpick__line">
            <span className="boardpick__rank">{rank}</span>
            {rec.tiedWithTop && (
              <Tooltip
                label={
                  tieTitle(rec) ??
                  'Statistically indistinguishable from the top pick — pick on comfort.'
                }
              >
                <span className="boardpick__tie">≈ tied</span>
              </Tooltip>
            )}
            <span className={'boardpick__ev ' + (favorable(rec) ? 'pos' : 'neg')}>{scoreText(rec)}</span>
            {deltaText(rec) && (
              <span
                className={'boardpick__delta ' + ((rec.winProbDelta as number) >= 0 ? 'pos' : 'neg')}
                title="vs an average pick for this role — positive = a better-than-field choice even if the board's absolute win% is below 50%"
              >
                {deltaText(rec)}
              </span>
            )}
          </span>
        </span>
      </div>
      {isOpen && <PickDetail rec={rec} lowData={lowData} />}
    </li>
  );
}

interface PoolMember {
  memberId: string;
  name: string;
  role: Role;
  pool: string[];
  isMe: boolean;
}

// The all-roles board: top picks for every role at once. When you're in a premade
// lobby, each role column ALSO lists every teammate's champion pool for that role
// (same recommender, restricted to their pool) beneath the global list — so you can
// coordinate who plays what at a glance.
export function AllRolesBoard() {
  const { state, autoWeights, focus, toggleFocus, datasets, dataset } = useDraft();
  const { lobbyId, members, me } = useLobby();
  const focusSet = useMemo(() => new Set(focus), [focus]);
  const focusing = focusSet.size > 0;
  const [data, setData] = useState<BoardResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  // roles whose global list is fully unfolded (default: top GLOBAL_PREVIEW)
  const [unfolded, setUnfolded] = useState<Set<Role>>(new Set());
  const reqId = useRef(0);

  // Same honesty rule the dataset toggle warns with — a thin/unproven model
  // renders its confidence bars hatched instead of looking authoritative.
  const activeDs = datasets.find((d) => d.id === dataset);
  const lowData = !!activeDs && (!activeDs.beatsNull || (activeDs.nMatches != null && activeDs.nMatches < 1000));

  // Premade members who have a role AND a non-empty pool (my own fresh local state
  // takes precedence over the polled server copy). Empty when not in a lobby.
  const roster = useMemo<PoolMember[]>(() => {
    if (!lobbyId) return [];
    const out: PoolMember[] = [];
    if (me.role && me.pool.length) {
      out.push({ memberId: me.memberId, name: me.name, role: me.role, pool: me.pool, isMe: true });
    }
    for (const m of members) {
      if (m.memberId === me.memberId) continue;
      if (m.role && m.pool.length) {
        out.push({ memberId: m.memberId, name: m.name, role: m.role, pool: m.pool, isMe: false });
      }
    }
    return out;
  }, [lobbyId, members, me]);
  // Stable signature so the 5s lobby poll doesn't refetch pools when nothing changed.
  const rosterKey = useMemo(
    () => roster.map((r) => `${r.memberId}:${r.role}:${r.pool.join(',')}`).join('|'),
    [roster],
  );

  const [poolRecs, setPoolRecs] = useState<Record<string, Recommendation[]>>({});
  const poolReqId = useRef(0);

  const toggle = (key: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const toggleUnfold = (role: Role) =>
    setUnfolded((s) => {
      const next = new Set(s);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });

  // global board
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

  // per-member pool picks (one pool-filtered recommend per member, same draft state)
  useEffect(() => {
    if (!roster.length) {
      setPoolRecs({});
      return;
    }
    const id = ++poolReqId.current;
    const t = setTimeout(async () => {
      const entries = await Promise.all(
        roster.map(async (m) => {
          const recs = await fetchPoolRecs(state, m.role, m.pool);
          return [m.memberId, recs] as const;
        }),
      );
      if (id === poolReqId.current) setPoolRecs(Object.fromEntries(entries));
    }, 220);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, rosterKey]);

  if (!data) return null;

  return (
    <section className="board">
      <div className="board__head">
        <h2>Best picks · all roles</h2>
        {focusing ? (
          <span
            className="board__focus-note"
            aria-live="polite"
            title="Best Picks are re-ranked to prioritize these champions"
          >
            ⚔ prioritizing {[...focusSet].map(champName).join(', ')}
            <button
              type="button"
              className="board__focus-clear"
              onClick={() => focus.forEach((c) => toggleFocus(c))}
            >
              clear
            </button>
          </span>
        ) : (
          <span className="board__hint">click a picked champ to prioritize · click a pick for details</span>
        )}
        {loading && <span className="recos__spinner" aria-label="Updating" />}
      </div>
      <div className="board__cols">
        {data.roles.map((r) => {
          const rolePool = roster.filter((m) => m.role === r.role);
          const globalPicks = applyFocus(r.picks, focusSet);
          return (
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
                <>
                  {/* premade pools FIRST — above the global list */}
                  {rolePool.map((m) => {
                    const loaded = poolRecs[m.memberId] != null;
                    const recs = applyFocus(poolRecs[m.memberId] ?? [], focusSet);
                    return (
                      <div className="board__pool board__pool--top" key={m.memberId}>
                        <div className="board__pool-head">
                          <span className={'board__pool-name' + (m.isMe ? ' is-me' : '')}>
                            {m.name}
                            {m.isMe && <span className="board__pool-you">you</span>}
                          </span>
                          <span className="board__pool-tag">pool · {m.pool.length}</span>
                        </div>
                        <ol className="board__picks board__picks--pool">
                          {recs.length > 0 ? (
                            recs.map((p, i) => (
                              <BoardPickRow
                                key={p.championId}
                                rec={p}
                                rank={i + 1}
                                rowKey={`${r.role}:p:${m.memberId}:${p.championId}`}
                                open={open}
                                toggle={toggle}
                                focusVal={focusing ? focusScore(p, focusSet) : undefined}
                                lowData={lowData}
                              />
                            ))
                          ) : (
                            <li className="boardpick boardpick--empty">
                              {loaded ? 'no pool champ plays here' : 'loading…'}
                            </li>
                          )}
                        </ol>
                      </div>
                    );
                  })}

                  {/* global best picks (labelled when pools are shown above) */}
                  {rolePool.length > 0 && (
                    <div className="board__global-label">
                      <span>all champions</span>
                    </div>
                  )}
                  <ol className="board__picks">
                    {(unfolded.has(r.role) ? globalPicks : globalPicks.slice(0, GLOBAL_PREVIEW)).map((p, i) => (
                      <BoardPickRow
                        key={p.championId}
                        rec={p}
                        rank={i + 1}
                        rowKey={`${r.role}:g:${p.championId}`}
                        open={open}
                        toggle={toggle}
                        focusVal={focusing ? focusScore(p, focusSet) : undefined}
                        lowData={lowData}
                      />
                    ))}
                    {globalPicks.length === 0 && <li className="boardpick boardpick--empty">no candidates</li>}
                  </ol>
                  {globalPicks.length > GLOBAL_PREVIEW && (
                    <button
                      type="button"
                      className="board__more"
                      aria-expanded={unfolded.has(r.role)}
                      onClick={() => toggleUnfold(r.role)}
                    >
                      {unfolded.has(r.role) ? '▴ top 5' : `▾ all ${globalPicks.length}`}
                    </button>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
