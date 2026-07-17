import { useOnboarding } from './Onboarding';

// Collapsible "How to use this" card for the Premade Lobby dead-space.
// Expanded on first run; collapse state persists via useOnboarding. Copy stays
// honest about the small draft-only edge — see STATUS.md before changing it.
export function GuidePanel() {
  const { guideCollapsed, setGuideCollapsed } = useOnboarding();
  const open = !guideCollapsed;

  return (
    <section className={'guide' + (open ? ' guide--open' : '')}>
      <button
        className="guide__head"
        aria-expanded={open}
        aria-controls="guide-body"
        onClick={() => setGuideCollapsed(open)}
      >
        <span className="guide__title">How to use this</span>
        <span className="guide__chev" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
      </button>

      {open && (
        <div className="guide__body" id="guide-body">
          <p className="guide__lede">
            BradDraft helps your 5-stack draft the highest-EV team during champ select — and
            spot high-impact off-meta picks your opponents won't expect. Everyone joins the
            lobby, sets their role + champion pool, and the board coordinates who should pick
            what.
          </p>

          <div className="guide__rule hex-rule">
            <span className="hex-rule__gem" />
          </div>
          <h4 className="guide__sub">Premade flow</h4>
          <ol className="guide__steps">
            <li>
              Anyone creates a lobby and shares the link — teammates open it (website is fine)
              and set a username, role, and champion pool.
            </li>
            <li>
              <strong>Whoever is in champ select</strong> runs the desktop app (BRADDRAFT.exe)
              with that lobby open and leaves <strong>Go Live</strong> on. It reads their
              League client and broadcasts the draft so everyone else auto-fills — no designated
              host, and Brad does not need to be online.
            </li>
            <li>
              Read “who picks next” and the per-role board — your teammates' pool champs show
              above the global best, so you can swap roles or picks to maximize the team's
              odds.
            </li>
            <li>
              Lock in the picks with the best win probability — and watch for “≈ tied” picks,
              where comfort should decide.
            </li>
          </ol>

          <div className="guide__rule hex-rule">
            <span className="hex-rule__gem" />
          </div>
          <h4 className="guide__sub">How the numbers work</h4>
          <ul className="guide__notes">
            <li>
              Picks are ranked by a calibrated win-probability model (logistic regression on
              Riot Match-V5 games), not raw z-scores. It uses four team-level signals: lane
              matchups, counter matchups, synergy, and champion strength.
            </li>
            <li>
              Every number comes with honest error bars — a 90% confidence interval from a
              1000-sample bootstrap. The draft-only edge is genuinely small, and the tool says
              so instead of faking precision.
            </li>
            <li>
              When two picks are statistically indistinguishable they're flagged “too close to
              call — pick on comfort” rather than inventing a winner.
            </li>
            <li>
              Validated out-of-sample (held-out games), so the ranking reflects signal, not
              memorized noise.
            </li>
          </ul>
          <p className="guide__disclaimer">
            Win rates reflect aggregate soloqueue tendencies — in a premade, comfort and
            communication still matter.
          </p>
        </div>
      )}
    </section>
  );
}
