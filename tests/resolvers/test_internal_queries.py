from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, ServiceAccount
from graphql_api.datasources.postgres import NewsRecord, TypesenseDocRecord
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.internal_queries import InternalQuery


@strawberry.type
class _Query(HealthQuery, InternalQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


def _make_internal_context(postgres_ds=None):
    ctx = GraphQLContext(postgres_ds=postgres_ds)
    ctx.service_account = ServiceAccount(email="worker@project.iam.gserviceaccount.com")
    return ctx


def _make_public_context():
    return GraphQLContext()


def _sample_news_record(**overrides) -> NewsRecord:
    defaults = dict(
        unique_id="news-001",
        title="Governo anuncia programa",
        url="https://gov.br/news/001",
        image_url="https://gov.br/img/001.jpg",
        video_url=None,
        content="Conteudo completo.",
        summary="Resumo.",
        subtitle="Subtitulo",
        editorial_lead="Lead",
        category="economia",
        tags=["economia", "governo"],
        agency_key="agencia-brasil",
        agency_name="Agencia Brasil",
        published_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        extracted_at=datetime(2024, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
        theme_l1_code="ECO",
        theme_l1_label="Economia",
        theme_l2_code="ECO-FIN",
        theme_l2_label="Financas",
        theme_l3_code=None,
        theme_l3_label=None,
        most_specific_theme_code="ECO-FIN",
        most_specific_theme_label="Financas",
        features={"word_count": 350},
    )
    defaults.update(overrides)
    return NewsRecord(**defaults)


def _sample_typesense_record(**overrides) -> TypesenseDocRecord:
    defaults = dict(
        unique_id="news-001",
        title="Governo anuncia programa",
        url="https://gov.br/news/001",
        image_url="https://gov.br/img/001.jpg",
        video_url=None,
        content="Conteudo completo.",
        summary="Resumo.",
        subtitle="Subtitulo",
        editorial_lead="Lead",
        category="economia",
        tags=["economia"],
        agency_key="agencia-brasil",
        agency_name="Agencia Brasil",
        published_at=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
        extracted_at=datetime(2024, 6, 15, 11, 0, 0, tzinfo=timezone.utc),
        theme_l1_code="ECO",
        theme_l1_label="Economia",
        theme_l2_code=None,
        theme_l2_label=None,
        theme_l3_code=None,
        theme_l3_label=None,
        most_specific_theme_code="ECO",
        most_specific_theme_label="Economia",
        features={"word_count": 350},
        content_embedding=[0.1, 0.2, 0.3],
        sentiment_label="positive",
        sentiment_score=0.85,
        trending_score=12.5,
        word_count=350,
        has_image=True,
        has_video=False,
        image_broken=False,
        readability_flesch=45.2,
    )
    defaults.update(overrides)
    return TypesenseDocRecord(**defaults)


class TestNewsByIdQuery:
    @pytest.mark.asyncio
    async def test_news_by_id_forbidden_without_service_account(self):
        result = await test_schema.execute(
            """
            query($uniqueId: String!) {
                newsById(uniqueId: $uniqueId) {
                    uniqueId
                    title
                }
            }
            """,
            variable_values={"uniqueId": "news-001"},
            context_value=_make_public_context(),
        )

        assert result.errors is not None
        assert len(result.errors) > 0
        assert "FORBIDDEN" in str(result.errors[0].message)

    @pytest.mark.asyncio
    async def test_news_by_id_with_service_account(self):
        mock_pg = AsyncMock()
        mock_pg.get_news_by_id = AsyncMock(return_value=_sample_news_record())

        result = await test_schema.execute(
            """
            query($uniqueId: String!) {
                newsById(uniqueId: $uniqueId) {
                    uniqueId
                    title
                    url
                    imageUrl
                    category
                    tags
                    agencyKey
                    agencyName
                    themeL1Code
                    themeL1Label
                    themeL2Code
                    themeL2Label
                    mostSpecificThemeCode
                    mostSpecificThemeLabel
                    features
                }
            }
            """,
            variable_values={"uniqueId": "news-001"},
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["newsById"]
        assert data["uniqueId"] == "news-001"
        assert data["title"] == "Governo anuncia programa"
        assert data["agencyKey"] == "agencia-brasil"
        assert data["themeL1Code"] == "ECO"
        assert data["tags"] == ["economia", "governo"]
        mock_pg.get_news_by_id.assert_awaited_once_with("news-001")


class TestNewsBatchQuery:
    @pytest.mark.asyncio
    async def test_news_batch_returns_list(self):
        mock_pg = AsyncMock()
        records = [_sample_news_record(unique_id=f"news-{i}") for i in range(3)]
        mock_pg.get_news_batch = AsyncMock(return_value=records)

        result = await test_schema.execute(
            """
            query($uniqueIds: [String!]!) {
                newsBatch(uniqueIds: $uniqueIds) {
                    uniqueId
                    title
                }
            }
            """,
            variable_values={"uniqueIds": ["news-0", "news-1", "news-2"]},
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["newsBatch"]
        assert len(data) == 3
        assert data[0]["uniqueId"] == "news-0"
        assert data[2]["uniqueId"] == "news-2"
        mock_pg.get_news_batch.assert_awaited_once()


class TestNewsForTypesenseQuery:
    @pytest.mark.asyncio
    async def test_news_for_typesense_returns_document(self):
        mock_pg = AsyncMock()
        mock_pg.get_news_for_typesense = AsyncMock(
            return_value=_sample_typesense_record()
        )

        result = await test_schema.execute(
            """
            query($uniqueId: String!) {
                newsForTypesense(uniqueId: $uniqueId) {
                    uniqueId
                    title
                    contentEmbedding
                    sentimentLabel
                    sentimentScore
                    trendingScore
                    wordCount
                    hasImage
                    hasVideo
                    imageBroken
                    readabilityFlesch
                }
            }
            """,
            variable_values={"uniqueId": "news-001"},
            context_value=_make_internal_context(postgres_ds=mock_pg),
        )

        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["newsForTypesense"]
        assert data["uniqueId"] == "news-001"
        assert data["contentEmbedding"] == [0.1, 0.2, 0.3]
        assert data["sentimentLabel"] == "positive"
        assert data["sentimentScore"] == 0.85
        assert data["trendingScore"] == 12.5
        assert data["wordCount"] == 350
        assert data["hasImage"] is True
        assert data["hasVideo"] is False
        assert data["readabilityFlesch"] == 45.2
        mock_pg.get_news_for_typesense.assert_awaited_once_with("news-001")
