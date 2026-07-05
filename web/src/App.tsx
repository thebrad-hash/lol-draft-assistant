import { useRef, useState } from 'react';
import { LobbyProvider, useLobby } from './lobby';
import { BansBar } from './components/BansBar';
import { ChampionPicker } from './components/ChampionPicker';
import { DraftGrid } from './components/DraftGrid';
import { LobbyBar } from './components/LobbyBar';
import { LobbyPanel } from './components/LobbyPanel';
import { AllRolesBoard } from './components/AllRolesBoard';
import { DatasetToggle } from './components/DatasetToggle';
import { PickOrder } from './components/PickOrder';
import { PoolPicks } from './components/PoolPicks';
import { TeamAnalysis } from './components/TeamAnalysis';
import { WeightsDrawer } from './components/WeightsDrawer';
import { DraftProvider, useDraft, type Side } from './store';

type PickerTarget = { type: 'ban' } | { type: 'add'; side: Side } | { type: 'pool' } | null;

function LiveControl() {
  const { live, setLive, liveStatus } = useDraft();
  const friend = liveStatus.following; // we're a remote member following the host
  let dotClass = 'live-dot';
  let label = '';
  if (live) {
    if (friend) {
      // friends can't read their own client over a shared link — they follow a
      // teammate's broadcast, auto-filled with their own role.
      dotClass += liveStatus.inChampSelect ? ' is-on' : ' is-wait';
      if (liveStatus.inChampSelect) {
        label = liveStatus.sourceName ? `following ${liveStatus.sourceName}` : 'following';
      } else {
        label = 'waiting for live draft';
      }
    } else if (liveStatus.inChampSelect) {
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
  const title = friend
    ? "Following a teammate's live champ select — whoever's in the game broadcasts it and you see the draft with your own role."
    : 'Sync the draft from your live League champ select (reads the local client, read-only)';
  return (
    <button
      className={'btn btn--ghost live-toggle' + (live ? ' is-live' : '')}
      onClick={() => setLive(!live)}
      title={title}
    >
      <span className={dotClass} />
      {/* polite live region so sync-state changes (synced / waiting / following)
          are announced without stealing focus */}
      <span aria-live="polite">{live ? `Live · ${label}` : 'Go Live'}</span>
    </button>
  );
}

// Narrow-screen stage tabs: Ally / Board / Enemy, Board default. Desktop shows
// all three regions side by side and hides the tablist entirely (CSS).
const STAGE_TABS = [
  { id: 'ally', label: 'My team' },
  { id: 'board', label: 'Board' },
  { id: 'enemy', label: 'Enemy' },
] as const;
type StageTab = (typeof STAGE_TABS)[number]['id'];

function StageTabs({ tab, onChange }: { tab: StageTab; onChange: (t: StageTab) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = STAGE_TABS.findIndex((t) => t.id === tab);
    const next = (i + (e.key === 'ArrowRight' ? 1 : -1) + STAGE_TABS.length) % STAGE_TABS.length;
    onChange(STAGE_TABS[next].id);
    refs.current[next]?.focus();
  };
  return (
    <div className="stagetabs" role="tablist" aria-label="Stage view" onKeyDown={onKeyDown}>
      {STAGE_TABS.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="tab"
          id={`stagetab-${t.id}`}
          aria-selected={tab === t.id}
          aria-controls={`stagepanel-${t.id}`}
          tabIndex={tab === t.id ? 0 : -1}
          className={'stagetabs__tab' + (tab === t.id ? ' is-active' : '')}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Shell() {
  const { state, toggleBan, addPick, reset } = useDraft();
  const { me, togglePool } = useLobby();
  const [picker, setPicker] = useState<PickerTarget>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [stageTab, setStageTab] = useState<StageTab>('board');

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
            <div className="brand__title">BradDraft</div>
            <div className="brand__sub">EV pick recommendations · machineloling z-scores</div>
          </div>
        </div>
        <div className="topbar__actions">
          <DatasetToggle />
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

      {/* Narrow screens collapse the three regions into tabs (Board default). */}
      <StageTabs tab={stageTab} onChange={setStageTab} />

      {/* Three-region champ-select stage: ally rail · stage · enemy rail. */}
      <main className="champ-select" data-tab={stageTab}>
        <aside className="rail rail--ally" id="stagepanel-ally" aria-label="Your team">
          <DraftGrid side="my" onAdd={(side) => setPicker({ type: 'add', side })} />
        </aside>

        {/* Hierarchy: turn ribbon → YOUR pick (hero) → the full board → analysis. */}
        <section className="stage" id="stagepanel-board">
          <PickOrder />
          <PoolPicks />
          <AllRolesBoard />
          <TeamAnalysis />
        </section>

        <aside className="rail rail--enemy" id="stagepanel-enemy" aria-label="Enemy team">
          <DraftGrid side="enemy" onAdd={(side) => setPicker({ type: 'add', side })} />
        </aside>
      </main>

      <footer className="site-footer">
        <div className="hex-rule site-footer__rule">
          <span className="hex-rule__gem" />
        </div>
        <p className="site-footer__legal">
          <strong>BradDraft</strong> — a fan-made, non-commercial tool.
          Not affiliated with, endorsed, or sponsored by Riot Games. League of Legends
          and all associated assets are trademarks or registered trademarks of Riot Games, Inc.
        </p>
      </footer>

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
