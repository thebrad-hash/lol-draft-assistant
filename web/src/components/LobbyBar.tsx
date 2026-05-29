import { useState } from 'react';
import { useLobby } from '../lobby';

// Topbar control: create a premade lobby, or (when in one) copy the share link
// and leave. The share link is the current origin + ?lobby=<id>.
export function LobbyBar() {
  const { lobbyId, connected, members, shareUrl, create, leave } = useLobby();
  const [copied, setCopied] = useState(false);

  if (!lobbyId) {
    return (
      <button
        className="btn btn--ghost"
        onClick={() => void create()}
        title="Create a shareable premade lobby"
      >
        + Premade
      </button>
    );
  }

  const copy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — the URL is still shown in the button title */
    }
  };

  return (
    <span className="lobbybar">
      <button
        className={'btn btn--ghost lobbybar__share' + (connected ? '' : ' is-stale')}
        onClick={copy}
        title={shareUrl ?? ''}
      >
        {copied ? '✓ Link copied' : connected ? `🔗 Share lobby · ${members.length}` : 'Lobby expired'}
      </button>
      <button className="btn btn--ghost" onClick={() => void leave()}>
        Leave
      </button>
    </span>
  );
}
