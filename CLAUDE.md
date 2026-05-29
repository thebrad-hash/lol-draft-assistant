# LoL Draft Assistant

EV-based League of Legends draft pick recommender built **on top of machineloling.com's
z-scores** — treat their numbers as ground truth; this is **not** a win-prediction model.
Given the current draft (bans, allies, enemies) it ranks the highest-EV champions for your
open role. **Differentiator:** scoring uses cross-role **counters** (your candidate vs *every*
enemy, weighted in-lane vs out-of-lane) **and** cross-role **synergies** (fit with every ally),
plus a blind-pick safety term.

GitHub: `github.com/thebrad-hash/lol-draft-assistant` (private). Absolute paths are
machine-specific (the repo is cloned under the user's home dir).

## Run
- The real Python is `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` — the bare `python`
  on PATH is the Microsoft Store stub. The `draft.bat` (CLI) and `serve.bat` (API) launchers
  call the real one. **`.bat` files must be CRLF + ASCII** or `cmd.exe` mis-parses them.
- Setup: `pip install -r requirements.txt` → `draft.bat build` (builds `data/draft.db` from the
  committed `data/raw/`) → `cd web && npm install && npm run dev` (UI on :5173) → `serve.bat`
  (API on :8000). Vite proxies `/api` → 127.0.0.1:8000.
- CLI example: `draft.bat recommend -r TOP -e TOP=Darius -e MID=Ahri -a SUP=Thresh --explain`
- Preview live champ-select without a game: open `http://localhost:5173/?demo`, click **Go Live**.

## Data source (machineloling) — reverse-engineered & validated
- Static files (committed in `data/raw/`): `matrices.bin`, `index.json`, `champions.json`.
  `tools/engine.wasm` is machineloling's Rust→WASM engine, used as a ground-truth **oracle**.
- **matrices.bin** per block (`index.json[mode][roleA][roleB] = {offset,rows_n,cols_n,rows[],cols[]}`)
  is **planar with the header at the END**. With r=rows_n, c=cols_n:
  `channelA [offset, +r*c*2)` = shrunk Δpp (unused); `channelB [+r*c*2, +2*r*c*2)` = **Δpp**;
  `header [+2*r*c*2, end)` = `(r+c)` f16 aggregates. All **IEEE f16 little-endian**.
  blockSz = `(r+c)*2 + r*c*4`. Cell: `pp(i,j) = f16(offset + r*c*2 + (i*c+j)*2)`.
- **z is DERIVED, not stored:** `z[i][j] = (pp[i][j] − colMean[j]) / colStd[j]`, population stats
  down each column (all rows). Matches the oracle to <0.012 (f16 rounding only). Cross-role mirror
  blocks are exact negatives; same-role diagonal is not antisymmetric (learned model).
- Data roles: `TOP JUNGLE MID ADC SUP`. `champions.json` = `by_patch[rank][ROLE]` playrates;
  ranks silver/gold/platinum/emerald/**diamond** (default)/master_plus. Matchup/synergy data is
  patch-invariant; only playrates vary by rank.
- **Blindability is NOT in matrices.bin** (a direct-decode proxy correlated only ~0.25 with the
  app). It comes from the WASM engine's blindability endpoint (`aggregate` field, ~1.0 scale,
  rank-sensitive), precomputed at build time by `tools/gen_blindability.mjs` →
  `data/raw/blindability.json`. Scoring **standardizes** it (z-score across the role) so it sits
  on the same unit scale as the other components — that rescaling is a deliberate choice.

## Scoring
`Total = 0.7·InLane + 0.5·OutOfLane + 1.0·Synergy + 0.9·Blindability` (weights editable/persisted).
InLane = z vs the direct-lane enemy; OutOfLane = aggregate z vs other enemies; Synergy = aggregate
z with allies; Blindability = standardized field-safety. Default aggregation = mean (also
topn / sum). Bans + already-picked champs are excluded; partial drafts never penalize unknown slots.

## Architecture
- **`lol_draft/`** (Python 3.12): `config`, `fetch` (download+cache), `decode` (channelB Δpp +
  column-z + playrates + blindability), `store` (SQLite `data/draft.db`:
  cells/playrates/blindability/weights/settings), `scoring` (`DraftState` → ranked
  `CandidateScore`s with per-contribution breakdown), `cli` (`build`/`recommend`/`weights`/`info`,
  fuzzy champion-name resolver), `server` (FastAPI), `lcu` (live champ select).
- **`server.py`**: `GET /api/health`, `POST /api/recommend`, `GET /api/live`. Maps UI roles
  **BOT→ADC, SUPPORT→SUP** and camelCase weights → snake_case; returns the web contract.
- **`lcu.py`**: reads the **local League client (LCU)**, **read-only** — finds port+token from the
  `LeagueClientUx` process args (or lockfile), polls `/lol-champ-select/v1/session`, maps numeric
  champion ids via the client's `champion-summary.json`. ToS: only READ; never automate in-client
  actions. Enemy picks are hidden during champ select, so live mode mainly yields your role +
  ally locks + bans.
- **`web/`** (React + TS + Vite): pick-order draft board (empty rows show no role; filled rows
  show a role-key chip — **manual picks infer the champion's most-played role; live picks use the
  real LCU position**), bans bar, recommendations panel with color-coded z breakdown, weights
  drawer + champion-pool filter, a **Go Live** toggle. `types.ts` is the data contract; `api.ts`
  is the swap point (`realApi.ts` POSTs `/api/recommend` with a mock fallback when the API is
  down); `store.tsx` holds state + debounced recompute + 1.5s live polling. `src/mock/champions.ts`
  is generated from `index.json` by `scripts/gen_champions.mjs` (roles sorted by play so
  `roles[0]` = primary). Champion icons are initials placeholders (Data Dragon hook stubbed).

## Conventions
- machineloling z-scores are the source of truth — do not build a win-predictor.
- LCU access stays read-only.
- Rebuildable artifacts (`data/draft.db`, `web/node_modules/`, `web/dist/`) are git-ignored;
  `data/raw/` + `tools/engine.wasm` are committed so the repo is self-contained.
- Don't add `*.md` documentation files unless explicitly asked.

## State
Builds clean (`tsc`), CLI / API / UI agree on the same draft, both servers run. Live sync verified
via the `?demo` path + `tests/smoke_test.py`; **not yet** tested against a real in-game champ select.
