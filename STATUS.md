# Project status — BradDraft (LoL draft optimizer)

A snapshot of where the app is, to bring a person (or an AI assistant) up to speed.
For build/run commands and deeper conventions see [CLAUDE.md](CLAUDE.md).

_Last updated: 2026-07-05 · branch `draft-assistant-features` (work below is **not yet
committed**)._ The in-app brand was renamed **“LoL Draft Assistant” → “BradDraft”**.

## What it is

A League of Legends draft optimizer aimed at **5-stack premades**. Given a draft state
(bans + both teams’ picks by role) it recommends champions ranked by **calibrated win
probability with honest error bars**, and — its differentiator — surfaces **every
teammate’s champion pool per role** so a premade can coordinate role/pick swaps at a
glance. Also: full-draft team analysis, “who picks next”, a shareable premade lobby with
**team chat**, and optional live champ-select sync.

## Stack

- **Backend:** Python + FastAPI (`lol_draft/`). One process serves the API **and** the
  built web UI on a single port (Vercel function, or local + a Cloudflare tunnel).
- **Frontend:** React 18 + Vite + TypeScript (`web/`). **Hextech “champ-select” UI** —
  see Design system below. Champion icons + splash/loading art from Riot Data Dragon.
- **Data:** machineloling static files (matchup Δpp + synergy z, per-rank playrates incl.
  `games`, blindability) decoded into `data/draft.db` (SQLite, rebuildable). Win-prob model
  + bootstrap ensemble live in `data/models/*.json` (committed so Vercel ships them).

## The ranking core (calibrated win prob + uncertainty)

This is the analytical heart and what differentiates the numbers from a raw z-score tool.

1. **Calibrated logistic regression** (`train.py` → `model.py`/`winprob.py`). Four
   team-level features (lane_z, counter_z, synergy_z, champ_strength) → `P(win)`. Fit on
   Riot Match-V5 games, validated **out-of-sample** with GroupKFold on `matchId` (a game’s
   two anti-correlated rows never split). Held-out AUC ≈ **0.569**, log-loss 0.6863 (beats
   additive-z 0.6893 and the 0.6931 null); well-calibrated (ECE ≈ 0.007). The draft-only
   edge is genuinely small — by design the tool says so rather than faking precision.
2. **Bootstrap uncertainty** (`bootstrap.py` → `WinProbEnsemble` in `model.py`). Resamples
   whole **games** with replacement (1000×, fixed seed), refits, stores the coefficient
   ensemble (`data/models/winprob_bootstrap.json`). Every pick reports **median, 90%
   interval, sd**. **Distinguishability:** for two picks it reports the paired fraction of
   resamples where A>B; below a tunable bar (default 0.85, env `WINPROB_TIE_THRESHOLD`) it’s
   flagged **“too close to call — pick on comfort.”** 1000 refits ≈ 6s, done offline.
3. **Feature-value uncertainty.** champ_strength’s `win_rate` is a binomial proportion with
   known `games`, so each member is resampled from `√(wr(1-wr)/games)` (champion-consistent,
   so shared champs cancel in head-to-heads). This roughly **doubles** the honest interval.
4. **z-cell uncertainty (WS3).** The matchup/synergy z cells feeding lane_z / counter_z /
   synergy_z are perturbed too, via an EB normal-normal posterior: published cells are
   already shrunk, so the residual variance is `v = s²/(s²+1)` (never `z ± SE` — that
   double-counts on well-sampled cells), with `s² = c/N̂` and `N̂ ≈ G·PR_A·PR_B` a
   playrate-based per-cell sample-size proxy (machineloling publishes no per-cell N — this
   is a documented heuristic). Draws are keyed per (cell, replicate) so cells shared by two
   candidates cancel in head-to-heads, same discipline as champ_strength. The constant `c`
   lives in `data/models/zcell_noise.json` (`python -m lol_draft.cellnoise fit`:
   snapshot-calibrated when ≥2 archives with DIFFERENT matrices exist, heuristic fallback
   otherwise); sampling is on only while that artifact exists (env: `WINPROB_ZCELL=off`,
   `WINPROB_ZCELL_C`). Effect on the fixed 50-state benchmark
   (`python -m lol_draft.benchmark`, fallback c=17.96): median top-5 interval width
   0.040 → 0.053, p90 0.059 → 0.084; adjacent top-5 pairs "too close to call" at the 0.85
   bar 74% → 88%; candidates tied WITH THE TOP PICK (the UI badge) 24% → 40%. Wider + more
   ties is intended honesty; the 0.85 threshold was kept — at 40% tied-with-top the badge
   still separates genuine alternatives from measurably-worse picks.
