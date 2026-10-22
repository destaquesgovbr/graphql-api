import re
from unittest.mock import AsyncMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext
from graphql_api.datasources.postgres import NewsRecord
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
    async def test_unique_content_count(self):
        mock_pg = AsyncMock()
        mock_pg.entity_coverage = AsyncMock(return_value=[
            {
                "period": "2026-10-01 00:00:00+00",
                "agency_key": "mec",
                "agency_name": "MEC",
                "article_count": 3,
                "unique_content_count": 2,
                "total_mentions": 5,
                "avg_sentiment_score": None,
            },
            # Só republicações: nenhum conteúdo creditado a esta agência.
            {
                "period": "2026-10-01 00:00:00+00",
                "agency_key": "ebc",
                "agency_name": "EBC",
                "article_count": 2,
                "unique_content_count": 0,
                "total_mentions": 2,
                "avg_sentiment_score": None,
            },
        ])
        query = """
        query {
            entityCoverage(entityId: "Q1", dateFrom: "2026-10-01", dateTo: "2026-10-08", granularity: DAY) {
                agencyKey articleCount uniqueContentCount
            }
        }
        """
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        assert result.data["entityCoverage"] == [
            {"agencyKey": "mec", "articleCount": 3, "uniqueContentCount": 2},
            {"agencyKey": "ebc", "articleCount": 2, "uniqueContentCount": 0},
        ]
        mock_pg.entity_coverage.assert_awaited_once_with("Q1", "day", "2026-10-01", "2026-10-08")

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


class TestEntityCoverageSDL:
    def test_descricao_documenta_dias_utc_e_date_to_exclusivo(self):
        field = next(
            f for f in test_schema.get_type_by_name("Query").fields if f.python_name == "entity_coverage"
        )
        assert "dias UTC" in field.description
        assert "dateFrom inclusivo" in field.description
        assert "dateTo exclusivo" in field.description


    def test_unique_content_count_no_sdl(self):
        sdl = test_schema.as_str()
        block = sdl.split("type EntityCoveragePoint {", 1)[1].split("}", 1)[0]
        assert "uniqueContentCount: Int!" in block

    def test_unique_content_count_documenta_chave_de_quase_duplicata(self):
        # A chave deixou de ser só o hash exato: a descrição precisa dizer que
        # quase-duplicatas (news.content_key) contam como o mesmo conteúdo.
        field = next(
            f
            for f in test_schema.get_type_by_name("EntityCoveragePoint").fields
            if f.python_name == "unique_content_count"
        )
        assert "content_key" in field.description
        assert "quase-duplicatas" in field.description


class TestEntityArticlesContentKey:
    """`Article.contentKey` no entityArticles (Postgres): a mesma chave do
    uniqueContentCount, para o cliente agrupar republicações."""

    @pytest.mark.asyncio
    async def test_entity_articles_expoe_content_key(self):
        mock_pg = AsyncMock()
        mock_pg.get_entity_articles = AsyncMock(
            return_value=(
                [
                    NewsRecord(unique_id="b", title="B", url="https://b", agency_key="ebc", content_key="a"),
                    NewsRecord(unique_id="a", title="A", url="https://a", agency_key="mec", content_key="a"),
                    NewsRecord(unique_id="c", title="C", url="https://c", agency_key="mec", content_key="c"),
                ],
                3,
            )
        )
        query = '{ entityArticles(entityId: "Q1") { found articles { uniqueId agency contentKey } } }'
        result = await test_schema.execute(query, context_value=_make_ctx(postgres_ds=mock_pg))
        assert result.errors is None
        arts = result.data["entityArticles"]["articles"]
        assert [(a["uniqueId"], a["contentKey"]) for a in arts] == [("b", "a"), ("a", "a"), ("c", "c")]

    def test_content_key_no_sdl_nulavel_e_documentado(self):
        art = test_schema.get_type_by_name("Article")
        field = next(f for f in art.fields if f.python_name == "content_key")
        # Nulável: null nos caminhos do Typesense.
        assert re.search(r"^\s*contentKey: String$", test_schema.as_str(), re.M)
        # Só os caminhos servidos pelo Postgres preenchem; o Typesense ainda não
        # indexa a chave.
        assert "entityArticles" in field.description
        assert "Typesense" in field.description
