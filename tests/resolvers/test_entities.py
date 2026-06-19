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


class TestEntityCoverage:
    @pytest.mark.asyncio
    async def test_retorna_pontos_de_cobertura(self):
        mock_pg = AsyncMock()
        mock_pg.entity_coverage = AsyncMock(return_value=[
            {
                "period": "2024-01",
                "agency_key": "mec",
                "agency_name": "Ministério da Educação",
                "article_count": 12,
                "total_mentions": 38,
                "avg_sentiment_score": 0.55,
            }
        ])
        query = """
        query {
            entityCoverage(entityId: "Q4294522" granularity: MONTH) {
                period agencyKey agencyName articleCount totalMentions avgSentimentScore
            }
        }
        """
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        pts = result.data["entityCoverage"]
        assert len(pts) == 1
        assert pts[0]["period"] == "2024-01"
        assert pts[0]["articleCount"] == 12
        mock_pg.entity_coverage.assert_awaited_once_with("Q4294522", "month", None, None)

    @pytest.mark.asyncio
    async def test_entity_id_vazio_retorna_lista_vazia(self):
        mock_pg = AsyncMock()
        mock_pg.entity_coverage = AsyncMock(return_value=[])
        query = '{ entityCoverage(entityId: "inexistente") { period } }'
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        assert result.data["entityCoverage"] == []


class TestEntitySearch:
    @pytest.mark.asyncio
    async def test_retorna_resultados_por_nome(self):
        mock_pg = AsyncMock()
        mock_pg.entity_search = AsyncMock(return_value=[
            {
                "entity_id": "Q4294522",
                "canonical_name": "Ministério da Educação",
                "type": "ORG",
                "description": "Ministério federal",
                "wikidata_url": "https://www.wikidata.org/wiki/Q4294522",
                "agency_key": "mec",
                "aliases": ["MEC"],
                "article_count": 1223,
                "confidence": 1.0,
                "match_type": "alias_exact",
            }
        ])
        query = """
        query {
            entitySearch(query: "MEC" entityType: ORG) {
                entityId canonicalName type aliases articleCount confidence matchType
            }
        }
        """
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        hits = result.data["entitySearch"]
        assert len(hits) == 1
        assert hits[0]["entityId"] == "Q4294522"
        assert hits[0]["confidence"] == 1.0
        mock_pg.entity_search.assert_awaited_once_with("MEC", "ORG", 5)

    @pytest.mark.asyncio
    async def test_query_vazia_retorna_lista_vazia(self):
        mock_pg = AsyncMock()
        query = '{ entitySearch(query: "   ") { entityId } }'
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        assert result.data["entitySearch"] == []
