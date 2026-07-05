// The uncertainty signature: ONE reusable hextech confidence bar for every
// win-probability estimate in the app (styles/confidence-bar.css). Renders the
// bootstrap interval as a gold-capped range on a fixed 35–65% display window —
// widths stay comparable across bars — with a diamond median marker and a 50%
// coin-flip reference tick. Tones: favorable (teal) / unfavorable (crimson) /
// tied (gold) / low-data (hatched). Purely presentational: parents pass the
// model's values through; this component never fetches or reinterprets them.

// Display window (percentage points). The draft-only model lives in ~35–65%.
const WIN_MIN = 35;
const WIN_MAX = 65;

const toPos = (pct: number) =>
  Math.max(0, Math.min(100, ((pct - WIN_MIN) / (WIN_MAX - WIN_MIN)) * 100));

export interface ConfidenceBarProps {
  /** central estimate (bootstrap median), 0..1 */
  median: number;
  /** interval bounds, 0..1 */
  lo: number;
  hi: number;
  /** bootstrap sd, 0..1 */
  sd?: number;
  /** interval percentiles (default 5–95 ⇒ "90% CI") */
  ciLoPct?: number;
  ciHiPct?: number;
  /** statistically indistinguishable from the top pick */
  tied?: boolean;
  /** the active model dataset isn't trustworthy yet (thin sample / not beating the null) */
  lowData?: boolean;
  size?: 'md' | 'sm';
  /** render the "lo–hi% · σ" readout under the bar */
  showText?: boolean;
  /** render the 35 / 50 / 65 axis labels (large or expanded contexts) */
  axis?: boolean;
  className?: string;
}

export function ConfidenceBar({
  median,
  lo,
  hi,
  sd,
  ciLoPct = 5,
  ciHiPct = 95,
  tied = false,
  lowData = false,
  size = 'md',
  showText = false,
  axis = false,
  className,
}: ConfidenceBarProps) {
  const medPct = median * 100;
  const loPct = lo * 100;
  const hiPct = hi * 100;
  const left = toPos(loPct);
  const right = toPos(hiPct);
  const width = Math.max(2, right - left);
  // An interval wider than the window gets an outward chevron instead of a cap,
  // so a clamped bar never pretends the uncertainty is smaller than it is.
  const clipLo = loPct < WIN_MIN;
  const clipHi = hiPct > WIN_MAX;
  const tone = tied ? 'tied' : lowData ? 'lowdata' : median >= 0.5 ? 'win' : 'loss';
  const ciWidth = ciHiPct - ciLoPct;

  const label =
    `Win probability ${medPct.toFixed(1)}%; ${ciWidth}% interval ${loPct.toFixed(1)} to ${hiPct.toFixed(1)}%` +
    (typeof sd === 'number' ? `; sd ${(sd * 100).toFixed(1)}` : '') +
    (tied ? '. Too close to call versus the top pick — pick on comfort.' : '.') +
    (lowData ? ' Low-data model — treat as a rough estimate.' : '');

  const cls = ['cbar', `cbar--${tone}`, size === 'sm' ? 'cbar--sm' : '', className ?? '']
    .filter(Boolean)
    .join(' ');

  return (
    <span className={cls} role="img" aria-label={label}>
      <span className="cbar__track" aria-hidden="true">
        <span className="cbar__mid" style={{ left: `${toPos(50)}%` }} />
        <span className="cbar__range" style={{ left: `${left}%`, width: `${width}%` }} />
        {clipLo ? (
          <span className="cbar__clip cbar__clip--lo" />
        ) : (
          <span className="cbar__cap" style={{ left: `${left}%` }} />
        )}
        {clipHi ? (
          <span className="cbar__clip cbar__clip--hi" />
        ) : (
          <span className="cbar__cap" style={{ left: `${right}%` }} />
        )}
        <span className="cbar__node" style={{ left: `${toPos(medPct)}%` }} />
      </span>
      {axis && (
        <span className="cbar__axis" aria-hidden="true">
          <span>{WIN_MIN}</span>
          <span>50</span>
          <span>{WIN_MAX}</span>
        </span>
      )}
      {showText && (
        <span className="cbar__text hex-num" aria-hidden="true">
          {loPct.toFixed(1)}–{hiPct.toFixed(1)}%
          {typeof sd === 'number' && <span className="cbar__sd"> · σ {(sd * 100).toFixed(1)}</span>}
          {tied && <span className="cbar__flag"> · ≈ tied</span>}
        </span>
      )}
    </span>
  );
}
