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
