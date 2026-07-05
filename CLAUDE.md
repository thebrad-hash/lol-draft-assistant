# CLAUDE.md

Guidance for working in **lol-draft-assistant** — an EV-based League of Legends
draft pick recommender.

## What this is

Given a (partial) champ-select state and the role you're picking for, the engine
scores every available candidate by combining four weighted z-score components
and ranks them. The z-data is decoded directly from
[machineloling](https://pooldesigner.machineloling.com)'s static files (validated
cell-for-cell against the site's own WASM engine). It's exposed three ways:

- a **CLI** (`python -m lol_draft.cli`),
- a **FastAPI** server (`python -m lol_draft.server`) consumed by
- a **React + Vite** web UI, which can optionally pull **live champ-select state**
  from a running League client (read-only).

## Scoring model (the heart of it)

```
Total = w_in·InLane + w_out·OutOfLane + w_syn·Synergy + w_blind·Blind
```

- **InLane** — candidate's matchup z vs the direct-lane enemy (same-role block).
- **OutOfLane** — aggregate of candidate's matchup z vs every *other* enemy.
- **Synergy** — aggregate of candidate's synergy z with every known ally.
- **Blind** — oracle-derived blind-pick field-safety, standardized across the role
  so it sits on the same unit-z scale as the others.

Aggregation across multiple opponents/allies is `mean` (default), `topn`, or `sum`.
Defaults for weights/agg/rank live in [`lol_draft/config.py`](lol_draft/config.py)
and are persisted (and editable) in the SQLite store.

**Invariant — partial drafts never penalize:** unknown slots are simply omitted.
A component with no data has `value=None` and contributes 0; every candidate is
judged against the same known state, so comparisons stay fair. Preserve this when
touching [`lol_draft/scoring.py`](lol_draft/scoring.py).

## Repo layout

```
lol_draft/            Python package (the engine)
  config.py           defaults, data source URL, roles/ranks, paths
  fetch.py            download + cache machineloling static files -> data/raw/
  decode.py           decode matrices.bin (f16) into matchup/synergy z-scores
  store.py            build + read the SQLite store (build_db, class Store)
  scoring.py          EV engine: DraftState -> ranked CandidateScore list
  cli.py              CLI: build / info / weights / recommend
  server.py           FastAPI wrapper (mirrors the web contract)
  lcu.py              live champ-select reader via the League client (LCU) API
tools/                engine.wasm + engine_glue.mjs (the site's oracle)
  gen_blindability.mjs  WASM-derived blindability -> data/raw/blindability.json
web/                  React 18 + Vite + TS frontend
  src/api.ts          THE SWAP POINT (re-exports getRecommendations)
  src/realApi.ts      real client: POST /api/recommend, mock fallback if down
  src/mock/           offline mock engine + generated champions.ts
  scripts/gen_champions.mjs  regenerate src/mock/champions.ts from the roster
data/
  raw/                source data (matrices.bin, index.json, champions.json,
                      blindability.json) + .meta.json fetch sidecars
  snapshots/          patch-stamped archives of raw/ (one dir per fetched
                      snapshot + manifest.json; committed — NOT rebuildable)
  reports/            snapshot-audit + benchmark output (regenerable; gitignored)
  benchmarks/         fixed pick-interval benchmark states (committed)
  draft.db            built SQLite store (rebuildable; gitignored)
tests/smoke_test.py   end-to-end parity + scoring smoke test (no pytest)
draft.bat / serve.bat Windows launchers (see Environment caveat below)
```

## Data pipeline

```
fetch.py ──> data/raw/*.bin,*.json ──> decode.py ──> store.py (build_db)
                                                          │
                                                     data/draft.db
                                                          │
                                      scoring.py ◀─ Store (read) ─▶ cli.py
                                          │
                                      server.py (FastAPI) ◀── web/ (Vite, proxied)
```

- **matrices.bin** is half-precision (f16), planar, header-at-end per block; the
  scoring z is *derived* (`z = (pp − colMean)/colStd`, population stats per column),
  not stored. The binary layout is documented at the top of
  [`decode.py`](lol_draft/decode.py).
- **blindability** is the one metric that can't be reproduced by direct decode — it
  comes from the site's WASM engine via `node tools/gen_blindability.mjs`. If
  `data/raw/blindability.json` is absent, the Blind component is omitted (with a
  warning) rather than failing.
- Static data is fetched once and cached; `--force` re-downloads.

## Common commands

Run module-form commands from the **repo root** with a Python that has `numpy`
(plus `fastapi`+`uvicorn` for the server — see [requirements.txt](requirements.txt)):

```bash
# Build / inspect the store
python -m lol_draft.cli build            # download (if needed) + (re)build data/draft.db
python -m lol_draft.cli build --force    # also re-download source data
python -m lol_draft.cli info             # provenance, weights, settings

# Snapshot archive + stationarity audit (WS1). Every build archives the raw
# source files under data/snapshots/<patch>_<date>/ (patch = Riot's live patch
# at fetch time; content-hashed, so unchanged data is skipped). The audit
# compares two snapshots' matchup/synergy z matrices — it WARNS (correlation
# < 0.90, top per-champion movers), it never blocks anything.
python -m lol_draft.cli snapshot-audit             # newest vs previous snapshot
python -m lol_draft.cli snapshot-audit --a unknown_2026-05-29 --b 16.14_2026-07-10
python -m lol_draft.snapshot --selftest            # offline synthetic-fixture test

# Own-data champ_strength (WS2; currently NOT promoted — gate failed, see
# STATUS.md). Builds the per-champion, per-patch EB random-walk posterior from
# the collected games; train/bootstrap consume it only with --champ-strength
# own_data, and scoring paths follow the MODEL's meta (champ_strength_source),
# so nothing changes at serve time unless an own-data model is promoted.
python -m lol_draft.champstats build               # -> data/models/champ_strength.json
python -m lol_draft.champstats --selftest
python -m lol_draft.train --patch all --champ-strength own_data --out <candidate.json>
python -m lol_draft.bootstrap --patch all --champ-strength own_data --out <candidate_bootstrap.json>

# z-cell noise (WS3). Fits the global constant c for the matchup/synergy
# z-cell uncertainty (EB posterior: v = s²/(s²+1), s² = c/N̂) and writes
# data/models/zcell_noise.json — the serve path samples z cells in the
# uncertainty ensemble ONLY while that artifact exists (env overrides:
# WINPROB_ZCELL=off, WINPROB_ZCELL_C=<float>). With >=2 archived snapshots the
# fit is snapshot-calibrated; otherwise a documented heuristic fallback.
python -m lol_draft.cellnoise fit                  # calibrate + persist c
python -m lol_draft.cellnoise --selftest
python -m lol_draft.benchmark                      # interval width + tie-rate report,
python -m lol_draft.benchmark regen                #   OFF vs ON (fixed 50-state file)

# Recommend (role is required; -e enemy, -a ally, -b ban; all repeatable)
python -m lol_draft.cli recommend -r TOP -e MID=Ahri -e JUNGLE=LeeSin \
    -a ADC=Jinx -a SUP=Thresh -b Darius --explain

# Evaluate a full 5v5 — team scores + win conditions (-A team A, -B team B)
python -m lol_draft.cli evaluate \
    -A TOP=Aatrox -A JUNGLE=LeeSin -A MID=Ahri -A ADC=Jinx -A SUP=Thresh \
    -B TOP=Darius -B JUNGLE=Sejuani -B MID=Zed -B ADC=Caitlyn -B SUP=Lulu

# Persist weights / settings
python -m lol_draft.cli weights --set in_lane=0.8 --agg topn --top-n 3

# API for the web UI (serves http://127.0.0.1:8000)
python -m lol_draft.server

# Web UI (separate terminal)
cd web && npm install && npm run dev     # http://127.0.0.1:5173, proxies /api -> :8000
cd web && npm run build                  # tsc + vite build
cd web && npm run gen:champions          # regenerate src/mock/champions.ts

# Regenerate blindability (needs Node; reads data/raw/)
node tools/gen_blindability.mjs

# Premade lobby over the internet: build, serve single-port, tunnel
cd web && npm run build           # -> web/dist (server.py then serves it on :8000)
cloudflared tunnel --url http://localhost:8000   # public link; share <url>/?lobby=<id>

# Smoke test (parity vs WASM oracle + scoring sanity; run after build)
python tests/smoke_test.py
```

## Environment / interpreter

`draft.bat` and `serve.bat` invoke the engine via the Windows **`py -3`** launcher,
which finds the real Python install and skips the Microsoft Store stub. Whatever
`py -3` resolves to must have `numpy` (plus `fastapi`+`uvicorn` for `serve.bat`) —
check the available versions with `py -0p`. The module-form commands above work
with any `python` on PATH too.

Any Python 3.11+ with `numpy` works for the engine; the CLI does **not** need
`fastapi`/`uvicorn` (only the server does).

> The launchers previously hardcoded a `…\Python312\python.exe` path, which broke
> on machines without that exact install; `py -3` is the portable replacement.

## Conventions & gotchas

- **Two role vocabularies.** The engine uses `TOP/JUNGLE/MID/ADC/SUP`; the web UI
  uses `TOP/JUNGLE/MID/BOT/SUPPORT`. [`server.py`](lol_draft/server.py) and
  [`lcu.py`](lol_draft/lcu.py) translate `BOT↔ADC`, `SUPPORT↔SUP` at the boundary.
  Weights are camelCase on the web (`inLane`), snake_case in the engine (`in_lane`);
  the server maps these too. Keep all mapping at the boundary — don't leak either
  vocabulary across it.
- **The web "swap point"** is [`web/src/api.ts`](web/src/api.ts): the whole UI
  imports `getRecommendations` from there. It currently re-exports the real client
  ([`realApi.ts`](web/src/realApi.ts)), which falls back to the offline mock if the
  API is unreachable. To force the mock, switch the export to `./mock/api`.
- **Team evaluation** ([`evaluate.py`](lol_draft/evaluate.py) → `POST /api/evaluate`
  → [`TeamAnalysis.tsx`](web/src/components/TeamAnalysis.tsx)) scores a full 5v5 once
  both teams are locked, with win conditions. It reuses the matchup Δpp + synergy z;
  since the data has **no game-timing field**, "win early vs late" is derived as
  lane-matchup edge (laning) vs synergy edge (teamfights). The favorability % is a
  heuristic blend (constants at the top of `evaluate.py`); raw components (lane pp,
  synergy z) are returned unmodified.
- **The store's sqlite connection is shared and access is serialized.** `Store` opens
  with `check_same_thread=False`, and the server holds `_store_lock` around every
  query — concurrent requests (`/api/recommend` + `/api/evaluate` fire on the same
  draft change) would otherwise corrupt the shared cursor and 500. Any new
  store-touching endpoint must take `_store_lock` too.
- **Premade lobby** is in-memory on the server (`_lobbies`, guarded by `_lobby_lock`)
  with REST create/join/update/leave under `/api/lobby*`; clients poll every 2s
  ([`lobby.tsx`](web/src/lobby.tsx) → [`LobbyPanel`](web/src/components/LobbyPanel.tsx)).
  The share link is `origin + ?lobby=<id>`, so it auto-uses whatever URL the app is
  served from — open the app via the tunnel/LAN URL (not localhost) for the link to
  work for friends. Lobbies are lost on server restart; there's no auth (the random
  link is the only gate). `server.py` also mounts `web/dist` at `/`, so one port — and
  thus one `cloudflared` tunnel — serves the whole app.
