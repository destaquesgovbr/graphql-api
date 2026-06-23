from unittest.mock import AsyncMock, MagicMock

import pytest

from graphql_api.datasources.postgres import PostgresDatasource


def _make_mock_pool(fetch_results=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=list(fetch_results or [[]]))
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


def _rows():
    return [
        {
            "entity_id": "Q12345",
            "canonical_name": "Ministério da Saúde",
            "type": "ORG",
            "trending_score": 4.25,
            "volume_ratio": 3.2,
            "window_count": 12,
            "window_agencies": 8,
            "computed_at": "2026-06-23 10:00:00",
        },
        {
            "entity_id": "Q67890",
            "canonical_name": "Ministério da Educação",
            "type": "ORG",
            "trending_score": 3.1,
            "volume_ratio": 2.0,
            "window_count": 7,
            "window_agencies": 4,
            "computed_at": "2026-06-23 10:00:00",
        },
    ]


class TestGetTrendingEntities:
    @pytest.mark.asyncio
    async def test_retorna_lista_de_dicts(self):
        pool, conn = _make_mock_pool([_rows()])
        ds = PostgresDatasource(pool)
        result = await ds.get_trending_entities(10)
        assert len(result) == 2
        assert all(isinstance(r, dict) for r in result)

    @pytest.mark.asyncio
    async def test_campos_mapeados_corretamente(self):
        pool, conn = _make_mock_pool([_rows()])
        ds = PostgresDatasource(pool)
        result = await ds.get_trending_entities(10)
        first = result[0]
        assert first["entity_id"] == "Q12345"
        assert first["canonical_name"] == "Ministério da Saúde"
        assert first["type"] == "ORG"
        assert first["trending_score"] == 4.25
        assert first["volume_ratio"] == 3.2
        assert first["window_count"] == 12
        assert first["window_agencies"] == 8
        assert first["computed_at"] == "2026-06-23 10:00:00"

    @pytest.mark.asyncio
    async def test_retorna_vazio_quando_sem_dados(self):
        pool, conn = _make_mock_pool([[]])
        ds = PostgresDatasource(pool)
        result = await ds.get_trending_entities(10)
        assert result == []

    @pytest.mark.asyncio
    async def test_usa_limit_parametro(self):
        pool, conn = _make_mock_pool([[]])
        ds = PostgresDatasource(pool)
        await ds.get_trending_entities(7)
        args = conn.fetch.await_args.args
        assert args[1] == 7
