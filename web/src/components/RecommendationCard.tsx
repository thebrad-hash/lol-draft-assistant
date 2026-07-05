import { useState } from 'react';
import { CHAMPIONS_BY_ID } from '../mock/champions';
import { useDraft } from '../store';
import type { Contribution, Recommendation } from '../types';
import { ROLE_LABEL } from '../types';
import { championLoadingUrl, championSplashUrl } from './ChampionAvatar';
import { CoachlessLink } from './CoachlessLink';
import { ConfidenceBar } from './ConfidenceBar';
import { Tooltip } from './Onboarding';

function fmtSigned(v: number): string {
  return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2);
}

const METRIC_LABEL: Record<Contribution['metric'], string> = {
  in_lane_z: 'in-lane',
  out_of_lane_z: 'cross-lane',
  synergy_z: 'synergy',
};

function describe(c: Contribution): string {
  const target = CHAMPIONS_BY_ID[c.targetChampion]?.name ?? c.targetChampion;
  const role = ROLE_LABEL[c.targetRole];
  if (c.kind === 'counter') {
    return c.value >= 0 ? `counters ${role} ${target}` : `weak into ${role} ${target}`;
  }
  return c.value >= 0 ? `synergy with ${role} ${target}` : `anti-synergy with ${role} ${target}`;
}

export function RecommendationCard({ rank, rec }: { rank: number; rec: Recommendation }) {
  const [open, setOpen] = useState(false);
  // Honesty flag for the active model dataset — same rule DatasetToggle warns
  // with: not yet beating the 50/50 null out-of-sample, or a thin sample. The
  // confidence bar renders hatched/dim so a noisy model never looks confident.
  const { datasets, dataset } = useDraft();
  const active = datasets.find((d) => d.id === dataset);
  const lowData = !!active && (!active.beatsNull || (active.nMatches != null && active.nMatches < 1000));

  const hasCI = typeof rec.winProbMedian === 'number';
  const hasWin = typeof rec.winProb === 'number';
  // Headline win%: bootstrap median when available, else the point estimate.
  const headlineWin = hasCI ? (rec.winProbMedian as number) : hasWin ? (rec.winProb as number) : null;
  const favorable = headlineWin != null ? headlineWin >= 0.5 : rec.totalEv >= 0;
  const tied = rec.tiedWithTop === true;
  const isTop = rank === 1;
  const loPct = rec.ciLoPct ?? 5;
  const hiPct = rec.ciHiPct ?? 95;

  // Build the contribution rows once (shared by teaser + expanded detail).
  const rows = rec.contributions.map((c) => ({ value: c.value, text: describe(c), metric: METRIC_LABEL[c.metric] }));
  if (typeof rec.blindabilityZ === 'number') {
    rows.push({ value: rec.blindabilityZ, text: 'blind-pick safety', metric: 'blind' });
  }
  const adv = rows.filter((r) => r.value >= 0).length;
  const dis = rows.length - adv;

  const tieTitle =
    rec.probTopBetter != null
      ? `Too close to call — the top pick wins only ${(rec.probTopBetter * 100).toFixed(0)}% of ` +
        `bootstrap resamples (below the ${((rec.tieThreshold ?? 0.85) * 100).toFixed(0)}% bar). ` +
        `The ordering is within noise; pick on comfort.`
      : 'Statistically indistinguishable from the top pick — pick on comfort.';

  // Portrait stays the loading art (portrait-cropped); the hero card's backdrop
  // bleed uses the wide cinematic splash, which composes better across a slab.
  const portrait = championLoadingUrl(rec.championId);
  const bleed = isTop ? championSplashUrl(rec.championId) : portrait;
  const headline = headlineWin != null ? (headlineWin * 100).toFixed(1) : null;

  return (
    <li
      className={
        'pc' +
        (isTop ? ' pc--top' : '') +
        (tied ? ' pc--tied' : '') +
        (open ? ' is-open' : '') +
        (favorable ? ' pc--fav' : ' pc--unfav')
      }
      style={{ '--splash': `url("${bleed}")` } as React.CSSProperties}
    >
      <button
        type="button"
        className="pc__head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="pc__portrait">
          <img className="pc__art" src={portrait} alt={rec.championName} loading="lazy" />
          <span className="pc__rank">{rank}</span>
        </span>

        <span className="pc__id">
          <span className="pc__name">{rec.championName}</span>
          <span className="pc__sub">
            {tied ? (
              <Tooltip label={tieTitle}>
                <span className="pc__tie">
                  ◆ <span className="pc__tie-long">too close to call</span>
                  <span className="pc__tie-short">≈ tied</span>
                </span>
              </Tooltip>
            ) : (
              <span className="pc__meta hex-num">
                {adv > 0 && <span className="pc__adv">▲ {adv}</span>}
                {dis > 0 && <span className="pc__dis">▼ {dis}</span>}
                <span className="pc__sep">{rows.length ? 'reasons' : 'blind safety'}</span>
              </span>
            )}
            <CoachlessLink championId={rec.championId} compact />
          </span>
        </span>

        <span className="pc__score">
          {headline != null ? (
            <>
              <span className="pc__pct hex-num">
                {headline}
                <span className="pc__unit">%</span>
              </span>
              {hasCI && (
                <ConfidenceBar
                  median={rec.winProbMedian as number}
                  lo={rec.winProbLo as number}
                  hi={rec.winProbHi as number}
                  sd={rec.winProbStd}
                  ciLoPct={loPct}
                  ciHiPct={hiPct}
                  tied={tied}
                  lowData={lowData}
                  size={isTop ? 'md' : 'sm'}
                  showText
                />
              )}
            </>
          ) : (
            <span className="pc__pct hex-num">{fmtSigned(rec.totalEv)}</span>
          )}
        </span>

        <span className={'pc__chevron' + (open ? ' is-open' : '')} aria-hidden="true">⌄</span>
      </button>

      <div className="pc__detail">
        <div className="pc__detail-inner">
          {hasCI && (
            <>
              <div className="hex-rule">
                <span className="hex-overline">{loPct}–{hiPct}% interval · 1000 refits</span>
                <span className="hex-rule__gem" />
              </div>
              <div className="pc__uncert">
                <ConfidenceBar
                  median={rec.winProbMedian as number}
                  lo={rec.winProbLo as number}
                  hi={rec.winProbHi as number}
                  sd={rec.winProbStd}
                  ciLoPct={loPct}
                  ciHiPct={hiPct}
                  tied={tied}
                  lowData={lowData}
                  axis
                  showText
                />
                {tied && <p className="pc__tie-note">{tieTitle}</p>}
                {lowData && (
                  <p className="pc__lowdata-note">
                    Low-data model — it doesn't yet beat a coin flip out-of-sample. Treat this as
                    a rough estimate.
                  </p>
                )}
              </div>
            </>
          )}
          {rows.length === 0 ? (
            <p className="pc__empty">No picks locked yet — ranked by blind-pick safety.</p>
          ) : (
            <ul className="pc__factors">
              {rows.map((r, i) => {
                const pos = r.value >= 0;
                return (
                  <li key={i} className={'pc__factor ' + (pos ? 'is-pos' : 'is-neg')}>
                    <span className="pc__factor-mark" aria-hidden="true">{pos ? '▲' : '▼'}</span>
                    <span className="pc__factor-text">{r.text}</span>
                    <span className="pc__factor-metric">{r.metric}</span>
                    <span className="pc__factor-val hex-num">{fmtSigned(r.value)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </li>
  );
}
