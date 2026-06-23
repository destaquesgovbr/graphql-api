from unittest.mock import AsyncMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext
from graphql_api.schema.resolvers.entities import EntityQuery
from graphql_api.schema.resolvers.health import HealthQuery


@strawberry.type
class _Query(HealthQuery, EntityQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _make_ctx(postgres_ds=None):
    return GraphQLContext(postgres_ds=postgres_ds)


_FULL_QUERY = """
query {
  trendingEntities(limit: 10) {
    entityId
    canonicalName
    type
    trendingScore
    volumeRatio
    windowCount
    windowAgencies
    computedAt
  }
}
"""


def _row():
    return {
        "entity_id": "Q12345",
        "canonical_name": "Ministério da Saúde",
        "type": "ORG",
        "trending_score": 4.25,
        "volume_ratio": 3.2,
        "window_count": 12,
        "window_agencies": 8,
        "computed_at": "2026-06-23 10:00:00",
    }


class TestTrendingEntities:
    @pytest.mark.asyncio
    async def test_retorna_lista_com_campos_corretos(self):
        mock_pg = AsyncMock()
        mock_pg.get_trending_entities = AsyncMock(return_value=[_row()])
        result = await test_schema.execute(_FULL_QUERY, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        items = result.data["trendingEntities"]
        assert len(items) == 1
        item = items[0]
        assert item["entityId"] == "Q12345"
        assert item["canonicalName"] == "Ministério da Saúde"
        assert item["type"] == "ORG"
        assert item["trendingScore"] == 4.25
        assert item["volumeRatio"] == 3.2
        assert item["windowCount"] == 12
        assert item["windowAgencies"] == 8
        assert item["computedAt"] == "2026-06-23 10:00:00"

    @pytest.mark.asyncio
    async def test_limit_clampado_a_50(self):
        mock_pg = AsyncMock()
        mock_pg.get_trending_entities = AsyncMock(return_value=[])
        query = "{ trendingEntities(limit: 100) { entityId } }"
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        mock_pg.get_trending_entities.assert_awaited_once_with(50)

    @pytest.mark.asyncio
    async def test_datasource_chamado_com_limit_correto(self):
        mock_pg = AsyncMock()
        mock_pg.get_trending_entities = AsyncMock(return_value=[])
        query = "{ trendingEntities(limit: 5) { entityId } }"
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        mock_pg.get_trending_entities.assert_awaited_once_with(5)

    @pytest.mark.asyncio
    async def test_retorna_lista_vazia(self):
        mock_pg = AsyncMock()
        mock_pg.get_trending_entities = AsyncMock(return_value=[])
        query = "{ trendingEntities(limit: 10) { entityId } }"
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        assert result.data["trendingEntities"] == []

    @pytest.mark.asyncio
    async def test_campos_nulos_sao_tratados_com_fallback(self):
        mock_pg = AsyncMock()
        mock_pg.get_trending_entities = AsyncMock(
            return_value=[
                {
                    "entity_id": "Q999",
                    "canonical_name": None,
                    "type": None,
                    "trending_score": None,
                    "volume_ratio": None,
                    "window_count": None,
                    "window_agencies": None,
                    "computed_at": None,
                }
            ]
        )
        result = await test_schema.execute(_FULL_QUERY, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        item = result.data["trendingEntities"][0]
        assert item["entityId"] == "Q999"
        assert item["canonicalName"] == ""
        assert item["type"] == ""
        assert item["trendingScore"] == 0.0
        assert item["volumeRatio"] == 0.0
        assert item["windowCount"] == 0
        assert item["windowAgencies"] == 0
        assert item["computedAt"] is None
