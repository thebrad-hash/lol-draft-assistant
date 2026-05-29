import { coachlessBuildUrl } from '../coachless';

// External link to a champion's highest-WPA build + runes on coachless.gg.
// We link out (their own page, opened under the user's own login) rather than
// scrape — see coachless.ts. stopPropagation so it works inside clickable rows.
export function CoachlessLink({ championId, compact = false }: { championId: string; compact?: boolean }) {
  return (
    <a
      className={'coachless-link' + (compact ? ' coachless-link--compact' : '')}
      href={coachlessBuildUrl(championId)}
      target="_blank"
      rel="noopener noreferrer"
      title="Highest-WPA build & runes on coachless.gg (opens in a new tab)"
      onClick={(e) => e.stopPropagation()}
    >
      {compact ? 'WPA ↗' : 'WPA build ↗'}
    </a>
  );
}
