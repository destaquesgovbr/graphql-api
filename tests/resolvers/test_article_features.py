"""Testes do campo lazy `Article.features` (Fase 1).

O campo resolve features computadas (entidades, popularidade, leitura) sob
demanda do Postgres (`news_features`) via DataLoader, sem tocar no Typesense.
Mock de datasources; schema mínimo (article + features).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext
from graphql_api.datasources.typesense import ArticleDocument
from graphql_api.schema.resolvers.articles import ArticleQuery
from graphql_api.schema.resolvers.health import HealthQuery


@strawberry.type
class _Query(HealthQuery, ArticleQuery):
    pass


test_schema = strawberry.Schema(query=_Query)

ARTICLE_FEATURES_QUERY = """
    query($id: String!) {
        article(uniqueId: $id) {
            uniqueId
            features {
                entities { text type count }
                viewCount
                uniqueSessions
                trendingScore
                wordCount
                readabilityFlesch
            }
        }
    }
"""


def _ts_with_article(unique_id="a"):
    ts = MagicMock()
    ts.get_article_by_id.return_value = ArticleDocument(
        unique_id=unique_id,
        title=f"Titulo {unique_id}",
        url=f"https://example.gov.br/{unique_id}",
    )
    return ts


def _pg_with_features(features_by_id):
    pg = MagicMock()
    pg.get_features_batch = AsyncMock(return_value=features_by_id)
    return pg


@pytest.mark.asyncio
async def test_features_mapped_from_jsonb():
    ts = _ts_with_article("a")
    pg = _pg_with_features(
        {
            "a": {
                "word_count": 123,
                "trending_score": 2.5,
                "readability_flesch": 60.0,
                "view_count": 50,
                "unique_sessions": 30,
                "entities": [
                    {"text": "Ministério da Saúde", "type": "ORG", "count": 3},
                    {"text": "Brasília", "type": "LOC", "count": 1},
                ],
            }
        }
    )
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    feats = result.data["article"]["features"]
    assert feats["wordCount"] == 123
    assert feats["trendingScore"] == 2.5
    assert feats["viewCount"] == 50
    assert feats["uniqueSessions"] == 30
    assert feats["readabilityFlesch"] == 60.0
    assert feats["entities"] == [
        {"text": "Ministério da Saúde", "type": "ORG", "count": 3},
        {"text": "Brasília", "type": "LOC", "count": 1},
    ]
    pg.get_features_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_features_null_when_no_row():
    ts = _ts_with_article("a")
    pg = _pg_with_features({})  # nenhuma feature para "a"
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    assert result.data["article"]["features"] is None


@pytest.mark.asyncio
async def test_features_null_without_postgres():
    ts = _ts_with_article("a")
    ctx = GraphQLContext(typesense_ds=ts)  # sem postgres_ds → loader None
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    assert result.data["article"]["features"] is None


@pytest.mark.asyncio
async def test_entities_default_empty_when_only_scalar_features():
    ts = _ts_with_article("a")
    pg = _pg_with_features({"a": {"word_count": 10}})
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    feats = result.data["article"]["features"]
    assert feats["entities"] == []
    assert feats["wordCount"] == 10
    assert feats["viewCount"] is None


@pytest.mark.asyncio
async def test_malformed_entities_are_skipped():
    ts = _ts_with_article("a")
    pg = _pg_with_features(
        {
            "a": {
                "entities": [
                    {"text": "Válida", "type": "ORG", "count": 2},
                    {"type": "ORG"},  # sem text → ignorada
                    "string-solta",  # não-dict → ignorada
                    {"text": "Sem count", "type": "PER"},  # count default 1
                ]
            }
        }
    )
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    ents = result.data["article"]["features"]["entities"]
    assert ents == [
        {"text": "Válida", "type": "ORG", "count": 2},
        {"text": "Sem count", "type": "PER", "count": 1},
    ]


@pytest.mark.asyncio
async def test_bool_scalars_are_rejected():
    # bool é subclasse de int — não deve ser interpretado como número de feature.
    ts = _ts_with_article("a")
    pg = _pg_with_features(
        {"a": {"view_count": True, "trending_score": False, "word_count": 7}}
    )
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    feats = result.data["article"]["features"]
    assert feats["viewCount"] is None
    assert feats["trendingScore"] is None
    assert feats["wordCount"] == 7


@pytest.mark.asyncio
async def test_entity_count_zero_coerced_to_one():
    ts = _ts_with_article("a")
    pg = _pg_with_features({"a": {"entities": [{"text": "X", "type": "ORG", "count": 0}]}})
    ctx = GraphQLContext(typesense_ds=ts, postgres_ds=pg)
    result = await test_schema.execute(
        ARTICLE_FEATURES_QUERY, variable_values={"id": "a"}, context_value=ctx
    )
    assert result.errors is None, f"Errors: {result.errors}"
    ents = result.data["article"]["features"]["entities"]
    assert ents == [{"text": "X", "type": "ORG", "count": 1}]
