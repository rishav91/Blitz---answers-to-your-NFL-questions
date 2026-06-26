"""One-time setup: load NFL schedules, chunk them, embed, upsert to ChromaDB.

Run once before the chat graph ever starts: `python data/ingest.py`.
Re-running is safe — chunks are upserted by game_id, so nothing duplicates.
"""

import chromadb
import nfl_data_py as nfl
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

SEASONS = [2021, 2022, 2023]
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 100
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "games"

TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills",
    "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns",
    "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs",
    "LA": "Rams", "LAC": "Chargers", "LV": "Raiders", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks",
    "SF": "49ers", "TB": "Buccaneers", "TEN": "Titans", "WAS": "Commanders",
}

# nfl_data_py's game_type is the specific round (WC/DIV/CON/SB/REG). The
# round detail goes in the chunk text; metadata buckets it to REG/POST so
# "this season's playoffs" is a single equality filter, per ADR-003.
ROUND_NAMES = {
    "WC": "Wild Card",
    "DIV": "Divisional Round",
    "CON": "Conference Championship",
    "SB": "Super Bowl",
}


def load_games(seasons: list[int]) -> pd.DataFrame:
    games = nfl.import_schedules(seasons)
    games = games.dropna(subset=["home_score", "away_score"]).copy()
    games["home_score"] = games["home_score"].astype(int)
    games["away_score"] = games["away_score"].astype(int)
    return games


def format_chunk(row: pd.Series) -> str:
    home, away = TEAM_NAMES[row.home_team], TEAM_NAMES[row.away_team]

    if row.home_score > row.away_score:
        result = f"{home} won {row.home_score}-{row.away_score}"
    elif row.away_score > row.home_score:
        result = f"{away} won {row.away_score}-{row.home_score}"
    else:
        result = f"Tied {row.home_score}-{row.away_score}"

    header = f"Week {row.week}, {row.season} | {home} vs {away}"
    if row.game_type in ROUND_NAMES:
        header += f" ({ROUND_NAMES[row.game_type]})"

    surface = row.surface.strip() if isinstance(row.surface, str) else "unknown"

    return (
        f"{header}\n"
        f"Result: {result}\n"
        f"Venue: {row.stadium}\n"
        f"Surface: {surface} | Roof: {row.roof}"
    )


def build_metadata(row: pd.Series) -> dict:
    return {
        "season": int(row.season),
        "game_type": "REG" if row.game_type == "REG" else "POST",
        "week": int(row.week),
        "home_team": row.home_team,
        "away_team": row.away_team,
    }


def embed_documents(client: OpenAI, documents: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for i in range(0, len(documents), EMBEDDING_BATCH_SIZE):
        batch = documents[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


def verify(collection) -> None:
    # This chromadb version requires multi-key filters wrapped in $and —
    # implicit-AND on multiple top-level keys is rejected.
    result = collection.get(
        where={"$and": [{"season": 2023}, {"game_type": "POST"}]}
    )
    print(f"\nVerify: season=2023, game_type=POST -> {len(result['ids'])} games")
    for doc in sorted(result["documents"]):
        print(f"  {doc.splitlines()[0]}")


def ingest() -> None:
    games = load_games(SEASONS)

    ids = games["game_id"].tolist()
    documents = [format_chunk(row) for _, row in games.iterrows()]
    metadatas = [build_metadata(row) for _, row in games.iterrows()]

    embeddings = embed_documents(OpenAI(), documents)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(COLLECTION_NAME)
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    print(f"Upserted {len(ids)} game chunks into '{COLLECTION_NAME}' at {CHROMA_PATH}")
    verify(collection)


if __name__ == "__main__":
    ingest()
