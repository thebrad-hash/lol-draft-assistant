import { CHAMPIONS_BY_ID } from '../mock/champions';
import type { Contribution, Recommendation } from '../types';
import { ROLE_LABEL } from '../types';
import { ChampionAvatar } from './ChampionAvatar';
import { CoachlessLink } from './CoachlessLink';

function fmtSigned(v: number): string {
  return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);
}

const METRIC_LABEL: Record<Contribution['metric'], string> = {
  in_lane_z: 'in-lane z',
  out_of_lane_z: 'out-of-lane z',
  synergy_z: 'synergy z',
};

function describe(c: Contribution): string {
  const target = CHAMPIONS_BY_ID[c.targetChampion]?.name ?? c.targetChampion;
  const role = ROLE_LABEL[c.targetRole];
  if (c.kind === 'counter') {
    return c.value >= 0
      ? `counters enemy ${role} ${target}`
      : `weak into enemy ${role} ${target}`;
  }
  return c.value >= 0
    ? `synergy w/ ally ${role} ${target}`
    : `anti-synergy w/ ally ${role} ${target}`;
}

export function RecommendationCard({ rank, rec }: { rank: number; rec: Recommendation }) {
  const positive = rec.totalEv >= 0;
  return (
    <li className="card">
      <div className="card__rank">{rank}</div>
      <ChampionAvatar id={rec.championId} name={rec.championName} size={42} />
      <div className="card__body">
        <div className="card__top">
          <span className="card__head">
            <span className="card__name">{rec.championName}</span>
            <CoachlessLink championId={rec.championId} compact />
          </span>
          <span className={'card__ev ' + (positive ? 'pos' : 'neg')}>
            {fmtSigned(rec.totalEv)}
          </span>
        </div>
        <ul className="contribs">
          {rec.contributions.length === 0 && (
            <li className="contrib contrib--muted">
              <span className="contrib__text">no picks known yet — ranked by blind safety</span>
            </li>
          )}
          {rec.contributions.map((c, i) => {
            const pos = c.value >= 0;
            return (
              <li key={i} className={'contrib ' + (pos ? 'contrib--pos' : 'contrib--neg')}>
                <span className="contrib__sign">{pos ? '+' : '−'}</span>
                <span className="contrib__text">{describe(c)}</span>
                <span className="contrib__metric">{METRIC_LABEL[c.metric]}</span>
                <span className="contrib__val">{fmtSigned(c.value)}</span>
              </li>
            );
          })}
          {typeof rec.blindabilityZ === 'number' && (
            <li
              className={
                'contrib contrib--blind ' + (rec.blindabilityZ >= 0 ? 'contrib--pos' : 'contrib--neg')
              }
            >
              <span className="contrib__sign">{rec.blindabilityZ >= 0 ? '+' : '−'}</span>
              <span className="contrib__text">blind-pick safety</span>
              <span className="contrib__metric">blind z</span>
              <span className="contrib__val">{fmtSigned(rec.blindabilityZ)}</span>
            </li>
          )}
        </ul>
      </div>
    </li>
  );
}
