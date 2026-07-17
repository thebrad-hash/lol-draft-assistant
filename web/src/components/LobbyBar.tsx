import { useState } from 'react';
import { useLobby } from '../lobby';

// Topbar control: create a premade lobby, join via paste, or (when in one)
// copy the share link (public website origin when desktop) and leave.
export function LobbyBar() {
  const { lobbyId, connected, members, shareUrl, create, join, leave, lobbyRemote } = useLobby();
  const [copied, setCopied] = useState(false);
  const [joining, setJoining] = useState(false);
  const [joinRaw, setJoinRaw] = useState('');
  const [joinErr, setJoinErr] = useState(false);

  if (!lobbyId) {
    if (joining) {
      return (
        <span className="lobbybar lobbybar--join">
          <input
            className="lobbybar__input"
            autoFocus
            placeholder="Paste lobby link or id"
            value={joinRaw}
            onChange={(e) => {
              setJoinRaw(e.target.value);
              setJoinErr(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const ok = join(joinRaw);
                setJoinErr(!ok);
                if (ok) {
                  setJoining(false);
                  setJoinRaw('');
                }
              }
              if (e.key === 'Escape') {
                setJoining(false);
                setJoinRaw('');
                setJoinErr(false);
              }
            }}
            aria-label="Lobby link or id"
          />
          <button
            className="btn btn--ghost"
            onClick={() => {
              const ok = join(joinRaw);
              setJoinErr(!ok);
              if (ok) {
                setJoining(false);
                setJoinRaw('');
              }
            }}
          >
            Join
          </button>
          <button
            className="btn btn--ghost"
            onClick={() => {
              setJoining(false);
              setJoinRaw('');
              setJoinErr(false);
            }}
          >
            Cancel
          </button>
          {joinErr && <span className="lobbybar__err">Invalid link</span>}
        </span>
      );
    }
    return (
      <span className="lobbybar">
        <button
          className="btn btn--ghost"
          onClick={() => void create()}
          title={
            lobbyRemote
              ? 'Create a shareable premade lobby (friends open the public site link)'
              : 'Create a shareable premade lobby'
          }
        >
          + Premade
        </button>
        <button
          className="btn btn--ghost"
          onClick={() => setJoining(true)}
          title="Join with a lobby link or id (e.g. from a teammate)"
        >
          Join
        </button>
      </span>
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
        title={
          shareUrl
            ? `${shareUrl}${lobbyRemote ? ' — friends open this on the website; you keep Go Live on desktop' : ''}`
            : ''
        }
      >
        {copied ? '✓ Link copied' : connected ? `🔗 Share lobby · ${members.length}` : 'Lobby expired'}
      </button>
      <button className="btn btn--ghost" onClick={() => void leave()}>
        Leave
      </button>
    </span>
  );
}
