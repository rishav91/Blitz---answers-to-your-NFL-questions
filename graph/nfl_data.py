"""In-memory NFL DataFrames backing the tool layer (ADR-007).

`pbp` (play-by-play, 2023) and `games` (2021-2023 completed games) are loaded
lazily once per process and only ever read by tools — never embedded, never in
Chroma; the tool-vs-retrieval boundary is structural, not a prompting
convention. First access downloads from nflverse via `nfl_data_py` and caches
a trimmed parquet under `.nfl_cache/` (gitignored) so later process starts
skip the multi-minute download; delete the directory to force a re-fetch.
"""

from pathlib import Path
from typing import Optional

import nfl_data_py as nfl
import pandas as pd

CACHE_DIR = Path(".nfl_cache")

# ARCHITECTURE.md §Scale: pbp is ~50k rows, 2023 only; games spans 2021-2023.
PBP_SEASONS = [2023]
GAMES_SEASONS = [2021, 2022, 2023]

# The full pbp frame is ~400 columns; keep only what calculate_team_stats'
# five metrics read (FR-3.1) to keep the in-memory footprint small.
PBP_COLUMNS = [
    "game_id", "season", "season_type", "week",
    "home_team", "away_team", "home_score", "away_score",
    "posteam", "defteam", "yards_gained",
    "interception", "fumble_lost", "fumbled_1_team", "fumble_recovery_1_team",
    "down", "third_down_converted", "third_down_failed",
    "yardline_100", "fixed_drive", "fixed_drive_result",
]

_pbp: Optional[pd.DataFrame] = None
_games: Optional[pd.DataFrame] = None
_team_conf: Optional[dict] = None


def _cached(name: str, loader) -> pd.DataFrame:
    path = CACHE_DIR / f"{name}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    frame = loader()
    CACHE_DIR.mkdir(exist_ok=True)
    frame.to_parquet(path, index=False)
    return frame


def get_pbp() -> pd.DataFrame:
    """Play-by-play for PBP_SEASONS, trimmed to PBP_COLUMNS. Tools only."""
    global _pbp
    if _pbp is None:
        _pbp = _cached(
            "pbp", lambda: nfl.import_pbp_data(PBP_SEASONS, cache=False)[PBP_COLUMNS]
        )
    return _pbp


def get_games() -> pd.DataFrame:
    """Completed games for GAMES_SEASONS — same slice data/ingest.py embeds,
    but consumed here as a plain DataFrame by get_standings (FR-3.2)."""
    global _games
    if _games is None:

        def load():
            try:
                games = nfl.import_schedules(GAMES_SEASONS)
            except Exception:
                # import_schedules reads Lee Sharpe's personal domain
                # (habitatring.com); fall back to the same file in the
                # canonical nflverse repo when that's unreachable.
                games = pd.read_csv(
                    "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
                )
                games = games[games["season"].isin(GAMES_SEASONS)]
            games = games.dropna(subset=["home_score", "away_score"]).copy()
            games["home_score"] = games["home_score"].astype(int)
            games["away_score"] = games["away_score"].astype(int)
            return games[
                ["game_id", "season", "game_type", "week",
                 "home_team", "away_team", "home_score", "away_score"]
            ]

        _games = _cached("games", load)
    return _games


def get_team_conference() -> dict:
    """Team abbreviation -> conference ('AFC'/'NFC'), for get_standings."""
    global _team_conf
    if _team_conf is None:
        desc = _cached(
            "teams", lambda: nfl.import_team_desc()[["team_abbr", "team_conf"]]
        )
        _team_conf = dict(zip(desc["team_abbr"], desc["team_conf"]))
    return _team_conf
