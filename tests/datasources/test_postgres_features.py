import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import _UPSERT_FEATURES_SQL, PostgresDatasource


def _make_mock_pool():
    """Create a mock asyncpg pool with acquire/transaction context managers."""
    pool = AsyncMock()

    # Direct execute on pool (for upsert_features)
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    # Connection returned by acquire
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    # Transaction context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    # Acquire context manager
    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)

    return pool, conn


class TestUpsertFeatures:
    @pytest.mark.asyncio
    async def test_upsert_features_merges_jsonb(self):
        pool, _ = _make_mock_pool()
        ds = PostgresDatasource(pool)

        features = {"sentiment_score": 0.85, "word_count": 350}
        result = await ds.upsert_features("news-123", features)

        assert result is True
        pool.execute.assert_awaited_once_with(
            _UPSERT_FEATURES_SQL, "news-123", json.dumps(features)
        )

    @pytest.mark.asyncio
    async def test_upsert_nonexistent_news_returns_false(self):
        pool, _ = _make_mock_pool()
        pool.execute = AsyncMock(return_value=None)
        ds = PostgresDatasource(pool)

        result = await ds.upsert_features("nonexistent-id", {"key": "value"})

        assert result is False


class TestBatchUpsertFeatures:
    @pytest.mark.asyncio
    async def test_batch_upsert_features_processes_all(self):
        pool, conn = _make_mock_pool()
        ds = PostgresDatasource(pool)

        items = [
            ("news-1", {"sentiment_score": 0.9}),
            ("news-2", {"word_count": 200}),
            ("news-3", {"has_image": True}),
        ]

        processed, failed = await ds.batch_upsert_features(items)

        assert processed == 3
        assert failed == 0
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_batch_upsert_empty_returns_zero(self):
        pool, conn = _make_mock_pool()
        ds = PostgresDatasource(pool)

        processed, failed = await ds.batch_upsert_features([])

        assert processed == 0
        assert failed == 0
        conn.execute.assert_not_awaited()


def _make_fetch_pool(rows):
    """Pool mock com `conn.fetch` retornando `rows` (lista de dicts)."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    acq = AsyncMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    return pool, conn


class TestGetFeaturesBatch:
    @pytest.mark.asyncio
    async def test_returns_features_keyed_by_unique_id(self):
        rows = [
            {
                "unique_id": "a",
                "features": {
                    "word_count": 100,
                    "entities": [{"text": "X", "type": "ORG", "count": 2}],
                },
            },
            {"unique_id": "b", "features": {"trending_score": 1.5}},
        ]
        pool, conn = _make_fetch_pool(rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch(["a", "b", "c"])

        assert result["a"]["word_count"] == 100
        assert result["a"]["entities"][0]["text"] == "X"
        assert result["b"]["trending_score"] == 1.5
        assert "c" not in result  # ausente em news_features
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_jsonb_string_is_parsed(self):
        # asyncpg devolve jsonb como str (sem codec) — deve ser desserializado.
        rows = [{"unique_id": "a", "features": '{"word_count": 5}'}]
        pool, _ = _make_fetch_pool(rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch(["a"])

        assert result["a"]["word_count"] == 5

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_no_query(self):
        pool, conn = _make_fetch_pool([])
        ds = PostgresDatasource(pool)

        result = await ds.get_features_batch([])

        assert result == {}
        conn.fetch.assert_not_awaited()
