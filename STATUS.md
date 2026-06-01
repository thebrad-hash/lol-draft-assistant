# Project status — LoL Draft Assistant

A snapshot of where the app is, for quickly bringing a person (or an AI assistant)
up to speed. For build/run commands and deeper conventions see [CLAUDE.md](CLAUDE.md).

_Last updated: 2026-06-01 · branch `draft-assistant-features`._

## What it is

A League of Legends draft assistant. Given both teams' picks + bans it recommends
champions ranked by **calibrated win probability** (a logistic model trained on Riot
Match-V5 data), with an **additive-z EV** fallback built on machineloling matchup /
synergy z-scores. It also does full-draft team analysis, premade lobbies, pick-order
guidance, build links, and live champ-select sync.

## Stack

- **Backend:** Python + FastAPI (`lol_draft/`). A single FastAPI function serves both the
  API and the built web UI (one port; deployed on Vercel).
- **Frontend:** React + Vite + TypeScript (`web/`). Dark theme; real champion icons via
  Riot Data Dragon.
- **Data:** machineloling static files (matchup Δpp + synergy z, playrates, blindability)
  decoded into `data/draft.db` (SQLite). `draft.db` is rebuildable; `web/dist` is committed
  (Vercel serves it).

## Backend modules (`lol_draft/`)

| Module | Role |
| --- | --- |
| `scoring.py` | additive-z EV engine (`DraftState`, `score_draft`) |
| `winprob.py`, `model.py`, `features.py`, `collect.py`, `train.py` | calibrated win-probability model — Riot Match-V5 collector, team-level features, logistic regression. `rank_candidates()` ranks by P(win); `get_model()` loads it (None → additive-z fallback) |
| `weights.py` | context-adaptive ("auto") weights: rescale base weights to the pick context (lane known? allies locked? pick order) and renormalize |
| `evaluate.py` | full 5v5 team score + win conditions (lane edge ≈ early, synergy ≈ late) |
| `lcu.py` | live champ-select read from the local League client (role inference for hidden enemy positions; 10 bans) |
| `lobby_store.py` | premade-lobby persistence (Upstash Redis on serverless via `KV_REST_API_*`/`UPSTASH_*`; in-memory locally) |
| `store.py`, `decode.py`, `fetch.py`, `config.py`, `cli.py` | data store + decode + fetch + config + CLI |
| `server.py` | all HTTP endpoints |

## API endpoints

`/api/health` · `/api/live` (LCU; `?demo=1`) · `/api/recommend` · `/api/weights`
(context-adaptive) · `/api/evaluate` (team analysis) · `/api/pick-order` (who picks next) ·
`/api/board` (top picks for ALL roles at once) · `/api/lobby*` (create / get / member / leave).

## Frontend (`web/src/components`)

`AllRolesBoard` (top-15 picks per role; click a pick to expand its advantages /
disadvantages), `PickOrder` ("who picks next"), `TeamAnalysis` (locked 5v5), `PoolPicks`
("from your pool"), `WeightsDrawer` (Auto-adapt toggle + base → applied), `LobbyBar` /
`LobbyPanel` (premade: shareable `?lobby` link, per-player role + champion pool, live
roster), `DraftGrid`, `BansBar` (10 bans), `ChampionPicker`, `ChampionAvatar` (DDragon
icons), `CoachlessLink`, `RecommendationCard`. State: `store.tsx` (`DraftProvider`) +
`lobby.tsx` (`LobbyProvider`).

## Feature notes / gotchas

- **Coachless:** we only **link out** to coachless.gg per champion (their ToS forbids
  scraping / redistributing their WPA data) — never scrape it.
- When the **win-prob model is loaded it owns the ranking**; the additive-z weights / auto
  mode only matter as the fallback.
- **Two role vocabularies:** engine `TOP/JUNGLE/MID/ADC/SUP` vs UI `TOP/JUNGLE/MID/BOT/SUPPORT`,
  mapped at the API boundary; weights are camelCase (UI) / snake_case (engine).
- The shared **SQLite connection is serialized by `_store_lock`** — any new store-touching
  endpoint must acquire it.

## Deployment & sharing

- Set up for **Vercel** (single FastAPI function serves UI + API) with **Upstash Redis**
  lobby persistence.
- A **one-file Windows desktop build** lets friends run the local champ-select (LCU) sync.
- Local dev: `serve.bat` (API on :8000) + `cd web && npm run dev` (:5180); or
  `cd web && npm run build` then serve `web/dist` via the API on :8000.
