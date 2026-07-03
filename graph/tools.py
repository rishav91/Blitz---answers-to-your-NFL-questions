"""Tools for the analytical path (FR-3.x) — deterministic pandas aggregation
over the in-memory DataFrames in graph/nfl_data.py. The model decides *when*
and *how many times* to call these (ADR-005: no compare_teams — comparisons
are one call per team); it never computes numbers itself
(AI-ARCHITECTURE.md §Deterministic vs. ML vs. LLM split).

Invalid arguments return an {"error": ...} payload instead of raising, so the
model can read the problem and correct its next call.
"""

from typing import Literal

from langchain_core.tools import tool

from graph.nfl_data import GAMES_SEASONS, PBP_SEASONS, get_games, get_pbp, get_team_conference

# Reverse of data/ingest.py's TEAM_NAMES, so the model can pass either an
# abbreviation ("KC") or a nickname ("Chiefs").
TEAM_ABBR = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF",
    "panthers": "CAR", "bears": "CHI", "bengals": "CIN", "browns": "CLE",
    "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
    "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC",
    "rams": "LA", "chargers": "LAC", "raiders": "LV", "dolphins": "MIA",
    "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
    "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA",
    "49ers": "SF", "buccaneers": "TB", "titans": "TEN", "commanders": "WAS",
}

Metric = Literal[
    "points_per_game",
    "yards_per_game",
    "turnover_differential",
    "third_down_pct",
    "red_zone_efficiency",
]


def normalize_team(team: str) -> str | None:
    """'KC', 'Chiefs', or 'Kansas City Chiefs' -> 'KC'; None if unrecognized."""
    cleaned = team.strip().lower()
    if cleaned.upper() in TEAM_ABBR.values():
        return cleaned.upper()
    for nickname, abbr in TEAM_ABBR.items():
        if cleaned == nickname or cleaned.endswith(" " + nickname):
            return abbr
    return None


@tool
def calculate_team_stats(team: str, season: int, metric: Metric) -> dict:
    """Compute one regular-season stat for one team from play-by-play data.

    Call once per team — for comparisons between teams, call this once for
    each team and combine the results yourself.

    Args:
        team: Team abbreviation or nickname, e.g. "KC" or "Chiefs".
        season: Season year (the year the season started). Only 2023 is loaded.
        metric: One of points_per_game, yards_per_game, turnover_differential,
            third_down_pct, red_zone_efficiency.
    """
    abbr = normalize_team(team)
    if abbr is None:
        return {"error": f"Unknown team {team!r}. Pass an abbreviation like 'KC' or a nickname like 'Chiefs'."}
    if season not in PBP_SEASONS:
        return {"error": f"No play-by-play loaded for season {season}. Available seasons: {PBP_SEASONS}."}

    pbp = get_pbp()
    reg = pbp[(pbp["season"] == season) & (pbp["season_type"] == "REG")]
    result = {"team": abbr, "season": season, "metric": metric, "scope": "regular season"}

    if metric == "points_per_game":
        games = reg[(reg["home_team"] == abbr) | (reg["away_team"] == abbr)]
        pts = games.groupby("game_id")[["home_team", "home_score", "away_score"]].first()
        scored = pts.apply(lambda g: g["home_score"] if g["home_team"] == abbr else g["away_score"], axis=1)
        result.update(value=round(scored.mean(), 1), games=len(scored), total_points=int(scored.sum()))

    elif metric == "yards_per_game":
        per_game = reg[reg["posteam"] == abbr].groupby("game_id")["yards_gained"].sum()
        result.update(value=round(per_game.mean(), 1), games=len(per_game), total_yards=int(per_game.sum()))

    elif metric == "turnover_differential":
        # Fumbles are attributed by the fumbling/recovering team, not posteam —
        # special-teams fumbles (e.g. a muffed punt) belong to the returner's
        # team. Verified against the 2023 public record (KC -11, SF +10).
        picked = reg["interception"] == 1
        lost = reg["fumble_lost"] == 1
        giveaways = int((picked & (reg["posteam"] == abbr)).sum()) + int((lost & (reg["fumbled_1_team"] == abbr)).sum())
        takeaways = int((picked & (reg["defteam"] == abbr)).sum()) + int((lost & (reg["fumble_recovery_1_team"] == abbr)).sum())
        games = reg.loc[(reg["home_team"] == abbr) | (reg["away_team"] == abbr), "game_id"].nunique()
        if games == 0:
            return {"error": f"No regular-season games found for {abbr} in {season}."}
        result.update(
            value=takeaways - giveaways, takeaways=takeaways, giveaways=giveaways,
            games=games, per_game=round((takeaways - giveaways) / games, 2),
        )

    elif metric == "third_down_pct":
        attempts = reg[(reg["posteam"] == abbr) & (reg["down"] == 3)
                       & ((reg["third_down_converted"] == 1) | (reg["third_down_failed"] == 1))]
        if len(attempts) == 0:
            return {"error": f"No third-down plays found for {abbr} in {season}."}
        converted = int((attempts["third_down_converted"] == 1).sum())
        result.update(value=round(100 * converted / len(attempts), 1), converted=converted, attempts=len(attempts))

    elif metric == "red_zone_efficiency":
        # TD rate per red-zone trip: drives with any snap inside the 20.
        rz = reg[(reg["posteam"] == abbr) & (reg["yardline_100"] <= 20)]
        drives = rz.groupby(["game_id", "fixed_drive"])["fixed_drive_result"].first()
        if len(drives) == 0:
            return {"error": f"No red-zone drives found for {abbr} in {season}."}
        tds = int((drives == "Touchdown").sum())
        result.update(value=round(100 * tds / len(drives), 1), td_drives=tds, red_zone_trips=len(drives))

    return result


@tool
def get_standings(conference: Literal["AFC", "NFC"], season: int) -> dict:
    """Regular-season conference standings: every team's W-L-T record,
    ordered by win percentage. Aggregated from game results only.

    Ordering breaks win-pct ties by wins, not the NFL's official tiebreaker
    rules (head-to-head, division record, etc.) — say so if seeding hinges
    on a tie.

    Args:
        conference: "AFC" or "NFC".
        season: Season year (the year the season started), 2021-2023.
    """
    if season not in GAMES_SEASONS:
        return {"error": f"No games loaded for season {season}. Available seasons: {GAMES_SEASONS}."}

    games = get_games()
    reg = games[(games["season"] == season) & (games["game_type"] == "REG")]
    conferences = get_team_conference()

    records: dict[str, dict] = {}
    for row in reg.itertuples():
        if row.home_score > row.away_score:
            winner, loser = row.home_team, row.away_team
        elif row.away_score > row.home_score:
            winner, loser = row.away_team, row.home_team
        else:
            winner = loser = None
        for team in (row.home_team, row.away_team):
            rec = records.setdefault(team, {"team": team, "wins": 0, "losses": 0, "ties": 0})
            if winner is None:
                rec["ties"] += 1
            elif team == winner:
                rec["wins"] += 1
            else:
                rec["losses"] += 1

    table = [rec for rec in records.values() if conferences.get(rec["team"]) == conference]
    for rec in table:
        played = rec["wins"] + rec["losses"] + rec["ties"]
        rec["win_pct"] = round((rec["wins"] + 0.5 * rec["ties"]) / played, 3)
    table.sort(key=lambda rec: (rec["win_pct"], rec["wins"]), reverse=True)
    return {"conference": conference, "season": season, "scope": "regular season", "standings": table}
