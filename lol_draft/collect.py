"""Collect labeled (draft -> win/loss) rows from the Riot Match-V5 API.

The EV engine's z-data is the FEATURE side of a model; this module produces the
LABEL side that the project otherwise lacks — one observation per team per
ranked game:

    { matchId, patch, queueId, side, win,                # label + provenance
      allies: {role: champ}, enemies: {role: champ} }    # the draft (this team's view)

Each completed 5v5 yields TWO rows (one per team). They are perfectly
anti-correlated (the same game from both sides), so they are NOT independent —
`matchId` is stored on every row so the trainer can keep both halves of a game
in the SAME train/test fold (group split) and avoid leakage.

Pipeline (all Riot endpoints; ToS-compliant, dev-key friendly):

    seed PUUIDs (League-V4 by tier)  ->  matchIds (Match-V5 by-puuid, queue 420)
        ->  full match (Match-V5)    ->  extract_rows()  ->  append JSONL

Design notes
------------
- **Key from the environment only** (`RIOT_API_KEY`); never hard-coded or logged.
- **Resumable**: every fetched matchId is recorded; re-running skips them, so a
  multi-hour dev-key collection can be interrupted and continued.
- **Rate-limited + retry**: honors 429 `Retry-After`, backs off on 5xx, tolerates
  404 (match pruned).
- **Name parity is a correctness bar.** Riot `championName` mostly matches the
  machineloling roster, but not always (e.g. Riot "MonkeyKing" == roster
  "Wukong"). We normalize via an explicit alias table and VALIDATE every name
  against the store roster, warning (and counting) anything unmapped rather than
  silently corrupting a draft. Pass a `valid_champions` set to enable this.

This module does no scoring and adds no third-party dependency (stdlib only).
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import config

# --- routing -------------------------------------------------------------
# League-V4 / Summoner-V4 use PLATFORM hosts; Match-V5 uses REGIONAL hosts.
PLATFORM_TO_REGION = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "oc1": "americas",
    "kr": "asia", "jp1": "asia",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}
APEX_TIERS = {"CHALLENGER", "GRANDMASTER", "MASTER"}
RANKED_SOLO = "RANKED_SOLO_5x5"
QUEUE_RANKED_SOLO = 420

# Riot teamPosition -> engine role keys (TOP/JUNGLE/MID/ADC/SUP).
POSITION_TO_ROLE = {
    "TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID",
    "BOTTOM": "ADC", "UTILITY": "SUP",
}

# Riot championName -> machineloling roster key. Only structural divergences;
# everything else is identity. Unmapped-but-unknown names are caught at runtime.
CHAMPION_ALIASES = {
    "MonkeyKing": "Wukong",
    "Fiddlesticks": "FiddleSticks",
}

MIN_GAME_SECONDS = 300  # shorter than this is a remake/early-FF; skip.


def normalize_champion(name: str) -> str:
    return CHAMPION_ALIASES.get(name, name)


# --- HTTP client ---------------------------------------------------------
class RiotClient:
    """Thin Riot API client with conservative dev-key rate limiting + retry.

    Dev keys allow ~20 req/s and ~100 req/2 min; we self-throttle to a steady
    rate well under both and additionally honor server-sent `Retry-After`.
    """

    def __init__(self, api_key: str, platform: str, region: Optional[str] = None,
                 *, min_interval: float = 1.25, timeout: float = 15.0):
        self.api_key = api_key
        self.platform = platform
        self.region = region or PLATFORM_TO_REGION.get(platform)
        if not self.region:
            raise ValueError(
                f"Unknown platform {platform!r}; pass region= explicitly "
                f"(one of: americas, asia, europe, sea)."
            )
        self.min_interval = min_interval          # seconds between requests
        self.timeout = timeout
        self._last = 0.0
        self.request_count = 0

    def _host(self, routing: str) -> str:
        return f"https://{routing}.api.riotgames.com"

    def _throttle(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, routing: str, path: str, params: Optional[dict] = None,
            *, max_retries: int = 5):
        """GET {routing host}{path}; returns parsed JSON or None on 404."""
        url = self._host(routing) + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(max_retries + 1):
            self._throttle()
            # A browser User-Agent is REQUIRED: the Riot API is fronted by
            # Cloudflare, which blocks the default Python-urllib UA at the edge
            # (Cloudflare "error 1010") before the key is ever checked. Reuse the
            # roster's existing browser UA (see config.USER_AGENT).
            req = urllib.request.Request(url, headers={
                "X-Riot-Token": self.api_key,
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })
            self._last = time.monotonic()
            self.request_count += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 429:
                    retry_after = int(e.headers.get("Retry-After", "5"))
                    time.sleep(retry_after + 1)
                    continue
                if 500 <= e.code < 600:
                    time.sleep(2 ** attempt)
                    continue
                # 400/401/403 are config errors (bad/expired key, bad params) —
                # surface immediately; retrying won't help.
                raise
            except urllib.error.URLError:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Giving up after {max_retries} retries: {url}")

    # --- seeding: tier -> PUUIDs ---
    def seed_puuids(self, tier: str, division: str = "I",
                    pages: int = 1, limit: Optional[int] = None) -> list[str]:
        """Return PUUIDs of players in a tier, up to `limit`. Apex tiers use the
        league endpoints; others page through League-V4 entries. summonerId is
        resolved to puuid via Summoner-V4 only when the entry omits it, and only
        for as many seeds as we actually need (avoids hundreds of wasted calls)."""
        tier = tier.upper()
        puuids: list[str] = []

        def take(entries: list[dict]) -> bool:
            """Append puuids from these entries; return True once limit reached."""
            for e in entries:
                pid = e.get("puuid")
                if not pid and e.get("summonerId"):
                    s = self.get(self.platform,
                                 f"/lol/summoner/v4/summoners/{e['summonerId']}")
                    pid = s.get("puuid") if s else None
                if pid:
                    puuids.append(pid)
                    if limit and len(puuids) >= limit:
                        return True
            return False

        if tier in APEX_TIERS:
            ep = {"CHALLENGER": "challengerleagues", "GRANDMASTER": "grandmasterleagues",
                  "MASTER": "masterleagues"}[tier]
            data = self.get(self.platform, f"/lol/league/v4/{ep}/by-queue/{RANKED_SOLO}")
            take((data or {}).get("entries", []) if data else [])
        else:
            for page in range(1, pages + 1):
                data = self.get(self.platform,
                                f"/lol/league/v4/entries/{RANKED_SOLO}/{tier}/{division}",
                                {"page": page})
                if not data:
                    break
                if take(data):
                    break
        return puuids

    def match_ids(self, puuid: str, *, queue: int = QUEUE_RANKED_SOLO,
                  count: int = 20, start: int = 0) -> list[str]:
        data = self.get(self.region,
                        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
                        {"queue": queue, "type": "ranked", "start": start, "count": count})
        return data or []

    def match(self, match_id: str) -> Optional[dict]:
        return self.get(self.region, f"/lol/match/v5/matches/{match_id}")


# --- extraction ----------------------------------------------------------
@dataclass
class ExtractStats:
    matches_ok: int = 0
    matches_skipped: int = 0
    rows: int = 0
    unmapped_champs: Counter = field(default_factory=Counter)
    incomplete_roles: int = 0


def patch_of(game_version: str) -> str:
    """'14.10.581.1234' -> '14.10' (major.minor)."""
    parts = (game_version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (game_version or "")


def extract_rows(match: dict, *, valid_champions: Optional[set[str]] = None,
                 stats: Optional[ExtractStats] = None) -> list[dict]:
    """Turn one Match-V5 payload into up to 2 labeled rows (one per team).

    Returns [] for matches that aren't a clean, complete 5v5 (remakes, missing
    positions, unmapped champions). Each row is this team's draft view:
    {matchId, patch, queueId, side(100/200), win(0/1), allies{role:champ},
     enemies{role:champ}}.
    """
    info = match.get("info", {})
    meta = match.get("metadata", {})
    if info.get("queueId") != QUEUE_RANKED_SOLO:
        if stats: stats.matches_skipped += 1
        return []
    if info.get("gameDuration", 0) < MIN_GAME_SECONDS:
        if stats: stats.matches_skipped += 1
        return []

    teams: dict[int, dict] = {100: {}, 200: {}}
    wins: dict[int, bool] = {}
    ok = True
    for p in info.get("participants", []):
        side = p.get("teamId")
        role = POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper())
        champ = normalize_champion(p.get("championName", ""))
        if side not in teams or role is None:
            ok = False
            continue
        if valid_champions is not None and champ not in valid_champions:
            if stats: stats.unmapped_champs[p.get("championName", "")] += 1
            ok = False
            continue
        if role in teams[side]:        # duplicate position on a side -> unreliable
            ok = False
            continue
        teams[side][role] = champ
        wins[side] = bool(p.get("win"))

    if not ok or any(len(teams[s]) != 5 for s in (100, 200)):
        if stats:
            stats.matches_skipped += 1
            stats.incomplete_roles += 1
        return []

    patch = patch_of(info.get("gameVersion", ""))
    rows = []
    for side, other in ((100, 200), (200, 100)):
        rows.append({
            "matchId": meta.get("matchId"),
            "patch": patch,
            "queueId": info.get("queueId"),
            "side": side,
            "win": int(wins[side]),
            "allies": teams[side],
            "enemies": teams[other],
        })
    if stats:
        stats.matches_ok += 1
        stats.rows += len(rows)
    return rows


# --- orchestration -------------------------------------------------------
def _load_seen(out_path: Path) -> set[str]:
    """matchIds already in the output file, so re-runs are incremental."""
    seen: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["matchId"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return seen


def collect(client: RiotClient, *, tier: str, division: str = "I",
            target_games: int = 5000, matches_per_seed: int = 20,
            seed_pages: int = 3, out_path: Path,
            valid_champions: Optional[set[str]] = None,
            progress_every: int = 25) -> ExtractStats:
    """Seed -> matchIds -> matches -> labeled rows, appended to `out_path`
    (JSONL). Stops once `target_games` distinct complete matches are written.
    Resumable: matches already present are skipped."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(out_path)
    stats = ExtractStats()
    start_count = len(seen)
    # Only resolve as many seeds as the target needs (2x margin for dedup/skips).
    remaining = max(0, target_games - start_count)
    max_seeds = max(8, math.ceil(remaining / max(1, matches_per_seed)) * 2)
    print(f"Seeding {tier} {division if tier.upper() not in APEX_TIERS else ''} "
          f"on {client.platform} (region {client.region}); up to {max_seeds} seeds...")
    puuids = client.seed_puuids(tier, division, pages=seed_pages, limit=max_seeds)
    print(f"  {len(puuids)} seed players; {start_count} matches already collected.")

    with open(out_path, "a", encoding="utf-8") as out:
        for pi, puuid in enumerate(puuids):
            if stats.matches_ok + start_count >= target_games:
                break
            for mid in client.match_ids(puuid, count=matches_per_seed):
                if mid in seen:
                    continue
                seen.add(mid)
                m = client.match(mid)
                if not m:
                    continue
                for row in extract_rows(m, valid_champions=valid_champions, stats=stats):
                    out.write(json.dumps(row) + "\n")
                out.flush()
                if stats.matches_ok and stats.matches_ok % progress_every == 0:
                    print(f"  [{pi+1}/{len(puuids)} seeds] "
                          f"{stats.matches_ok} matches, {stats.rows} rows, "
                          f"{stats.matches_skipped} skipped, "
                          f"{client.request_count} API calls")
                if stats.matches_ok + start_count >= target_games:
                    break

    print(f"\nDone. {stats.matches_ok} new matches -> {stats.rows} rows "
          f"(total ~{start_count + stats.rows} rows in {out_path.name}).")
    print(f"  skipped {stats.matches_skipped} (remakes/incomplete/unknown).")
    if stats.unmapped_champs:
        print("  ! UNMAPPED champion names (add to CHAMPION_ALIASES if real):")
        for name, n in stats.unmapped_champs.most_common(15):
            print(f"      {name!r}: {n}")
    return stats


