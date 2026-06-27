"""Phase B tests — Voyage AI embeddings + Atlas $vectorSearch retrieval."""
import pytest
from unittest.mock import patch, MagicMock


FAKE_EMBEDDING = [0.1] * 1024  # 1024-dim fake vector


# ---------------------------------------------------------------------------
# Unit tests — all external calls mocked
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_voyage(monkeypatch):
    """Mock Voyage AI client so no real API calls are made."""
    import darwin.retrieval as retrieval
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.embeddings = [FAKE_EMBEDDING]
    mock_client.embed.return_value = mock_result
    monkeypatch.setattr(retrieval, "_voyage", mock_client)
    return mock_client


@pytest.fixture
def mock_kb_collection(monkeypatch):
    """Mock MongoDB knowledge_articles collection."""
    import darwin.retrieval as retrieval
    import darwin.storage as storage
    mock_col = MagicMock()
    monkeypatch.setattr(retrieval, "knowledge_articles", mock_col)
    monkeypatch.setattr(storage, "knowledge_articles", mock_col)
    return mock_col


class TestEmbedding:
    def test_embed_returns_list_of_vectors(self, mock_voyage):
        from darwin.retrieval import embed
        result = embed(["test text"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert len(result[0]) == 1024

    def test_embed_one_returns_single_vector(self, mock_voyage):
        from darwin.retrieval import embed_one
        result = embed_one("test text")
        assert isinstance(result, list)
        assert len(result) == 1024

    def test_embed_calls_voyage_with_correct_model(self, mock_voyage):
        from darwin.retrieval import embed
        from config import VOYAGE_MODEL
        embed(["hello"], input_type="query")
        mock_voyage.embed.assert_called_once()
        call_kwargs = mock_voyage.embed.call_args
        assert call_kwargs[1]["model"] == VOYAGE_MODEL or call_kwargs[0][1] == VOYAGE_MODEL

    def test_embed_document_vs_query_type(self, mock_voyage):
        from darwin.retrieval import embed
        embed(["kb article text"], input_type="document")
        call_args = mock_voyage.embed.call_args
        assert "document" in str(call_args)

    def test_embedding_dimension_is_1024(self, mock_voyage):
        from darwin.retrieval import embed_one
        from config import VOYAGE_EMBEDDING_DIM
        result = embed_one("any text")
        assert len(result) == VOYAGE_EMBEDDING_DIM


class TestIndexKBArticle:
    def test_index_adds_embedding_field(self, mock_voyage, mock_kb_collection, sample_kb_article):
        from darwin.retrieval import index_kb_article
        mock_kb_collection.find_one.return_value = None
        result = index_kb_article(sample_kb_article)
        assert "embedding" in result
        assert len(result["embedding"]) == 1024

    def test_index_calls_save(self, mock_voyage, mock_kb_collection, sample_kb_article):
        from darwin.retrieval import index_kb_article
        mock_kb_collection.find_one.return_value = None
        index_kb_article(sample_kb_article)
        assert mock_kb_collection.insert_one.called or mock_kb_collection.replace_one.called

    def test_index_preserves_article_fields(self, mock_voyage, mock_kb_collection, sample_kb_article):
        from darwin.retrieval import index_kb_article
        mock_kb_collection.find_one.return_value = None
        result = index_kb_article(sample_kb_article)
        assert result["id"] == sample_kb_article["id"]
        assert result["title"] == sample_kb_article["title"]
        assert result["source"] == "seed"


class TestRetrieveKBs:
    def _make_kb_result(self, article_id: str, score: float = 0.92) -> dict:
        return {
            "id": article_id,
            "title": f"Runbook: {article_id}",
            "body": "Some runbook content",
            "service": "general",
            "tags": ["database"],
            "source": "seed",
            "score": score,
        }

    def test_retrieve_returns_list(self, mock_voyage, mock_kb_collection, sample_normal_incident):
        from darwin.retrieval import retrieve_kbs
        mock_kb_collection.aggregate.return_value = [self._make_kb_result("kb_001")]
        results = retrieve_kbs(sample_normal_incident)
        assert isinstance(results, list)

    def test_retrieve_top_result_is_most_relevant(self, mock_voyage, mock_kb_collection,
                                                   sample_normal_incident):
        from darwin.retrieval import retrieve_kbs
        mock_kb_collection.aggregate.return_value = [
            self._make_kb_result("kb_001", score=0.95),
            self._make_kb_result("kb_002", score=0.80),
        ]
        results = retrieve_kbs(sample_normal_incident, top_k=2)
        assert results[0]["id"] == "kb_001"

    def test_retrieve_strips_embedding_field(self, mock_voyage, mock_kb_collection,
                                             sample_normal_incident):
        from darwin.retrieval import retrieve_kbs
        result_with_embedding = {**self._make_kb_result("kb_001"), "embedding": FAKE_EMBEDDING}
        mock_kb_collection.aggregate.return_value = [result_with_embedding]
        results = retrieve_kbs(sample_normal_incident)
        for r in results:
            assert "embedding" not in r

    def test_retrieve_adds_similarity_score(self, mock_voyage, mock_kb_collection,
                                            sample_normal_incident):
        from darwin.retrieval import retrieve_kbs
        mock_kb_collection.aggregate.return_value = [self._make_kb_result("kb_001", score=0.87)]
        results = retrieve_kbs(sample_normal_incident)
        assert "similarity" in results[0]
        assert 0.0 <= results[0]["similarity"] <= 1.0

    def test_retrieve_uses_vectorsearch_pipeline(self, mock_voyage, mock_kb_collection,
                                                  sample_normal_incident):
        from darwin.retrieval import retrieve_kbs
        mock_kb_collection.aggregate.return_value = []
        retrieve_kbs(sample_normal_incident)
        pipeline = mock_kb_collection.aggregate.call_args[0][0]
        assert pipeline[0].get("$vectorSearch") is not None
        vs = pipeline[0]["$vectorSearch"]
        assert vs["path"] == "embedding"
        assert vs["queryVector"] == FAKE_EMBEDDING

    def test_edge_case_incident_can_still_retrieve(self, mock_voyage, mock_kb_collection,
                                                    sample_edge_case_incident):
        """Corner cases can retrieve KB (may get irrelevant results — that's the point)."""
        from darwin.retrieval import retrieve_kbs
        mock_kb_collection.aggregate.return_value = []
        results = retrieve_kbs(sample_edge_case_incident)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Integration tests — hit real Voyage + Atlas
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRetrievalIntegration:
    def test_embed_one_real_voyage(self):
        from darwin.retrieval import embed_one
        from config import VOYAGE_EMBEDDING_DIM
        vec = embed_one("PostgreSQL connection pool exhaustion", input_type="query")
        assert len(vec) == VOYAGE_EMBEDDING_DIM
        assert any(v != 0.0 for v in vec)

    def test_retrieve_from_real_atlas(self, sample_normal_incident):
        """Requires seed KB to be loaded (run setup_atlas_vector.py first)."""
        from darwin.retrieval import retrieve_kbs
        from darwin.storage import knowledge_articles
        count = knowledge_articles.count_documents({"source": "seed"})
        if count == 0:
            pytest.skip("Seed KB not loaded — run scripts/setup_atlas_vector.py first")
        results = retrieve_kbs(sample_normal_incident, top_k=3)
        assert isinstance(results, list)
        # May return 0 results if index not ready yet, that's ok
        for r in results:
            assert "embedding" not in r
            assert "similarity" in r
