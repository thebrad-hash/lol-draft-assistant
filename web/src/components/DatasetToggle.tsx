// ---------------------------------------------------------------------------
// Win-prob model dataset toggle. Lets you swap which trained model ranks the
// board: the all-patch backbone, or a single recent patch (e.g. 16.12). Hidden
// until at least two datasets exist (nothing to toggle otherwise). The active
// dataset's provenance — patch, #matches, out-of-sample AUC — is shown inline so
// the thin-data reality of a freshly dropped patch is never hidden.
// ---------------------------------------------------------------------------
import { useDraft } from '../store';
import type { ModelDataset } from '../types';

function rankLabel(rank: string): string {
  if (rank === 'master_plus') return 'Master+';
  return rank.charAt(0).toUpperCase() + rank.slice(1);
}

function provenance(d: ModelDataset): string {
  const bits: string[] = [];
  if (d.nMatches != null) bits.push(`${d.nMatches.toLocaleString()} games`);
  if (d.cvAuc != null) bits.push(`AUC ${d.cvAuc.toFixed(3)}`);
  if (d.rank) bits.push(rankLabel(d.rank));
  if (!d.hasUncertainty) bits.push('no error bars yet');
  return bits.join(' · ');
}

// Honest low-confidence warning for the active model: the calibrated model is only
// trustworthy once it beats the 50/50 null out-of-sample. Until then (too few
// games) say so plainly rather than letting a noisy number look authoritative.
function warning(d: ModelDataset): string | null {
  if (!d.beatsNull) return '⚠ not yet better than 50/50 — needs more games';
  if (d.nMatches != null && d.nMatches < 1000) return '⚠ small sample';
  return null;
}

export function DatasetToggle() {
  const { datasets, dataset, setDataset } = useDraft();
  if (datasets.length < 2) return null;
  const active = datasets.find((d) => d.id === dataset) ?? datasets[0];
  const warn = warning(active);

  return (
    <div className="dataset-toggle" title="Which win-probability model ranks the picks">
      <span className="dataset-toggle__label">Model</span>
      <select
        className="dataset-toggle__select"
        value={dataset}
        onChange={(e) => setDataset(e.target.value)}
        aria-label="Win-probability model dataset"
      >
        {datasets.map((d) => (
          <option key={d.id} value={d.id}>
            {d.label}
          </option>
        ))}
      </select>
      <span className={'dataset-toggle__meta' + (warn ? ' is-thin' : '')}>
        {provenance(active)}
        {warn ? ` ${warn}` : ''}
      </span>
    </div>
  );
}