def _default_out() -> Path:
    return config.PROJECT_DIR / "data" / "games" / "labeled_games.jsonl"


# --- offline self-test (no network) --------------------------------------
def _selftest() -> bool:
    """Prove extraction/role-mapping/normalization on a synthetic payload."""
    def part(side, pos, champ, win):
        return {"teamId": side, "teamPosition": pos, "championName": champ, "win": win}
    synthetic = {
        "metadata": {"matchId": "NA1_TEST"},
        "info": {
            "queueId": QUEUE_RANKED_SOLO, "gameDuration": 1800,
            "gameVersion": "14.10.581.1234",
            "participants": [
                part(100, "TOP", "Aatrox", True), part(100, "JUNGLE", "LeeSin", True),
                part(100, "MIDDLE", "Ahri", True), part(100, "BOTTOM", "Jinx", True),
                part(100, "UTILITY", "Thresh", True),
                part(200, "TOP", "Darius", False), part(200, "JUNGLE", "MonkeyKing", False),
                part(200, "MIDDLE", "Zed", False), part(200, "BOTTOM", "Caitlyn", False),
                part(200, "UTILITY", "Lulu", False),
            ],
        },
    }
    stats = ExtractStats()
    rows = extract_rows(synthetic, valid_champions={
        "Aatrox", "LeeSin", "Ahri", "Jinx", "Thresh",
        "Darius", "Wukong", "Zed", "Caitlyn", "Lulu"}, stats=stats)
    ok = True
    if len(rows) != 2:
        print(f"  FAIL: expected 2 rows, got {len(rows)}"); ok = False
    blue = next((r for r in rows if r["side"] == 100), None)
    red = next((r for r in rows if r["side"] == 200), None)
    if not blue or blue["win"] != 1 or blue["allies"].get("MID") != "Ahri":
        print(f"  FAIL: blue row wrong: {blue}"); ok = False
    if not red or red["win"] != 0 or red["allies"].get("JUNGLE") != "Wukong":
        # MonkeyKing must normalize to Wukong
        print(f"  FAIL: red row / normalization wrong: {red}"); ok = False
    if blue and blue["enemies"].get("JUNGLE") != "Wukong":
        print(f"  FAIL: enemy view wrong: {blue}"); ok = False
    if blue and blue["patch"] != "14.10":
        print(f"  FAIL: patch parse wrong: {blue}"); ok = False
    # a remake must be skipped
    remake = json.loads(json.dumps(synthetic))
    remake["info"]["gameDuration"] = 120
    if extract_rows(remake) != []:
        print("  FAIL: remake not skipped"); ok = False
    print("  OK: extract_rows self-test passed" if ok else "  self-test FAILED")
    return ok


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        prog="lol-draft-collect",
        description="Collect labeled (draft->win/loss) rows from Riot Match-V5.")
    p.add_argument("--selftest", action="store_true",
                   help="run the offline extraction self-test and exit (no network/key)")
    p.add_argument("--platform", help="platform host: na1, euw1, kr, eun1, br1, ...")
    p.add_argument("--region", help="regional route: americas|asia|europe|sea "
                                    "(derived from platform if omitted)")
    p.add_argument("--tier", default="DIAMOND",
                   help="rank tier to seed from (default DIAMOND, to match config.DEFAULT_RANK)")
    p.add_argument("--division", default="I", help="I-IV for non-apex tiers (default I)")
    p.add_argument("--target", type=int, default=5000, help="target distinct matches")
    p.add_argument("--seed-pages", type=int, default=3, help="League-V4 entry pages to seed")
    p.add_argument("--matches-per-seed", type=int, default=20)
    p.add_argument("--out", help=f"output JSONL (default {_default_out()})")
    p.add_argument("--no-validate", action="store_true",
                   help="skip champion-name validation against the built store roster")
    args = p.parse_args(argv)

    if args.selftest:
        raise SystemExit(0 if _selftest() else 1)

    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        raise SystemExit("Set RIOT_API_KEY in your environment (do not hard-code it).")
    if not args.platform:
        raise SystemExit("--platform is required (e.g. na1, euw1, kr).")

    valid = None
    if not args.no_validate:
        from .store import Store
        try:
            with Store() as store:
                valid = store.all_champions()
        except FileNotFoundError:
            print("  ! No store built yet; skipping name validation "
                  "(run `python -m lol_draft.cli build` to enable).")

    client = RiotClient(api_key, args.platform, args.region)
    out_path = Path(args.out) if args.out else _default_out()
    collect(client, tier=args.tier, division=args.division,
            target_games=args.target, matches_per_seed=args.matches_per_seed,
            seed_pages=args.seed_pages, out_path=out_path, valid_champions=valid)


if __name__ == "__main__":
    main()
