from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    PostgresDatasource,
    SimilarArticleRecord,
)


def _make_mock_pool(fetchval_result=None, fetch_result=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    conn.fetch = AsyncMock(return_value=fetch_result or [])

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


class TestGetSimilarArticles:
    @pytest.mark.asyncio
    async def test_similar_articles_returns_list(self):
        similar_rows = [
            {"unique_id": "news-002", "similarity": 0.95},
            {"unique_id": "news-003", "similarity": 0.88},
            {"unique_id": "news-004", "similarity": 0.82},
        ]
        # fetchval devolve o embedding do artigo base (texto pgvector), reusado
        # como literal `$1::vector` na busca por vizinhos (HNSW).
        pool, conn = _make_mock_pool(
            fetchval_result="[0.1, 0.2, 0.3]", fetch_result=similar_rows
        )
        ds = PostgresDatasource(pool)

        result = await ds.get_similar_articles("news-001", threshold=0.8, limit=5)

        assert len(result) == 3
        assert all(isinstance(r, SimilarArticleRecord) for r in result)
        assert result[0].unique_id == "news-002"
        assert result[0].similarity == 0.95
        assert result[1].similarity == 0.88
        assert result[2].similarity == 0.82
        conn.fetchval.assert_awaited_once()
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_similar_articles_no_embedding_returns_empty(self):
        # Sem embedding → fetchval devolve None (content_embedding::text IS NULL).
        pool, conn = _make_mock_pool(fetchval_result=None, fetch_result=[])
        ds = PostgresDatasource(pool)

        result = await ds.get_similar_articles("news-no-embed")

        assert result == []
        conn.fetchval.assert_awaited_once()
        # Não deve buscar vizinhos se o artigo base não tem embedding.
        conn.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_threshold_filters_below_in_python(self):
        # O SQL devolve nearest-first; o threshold corta os abaixo do limiar.
        similar_rows = [
            {"unique_id": "near", "similarity": 0.95},
            {"unique_id": "mid", "similarity": 0.70},
            {"unique_id": "far", "similarity": 0.50},
        ]
        pool, _ = _make_mock_pool(
            fetchval_result="[0.1, 0.2, 0.3]", fetch_result=similar_rows
        )
        ds = PostgresDatasource(pool)

        result = await ds.get_similar_articles("news-001", threshold=0.8, limit=5)

        assert [r.unique_id for r in result] == ["near"]
