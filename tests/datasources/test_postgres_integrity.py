from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import (
    IntegrityCandidateRecord,
    PostgresDatasource,
)


def _make_mock_pool(fetch_result=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_result or [])

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


class TestGetIntegrityBatch:
    @pytest.mark.asyncio
    async def test_integrity_batch_returns_prioritized(self):
        rows = [
            {
                "unique_id": "news-001",
                "url": "https://gov.br/news/001",
                "image_url": "https://gov.br/img/001.jpg",
                "published_at": datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
                "integrity": None,
            },
            {
                "unique_id": "news-002",
                "url": "https://gov.br/news/002",
                "image_url": None,
                "published_at": datetime(2024, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
                "integrity": {"checked_at": "2024-06-01T00:00:00Z", "url_ok": True},
            },
        ]
        pool, conn = _make_mock_pool(fetch_result=rows)
        ds = PostgresDatasource(pool)

        result = await ds.get_integrity_batch(batch_size=50)

        assert len(result) == 2
        assert all(isinstance(r, IntegrityCandidateRecord) for r in result)
        assert result[0].unique_id == "news-001"
        assert result[0].url == "https://gov.br/news/001"
        assert result[0].integrity is None
        assert result[1].unique_id == "news-002"
        assert result[1].integrity["url_ok"] is True
        conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_integrity_excludes_recently_checked(self):
        """When no candidates match the criteria, returns empty."""
        pool, conn = _make_mock_pool(fetch_result=[])
        ds = PostgresDatasource(pool)

        result = await ds.get_integrity_batch(batch_size=50)

        assert result == []
        conn.fetch.assert_awaited_once()
        # Verify the SQL includes the interval check
        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        assert "INTERVAL" in sql
        assert "7 days" in sql
