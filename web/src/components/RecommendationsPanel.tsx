import { useLobby } from '../lobby';
import { useDraft } from '../store';
import { ROLE_LABEL } from '../types';
import { RecommendationCard } from './RecommendationCard';

export function RecommendationsPanel() {
  const { recommendations, loading, state, autoWeights, weightNotes } = useDraft();
  const { lobbyId, members } = useLobby();
  const picker = lobbyId ? members.find((m) => m.role === state.pickingForRole) : null;
  const autoNote = autoWeights && weightNotes.length ? weightNotes[weightNotes.length > 1 ? 1 : 0] : null;
  return (
    <aside className="recos">
      <div className="recos__head">
        <h2>
          Best picks · <span className="recos__role">{ROLE_LABEL[state.pickingForRole]}</span>
          {picker && <span className="recos__picker"> · {picker.name}</span>}
        </h2>
        {loading && <span className="recos__spinner" aria-label="Updating" />}
      </div>
      {recommendations[0]?.winProb != null && (
        <div className="recos__subnote">ranked by calibrated win probability</div>
      )}
      {autoNote && (
        <div className="recos__autonote" title={weightNotes.join(' · ')}>
          ⚖ auto · {autoNote}
        </div>
      )}
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
