// Deep link to a champion's WPA build page on coachless.gg.
//
// We only LINK to coachless's own public per-champion page — we never scrape,
// copy, or store their data (their Terms prohibit that). Each user opens the
// link under their own login. URL scheme (verified): coachless.gg/builds/<slug>
// where slug is the champion's Riot key, lowercased with separators stripped
// (e.g. LeeSin -> leesin, Aatrox -> aatrox).
const SLUG_OVERRIDE: Record<string, string> = {
  // our roster ids that don't lowercase cleanly to coachless's Riot-key slug
  RenataGlasc: 'renata',
};

export function coachlessBuildUrl(championId: string): string {
  const key = SLUG_OVERRIDE[championId] ?? championId;
  const slug = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  return `https://coachless.gg/builds/${slug}`;
}
