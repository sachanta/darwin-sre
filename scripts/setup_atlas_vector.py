"""One-time setup: embed seed KB articles and create Atlas Vector Search index.

Run once before the autonomous capture run:
  poetry run python scripts/setup_atlas_vector.py

Steps:
  1. Load knowledge_base.json (seed articles)
  2. Embed each article via Voyage AI (voyage-3, 1024-dim)
  3. Upsert into MongoDB knowledge_articles collection
  4. Create the kb_vector_idx Atlas Search index (if not already present)
"""
import json
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from config import MONGODB_URI, MONGODB_DB, KB_VECTOR_INDEX, VOYAGE_EMBEDDING_DIM
from darwin.storage import knowledge_articles as kb_col, save_kb_article
from darwin.retrieval import embed
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

DATA_DIR = Path(__file__).parent.parent / "data"


def load_seed_kb() -> list[dict]:
    path = DATA_DIR / "knowledge_base.json"
    if not path.exists():
        print("ERROR: data/knowledge_base.json not found. Run generate_incidents.py first.")
        sys.exit(1)
    return json.loads(path.read_text())


def embed_and_load(articles: list[dict]) -> None:
    """Embed all articles in one Voyage batch call (avoids 3 RPM free-tier limit)."""
    print(f"Embedding {len(articles)} KB articles via Voyage AI (single batch call)...")
    texts = [f"{a.get('title', '')} {a.get('body', '')}" for a in articles]
    embeddings = embed(texts, input_type="document")
    print(f"  ✓ Got {len(embeddings)} embeddings (dim={len(embeddings[0])})")
    for article, embedding in zip(articles, embeddings):
        enriched = {**article, "embedding": embedding}
        save_kb_article(enriched)
    print(f"  ✓ All {len(articles)} articles stored in MongoDB")


def create_vector_index(client: MongoClient) -> None:
    db = client[MONGODB_DB]
    col = db["knowledge_articles"]

    # Check if index already exists
    try:
        existing = list(col.list_search_indexes())
        names = [idx.get("name") for idx in existing]
        if KB_VECTOR_INDEX in names:
            print(f"  ✓ Vector index '{KB_VECTOR_INDEX}' already exists — skipping creation")
            return
    except Exception:
        pass  # list_search_indexes may not be available on all Atlas tiers

    index_def = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": VOYAGE_EMBEDDING_DIM,
                    "similarity": "cosine",
                }
            ]
        },
        name=KB_VECTOR_INDEX,
        type="vectorSearch",
    )

    try:
        col.create_search_index(index_def)
        print(f"  ✓ Created Atlas Search index '{KB_VECTOR_INDEX}' (cosine, {VOYAGE_EMBEDDING_DIM}-dim)")
        print("  Waiting for index to become active (may take 30-60s)...")
        # Poll until active
        for _ in range(30):
            time.sleep(5)
            try:
                indexes = list(col.list_search_indexes(KB_VECTOR_INDEX))
                if indexes and indexes[0].get("status") == "READY":
                    print("  ✓ Index is READY")
                    return
            except Exception:
                pass
        print("  Index creation initiated — check Atlas UI for status")
    except Exception as e:
        print(f"  [warn] Could not create index via SDK: {e}")
        print("  → Create manually in Atlas UI:")
        print(f"    Collection: knowledge_articles")
        print(f"    Index name: {KB_VECTOR_INDEX}")
        print(f"    Type: Vector Search")
        print(f"    Field: embedding, dimensions: {VOYAGE_EMBEDDING_DIM}, similarity: cosine")


def main():
    print("=== Darwin SRE — Atlas Vector Search Setup ===\n")

    # 1. Load seed KB
    articles = load_seed_kb()
    print(f"Loaded {len(articles)} seed KB articles from knowledge_base.json")

    # 2. Embed + store
    embed_and_load(articles)

    # 3. Create vector index
    print("\nCreating Atlas Vector Search index...")
    client = MongoClient(MONGODB_URI)
    create_vector_index(client)

    # 4. Verify
    count = kb_col.count_documents({"source": "seed"})
    sample = kb_col.find_one({"source": "seed"}, {"_id": 0, "id": 1, "title": 1, "embedding": 1})
    has_embedding = sample and isinstance(sample.get("embedding"), list)
    embedding_dim = len(sample["embedding"]) if has_embedding else 0

    print(f"\n=== Verification ===")
    print(f"  Seed articles in MongoDB : {count}")
    print(f"  Embedding present        : {has_embedding}")
    print(f"  Embedding dimensions     : {embedding_dim} (expected {VOYAGE_EMBEDDING_DIM})")
    print(f"  Vector index             : {KB_VECTOR_INDEX}")
    print("\nDone. You can now run the main.py autonomous capture.")


if __name__ == "__main__":
    main()