- **"From your pool"** ([`PoolPicks`](web/src/components/PoolPicks.tsx)) reuses the
  recommender with `poolFilter` set to your lobby pool — same EV engine, your champs only.
- **Pick-order** (`POST /api/pick-order` → [`PickOrder`](web/src/components/PickOrder.tsx))
  ranks your OPEN roles by the EV of each one's best still-available champion (runs
  `score_draft` once per open role under `_store_lock`) and badges the top as "pick
  next". Re-fetched on every draft change; hidden when fewer than two roles are open.
- **`web/src/mock/champions.ts` is generated but committed** (see `.gitignore`).
  Regenerate with `npm run gen:champions` after the roster changes; don't hand-edit.
- **`data/draft.db` is rebuildable and gitignored.** Don't commit it. The source of
  truth is `data/raw/` + the decode/build code.
- **Live sync is strictly read-only.** `lcu.py` polls the local League client's LCU
  API (`/lol-champ-select/v1/session`) and never automates in-client actions. It
  degrades gracefully (returns a status flag) when the client isn't running. Use
  `GET /api/live?demo=1` for a synthetic payload to exercise the feature offline.
- **Live picks are role-inferred when the client hides positions.** The LCU never
  exposes the *enemy* team's `assignedPosition` (and blind/quickplay/practice assign
  none for anyone). `session_to_draft` therefore infers a role for any positioned-less
  pick from the champion's most-played role (`server._ui_champ_roles` → passed into
  `lcu.live_draft`), falling back to any open lane, so known picks are never dropped.
  A real draft has 10 bans (5/team), so the bans UI holds 10 (`MAX_BANS`).
- **Data parity is the correctness bar.** `decode.py` is validated cell-for-cell
  against the WASM oracle (errors < 0.02 = f16 rounding). `tests/smoke_test.py`
  pins specific oracle cells — if you change decoding, those must still pass.
- **No external deps for champion names.** Display names are derived locally
  (de-camel + a small special-case table in `gen_champions.mjs`); the LCU path uses
  the client's own `champion-summary.json`. No Data Dragon calls.

## Current state (as of this writing)

Setup is already done in this checkout: `web/node_modules` is installed,
`data/raw/*` is present, and `data/draft.db` is built. The `.bat` launchers now use
`py -3`. Verified end-to-end: `tests/smoke_test.py` passes (oracle parity + scoring),
the CLI and FastAPI server return identical rankings, and the web UI renders live
recommendations through the Vite `/api` proxy from the real engine.