4. **Additive-z EV** (`scoring.py`) remains the fallback when the model isn’t loaded; the
   context-adaptive (“auto”) weights only matter in that mode.

## Backend modules (`lol_draft/`)

| Module | Role |
| --- | --- |
| `train.py`, `model.py`, `winprob.py`, `features.py`, `collect.py` | calibrated win-prob model + **bootstrap ensemble**; `rank_candidates_uncertain()` returns median/interval/sd + tie flags |
| `bootstrap.py` | offline: build the 1000-refit coefficient ensemble (game-level resample) |
| `scoring.py` | additive-z EV engine (fallback) |
| `weights.py` | context-adaptive (“auto”) weights |
| `evaluate.py` | full 5v5 team score + win conditions; off-role lanes with no data show as **“no data”** (not dropped) |
| `lcu.py` | live champ-select read from the local League client (role inference for hidden enemy positions; 10 bans) |
| `lobby_store.py` | premade lobby + **chat** (in-memory locally; Upstash Redis on serverless via `KV_REST_API_*`/`UPSTASH_*`) |
| `store.py`, `decode.py`, `fetch.py`, `config.py`, `cli.py` | data store + decode + fetch + config + CLI |
| `server.py` | all HTTP endpoints + serves `web/dist` |

## API endpoints

`/api/health` · `/api/live` (LCU; `?demo=1`; returns `isLocal` — host vs remote friend) ·
`/api/recommend` (returns win-prob median + interval + sd + tie flags + contributions) ·
`/api/weights` · `/api/evaluate` (team analysis) · `/api/pick-order` · `/api/board` (top
picks for ALL roles) · `/api/lobby*` (create / get / member / leave / **chat** /
**live** — host broadcasts its champ-select draft to the premade).

## Frontend / UI

**Design system** (`web/src/styles/`): `tokens.css` (Riot hextech palette as semantic
vars, Cinzel display + Barlow body fonts, `.hex-panel`/`.hex-rule` motifs) → `index.css`
(legacy vars remapped onto tokens) → `champ-select.css` (the skin) →
`recommendation-card.css`. **Layout** is a 3-region champ-select stage: **ally rail · center
stage · enemy rail**, bans centered up top, “not affiliated with Riot Games” footer.

Components (`web/src/components`):
- **AllRolesBoard** — best picks per role. In a lobby, each role shows **teammates’ pools
  ABOVE** the global list (their best pool champ, ranked), then an “all champions” divider.
  Click a pick to expand its advantages/disadvantages + the 90% CI line; ties badged
  “≈ tied”.
- **Focus / prioritize** — click any *already-picked* ally/enemy to toggle focus; the board
  re-ranks every role to favor counters to focused enemies / synergy with focused allies
  (computed from each pick’s own contribution data — no extra API calls), with a `⚔ +z`
  badge and a header note.
- **PickOrder** (“who picks next”) — in a premade, ranks open roles by each role’s
  **member-pool** best pick (falls back to global where no pool).
- **TeamAnalysis** — locked 5v5; verdict bar, lane-by-lane (off-role pairings → “no data”).
- **RecommendationCard** (“From your pool”) — champ-portrait slab: splash art, win% hero,
  hextech CI bar, expand-to-reveal breakdown.
- **LobbyPanel** — roster (role + pool) on the left, **Team Chat** on the right; shareable
  `?lobby=<id>` link. Plus DraftGrid (rails), BansBar, ChampionPicker, WeightsDrawer, etc.
- State: `store.tsx` (`DraftProvider`, incl. `focus`) + `lobby.tsx` (`LobbyProvider`, incl.
  `messages`/`sendMessage`). Shared pool-rec cache in `poolRecs.ts`.

## Key behaviors / gotchas

- **When the win-prob model is loaded it owns the ranking**; additive-z weights/auto are
  fallback-only.
- **Two role vocabularies:** engine `TOP/JUNGLE/MID/ADC/SUP` vs UI `…/BOT/SUPPORT`, mapped
  at the API boundary (weights camelCase UI / snake_case engine).
- **Shared SQLite is serialized by `_store_lock`** — any store-touching endpoint must take it.
- **`/api/live` is served from a background-thread cache** — the (blocking) local-client read
  runs off the request path, so a premade all polling “Go Live” can’t wedge the server. The
  static champ→roles map is cached too.
