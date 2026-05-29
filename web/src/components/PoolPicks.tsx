import { useEffect, useRef, useState } from 'react';
import { getRecommendations } from '../api';
import { useLobby } from '../lobby';
import { useDraft } from '../store';
import { ROLE_LABEL, type Recommendation } from '../types';
import { RecommendationCard } from './RecommendationCard';

// "From your pool": the same EV recommender, restricted to the champions you
// listed in your lobby pool, for your lobby role. Shown alongside global Best
// picks when you're in a premade lobby.
export function PoolPicks() {
  const { state } = useDraft();
  const { lobbyId, me } = useLobby();
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const reqId = useRef(0);

  const role = me.role ?? state.pickingForRole;
  const poolKey = me.pool.join(',');

  useEffect(() => {
    if (!lobbyId || me.pool.length === 0) {
      setRecs([]);
      return;
    }
    const id = ++reqId.current;
    setLoading(true);
    const t = setTimeout(() => {
      getRecommendations({ ...state, pickingForRole: role, poolFilter: me.pool })
        .then((r) => {
          if (id === reqId.current) {
            setRecs(r);
            setLoading(false);
          }
        })
        .catch(() => {
          if (id === reqId.current) setLoading(false);
        });
    }, 180);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lobbyId, poolKey, role, state]);

  if (!lobbyId) return null;

  return (
    <aside className="recos">
      <div className="recos__head">
        <h2>
          From your pool · <span className="recos__role">{ROLE_LABEL[role]}</span>
        </h2>
        {loading && <span className="recos__spinner" aria-label="Updating" />}
      </div>
      {me.pool.length === 0 ? (
        <div className="recos__empty">Set your champion pool (above) to see your best picks.</div>
      ) : recs.length === 0 && !loading ? (
        <div className="recos__empty">No champions from your pool play {ROLE_LABEL[role]}.</div>
      ) : (
        <ol className="recos__list">
          {recs.map((rec, i) => (
            <RecommendationCard key={rec.championId} rank={i + 1} rec={rec} />
          ))}
        </ol>
      )}
    </aside>
  );
}
