import { useState } from 'react';

// When a real asset pipeline lands, return the Data Dragon URL here, e.g.:
//   const V = '14.10.1';
//   return `https://ddragon.leagueoflegends.com/cdn/${V}/img/champion/${id}.png`;
// (ids already match Data Dragon keys for most champions). Returning undefined
// makes the avatar fall back to a colored initials badge.
export function championIconUrl(_id: string): string | undefined {
  return undefined;
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
  const url = championIconUrl(id);
  const [broken, setBroken] = useState(false);
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
        <img src={url} alt={name} onError={() => setBroken(true)} />
      ) : (
        <span className="avatar__initials">{initials(name)}</span>
      )}
    </span>
  );
}
