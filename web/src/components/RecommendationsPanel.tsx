import { useDraft } from '../store';
import { ROLE_LABEL } from '../types';
import { RecommendationCard } from './RecommendationCard';

export function RecommendationsPanel() {
  const { recommendations, loading, state } = useDraft();
  return (
    <aside className="recos">
      <div className="recos__head">
        <h2>
          Best picks · <span className="recos__role">{ROLE_LABEL[state.pickingForRole]}</span>
        </h2>
        {loading && <span className="recos__spinner" aria-label="Updating" />}
      </div>
      {recommendations.length === 0 && !loading ? (
        <div className="recos__empty">
          No candidates. Try clearing bans / pool filter, or pick a different role.
        </div>
      ) : (
        <ol className="recos__list">
          {recommendations.map((rec, i) => (
            <RecommendationCard key={rec.championId} rank={i + 1} rec={rec} />
          ))}
        </ol>
      )}
    </aside>
  );
}
