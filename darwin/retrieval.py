"""Voyage AI embeddings + MongoDB Atlas $vectorSearch for KB retrieval.

Public API:
  embed(texts, input_type)       → list[list[float]]
  embed_one(text, input_type)    → list[float]
  index_kb_article(article)      → article with embedding field added, saved to DB
  retrieve_kbs(incident, top_k)  → list of KB article dicts (no embedding field)
"""
from __future__ import annotations
import voyageai
from config import VOYAGE_API_KEY, VOYAGE_MODEL, VOYAGE_EMBEDDING_DIM, KB_VECTOR_INDEX, KB_TOP_K
from darwin.storage import knowledge_articles, save_kb_article

_voyage: voyageai.Client | None = None


def _client() -> voyageai.Client:
    global _voyage
    if _voyage is None:
        _voyage = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts. input_type: 'document' for KB articles, 'query' for incidents."""
    result = _client().embed(texts, model=VOYAGE_MODEL, input_type=input_type)
    return result.embeddings


def embed_one(text: str, input_type: str = "document") -> list[float]:
    return embed([text], input_type=input_type)[0]


def _build_query_text(incident: dict) -> str:
    """Build the retrieval query string from an incident."""
    parts = [
        incident.get("title", ""),
        incident.get("description", ""),
        incident.get("category", ""),
    ]
    log_id = incident.get("log_id")
    if log_id:
        from darwin.storage import get_log
        log = get_log(log_id)
        if log and log.get("lines"):
            top_lines = " ".join(l.get("msg", "") for l in log["lines"][:3])
            parts.append(top_lines)
    return " ".join(p for p in parts if p)


def index_kb_article(article: dict) -> dict:
    """Embed a KB article and upsert it into MongoDB with the embedding field."""
    body = f"{article.get('title', '')} {article.get('body', '')}"
    embedding = embed_one(body, input_type="document")
    article = {**article, "embedding": embedding}
    save_kb_article(article)
    return article


def retrieve_kbs(incident: dict, top_k: int = KB_TOP_K) -> list[dict]:
    """Vector-search the KB for articles relevant to this incident.

    Uses Atlas $vectorSearch with an optional service filter.
    Returns articles without the embedding field (clean for prompt injection).
    """
    query_text = _build_query_text(incident)
    query_embedding = embed_one(query_text, input_type="query")

    service = incident.get("service", "")

    pipeline = [
        {
            "$vectorSearch": {
                "index": KB_VECTOR_INDEX,
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": top_k * 10,
                "limit": top_k,
            }
        },
        {
            "$project": {
                "_id": 0,
                "embedding": 0,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]

    results = list(knowledge_articles.aggregate(pipeline))

    # Add similarity score; strip embedding field (large, not needed downstream)
    for r in results:
        r["similarity"] = round(r.pop("score", 0.0), 3)
        r.pop("embedding", None)
        r.pop("_id", None)

    return results
