"""Static configuration for the LoL Draft Assistant.

All values here are defaults; weights and aggregation are overridable at the
CLI and are persisted (and editable) in the SQLite store.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- Roles (must match index.json keys exactly) ---
ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUP"]

# Friendly aliases accepted on the CLI -> canonical role key.
ROLE_ALIASES = {
    "TOP": "TOP", "T": "TOP",
    "JUNGLE": "JUNGLE", "JG": "JUNGLE", "JUNG": "JUNGLE", "J": "JUNGLE",
    "MID": "MID", "MIDDLE": "MID", "M": "MID",
    "ADC": "ADC", "BOT": "ADC", "BOTTOM": "ADC", "AD": "ADC",
    "SUP": "SUP", "SUPPORT": "SUP", "SUPP": "SUP", "S": "SUP",
}

# --- Data source (static files on GitHub Pages / Fastly; CORS-open, permissive) ---
DATA_BASE_URL = "https://pooldesigner.machineloling.com/data"
DATA_FILES = ["matrices.bin", "index.json", "champions.json"]

# A real browser User-Agent (site etiquette: identify, fetch once, cache).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --- Rank brackets (machineloling calls these "patches"); matchup/synergy
# z-data is patch-invariant, only playrates differ across brackets. ---
RANKS = ["silver", "gold", "platinum", "emerald", "diamond", "master_plus"]
DEFAULT_RANK = "diamond"

# --- Scoring defaults (mirrors the Champion Pool Designer UI) ---
DEFAULT_WEIGHTS = {
    "in_lane": 0.70,
    "out_of_lane": 0.50,
    "synergy": 1.00,
    "blindability": 0.90,
}
# How to combine z-scores across multiple opponents / allies within a component.
#   "mean"  -> average across the set
#   "topn"  -> average of the best N (see DEFAULT_TOP_N)
#   "sum"   -> raw sum
DEFAULT_AGG = "mean"
DEFAULT_TOP_N = 3

# --- Paths ---
# In the PyInstaller "desktop" build the app is frozen and bundled data lives in
# the extraction dir (sys._MEIPASS); otherwise paths are relative to the repo.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
DB_PATH = PROJECT_DIR / "data" / "draft.db"


def normalize_role(s: str) -> str:
    """Map a user-supplied role token to a canonical role key."""
    key = ROLE_ALIASES.get(s.strip().upper())
    if key is None:
        raise ValueError(f"Unknown role {s!r}. Valid: {', '.join(ROLES)}")
    return key
