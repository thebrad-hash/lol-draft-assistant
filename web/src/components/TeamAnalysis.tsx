// Full-draft verdict: shown once both teams have all five picks. Renders the
// favorability score, the headline win condition (early/lane vs late/synergy),
// a lane-by-lane breakdown, and the remaining caveats — all from /api/evaluate.
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import { ROLE_LABEL, type EvalLane } from '../types';
import { ChampionAvatar } from './ChampionAvatar';

const name = (id: string) => CHAMPIONS_BY_ID[id]?.name ?? id;
const pp = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1);

export function TeamAnalysis() {
  const { evaluation: ev, evalLoading } = useDraft();

  // Hidden until both teams are locked in (no eval yet, not loading).
  if (!ev) {
    if (!evalLoading) return null;
    return (
      <section className="analysis">
        <div className="analysis__head">
          <h2>Team Analysis</h2>
          <span className="recos__spinner" aria-label="Analyzing" />
        </div>
        <div className="analysis__pending">Analyzing the matchup…</div>
      </section>
    );
  }

  const { a, b } = ev.score;
  const [headline, ...rest] = ev.winConditions;

  return (
    <section className="analysis">
      <div className="analysis__head">
        <h2>Team Analysis</h2>
        {evalLoading && <span className="recos__spinner" aria-label="Updating" />}
      </div>

      <div className="verdict">
        <div className="verdict__score">
          <span className="verdict__team you">
            Your team <b>{a}</b>
          </span>
          <span className="verdict__team enemy">
            <b>{b}</b> Enemy
          </span>
        </div>
        <div className="verdict__bar" role="img" aria-label={`Draft favorability ${a} to ${b}`}>
          <div className="verdict__fill you" style={{ width: `${a}%` }} />
          <div className="verdict__fill enemy" style={{ width: `${b}%` }} />
        </div>
        <div className="verdict__sub">
          est. draft favorability · lane{' '}
          <b className={ev.components.laneEdge >= 0 ? 'pos' : 'neg'}>{pp(ev.components.laneEdge)}pp</b>{' '}
          · synergy{' '}
          <b className={ev.components.synergyDiff >= 0 ? 'pos' : 'neg'}>{pp(ev.components.synergyDiff)}z</b>
        </div>
      </div>

      {headline && <div className="analysis__headline">{headline}</div>}

      <div className="lanes">
        {ev.lanes.map((ln) => (
          <LaneRow key={ln.role} ln={ln} />
        ))}
      </div>

      {rest.length > 0 && (
        <ul className="caveats">
          {rest.map((c, i) => (
            <li key={i} className="caveat">
              {c}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function LaneRow({ ln }: { ln: EvalLane }) {
  const noData = ln.dpp == null;
  return (
    <div className={'lane' + (noData ? ' lane--nodata' : '')}>
      <span className="lane__role">{ROLE_LABEL[ln.role]}</span>
      <span className={'lane__side you' + (!noData && ln.favored === 'A' ? ' win' : '')}>
        <ChampionAvatar id={ln.a} name={name(ln.a)} size={22} dimmed={!noData && ln.favored === 'B'} />
        <span className="lane__name">{name(ln.a)}</span>
      </span>
      <span
        className={'lane__dpp ' + (noData ? 'even' : (ln.dpp as number) > 0 ? 'pos' : (ln.dpp as number) < 0 ? 'neg' : 'even')}
        title={noData ? 'Off-role pick — no head-to-head matchup data for this pairing' : undefined}
      >
        {noData ? 'no data' : pp(ln.dpp as number)}
      </span>
      <span className={'lane__side enemy' + (!noData && ln.favored === 'B' ? ' win' : '')}>
        <span className="lane__name">{name(ln.b)}</span>
        <ChampionAvatar id={ln.b} name={name(ln.b)} size={22} dimmed={!noData && ln.favored === 'A'} />
      </span>
    </div>
  );
}