- **Live sync: host reads LCU, premade follows.** Only the host can read a local League
  client (the server runs on their machine; friends reach it through the tunnel). So the
  host **broadcasts** its champ-select draft to the lobby (`PUT /api/lobby/{id}/live`, no
  per-player role) and every member **follows** it from the lobby poll, overlaying their
  OWN role (their lobby role chip) as `pickingForRole`. Host vs friend is auto-detected per
  request (`/api/live` → `isLocal`: loopback + no proxy headers = host); a `?live=host|follow`
  URL param overrides it. Go Live auto-enables on lobby join. The broadcast goes stale after
  `LIVE_STALE` (12s) if the host stops publishing (left select / closed tab) — followed picks
  persist, but “following” reverts to “waiting for host”. Friends still get manual entry too.
- **Coachless:** link-out only (never scrape).
- **Fonts** load from Google Fonts; the offline desktop build falls back to serif/sans
  (self-host woff2 before shipping that build).

## Data freshness (interactions vs strength)

machineloling's matchup/synergy deltas are **interaction terms** — champion main effects
removed, shrunk toward the mean — and their authors report they are stable across patches;
champion **strength** is the genuinely non-stationary part. The architecture follows that
split, and verifies rather than trusts it:

- **Pool interactions across patches.** The z cells are used as-is from the latest fetch;
  no per-patch alignment. Every fetch is archived patch-stamped (Riot's live patch at fetch
  time) under `data/snapshots/`, and `python -m lol_draft.cli snapshot-audit` compares
  consecutive snapshots — per role-pair z correlations plus per-champion break scores (a
  rework detector). It **alerts, never blocks**: correlation < 0.90 warns, top movers are
  always listed. The cross-snapshot squared differences it stores are also what calibrates
  the WS3 cell-noise constant once two snapshots with different matrices exist.
  First empirical data point (2026-07-05): the May and July fetches carry **byte-identical
  matrices.bin** while champions.json (playrates/winrates) changed — machineloling itself
  treats the interactions as slow-moving and the strength data as fast-moving, which is the
  strongest possible endorsement of this exact split.
- **Patch-align strength.** The one patch-sensitive feature, `champ_strength`, has an
  own-data replacement: per-champion, per-patch win rates from our Riot collection under a
  Beta-Binomial random-walk prior (`python -m lol_draft.champstats build`), leave-one-match-
  out for training rows, latest posterior for serving. **Currently NOT promoted** — at 10k
  collected matches the gate failed (held-out log-loss +0.0020 vs a +0.0005 tolerance;
  calibration improved, ECE 0.0087 → 0.0027, but the coefficient collapsed: external-data
  bias < own-data variance at this collection size). The machinery ships default-off; the
  feature source is coupled to the model artifact's meta (`champ_strength_source`), so
  serve and train can never disagree. Revisit at ~40–80k matches or a hybrid
  machineloling-anchored prior (separately gated).
- **Audit cadence:** re-fetch (`build --force`) archives a new snapshot; run
  `snapshot-audit` after each. Only fetch fresh data deliberately — the committed model was
  trained against the archived data, and a re-fetch changes `draft.db` under it.

## Deployment & sharing

- **Vercel** (single function serves UI + API) + **Upstash Redis** for lobby/chat persistence
  (set the `UPSTASH_*`/`KV_REST_API_*` env vars). `web/dist` is committed.
- **Share with friends right now:** `npm run build` → `python -m lol_draft.server` (serves
  UI + API on `:8000`) → `cloudflared tunnel --url http://localhost:8000`. The lobby/chat are
  in-memory (lost on restart); the tunnel URL is ephemeral. Friends open the link → **+
  Premade** → **Share lobby**.
- One-file Windows desktop build for friends who want local LCU live-sync.

## Known limitations / next

- All the above is **uncommitted** on `draft-assistant-features`.
- **z-cell noise constant `c` is the heuristic fallback.** Two snapshots are archived
  (May, July 16.13) but their matrices.bin are byte-identical — machineloling republishes
  playrates without regenerating the interaction matrices — so there is no cross-snapshot
  variation to calibrate from yet. `python -m lol_draft.cellnoise fit` guards this and
  keeps the fallback; re-run it when a snapshot with different matrices lands.
- Heavy concurrent drafting by a full premade serializes on `_store_lock` (bounded compute,
  not a wedge) — could cache board/recommend results if it gets slow.
- Self-host fonts for the offline desktop build.
