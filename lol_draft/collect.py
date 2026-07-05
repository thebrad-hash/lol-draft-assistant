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
    off_patch: int = 0          # fetched but not on the requested --patch (filtered out)


def patch_of(game_version: str) -> str:
    """'14.10.581.1234' -> '14.10' (major.minor)."""
    parts = (game_version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (game_version or "")


def extract_rows(match: dict, *, valid_champions: Optional[set[str]] = None,
                 keep_patch: Optional[str] = None,
                 stats: Optional[ExtractStats] = None) -> list[dict]:
    """Turn one Match-V5 payload into up to 2 labeled rows (one per team).

    Returns [] for matches that aren't a clean, complete 5v5 (remakes, missing
    positions, unmapped champions). Each row is this team's draft view:
    {matchId, patch, queueId, side(100/200), win(0/1), allies{role:champ},
     enemies{role:champ}}.

    `keep_patch` (e.g. "16.12") restricts output to a single major.minor patch —
    games on any other patch are dropped (counted as `off_patch`, NOT as
    `matches_skipped`, since they're valid games, just not the one we want). This
    is how "current-patch-only" collection works: right after a patch drop most of
    a player's recent history is the PREVIOUS patch, so we fetch broadly and keep
    only the matching games.
    """
    info = match.get("info", {})
    meta = match.get("metadata", {})
    if info.get("queueId") != QUEUE_RANKED_SOLO:
        if stats: stats.matches_skipped += 1
        return []
    if info.get("gameDuration", 0) < MIN_GAME_SECONDS:
        if stats: stats.matches_skipped += 1
        return []
    if keep_patch is not None and patch_of(info.get("gameVersion", "")) != keep_patch:
        if stats: stats.off_patch += 1
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
# The Emerald+ ladder, high (apex) to low. Non-apex rungs are walked division
# I -> IV; apex tiers are a single league endpoint (no divisions).
TIER_LADDER = ["CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD",
               "PLATINUM", "GOLD", "SILVER", "BRONZE", "IRON"]
DIVISIONS = ["I", "II", "III", "IV"]


def ladder_from(min_tier: str) -> list[str]:
    """Tiers at or above `min_tier`, high to low. 'EMERALD' -> Emerald+."""
    min_tier = min_tier.upper()
    if min_tier not in TIER_LADDER:
        raise ValueError(f"Unknown tier {min_tier!r}; pick one of {TIER_LADDER}.")
    return TIER_LADDER[: TIER_LADDER.index(min_tier) + 1]


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


def _ledger_path(out_path: Path) -> Path:
    """Sidecar listing EVERY matchId we fetched (kept or not). With a patch
    filter, off-patch matches aren't written to the output, so without this they
    would be refetched on every resume; the ledger makes resume skip them too."""
    return out_path.with_suffix(out_path.suffix + ".seen")


def _load_ledger(out_path: Path) -> set[str]:
    p = _ledger_path(out_path)
    if not p.exists():
        return set()
    with open(p, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _drain_seeds(client: RiotClient, puuids: list[str], *, seen: set[str],
                 out, ledger, stats: ExtractStats, start_count: int,
                 target_games: int, matches_per_seed: int,
                 keep_patch: Optional[str], valid_champions: Optional[set[str]],
                 progress_every: int, label: str = "") -> None:
    """Process a batch of seed puuids: fetch each player's recent matches, extract
    labeled rows, append to `out` (and every fetched id to `ledger`). Mutates the
    shared `seen`/`stats` so callers can drive it across many tiers/divisions
    toward one cumulative `target_games`. Stops early once the target is hit."""
    for pi, puuid in enumerate(puuids):
        if stats.matches_ok + start_count >= target_games:
            return
        for mid in client.match_ids(puuid, count=matches_per_seed):
            if mid in seen:
                continue
            seen.add(mid)
            if ledger is not None:
                ledger.write(mid + "\n")
                ledger.flush()
            m = client.match(mid)
            if not m:
                continue
            for row in extract_rows(m, valid_champions=valid_champions,
                                    keep_patch=keep_patch, stats=stats):
                out.write(json.dumps(row) + "\n")
            out.flush()
            if stats.matches_ok and stats.matches_ok % progress_every == 0:
                print(f"  [{label}{pi+1}/{len(puuids)} seeds] "
                      f"{stats.matches_ok} kept, {stats.rows} rows, "
                      f"{stats.off_patch} off-patch, {stats.matches_skipped} skipped, "
                      f"{client.request_count} API calls")
            if stats.matches_ok + start_count >= target_games:
                return


def _report(stats: ExtractStats, start_count: int, out_path: Path,
            keep_patch: Optional[str]) -> None:
    print(f"\nDone. {stats.matches_ok} new matches -> {stats.rows} rows "
          f"(total ~{start_count + stats.matches_ok} matches in {out_path.name}).")
    if keep_patch is not None:
        print(f"  patch filter {keep_patch}: dropped {stats.off_patch} off-patch games.")
    print(f"  skipped {stats.matches_skipped} (remakes/incomplete/unknown).")
    if stats.unmapped_champs:
        print("  ! UNMAPPED champion names (add to CHAMPION_ALIASES if real):")
        for name, n in stats.unmapped_champs.most_common(15):
            print(f"      {name!r}: {n}")


def collect(client: RiotClient, *, tier: str, division: str = "I",
            target_games: int = 5000, matches_per_seed: int = 20,
            seed_pages: int = 3, out_path: Path,
            valid_champions: Optional[set[str]] = None,
            keep_patch: Optional[str] = None,
            progress_every: int = 25) -> ExtractStats:
    """Seed -> matchIds -> matches -> labeled rows, appended to `out_path`
    (JSONL). Stops once `target_games` distinct complete matches are written.
    Resumable: matches already present (or in the .seen ledger) are skipped.
    `keep_patch` restricts output to a single patch (see extract_rows)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(out_path) | _load_ledger(out_path)
    stats = ExtractStats()
    start_count = len(_load_seen(out_path))
    # Only resolve as many seeds as the target needs (2x margin for dedup/skips).
    # A patch filter slashes the per-seed yield, so widen the margin further.
    remaining = max(0, target_games - start_count)
    margin = 6 if keep_patch is not None else 2
    max_seeds = max(8, math.ceil(remaining / max(1, matches_per_seed)) * margin)
    print(f"Seeding {tier} {division if tier.upper() not in APEX_TIERS else ''} "
          f"on {client.platform} (region {client.region}); up to {max_seeds} seeds...")
    puuids = client.seed_puuids(tier, division, pages=seed_pages, limit=max_seeds)
    print(f"  {len(puuids)} seed players; {start_count} matches already collected.")

    with open(out_path, "a", encoding="utf-8") as out, \
            open(_ledger_path(out_path), "a", encoding="utf-8") as ledger:
        _drain_seeds(client, puuids, seen=seen, out=out, ledger=ledger, stats=stats,
                     start_count=start_count, target_games=target_games,
                     matches_per_seed=matches_per_seed, keep_patch=keep_patch,
                     valid_champions=valid_champions, progress_every=progress_every)

    _report(stats, start_count, out_path, keep_patch)
    return stats


def collect_ladder(client: RiotClient, *, min_tier: str = "EMERALD",
                   tiers: Optional[list[str]] = None,
                   target_games: int = 1000, matches_per_seed: int = 30,
                   seed_pages: int = 5, out_path: Path,
                   valid_champions: Optional[set[str]] = None,
                   keep_patch: Optional[str] = None,
                   progress_every: int = 25) -> ExtractStats:
    """Collect across a set of tiers, walking them in order and, within each
    non-apex tier, divisions I -> IV, until `target_games` matches are written or
    the seeds are exhausted. By default the tiers are the Emerald+ band (high to
    low via `ladder_from(min_tier)`); pass `tiers` to seed an EXPLICIT ordered set
    instead (e.g. ['EMERALD'] for Emerald only, or ['EMERALD','DIAMOND'] to start
    low and climb). One cumulative `seen`/`stats`/output/ledger is shared across
    all rungs, so the target counts distinct matches across them. Resumable like
    `collect`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_seen(out_path) | _load_ledger(out_path)
    stats = ExtractStats()
    start_count = len(_load_seen(out_path))
    if tiers:
        tiers = [t.upper() for t in tiers]
        for t in tiers:
            if t not in TIER_LADDER:
                raise ValueError(f"Unknown tier {t!r}; pick from {TIER_LADDER}.")
        band = " ".join(tiers)
    else:
        tiers = ladder_from(min_tier)
        band = f"{min_tier.upper()}+"
    print(f"=== LADDER COLLECT {band} on {client.platform} "
          f"(region {client.region}) ===")
    print(f"  tiers: {tiers}")
    print(f"  target {target_games} matches"
          + (f", patch {keep_patch} only" if keep_patch else "")
          + f"; {start_count} already collected.")

    with open(out_path, "a", encoding="utf-8") as out, \
            open(_ledger_path(out_path), "a", encoding="utf-8") as ledger:
        for tier in tiers:
            if stats.matches_ok + start_count >= target_games:
                break
            rungs = [None] if tier in APEX_TIERS else DIVISIONS
            for division in rungs:
                if stats.matches_ok + start_count >= target_games:
                    break
                got = stats.matches_ok
                puuids = client.seed_puuids(tier, division or "I", pages=seed_pages)
                rung = tier if division is None else f"{tier} {division}"
                print(f"  -- {rung}: {len(puuids)} seeds "
                      f"({stats.matches_ok + start_count}/{target_games} matches) --")
                _drain_seeds(client, puuids, seen=seen, out=out, ledger=ledger,
                             stats=stats, start_count=start_count,
                             target_games=target_games,
                             matches_per_seed=matches_per_seed, keep_patch=keep_patch,
                             valid_champions=valid_champions,
                             progress_every=progress_every, label=f"{rung} ")
                if keep_patch and (stats.matches_ok - got) == 0 and tier in APEX_TIERS:
                    # An apex tier with zero on-patch yield this rung is fine; keep going.
                    pass

    _report(stats, start_count, out_path, keep_patch)
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
    # patch filter: matching patch kept, off-patch dropped (and counted off_patch)
    pstats = ExtractStats()
    valid = {"Aatrox", "LeeSin", "Ahri", "Jinx", "Thresh",
             "Darius", "Wukong", "Zed", "Caitlyn", "Lulu"}
    if len(extract_rows(synthetic, valid_champions=valid, keep_patch="14.10")) != 2:
        print("  FAIL: keep_patch dropped a matching-patch game"); ok = False
    if extract_rows(synthetic, valid_champions=valid, keep_patch="16.12", stats=pstats) != []:
        print("  FAIL: keep_patch kept an off-patch game"); ok = False
    if pstats.off_patch != 1:
        print(f"  FAIL: off_patch counter = {pstats.off_patch}, expected 1"); ok = False
    # ladder_from must yield Emerald+ high->low
    if ladder_from("EMERALD") != ["CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD"]:
        print(f"  FAIL: ladder_from('EMERALD') = {ladder_from('EMERALD')}"); ok = False
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
                   help="single rank tier to seed from (default DIAMOND); ignored if --emerald-plus/--min-tier given")
    p.add_argument("--division", default="I", help="I-IV for non-apex tiers (default I)")
    p.add_argument("--emerald-plus", action="store_true",
                   help="collect across the whole Emerald+ band (shorthand for --min-tier EMERALD)")
    p.add_argument("--min-tier", help="collect across a tier band high->low from this tier "
                                      "(e.g. EMERALD for Emerald+); overrides --tier")
    p.add_argument("--tiers", help="explicit comma-separated tiers to seed, in order "
                                   "(e.g. EMERALD or EMERALD,DIAMOND); each non-apex tier "
                                   "walks divisions I-IV. Overrides --min-tier/--emerald-plus.")
    p.add_argument("--patch", help="keep ONLY this major.minor patch, e.g. 16.12 "
                                   "(fetches broadly, drops off-patch games)")
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
    tiers = [t.strip().upper() for t in args.tiers.split(",") if t.strip()] if args.tiers else None
    min_tier = "EMERALD" if args.emerald_plus else args.min_tier
    if tiers or min_tier:
        collect_ladder(client, tiers=tiers, min_tier=min_tier or "EMERALD",
                       target_games=args.target, matches_per_seed=args.matches_per_seed,
                       seed_pages=args.seed_pages, out_path=out_path,
                       valid_champions=valid, keep_patch=args.patch)
    else:
        collect(client, tier=args.tier, division=args.division,
                target_games=args.target, matches_per_seed=args.matches_per_seed,
                seed_pages=args.seed_pages, out_path=out_path, valid_champions=valid,
                keep_patch=args.patch)


if __name__ == "__main__":
    main()
