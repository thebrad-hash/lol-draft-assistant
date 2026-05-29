import { useState } from 'react';
import { LobbyProvider, useLobby } from './lobby';
import { BansBar } from './components/BansBar';
import { ChampionPicker } from './components/ChampionPicker';
import { DraftGrid } from './components/DraftGrid';
import { LobbyBar } from './components/LobbyBar';
import { LobbyPanel } from './components/LobbyPanel';
import { PickOrder } from './components/PickOrder';
import { PoolPicks } from './components/PoolPicks';
import { RecommendationsPanel } from './components/RecommendationsPanel';
import { TeamAnalysis } from './components/TeamAnalysis';
import { WeightsDrawer } from './components/WeightsDrawer';
import { DraftProvider, useDraft, type Side } from './store';

type PickerTarget = { type: 'ban' } | { type: 'add'; side: Side } | { type: 'pool' } | null;

function LiveControl() {
  const { live, setLive, liveStatus } = useDraft();
  let dotClass = 'live-dot';
  let label = '';
  if (live) {
    if (liveStatus.inChampSelect) {
      dotClass += ' is-on';
      label = liveStatus.demo ? 'demo' : 'synced';
    } else if (liveStatus.connected) {
      dotClass += ' is-wait';
      label = 'waiting…';
    } else {
      dotClass += ' is-err';
      label = liveStatus.reason?.toLowerCase().includes('unreachable') ? 'API down' : 'no client';
    }
  }
  return (
    <button
      className={'btn btn--ghost live-toggle' + (live ? ' is-live' : '')}
      onClick={() => setLive(!live)}
      title="Sync the draft from your live League champ select (reads the local client, read-only)"
    >
      <span className={dotClass} />
      {live ? `Live · ${label}` : 'Go Live'}
    </button>
  );
}

function Shell() {
  const { state, toggleBan, addPick, reset } = useDraft();
  const { me, togglePool } = useLobby();
  const [picker, setPicker] = useState<PickerTarget>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const pickedIds = [
    ...Object.values(state.myTeam),
    ...Object.values(state.enemyTeam),
  ].filter(Boolean) as string[];

  function renderPicker() {
    if (!picker) return null;

    if (picker.type === 'ban') {
      return (
        <ChampionPicker
          title="Ban a champion"
          roleFilter={null}
          unavailable={new Set(pickedIds)}
          selected={new Set(state.bans)}
          onSelect={(id) => {
            toggleBan(id);
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      );
    }

    if (picker.type === 'pool') {
      return (
        <ChampionPicker
          title="Your champion pool"
          roleFilter={me.role}
          unavailable={new Set()}
          selected={new Set(me.pool)}
          selectedLabel="in pool"
          onSelect={(id) => togglePool(id)} // multi-select: stays open
          onClose={() => setPicker(null)}
        />
      );
    }

    const { side } = picker;
    return (
      <ChampionPicker
        title={`${side === 'my' ? 'My team' : 'Enemy team'} · add champion`}
        roleFilter={null}
        unavailable={new Set([...state.bans, ...pickedIds])}
        onSelect={(id) => {
          addPick(side, id);
          setPicker(null);
        }}
        onClose={() => setPicker(null)}
      />
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark">⌖</span>
          <div>
            <div className="brand__title">LoL Draft Assistant</div>
            <div className="brand__sub">EV pick recommendations · machineloling z-scores</div>
          </div>
        </div>
        <div className="topbar__actions">
          <LobbyBar />
          <LiveControl />
          <button className="btn btn--ghost" onClick={reset}>
            Reset draft
          </button>
          <button className="btn" onClick={() => setDrawerOpen(true)}>
            ⚙ Weights
          </button>
        </div>
      </header>

      <BansBar onOpenBanPicker={() => setPicker({ type: 'ban' })} />

      <LobbyPanel onEditPool={() => setPicker({ type: 'pool' })} />

      <main className="main">
        <DraftGrid onAdd={(side) => setPicker({ type: 'add', side })} />
        <div className="rightcol">
          <PickOrder />
          <TeamAnalysis />
          <div className="recos-row">
            <RecommendationsPanel />
            <PoolPicks />
          </div>
        </div>
      </main>

      <WeightsDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      {renderPicker()}
    </div>
  );
}

export default function App() {
  return (
    <LobbyProvider>
      <DraftProvider>
        <Shell />
      </DraftProvider>
    </LobbyProvider>
  );
}
