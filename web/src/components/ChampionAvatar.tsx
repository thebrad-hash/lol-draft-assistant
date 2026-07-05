import { useEffect, useState } from 'react';

// Real champion icons from Riot's Data Dragon CDN. The latest patch version is
// fetched once (cached) so newly-released champions resolve; if the network/CDN
// is unavailable the avatar falls back to a colored initials badge (onError).
const FALLBACK_VERSION = '15.10.1';
let versionPromise: Promise<string> | null = null;

function ddragonVersion(): Promise<string> {
  if (!versionPromise) {
    versionPromise = fetch('https://ddragon.leagueoflegends.com/api/versions.json')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('versions'))))
      .then((vs: string[]) => vs[0] || FALLBACK_VERSION)
      .catch(() => FALLBACK_VERSION);
  }
  return versionPromise;
}

// machineloling roster id -> Data Dragon champion key, for the few that differ.
// (Most ids already match; unknown/missing ones just fall back to initials.)
const DDRAGON_ID: Record<string, string> = {
  Reksai: 'RekSai',
  RenataGlasc: 'Renata',
  FiddleSticks: 'Fiddlesticks',
  Kogmaw: 'KogMaw',
};

export function championIconUrl(id: string, version: string): string {
  const key = DDRAGON_ID[id] ?? id;
  return `https://ddragon.leagueoflegends.com/cdn/${version}/img/champion/${key}.png`;
}

// Splash / loading art are NOT version-pinned (they live under /cdn/img/, not
// /cdn/{version}/img/). Loading art is portrait-cropped — ideal for the champ-
// select portrait frame; splash is the wide cinematic, good for hero backdrops.
export function championLoadingUrl(id: string): string {
  const key = DDRAGON_ID[id] ?? id;
  return `https://ddragon.leagueoflegends.com/cdn/img/champion/loading/${key}_0.jpg`;
}
export function championSplashUrl(id: string): string {
  const key = DDRAGON_ID[id] ?? id;
  return `https://ddragon.leagueoflegends.com/cdn/img/champion/splash/${key}_0.jpg`;
}

function hashHue(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h % 360;
}

function initials(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 ]/g, '').split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

interface Props {
  id: string;
  name: string;
  size?: number;
  dimmed?: boolean;
}

export function ChampionAvatar({ id, name, size = 36, dimmed = false }: Props) {
  const [version, setVersion] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    let active = true;
    ddragonVersion().then((v) => {
      if (active) setVersion(v);
    });
    return () => {
      active = false;
    };
  }, []);

  const url = version ? championIconUrl(id, version) : undefined;
  const hue = hashHue(id);
  const style: React.CSSProperties = {
    width: size,
    height: size,
    fontSize: size * 0.36,
    background: `linear-gradient(150deg, hsl(${hue} 42% 32%), hsl(${(hue + 40) % 360} 38% 22%))`,
    opacity: dimmed ? 0.4 : 1,
  };
  return (
    <span className="avatar" style={style} title={name} aria-label={name}>
      {url && !broken ? (
        <img src={url} alt={name} loading="lazy" onError={() => setBroken(true)} />
      ) : (
        <span className="avatar__initials">{initials(name)}</span>
      )}
    </span>
  );
}
